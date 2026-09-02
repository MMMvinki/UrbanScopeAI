"""PydanticAI 迁移：工具适配层。

全部工具（本地注册表 + skill + gis-mcp）统一经 Tool.from_schema 包装成
PydanticAI Tool。包装函数职责（对齐旧 llm_executor 的 tool loop）：
① 每调用前查 cancel_event → token.cancel()（原生取消，下一检查点抛 RunCancelled）；
② emit TOOL_START；③ 调 _call_tool（本地 run_tool + gis_* MCP 双路由）；
④ emit TOOL_RESULT；⑤ 记录 calls/generated/trace 到 ToolContext.meta；
⑥ 返回 {success, output, message}（模型看到的契约不变）。

contextvar 坑（已确认）：execute_code/execute_skill_script 用 get_current_context()
读 ToolContext —— 包装必须走 llm_executor._call_tool（内部经 run_tool 调
set_current_context），绝不能绕过。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from pydantic_ai import RunContext
from pydantic_ai.tools import Tool

from app.agent import llm_executor
from app.models.agent import TraceEvent
from app.models.events import EventType
from app.models.tool import ToolResult
from app.tools.context import ToolContext


class _RunAborted(Exception):
    """run 已取消（客户端断连 / 服务停止）：工具侧抛出，交由 _pa_run 捕获。"""


def _make_wrapper(
    name: str,
    *,
    allowed_skill_ids: set[str] | None = None,
    stop_repeats: bool = False,
) -> Callable[..., Any]:
    """按工具名闭包出 PydanticAI 包装函数（异步、takes_ctx=True、**kwargs）。

    PydanticAI 校验模型参数（对照 json_schema）后以 kwargs 调用；多传的额外参数
    （strict=False 放行）由 **kwargs 收住透传给工具 handler，行为与旧 loop 一致。
    """

    async def _wrapped(ctx: RunContext[ToolContext], **kwargs: Any) -> dict:
        deps: ToolContext = ctx.deps
        # ① 取消护栏：断连后不再执行工具，取消当前 run（下一检查点抛 RunCancelled）
        if deps.cancel_event is not None and deps.cancel_event.is_set():
            token = deps.meta.get("_pa_token")
            if token is not None:
                token.cancel()
            raise _RunAborted("run 已取消（客户端断连）")
        emit = deps.event_emitter
        # 受控主场景只允许读取/执行指定 Skill，防止模型把固定分析链改走
        # 通用工具或另一个可执行 Skill。
        if allowed_skill_ids is not None and name in {"get_skill", "execute_skill_script"}:
            requested_skill = str(kwargs.get("skill_id") or "").strip()
            if requested_skill not in allowed_skill_ids:
                result = ToolResult(
                    success=False,
                    output={},
                    message=f"受控分析路径不允许调用 skill：{requested_skill or '（空）'}",
                )
                deps.meta.setdefault("_pa_calls", []).append((name, kwargs, result))
                return result.model_dump(mode="json")
        if stop_repeats:
            try:
                fingerprint = f"{name}:{json.dumps(kwargs, ensure_ascii=False, sort_keys=True, default=str)}"
            except (TypeError, ValueError):
                fingerprint = f"{name}:{kwargs!r}"
            seen = deps.meta.setdefault("_pa_controlled_fingerprints", set())
            if fingerprint in seen:
                result = ToolResult(
                    success=False,
                    output={},
                    message=f"已停止同参数重复调用：{name}",
                )
                deps.meta.setdefault("_pa_calls", []).append((name, kwargs, result))
                return result.model_dump(mode="json")
            seen.add(fingerprint)
        # ② TOOL_START
        if emit is not None:
            emit.emit(EventType.TOOL_START, {"tool": name, "args": kwargs})
        # ③ 执行（不抛给框架：任何异常转失败结果，不让工具级崩溃打穿 loop）
        try:
            result = llm_executor._call_tool(name, kwargs, ctx=deps)
        except _RunAborted:
            raise  # 取消是边界情况，让 _pa_run 处理
        except Exception as exc:
            result = ToolResult(success=False, output={}, message=f"工具执行异常：{type(exc).__name__}: {exc}")
        # ⑤ calls 记录（喂 _build_group 组成果卡）
        deps.meta.setdefault("_pa_calls", []).append((name, kwargs, result))
        # ④ TOOL_RESULT
        if emit is not None:
            emit.emit(
                EventType.TOOL_RESULT,
                {
                    "tool": name,
                    "ok": result.success,
                    "message": result.message,
                    "output": result.output,
                },
            )
        # ⑤ trace 记录（ran / generated / text），与旧 loop 逐条一致
        trace = deps.meta.setdefault("_pa_trace", [])
        if result.success:
            arg_text = ", ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
            trace.append(TraceEvent(type="ran", content=f"{name}({arg_text})", meta=result.message))
            output_layer_id = result.output.get("output_layer_id")
            if output_layer_id:
                output_layer_id = str(output_layer_id)
                generated = deps.meta.setdefault("_pa_generated", [])
                if output_layer_id not in [g[0] for g in generated]:
                    display_name, count = llm_executor._generated_info(output_layer_id, kwargs)
                    generated.append((output_layer_id, display_name, count))
                    trace.append(TraceEvent(type="generated", content=display_name, meta=f"+{count} 要素"))
                    if emit is not None:
                        emit.emit(
                            EventType.GENERATED_LAYER,
                            {
                                "layer_id": output_layer_id,
                                "name": display_name,
                                "feature_count": count,
                            },
                        )
        else:
            trace.append(TraceEvent(type="text", content=result.message or f"工具 {name} 执行失败"))
        # ⑥ 返回契约（模型看到的 JSON，与原 loop 的 _tool_result_payload 一致）
        return result.model_dump(mode="json")

    return _wrapped


def build_tools(
    *,
    allowed_names: set[str] | None = None,
    allowed_skill_ids: set[str] | None = None,
    stop_repeats: bool = False,
) -> list[Tool]:
    """构建 PydanticAI 工具集：本地注册表（含 skill）+ gis-mcp（动态）。

    每 run 现构（MCP 会话运行中可变；工具参数以当前注册表为唯一事实源）。
    """
    from app.tools.registry import TOOLS

    tools: list[Tool] = []
    for tool in TOOLS:
        if tool.status.value != "ready" or not tool.json_schema:
            continue
        if allowed_names is not None and tool.name not in allowed_names:
            continue
        tools.append(
            Tool.from_schema(
                _make_wrapper(
                    tool.name,
                    allowed_skill_ids=allowed_skill_ids,
                    stop_repeats=stop_repeats,
                ),
                name=tool.name,
                description=tool.description,
                json_schema=tool.json_schema,
                takes_ctx=True,
            )
        )
    if allowed_names is not None:
        return tools
    from app.mcp import get_gis_mcp_bridge

    bridge = get_gis_mcp_bridge()
    if bridge is not None and bridge.is_ready():
        for schema in bridge.schema_list():
            fn = schema["function"]
            name = f"gis_{fn['name']}"
            tools.append(
                Tool.from_schema(
                    _make_wrapper(name),
                    name=name,
                    description=fn.get("description") or "",
                    json_schema=fn.get("parameters") or {"type": "object", "properties": {}},
                    takes_ctx=True,
                )
            )
    return tools
