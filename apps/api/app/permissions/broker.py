"""ApprovalBroker —— SSE 单向流下的同步审批桥。

时序：worker 线程在 evaluate→ASK 处阻塞；SSE 流已发出 permission_request 事件；
前端弹 ApprovalModal，用户点击 → POST /agent/approve → broker.resolve 置 event →
worker 继续。审批走独立 POST，天然避开 SSE 单向限制；300s 超时自动 DENY 兜底。

同步安全：request_id → threading.Event 映射，resolve 可来自任意 HTTP 线程。
"""

from __future__ import annotations

import threading
import time
from typing import Any

from app.models.events import EventType
from app.permissions import PermissionAction, PermissionDecision
from app.permissions.store import PermissionRequest, PermissionRequestStore, PermissionRuleStore

APPROVAL_TIMEOUT_SECONDS = 300


class ApprovalBroker:
    def __init__(self) -> None:
        self._store = PermissionRequestStore()
        self._events: dict[str, threading.Event] = {}
        self._results: dict[str, bool] = {}
        self._lock = threading.Lock()

    # -- worker 侧（同步阻塞） ------------------------------------------

    def wait(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        decision: PermissionDecision,
        *,
        emit: Any | None = None,
        cancel_event: threading.Event | None = None,
    ) -> PermissionDecision:
        """发 permission_request，阻塞等审批；300s 超时或客户端断连自动 DENY。"""
        req = self._store.create(tool_name=tool_name, arguments=arguments, decision=decision)
        with self._lock:
            self._events[req.id] = threading.Event()

        if emit is not None:
            emit(
                EventType.PERMISSION_REQUEST,
                {
                    "request_id": req.id,
                    "tool": tool_name,
                    "arguments": arguments,
                    "reason": decision.reason,
                    "rule": decision.rule,
                    "timeout_seconds": APPROVAL_TIMEOUT_SECONDS,
                },
            )

        resolved = self._block_until_resolved(req.id, cancel_event=cancel_event)
        with self._lock:
            allowed = self._results.pop(req.id, False)
            self._events.pop(req.id, None)
            # 超时/断连自动 DENY 时同步落 store 状态（若 resolve() 已标记则跳过）
            if req.status == "pending":
                req.resolve(action=PermissionAction.DENY, reason="审批超时（300s）或连接中断，已自动拒绝。")

        if not resolved:
            reason = "审批超时（300s）或连接中断，已自动拒绝。"
        else:
            reason = f"用户{'允许' if allowed else '拒绝'}：{decision.reason}"
        if emit is not None:
            emit(
                EventType.PERMISSION_RESULT,
                {"request_id": req.id, "tool": tool_name, "allowed": allowed, "reason": reason},
            )
        return PermissionDecision(
            PermissionAction.ALLOW if allowed else PermissionAction.DENY,
            reason,
            decision.rule,
        )

    def _block_until_resolved(self, request_id: str, *, cancel_event: threading.Event | None) -> bool:
        """等待审批；每 1s 轮询 cancel_event（客户端断连提前放弃）。"""
        deadline = time.monotonic() + APPROVAL_TIMEOUT_SECONDS
        while True:
            event = self._events.get(request_id)
            if event is None or event.is_set():
                return bool(self._results.get(request_id))
            if cancel_event is not None and cancel_event.is_set():
                return False
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            event.wait(timeout=min(1.0, remaining))

    # -- HTTP 侧（POST /agent/approve） ---------------------------------

    def resolve(self, request_id: str, *, allowed: bool, remember: bool = False) -> bool:
        """解阻塞等待该 request 的 worker 线程。remember → 持久化规则。"""
        with self._lock:
            req = self._store.get(request_id)
            event = self._events.get(request_id)
        if req is None or event is None:
            return False
        if remember:
            try:
                PermissionRuleStore(self._store_workspace_path(req)).add_rule(
                    tool=req.tool_name,
                    action=PermissionAction.ALLOW if allowed else PermissionAction.DENY,
                    reason=f"User {'allowed' if allowed else 'denied'} {req.tool_name} via approval",
                )
            except Exception:
                pass
        with self._lock:
            self._results[request_id] = allowed
            # 同步落 store 状态，避免 pending_requests() 返回已审批的僵尸请求
            req.resolve(
                action=PermissionAction.ALLOW if allowed else PermissionAction.DENY,
                reason=f"User {'allowed' if allowed else 'denied'} {req.tool_name} via approval",
            )
        event.set()
        return True

    @staticmethod
    def _store_workspace_path(req: PermissionRequest) -> str | None:
        # 请求本身不携带 workspace；回退到默认 workspace 根。
        from app.workspace import WORKSPACE_ROOT

        return str(WORKSPACE_ROOT)

    # -- 只读 ---------------------------------------------

    def pending_requests(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._store.list(status="pending", limit=limit)


# 进程级单例：跨 SSE run 共享，/agent/approve 直接操作它
BROKER = ApprovalBroker()

__all__ = ["ApprovalBroker", "BROKER", "APPROVAL_TIMEOUT_SECONDS"]
