"""ToolContext：工具执行的运行期上下文（contextvars 注入）。

工具 handler 保持统一的 ``(params: dict) -> ToolResult`` 签名；需要上下文的新工具
（osm/datasource/csv/execute_code 等）内部用 get_current_context() 读取。
run_tool 仅在 handler 调用期间注入 contextvar，try/finally 复位，避免上下文泄漏。

裁剪为同步 ToolContext（我们不通过 IPC 通知前端，改为事件协议 EventEmitter；
移植来源见 NOTICE.md）。
"""

from __future__ import annotations

import contextvars
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # 避免循环导入：仅类型标注
    from app.agent.emitter import EventEmitter
    from app.execution.subprocess_executor import SubprocessPythonExecutor
    from app.permissions import PermissionRuntime


@dataclass
class ToolContext:
    """单次 agent run 的工具运行上下文。"""

    workspace_path: str | Path | None = None
    conversation_id: str | None = None
    run_id: str | None = None
    # Phase 0 引入：事件发射器（同步 emit 到 SSE 队列 + RunArchive 落盘）
    event_emitter: Any | None = None  # EventEmitter | None
    # Phase 1：权限运行时（L1 权限门）、客户端断连信号、run 级子进程执行器
    permission_runtime: Any | None = None  # PermissionRuntime | None
    cancel_event: threading.Event | None = None
    subprocess_executor: Any | None = None  # SubprocessPythonExecutor | None
    # 附加元数据（skill 命中、agent 配置等）
    meta: dict = field(default_factory=dict)

    def emit(self, event_type: str, data: dict[str, object] | None = None) -> None:
        """便捷方法：向事件流发出事件（无 emitter 时静默跳过）。"""
        if self.event_emitter is not None:
            self.event_emitter.emit(event_type, data or {})

    def request_cancel(self) -> None:
        """请求取消当前 run（客户端断连时置位）。"""
        if self.cancel_event is not None:
            self.cancel_event.set()

    def is_cancelled(self) -> bool:
        """客户端是否已断连。"""
        return self.cancel_event is not None and self.cancel_event.is_set()


_current_context: contextvars.ContextVar[Optional[ToolContext]] = contextvars.ContextVar(
    "urbanscope_tool_context", default=None
)


def set_current_context(ctx: ToolContext | None) -> contextvars.Token:
    """为当前栈安装 ToolContext，返回恢复用的 Token。"""
    return _current_context.set(ctx)


def reset_current_context(token: contextvars.Token) -> None:
    """恢复之前的上下文。"""
    _current_context.reset(token)


def get_current_context() -> ToolContext:
    """获取当前 ToolContext；无安装时返回空上下文（不 raise，兼容 demo/直接调用）。"""
    ctx = _current_context.get()
    if ctx is None:
        return ToolContext()
    return ctx
