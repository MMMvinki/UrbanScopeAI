"""SkillStore —— 可执行 Skill（kind=executable）的存储、校验、运行。

可执行 Skill = 一个目录：skill.json（契约）+ SKILL.md + scripts/main.py（执行入口）
+ 可选 README / requirements / examples / runs。**发现机制 = 扫 ``*/skill.json``**，
无注册表文件。两棵目录树：

- ``builtin``：<app>/skills/builtin/<skill_id>/    只读，平台内置（需 copy 才能改）
- ``workspace``：<workspace>/.workdir/skills/<skill_id>/  可变，agent/用户创建

同名 id：workspace 覆盖 builtin。状态机：``draft`` → ``validated``
（工作区首次成功 run 后自动升级）；``revision`` 每次写操作 +1。

运行契约：``python -m app.execution.skill_runner <skill_dir> <input> <output>``，
scripts/main.py 读 input.json（wrapper {workspace, skill_id, params}），算完
``json.dump`` 写 output.json。产物（GeoJSON 图层 / matplotlib 图）经
DATA_REGISTRY 注册上图、ARTIFACT 事件进父 run 的成果栏。

（迁移自 operations/store.py；流程引导 skill（kind=prompt）不在此，由 skills/loader.py 管理。）
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.models.events import EventType
from app.models.skill import SkillRuntime, SkillSpec, SkillValidation
from app.skills.validation import inspect_params_access, validate_skill_code
from app.workspace import SCRIPT_DIR, SKILLS_DIR, WORKSPACE_ROOT

__all__ = ["SkillError", "SkillStore"]


class SkillError(RuntimeError):
    """skill 操作失败（路径冲突 / 契约非法 / 内置只读等），转 ToolResult 失败信息。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _slug(skill_id: str) -> str:
    cleaned = str(skill_id or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,49}", cleaned):
        raise SkillError(
            f"skill id 必须为小写字母开头、仅含小写字母/数字/连字符、2-50 字符：{skill_id!r}"
        )
    return cleaned


def _skill_md_body(skill_dir: Path) -> str:
    """读目录里 SKILL.md（兼容旧 skill.md），剥离 frontmatter 返回方法论正文；无则空串。"""
    for name in ("SKILL.md", "skill.md"):
        md = skill_dir / name
        if not md.is_file():
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        if text.startswith("---"):
            _, _front, *rest = text.split("---", 2)
            return "---".join(rest).strip()
        return text.strip()
    return ""


# 手动 create_skill 未提供 code 时的占位实现（协议合规，可跑通再 edit 替换逻辑）
_DEFAULT_MAIN = '''# -*- coding: utf-8 -*-
import argparse
import json


def _main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    a = p.parse_args()
    with open(a.input, encoding="utf-8") as f:
        wrapper = json.load(f)
    result = {
        "success": True,
        "summary": "skill 占位实现：请用 edit_skill 替换 scripts/main.py 的分析逻辑",
        "params": wrapper.get("params") or {},
    }
    with open(a.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, default=str)


_main()
'''


def _promoted_wrapper(code: str, script_name: str) -> str:
    """promote_script 生成的协议包装：把原脚本包进 --input/--output 契约。

    脚本内可直接用 ``params``（dict，来自 input.json）、``save_plot`` / ``emit_chart``
    （skill_runner 注入）。约定：脚本末尾把结果放进 ``result``（dict）变量，
    否则兜底 success=True。不重写脚本逻辑；原脚本另存 original_<name>.py。
    """
    return (
        "# -*- coding: utf-8 -*-\n"
        f"# 由 promote_script_to_skill 生成（原脚本：{script_name}）。勿手改协议段。\n"
        "import argparse\n"
        "import json\n"
        "\n"
        "\n"
        "def _main():\n"
        "    p = argparse.ArgumentParser()\n"
        "    p.add_argument(\"--input\", required=True)\n"
        "    p.add_argument(\"--output\", required=True)\n"
        "    a = p.parse_args()\n"
        "    with open(a.input, encoding=\"utf-8\") as f:\n"
        "        wrapper = json.load(f)\n"
        "    params = wrapper.get(\"params\") or {}\n"
        "    ns = {\n"
        "        \"__name__\": \"__main__\",\n"
        "        \"__builtins__\": __builtins__,\n"
        "        \"params\": params,\n"
        "        \"save_plot\": save_plot,\n"
        "        \"emit_chart\": emit_chart,\n"
        "    }\n"
        f"    exec(compile({code!r}, \"<promoted-script>\", \"exec\"), ns, ns)\n"
        "    result = ns.get(\"result\")\n"
        "    if not isinstance(result, dict):\n"
        "        result = {\"success\": True, \"summary\": \"脚本执行完成（未设置 result 变量）\"}\n"
        "    with open(a.output, \"w\", encoding=\"utf-8\") as f:\n"
        "        json.dump(result, f, ensure_ascii=False, default=str)\n"
        "\n"
        "\n"
        "_main()\n"
    )


def _dominant_geometry(geojson: dict) -> str:
    """从 GeoJSON features 推断主几何类型（Point / LineString / Polygon）。"""
    counts: dict[str, int] = {}
    for feature in geojson.get("features", [])[:200]:
        geom = feature.get("geometry") or {}
        gtype = str(geom.get("type") or "")
        if gtype == "GeometryCollection":
            types = [g.get("type") or "" for g in geom.get("geometries", [])]
            gtype = types[0] if types else ""
        if gtype:
            counts[gtype] = counts.get(gtype, 0) + 1
    if not counts:
        return "Point"
    return max(counts, key=counts.get)


class SkillStore:
    """工作区 + 内置两棵目录树的可执行 skill 仓库。方法全同步（工具 handler 都是同步签名）。"""

    def __init__(
        self,
        *,
        builtin_root: Path | None = None,
        workspace_root: Path | None = None,
    ) -> None:
        self.builtin_root = builtin_root or (Path(__file__).resolve().parent / "builtin")
        self.workspace_root = workspace_root or SKILLS_DIR

    # ── 列表 / 读取 ──────────────────────────────────────────────

    def list(self, scope: str | None = None) -> list[dict]:
        """扫两棵根目录的 */skill.json，返回轻量 dict（含 status/scope/version/revision/kind）。"""
        scopes: list[tuple[str, Path]] = []
        if scope in (None, "all"):
            scopes = [("workspace", self.workspace_root), ("builtin", self.builtin_root)]
        elif scope in ("workspace", "builtin"):
            scopes = [(scope, self.workspace_root if scope == "workspace" else self.builtin_root)]
        else:
            return []

        seen: set[str] = set()
        out: list[dict] = []
        for s, root in scopes:
            if not root.is_dir():
                continue
            for meta_path in sorted(root.glob("*/skill.json")):
                op = self._load_meta(meta_path)
                if op is None:
                    continue
                if op.id in seen:
                    continue  # workspace 优先，跳过同名 builtin
                seen.add(op.id)
                d = op.model_dump(mode="json")
                d["scope"] = s
                d["kind"] = "executable"
                out.append(d)
        out.sort(key=lambda e: e["id"])
        return out

    def get(self, skill_id: str) -> SkillSpec | None:
        """workspace 优先，回退 builtin。"""
        for root in (self.workspace_root, self.builtin_root):
            meta_path = root / skill_id / "skill.json"
            if meta_path.exists():
                return self._load_meta(meta_path)
        return None

    def read_code(self, skill_id: str) -> str | None:
        op = self.get(skill_id)
        if op is None:
            return None
        loc = self._location(skill_id)
        if loc is None:
            return None
        try:
            return (loc[0] / op.entry).read_text(encoding="utf-8")
        except OSError:
            return None

    def _location(self, skill_id: str) -> tuple[Path, bool] | None:
        """返回 (目录, read_only)。workspace 优先。"""
        ws_dir = self.workspace_root / skill_id
        if (ws_dir / "skill.json").exists():
            return ws_dir, False
        bi_dir = self.builtin_root / skill_id
        if (bi_dir / "skill.json").exists():
            return bi_dir, True
        return None

    def _load_meta(self, meta_path: Path) -> SkillSpec | None:
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            data["kind"] = "executable"
            spec = SkillSpec(**data)
            # 契约（skill.json）不承载方法论正文：目录里存在 SKILL.md 时补读进 body，
            # 对齐 Anthropic agent skill（SKILL.md 是唯一方法论入口），供 Agent 引导与前端详情展示。
            if not spec.body:
                body = _skill_md_body(meta_path.parent)
                if body:
                    spec.body = body
                else:
                    # 无 SKILL.md 的存量/占位 skill：用契约信息拼一个可读说明（不落盘，仅展示兜底）
                    props = (
                        list((spec.input_schema or {}).get("properties", {}).keys())
                        if spec.input_schema
                        else []
                    )
                    desc = (spec.description or "").strip() or spec.name
                    lines = [desc, "", "## 执行方式", "- 带脚本技能：Executor 命中后用 `execute_skill_script` 代码直跑，真计算。"]
                    if props:
                        lines += ["", "## 参数"] + [f"- `{p}`" for p in props]
                    spec.body = "\n".join(lines)
            return spec
        except Exception:
            return None

    def _write_meta(self, skill_dir: Path, op: SkillSpec) -> None:
        (skill_dir / "skill.json").write_text(
            json.dumps(op.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ── 写操作 ──────────────────────────────────────────────────

    def create(
        self,
        *,
        skill_id: str,
        name: str = "",
        description: str = "",
        code: str | None = None,
        requirements: list[str] | None = None,
        input_schema: dict | None = None,
        output_schema: dict | None = None,
    ) -> SkillSpec:
        """新建工作区可执行 skill。写目录 + scripts/main.py + requirements + README + skill.json。"""
        skill_id = _slug(skill_id)
        if self._location(skill_id):
            raise SkillError(f"skill 已存在：{skill_id}（内置或工作区已有同名）")
        skill_dir = self.workspace_root / skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)
        scripts = skill_dir / "scripts"
        scripts.mkdir(exist_ok=True)
        (skill_dir / "examples").mkdir(exist_ok=True)
        (skill_dir / "runs").mkdir(exist_ok=True)
        (scripts / "main.py").write_text(code or _DEFAULT_MAIN, encoding="utf-8")
        if requirements:
            (skill_dir / "requirements.txt").write_text("\n".join(requirements) + "\n", encoding="utf-8")
        (skill_dir / "README.md").write_text(
            f"# {name or skill_id}\n\n{description or ''}\n", encoding="utf-8"
        )
        # 方法论唯一入口（对齐 Anthropic agent skill）：契约在 skill.json，正文在 SKILL.md。
        # 前端详情与 Agent get_skill 都读 SKILL.md 正文；_load_meta 会补读进 body。
        desc_line = (description or "").replace("\n", " ").strip()
        (skill_dir / "SKILL.md").write_text(
            f"---\nid: {skill_id}\nname: {name or skill_id}\ndescription: {desc_line}\n---\n\n"
            f"# {name or skill_id}\n\n{desc_line}\n\n"
            "## 执行方式\n\n"
            f"- **带脚本技能**：Executor 命中后用 `execute_skill_script`（skill_id=`{skill_id}`）代码直跑，真计算。\n"
            "- 运行参数以 scripts/main.py 读取的 input.json params 为准；input_schema 由 create/edit 维护。\n",
            encoding="utf-8",
        )
        validation = validate_skill_code(code or _DEFAULT_MAIN)
        op = SkillSpec(
            id=skill_id,
            kind="executable",
            name=name or skill_id,
            description=description,
            input_schema=input_schema or {"type": "object", "properties": {}, "required": []},
            output_schema=output_schema or {"type": "object", "properties": {}},
            validation=validation,
            scope="workspace",
            runtime=SkillRuntime(dependencies=list(requirements or [])),
            provenance={
                "created_by": "create_skill",
                "promoted_from": None,
                "run_count": 0,
                "last_run_at": None,
            },
            created_at=_now(),
            updated_at=_now(),
        )
        self._write_meta(skill_dir, op)
        return op

    def update(self, skill_id: str, patch: dict) -> SkillSpec:
        """改工作区可执行 skill（内置只读拒绝）。深合并非空字段 + revision+1 + 重新校验。"""
        loc = self._location(skill_id)
        if loc is None:
            raise SkillError(f"skill 不存在：{skill_id}")
        if loc[1]:
            raise SkillError(
                f"内置 skill 只读：{skill_id}。请先 copy_skill_to_workspace 复制到工作区再修改"
            )
        op = self.get(skill_id)
        if op is None:
            raise SkillError(f"skill 不存在：{skill_id}")
        data = op.model_dump(mode="json")

        for key in ("name", "description", "status", "version"):
            if patch.get(key) is not None:
                data[key] = str(patch[key])
        if patch.get("code") is not None:
            (loc[0] / op.entry).write_text(str(patch["code"]), encoding="utf-8")
        if patch.get("input_schema") is not None:
            data["input_schema"] = patch["input_schema"]
        if patch.get("output_schema") is not None:
            data["output_schema"] = patch["output_schema"]
        if patch.get("requirements") is not None:
            reqs = [str(r) for r in patch["requirements"]]
            data["runtime"]["dependencies"] = reqs
            if reqs:
                (loc[0] / "requirements.txt").write_text("\n".join(reqs) + "\n", encoding="utf-8")
            elif (loc[0] / "requirements.txt").exists():
                (loc[0] / "requirements.txt").unlink()

        validation = self.validate(skill_id, code=patch.get("code"))
        data["validation"] = validation.model_dump(mode="json")
        data["revision"] = int(data.get("revision", 1)) + 1
        data["updated_at"] = _now()
        data["scope"] = "workspace"
        data["kind"] = "executable"
        updated = SkillSpec(**data)
        self._write_meta(loc[0], updated)
        return updated

    def validate(self, skill_id: str, code: str | None = None) -> SkillValidation:
        """纯分析不落盘：静态校验（语法 / 协议 / params 键）+ 入口存在性。"""
        op = self.get(skill_id)
        if op is None:
            raise SkillError(f"skill 不存在：{skill_id}")
        loc = self._location(skill_id)
        if loc is None:
            raise SkillError(f"skill 不存在：{skill_id}")
        entry_path = loc[0] / op.entry
        src = code if code is not None else (
            entry_path.read_text(encoding="utf-8") if entry_path.exists() else ""
        )
        base = validate_skill_code(src or "")
        errors = list(base.errors)
        if not entry_path.exists():
            errors.append(f"入口文件 {op.entry} 不存在")
        return SkillValidation(
            protocol_ok=base.protocol_ok,
            syntax_ok=base.syntax_ok,
            direct_param_access=base.direct_param_access,
            warnings=base.warnings,
            errors=errors,
        )

    def copy_to_workspace(self, skill_id: str) -> SkillSpec:
        """内置 → 工作区整目录拷贝（存在则拒）。status 继承，revision+1。"""
        loc = self._location(skill_id)
        if loc is None:
            raise SkillError(f"skill 不存在：{skill_id}")
        if not loc[1]:
            existing = self.get(skill_id)
            if existing is not None:
                return existing
            raise SkillError(f"skill 不存在：{skill_id}")
        target = self.workspace_root / skill_id
        if (target / "skill.json").exists():
            raise SkillError(f"工作区已存在同名 skill：{skill_id}")
        shutil.copytree(
            loc[0],
            target,
            ignore=shutil.ignore_patterns("runs", "__pycache__", "*.pyc", "examples"),
        )
        copied = self.get(skill_id)  # 现在读 workspace 的
        if copied is None:
            raise SkillError(f"copy 失败：{skill_id}")
        data = copied.model_dump(mode="json")
        data["scope"] = "workspace"
        data["revision"] = int(data.get("revision", 1)) + 1
        data["updated_at"] = _now()
        prov = dict(data.get("provenance") or {})
        prov["copied_from"] = "builtin"
        prov["copied_at"] = _now()
        data["provenance"] = prov
        data["kind"] = "executable"
        result = SkillSpec(**data)
        self._write_meta(target, result)
        return result

    def promote_script(
        self,
        script_name: str,
        *,
        name: str | None = None,
        description: str | None = None,
        run_id: str | None = None,
    ) -> SkillSpec:
        """把脚本库（.workdir/script/）里的脚本升级为可执行 Skill。

        - 生成协议包装 scripts/main.py（原脚本另存 original_<name>.py），不重写脚本逻辑；
        - input_schema 用 AST 推断（脚本里 ``params["x"]`` 的键，类型保守标 string）；
        - 原脚本里依赖的 execute_code 专属工具（coverage 等 stub）在 skill 里不存在，
          需要靠 edit_skill 适配——validate 警告会提示。
        """
        script_path = self._resolve_script(script_name)
        code = script_path.read_text(encoding="utf-8")

        suggested = (name or script_path.stem).lower()
        skill_id = _slug(re.sub(r"[^a-z0-9]", "-", suggested) or "promoted_skill")
        if self._location(skill_id):
            raise SkillError(
                f"已存在同名 skill：{skill_id}。请用 name 参数换个 id，或先 edit/删除旧技能"
            )

        skill_dir = self.workspace_root / skill_id
        skill_dir.mkdir(parents=True, exist_ok=True)
        scripts = skill_dir / "scripts"
        scripts.mkdir(exist_ok=True)
        (skill_dir / "examples").mkdir(exist_ok=True)
        (skill_dir / "runs").mkdir(exist_ok=True)
        (scripts / "main.py").write_text(_promoted_wrapper(code, script_path.name), encoding="utf-8")
        (skill_dir / f"original_{script_path.name}").write_text(code, encoding="utf-8")
        (skill_dir / "README.md").write_text(
            f"# {name or skill_id}\n\n{description or ''}\n\n由脚本 {script_path.name} 提升。\n",
            encoding="utf-8",
        )

        params_keys = inspect_params_access(code)
        props = {k: {"type": "string", "description": f"参数 {k}（由脚本代码推断）"} for k in params_keys}
        input_schema = {"type": "object", "properties": props, "required": []}

        validation = validate_skill_code(_promoted_wrapper(code, script_path.name))
        validation.warnings.append(
            "input_schema 由脚本代码推断（类型统一为 string，未猜必填）；"
            "请用 edit_skill 修正为实际类型与必填。原脚本若调用 execute_code 专属工具需适配。"
        )
        op = SkillSpec(
            id=skill_id,
            kind="executable",
            name=name or skill_id,
            description=description or "",
            input_schema=input_schema,
            validation=validation,
            scope="workspace",
            provenance={
                "created_by": "promote_script_to_skill",
                "promoted_from": script_path.name,
                "run_id": run_id,
                "run_count": 0,
                "last_run_at": None,
            },
            created_at=_now(),
            updated_at=_now(),
        )
        self._write_meta(skill_dir, op)
        return op

    def _resolve_script(self, script_name: str) -> Path:
        name = Path(str(script_name)).name
        if not name.endswith(".py"):
            name += ".py"
        for root in (SCRIPT_DIR, Path(str(WORKSPACE_ROOT)) / "script"):
            candidate = root / name
            if candidate.is_file():
                return candidate
        raise SkillError(f"脚本不存在：{script_name}（应在 .workdir/script/ 下）")

    # ── 运行 ────────────────────────────────────────────────────

    def run(self, skill_id: str, params: dict, *, ctx: Any = None) -> dict:
        """执行可执行 skill（子进程全新解释器，可复现）。返回扁平 dict 供 ToolResult / REST。

        - 输入/输出/日志落 <skill_dir>/runs/<skill_run_id>/；
        - 产物图层注册上图、图事件进父 run 成果栏（ctx 提供时）；
        - 复用父 run 的 RunArchive，不开新 run（agent 路径下 execute_skill_script 本就是一次 tool call）；
        - 状态机：draft 首次成功 run 后自动升级 validated。
        """
        loc = self._location(skill_id)
        if loc is None:
            raise SkillError(f"skill 不存在：{skill_id}")
        op = self.get(skill_id)
        if op is None:
            raise SkillError(f"skill 不存在：{skill_id}")
        skill_dir, _read_only = loc

        run_id = f"skillrun_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
        run_dir = skill_dir / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        in_path = run_dir / "input.json"
        out_path = run_dir / "output.json"
        in_path.write_text(
            json.dumps(
                {"workspace": str(WORKSPACE_ROOT), "skill_id": skill_id, "params": params or {}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        env = os.environ.copy()
        api_root = str(Path(__file__).resolve().parents[2])  # app/skills → app → apps/api
        env["PYTHONPATH"] = (
            api_root + os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else api_root
        )
        try:
            proc = subprocess.run(
                [
                    sys.executable,
                    "-u",
                    "-m",
                    "app.execution.skill_runner",
                    str(skill_dir),
                    str(in_path),
                    str(out_path),
                ],
                cwd=str(WORKSPACE_ROOT),
                capture_output=True,
                text=True,
                timeout=600,
                env=env,
            )
        except subprocess.TimeoutExpired:
            self._bump_run(skill_id, op, success=False)
            return {
                "success": False,
                "skill_id": skill_id,
                "run_id": run_id,
                "returncode": None,
                "stdout_tail": "",
                "stderr_tail": "skill 运行超时（600s）",
                "output": {"success": False, "summary": "skill 运行超时（600s）"},
                "layers": [],
                "plot_files": [],
            }

        output: dict = {}
        try:
            output = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            output = {"success": False, "summary": "skill 未产出有效的 output.json"}
        success = proc.returncode == 0 and bool(output.get("success", True))

        plots = self._capture_plot_events((proc.stdout or ""), ctx)
        layers = [l for l in (output.get("layers") or []) if isinstance(l, dict)]
        registered: list[dict] = []
        if success:
            for layer in layers:
                registered.append(self._register_layer(layer, ctx))

        self._record_run_step(skill_id, run_id, output, ctx)
        self._bump_run(skill_id, op, success=success)

        return {
            "success": success,
            "skill_id": skill_id,
            "run_id": run_id,
            "returncode": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-4000:],
            "stderr_tail": (proc.stderr or "")[-4000:],
            "output": output,
            "layers": [r for r in registered if r is not None],
            "plot_files": [p["path"] for p in plots],
        }

    def _capture_plot_events(self, stdout: str, ctx: Any) -> list[dict]:
        """解析 skill stdout 里的 plot_saved / chart_saved JSON 行 → 父 run 成果栏。"""
        captured: list[dict] = []
        for line in stdout.splitlines():
            try:
                msg = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(msg, dict):
                continue
            kind = msg.get("kind")
            if kind == "plot_saved" and msg.get("path"):
                captured.append({"path": msg["path"], "caption": msg.get("caption") or ""})
                if ctx is not None:
                    self._emit_artifact(ctx, "plot", {"path": msg["path"], "caption": msg.get("caption") or ""})
                    ctx.meta.setdefault("plots", []).append(
                        {"path": str(msg["path"]), "caption": str(msg.get("caption") or "")}
                    )
            elif kind == "chart_saved" and isinstance(msg.get("spec"), dict):
                captured.append({"chart": msg["spec"]})
                if ctx is not None:
                    self._emit_artifact(ctx, "chart", {"spec": msg["spec"]})
                    ctx.meta.setdefault("charts", []).append({"spec": msg["spec"]})
        return captured

    def _emit_artifact(self, ctx: Any, kind: str, payload: dict) -> None:
        emitter = getattr(ctx, "event_emitter", None)
        archive = getattr(emitter, "archive", None) if emitter is not None else None
        if archive is not None:
            archive.record_artifact({"type": kind, **payload})
        try:
            ctx.emit(EventType.ARTIFACT, {"kind": kind, **payload})
        except Exception:
            pass

    def _register_layer(self, layer: dict, ctx: Any) -> dict | None:
        """把 skill 产出的 GeoJSON 图层注册上图 + 按 style 声明设色。"""
        from app.data_registry import DATA_REGISTRY
        from app.models.layer import FieldSpec, GeometryType, LayerMetadata, LayerSource

        layer_id = str(layer.get("layer_id") or "")
        if not layer_id:
            return None
        rel = str(layer.get("path") or f"data/layers/{layer_id}.json")
        try:
            resolved = (WORKSPACE_ROOT / rel).resolve()
            if WORKSPACE_ROOT not in resolved.parents and resolved != WORKSPACE_ROOT:
                return None
        except Exception:
            return None
        if not resolved.is_file():
            return None
        try:
            geojson = json.loads(resolved.read_text(encoding="utf-8"))
        except Exception:
            return None

        fields = []
        for f in layer.get("fields") or []:
            if isinstance(f, dict):
                fields.append(
                    FieldSpec(
                        name=str(f.get("name") or ""),
                        type=str(f.get("type") or "string"),
                        description=f.get("description"),
                    )
                )
        geom_type = str(layer.get("geometry") or _dominant_geometry(geojson))
        try:
            geometry = GeometryType(geom_type)
        except ValueError:
            geometry = GeometryType(_dominant_geometry(geojson))
        meta = LayerMetadata(
            id=layer_id,
            name=str(layer.get("name") or layer_id),
            source=LayerSource.generated,
            geometry=geometry,
            feature_count=int(layer.get("feature_count") or len(geojson.get("features", []))),
            crs="EPSG:4490",
            fields=fields,
            description=layer.get("description"),
            generated_by="skill",
        )
        DATA_REGISTRY.register_generated(meta, geojson=geojson)

        emit = getattr(ctx, "emit", None) if ctx is not None else None
        # generated_layer 事件：Agent 对话里显示「+N 要素」chip、会话记忆记录该图层
        # （对齐 execute_code 里 _register_generated 的语义，skill 路径不丢）
        if emit is not None:
            try:
                from app.models.events import EventType

                emit(
                    EventType.GENERATED_LAYER,
                    {
                        "layer_id": layer_id,
                        "name": meta.name,
                        "feature_count": meta.feature_count,
                    },
                )
            except Exception:
                pass
        style = layer.get("style")
        if isinstance(style, dict):
            try:
                from app.tools import layer_style as ls

                if style.get("mode") == "graduated" and style.get("field"):
                    st = ls.build_graduated_style(
                        layer_id,
                        style["field"],
                        method=str(style.get("method") or "quantile"),
                        classes=int(style.get("classes") or 5),
                    )
                    ls.apply_renderer_style(layer_id, st, emit=emit)
                elif style.get("mode") == "categorized" and style.get("field"):
                    st = ls.build_categorized_style(layer_id, style["field"])
                    ls.apply_renderer_style(layer_id, st, emit=emit)
            except Exception:
                pass  # 设色失败不致命，图层仍显示
        return {"layer_id": layer_id, "name": meta.name, "feature_count": meta.feature_count}

    def _record_run_step(self, skill_id: str, run_id: str, output: dict, ctx: Any) -> None:
        """把 skill run 记为父 run 的一个 step（steps.jsonl 目前无人写，正好补上）。"""
        if ctx is None:
            return
        emitter = getattr(ctx, "event_emitter", None)
        archive = getattr(emitter, "archive", None) if emitter is not None else None
        if archive is None:
            return
        try:
            step = int(ctx.meta.get("_skill_step", 0)) + 1
            ctx.meta["_skill_step"] = step
            archive.record_step(
                step=step,
                code=f"execute_skill_script(skill_id={skill_id!r})",
                output=str(output.get("summary") or ""),
                error=None if bool(output.get("success", True)) else "skill 运行失败",
                script_path=f".workdir/skills/{skill_id}/scripts/main.py",
            )
        except Exception:
            pass

    def _bump_run(self, skill_id: str, op: SkillSpec, *, success: bool) -> None:
        """run 后更新 skill.json：run_count+1、last_run_at、draft 首次成功 → validated。"""
        loc = self._location(skill_id)
        if loc is None:
            return
        try:
            data = op.model_dump(mode="json")
            prov = dict(data.get("provenance") or {})
            prov["run_count"] = int(prov.get("run_count", 0)) + 1
            prov["last_run_at"] = _now()
            if success and data.get("status") == "draft":
                data["status"] = "validated"
            data["provenance"] = prov
            data["updated_at"] = _now()
            data["kind"] = "executable"
            self._write_meta(loc[0], SkillSpec(**data))
        except Exception:
            pass


# 模块级单例：工具 handler / REST / LLM 提示注入共用同一份
STORE = SkillStore()
