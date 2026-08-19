"""最小权限审批模型（L1 权限门）。

裁剪掉 profile 维度，只保留我们需要的判定链与风险启发式（移植来源见 NOTICE.md）。

判定链（evaluate）：
    1. 持久化规则（用户"记住"的 allow/deny，.workdir/permissions.json）—— 命中直接返回；
    2. FORCE_ASK_TOOLS（execute_code 等写操作工具）—— 默认必须显式授权；
    3. policy.tool_overrides / default 策略表；
    4. ALLOW 时再跑风险启发式（pip install / 破坏性代码 / 外部路径）升级为 ASK；
    5. ASK 命中 → approval_callback 同步阻塞等用户审批（SSE 发 permission_request）。

execute_code 默认 ASK：首次必然询问；用户"记住允许"后写入持久化规则，后续直接放行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Callable

# 写操作工具：默认每次显式授权（可被持久化规则覆盖）
FORCE_ASK_TOOLS = frozenset({"execute_code"})


class PermissionAction(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class PermissionDecision:
    action: PermissionAction
    reason: str = ""
    rule: str = ""

    @property
    def allowed(self) -> bool:
        return self.action == PermissionAction.ALLOW


@dataclass
class PermissionPolicy:
    """工具调用授权策略表。"""

    default: PermissionAction = PermissionAction.ALLOW
    tool_overrides: dict[str, PermissionAction] = field(default_factory=dict)
    wildcard_rules: list[dict[str, Any]] = field(default_factory=list)
    ask_on_external_paths: bool = True
    workspace_path: str | None = None
    enforce: bool = True


class PermissionRuntime:
    """执行前评估工具调用是否被允许。"""

    def __init__(
        self,
        *,
        policy: PermissionPolicy | None = None,
        approval_callback: Callable[[str, dict[str, Any], PermissionDecision], PermissionDecision] | None = None,
    ) -> None:
        self.policy = policy or PermissionPolicy()
        self.approval_callback = approval_callback

    def evaluate(self, tool_name: str, arguments: dict[str, Any] | None = None) -> PermissionDecision:
        args = arguments or {}

        # 1) 持久化规则优先（用户"记住"的选择）
        persisted = self._persisted_rule_decision(tool_name)
        if persisted is not None:
            return persisted

        # 2) FORCE_ASK：写操作工具默认要求显式授权
        if tool_name in FORCE_ASK_TOOLS:
            decision = PermissionDecision(
                PermissionAction.ASK,
                f"工具 {tool_name} 会写入文件，需要显式授权。",
                f"tool:{tool_name}:force_ask",
            )
            if self.approval_callback is not None and self.policy.enforce:
                return self.approval_callback(tool_name, args, decision)
            return decision

        # 3) 策略表
        decision = self._policy_rule_decision(tool_name)

        # 4) ALLOW 时再跑风险启发式
        if decision.action == PermissionAction.ALLOW:
            risk = self._risk_decision(tool_name, args)
            if risk is not None:
                decision = risk

        # 5) ASK → 阻塞等用户审批
        if decision.action == PermissionAction.ASK and self.approval_callback is not None and self.policy.enforce:
            return self.approval_callback(tool_name, args, decision)
        return decision

    def _persisted_rule_decision(self, tool_name: str) -> PermissionDecision | None:
        if not self.policy.workspace_path:
            return None
        try:
            from app.permissions.store import PermissionRuleStore

            return PermissionRuleStore(self.policy.workspace_path).match(tool_name)
        except Exception:
            return None

    def _policy_rule_decision(self, tool_name: str) -> PermissionDecision:
        if tool_name in self.policy.tool_overrides:
            return PermissionDecision(
                action=self.policy.tool_overrides[tool_name],
                rule=f"tool:{tool_name}",
            )
        for rule in self.policy.wildcard_rules:
            pattern = str(rule.get("tool") or "")
            if fnmatch(tool_name, pattern):
                return PermissionDecision(
                    action=rule["action"],
                    reason=str(rule.get("reason") or ""),
                    rule=str(rule.get("rule") or f"policy:{pattern}"),
                )
        return PermissionDecision(action=self.policy.default, rule="policy:default")

    def _risk_decision(self, tool_name: str, args: dict[str, Any]) -> PermissionDecision | None:
        if tool_name == "execute_code":
            code = str(args.get("code") or "")
            if self._looks_package_install(code):
                return PermissionDecision(
                    PermissionAction.ASK,
                    "代码疑似安装 Python 包（pip install）。",
                    "risk:pip_install",
                )
            if self._looks_destructive_code(code):
                return PermissionDecision(
                    PermissionAction.ASK,
                    "代码疑似删除或破坏性修改文件。",
                    "risk:destructive_python",
                )

        path = self._extract_path_arg(args)
        if path and self.policy.ask_on_external_paths and self._is_external_path(path):
            return PermissionDecision(
                PermissionAction.ASK,
                f"工具参数指向 workspace 之外的路径：{path}",
                "risk:external_path",
            )
        return None

    @staticmethod
    def _looks_package_install(code: str) -> bool:
        return bool(re.search(r"\b(pip\s+install|python\s+-m\s+pip|subprocess\..*pip)", code))

    @staticmethod
    def _looks_destructive_code(code: str) -> bool:
        patterns = [
            r"\bdelete_file\s*\(",
            r"\bos\.(remove|unlink|rename|replace)\s*\(",
            r"\bPath\s*\([^)]*\)\.unlink\s*\(",
            r"\.unlink\s*\(",
            r"\bshutil\.(rmtree|move)\s*\(",
        ]
        return any(re.search(p, code) for p in patterns)

    @staticmethod
    def _extract_path_arg(args: dict[str, Any]) -> str | None:
        for key in ("path", "file_path", "src", "dst", "input_path", "output_path", "save_path"):
            value = args.get(key)
            if isinstance(value, str) and value:
                return value
        return None

    def _is_external_path(self, raw_path: str) -> bool:
        workspace = self.policy.workspace_path
        if not workspace:
            return False
        try:
            path = Path(raw_path).expanduser()
            if not path.is_absolute():
                return False
            resolved = path.resolve()
            root = Path(workspace).expanduser().resolve()
            return not str(resolved).startswith(str(root))
        except Exception:
            return False


__all__ = [
    "FORCE_ASK_TOOLS",
    "PermissionAction",
    "PermissionDecision",
    "PermissionPolicy",
    "PermissionRuntime",
]
