"""run 级子进程执行器的构建：把 stdout/risky_op/plot_saved 接到事件流 + RunArchive。

生命周期：每 run 一个子进程（存 ctx.subprocess_executor）。首次 execute_code 懒创建，
同 run 复用（namespace 跨 exec 持久，如 Jupyter kernel）；run 结束由 main.py 的
SSE worker finally 调用 ctx.subprocess_executor.cleanup() 关闭防泄漏。

SAFE_CODE_TOOLS：子进程里可调用的工具白名单 = 现有 9 个本地工具 + Phase 0 取数工具
+ save_plot。每个名字在子进程 namespace 里成为 stub，调用经管道 RPC 回父进程的
run_tool（闭包了当前 ToolContext），结果回传。
"""

from __future__ import annotations

import sys
from typing import Any, Callable

from app.models.events import EventType
from app.tools import TOOL_IMPLEMENTATIONS, run_tool
from app.tools.context import ToolContext

# 子进程里可调用的工具白名单（L2：工具面受限；子进程自己只 stub 这些）
# 排除 execute_code 本身——避免子进程里再开子进程的递归调用。
SAFE_CODE_TOOLS: tuple[str, ...] = tuple(
    name
    for name in TOOL_IMPLEMENTATIONS
    if name != "execute_code"
) + ("save_plot", "emit_chart")  # save_plot / emit_chart 由 subprocess_runner 本地实现，名字传给 init 即可


def _make_rpc_tool(name: str, ctx: ToolContext) -> Callable[..., Any]:
    """把父进程的 run_tool 包成可被 stub *args/**kwargs 调用的工具。

    child 代码用 kwargs（coverage(layer_id="schools")）或单 dict 位置参数
    （coverage({"layer_id": ...})）两种风格；其余形状交给 handler 报缺参。
    """

    def call(*args: Any, **kwargs: Any) -> Any:
        if args:
            if len(args) == 1 and isinstance(args[0], dict) and not kwargs:
                params = args[0]
            else:
                params = dict(kwargs)
        else:
            params = dict(kwargs)
        return run_tool(name, params, ctx=ctx)

    call.__name__ = name
    call.__qualname__ = name
    return call


def build_safe_code_tools(ctx: ToolContext) -> dict[str, Callable[..., Any]]:
    """SAFE_CODE_TOOLS 名字 → 父进程工具函数映射（值经 tool_call RPC 回传）。"""
    return {name: _make_rpc_tool(name, ctx) for name in SAFE_CODE_TOOLS}


def build_run_executor(ctx: ToolContext):
    """创建并接线 run 级子进程执行器，返回已 send_tools 的 SubprocessPythonExecutor。"""
    from app.execution.subprocess_executor import SubprocessExecutorConfig, SubprocessPythonExecutor

    emitter = ctx.event_emitter

    # 防御：子进程 print 若吐出超大文本（如把 GeoJSON 整个 dump），无限转发会撑爆前端。
    # 单次 run 的 stdout 转发累计超过预算后静默丢弃（执行不受影响，仅不再流式展示）。
    _stdout_budget = [500_000]  # 500KB
    _stdout_spent = [0]

    def stdout_listener(text: str) -> None:
        if emitter is None:
            return
        _stdout_spent[0] += len(text)
        if _stdout_spent[0] > _stdout_budget[0]:
            return
        # 子进程 print 近实时推给 SSE（CODE_DELTA kind=stdout），前端渲染为运行输出
        emitter.emit(EventType.CODE_DELTA, {"kind": "stdout", "text": text})

    def risky_op_listener(msg: dict[str, Any]) -> None:
        # L4 遥测：破坏性/写操作落 meta.json.risky_ops（观察，不拦截）
        archive = emitter.archive if emitter is not None else None
        if archive is not None:
            archive.record_risky_op(
                {"op": msg.get("op"), "path": msg.get("path"), "extra": msg.get("extra")}
            )

    def plot_saved_listener(msg: dict[str, Any]) -> None:
        # matplotlib 图落盘 → artifact 引用 + 事件流
        archive = emitter.archive if emitter is not None else None
        if archive is not None:
            archive.record_artifact({"type": "plot", "path": msg.get("path"), "caption": msg.get("caption")})
        if emitter is not None:
            emitter.emit(EventType.ARTIFACT, {"kind": "plot", "path": msg.get("path"), "caption": msg.get("caption")})
        # 捕获到 ctx.meta，供执行结束组装成果组时追加 image widget（图表进右侧成果栏）
        path = msg.get("path")
        if path:
            ctx.meta.setdefault("plots", []).append(
                {"path": str(path), "caption": str(msg.get("caption") or "")}
            )

    def chart_saved_listener(msg: dict[str, Any]) -> None:
        # emit_chart 结构化图表 → artifact 引用 + 事件流 + ctx.meta（组装时转 ECharts widget）
        spec = msg.get("spec")
        if not isinstance(spec, dict):
            return
        archive = emitter.archive if emitter is not None else None
        if archive is not None:
            archive.record_artifact({"type": "chart", "spec": spec})
        if emitter is not None:
            emitter.emit(EventType.ARTIFACT, {"kind": "chart", "spec": spec})
        ctx.meta.setdefault("charts", []).append({"spec": spec})

    executor = SubprocessPythonExecutor(
        config=SubprocessExecutorConfig(
            python_executable=sys.executable,
            working_dir=str(ctx.workspace_path or "."),
            exec_timeout=600.0,
        ),
        stdout_listener=stdout_listener,
        risky_op_listener=risky_op_listener,
        plot_saved_listener=plot_saved_listener,
        chart_saved_listener=chart_saved_listener,
    )
    executor.send_tools(build_safe_code_tools(ctx))
    return executor


__all__ = ["SAFE_CODE_TOOLS", "build_safe_code_tools", "build_run_executor"]
