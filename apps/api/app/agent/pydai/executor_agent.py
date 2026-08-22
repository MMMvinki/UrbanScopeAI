"""PydanticAI 迁移：Executor（两阶段第二步）。

在 worker 线程内 asyncio.run() 跑 PydanticAI 原生 loop（每 run 独立事件循环，
不碰 uvicorn 主循环）；iter() 逐节点驱动，映射现有 SSE 契约。产品层全部保留：
三道门 / 样式短路 / execute_code 子进程 / widget 组装 / 记忆 / RunArchive /
_register_generated。无轮次上限：模型返回无工具调用的消息即终结（PydanticAI 原生
终结条件，不再需要 MAX_ROUNDS/force_conclude）。

try_execute 签名不变；PA_AGENT=0 时走旧 loop（见 llm_executor.try_execute）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic_ai import Agent
from pydantic_ai._cancel import CancellationToken
from pydantic_ai.exceptions import RunCancelled
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider

from app.agent import llm_client, llm_executor
from app.agent.memory import MEMORY
from app.models.agent import ExecuteRequest, TraceEvent
from app.models.events import EventType
from app.models.result import ExecutionResult
from app.tools.context import ToolContext

from .event_bridge import handle_call_tools_node, record_llm_usage
from .tool_adapter import _RunAborted, build_tools

logger = logging.getLogger(__name__)


def _build_model() -> OpenAIChatModel:
    """DeepSeek 原生 provider（v2 唯一路径；env 与 llm_client 同源）。"""
    return OpenAIChatModel(
        llm_client.LLM_MODEL,
        provider=DeepSeekProvider(api_key=llm_client.API_KEY),
    )


def _history_messages(system_prompt: str, history: list[dict] | None) -> list[Any]:
    """把 system + 历史最后 6 轮 → PydanticAI 消息列表（同 planner）。"""
    messages: list[Any] = [ModelRequest(parts=[SystemPromptPart(content=system_prompt)])]
    for turn in (history or [])[-6:]:
        role = turn.get("role")
        content = turn.get("content")
        if role not in ("user", "assistant") or not content:
            continue
        if role == "user":
            # 用户轮必须用 UserPromptPart：OpenAI 模型映射 _map_user_message 只认
            # UserPromptPart，裸 TextPart 会撞 assert_never（→整个执行直接失败）。
            messages.append(ModelRequest(parts=[UserPromptPart(content=str(content))]))
        else:
            messages.append(ModelResponse(parts=[TextPart(content=str(content))]))
    return messages


async def _pa_run(request: ExecuteRequest, deps: ToolContext, emit: Any | None) -> ExecutionResult | None:
    """异步 PydanticAI loop：返回组装好的 ExecutionResult；无成果返回 None（回退规则版）。

    trace 初始 3 条（ran_command/created/text）与旧 loop 一字不差；写入
    deps.meta["_pa_trace"] 供工具包装函数追加（同一 list 对象）。
    """
    target_layers = [
        llm_executor.DATA_REGISTRY.get_layer(target)
        for target in request.targets
        if llm_executor.DATA_REGISTRY.get_layer(target) is not None
    ]
    missing = list(request.data_check.missing) if request.data_check else []
    trace: list[TraceEvent] = [
        TraceEvent(type="ran_command", content='check_readiness("auto")'),
        TraceEvent(
            type="created",
            content="数据检查报告",
            meta=f"+{len(target_layers)} 就绪 · {len(missing)} 缺失",
        ),
        TraceEvent(
            type="text",
            content=(f"缺失数据：{'、'.join(missing)}，先执行可用部分。" if missing else "所需数据已就绪，开始执行分析。"),
        ),
    ]
    deps.meta["_pa_trace"] = trace
    if emit is not None:
        emit(
            EventType.DATA_CHECK,
            {
                "state": "ready" if not missing else "partial",
                "ready": [layer.id for layer in target_layers],
                "missing": missing,
            },
        )

    system_prompt = llm_executor._system_prompt(request, target_layers)
    messages = _history_messages(system_prompt, request.history)
    agent = Agent(
        _build_model(),
        tools=build_tools(),
        deps_type=ToolContext,
        model_settings={"max_tokens": 3000, "temperature": 0.2},
    )
    # 取消：CancellationToken 线程安全/幂等，包装函数和节点循环都可触发
    token = CancellationToken()
    deps.meta["_pa_token"] = token

    interrupted = False
    conclusion: str | None = None
    run_obj: Any = None
    try:
        cancelled = False
        async with agent.iter(llm_executor._user_prompt(request), deps=deps, message_history=messages, cancellation_token=token) as run:
            run_obj = run
            async for node in run:
                # 断连检查：置位即取消 token（下一检查点抛 RunCancelled），保留已算结果
                if deps.cancel_event is not None and deps.cancel_event.is_set():
                    token.cancel()
                    cancelled = True
                    break
                handle_call_tools_node(node, deps, trace)
        if not cancelled and run_obj is not None:
            output = run_obj.result.output
            if isinstance(output, str) and output.strip():
                conclusion = output
            record_llm_usage(deps, run_obj.result)
    except (_RunAborted, RunCancelled, asyncio.CancelledError):
        interrupted = True
    except Exception:
        logger.exception("pydai loop failed run=%s", deps.run_id)
        interrupted = True

    return _assemble(request, deps, trace, conclusion, interrupted, emit)


def _assemble(
    request: ExecuteRequest,
    deps: ToolContext,
    trace: list[TraceEvent],
    conclusion: str | None,
    interrupted: bool,
    emit: Any | None,
) -> ExecutionResult | None:
    """复用旧 try_execute 的收尾组装（calls/generated/组卡/记忆/注册）。"""
    calls = deps.meta.get("_pa_calls", [])
    generated = deps.meta.get("_pa_generated", [])

    if interrupted:
        if not calls or not any(_r.success for _n, _a, _r in calls):
            return None  # 没有任何成功的工具结果 → 失败话术
        trace.append(
            TraceEvent(
                type="text",
                content="分析在途中遇到服务异常而中断，以下展示的是中断前已完成的真实计算结果。",
            )
        )
    elif not calls and not any(e.type == "text" for e in trace[3:]):
        return None  # 既无工具调用也无文字产出（空响应）→ 失败话术
    if not any(e.type == "text" for e in trace[3:]):
        trace.append(
            TraceEvent(type="text", content="分析完成，结果图层已同步到地图，指标与结论已生成到右侧成果栏。")
        )

    try:
        plots = deps.meta.get("plots", [])
        charts = deps.meta.get("charts", [])
        group = llm_executor._build_group(request, calls, generated, plots=plots, charts=charts)
        if group is None:
            if calls and all(name in llm_executor._MAP_CONTROL_TOOLS for name, _a, _r in calls):
                # 纯地图控制/样式 run：无指标卡、无生成图层，但操作已生效（事件已发射）。
                if not any(e.type == "text" for e in trace[3:]):
                    trace.append(
                        TraceEvent(type="text", content="已应用地图控制/样式操作，地图已同步更新（成果栏不新增卡片）。")
                    )
                return ExecutionResult(
                    intent_id=request.intent_id or "map-control",
                    trace=trace,
                    result_group=None,
                    evidence=[],
                    reflection="纯地图控制/样式类 run：工具事件已实时下发到地图，无分析成果卡。",
                    final_answer=llm_executor._last_text(trace),
                )
            if any(e.type == "text" for e in trace[3:]):
                # 组不出成果卡但有 Agent 文字产出（咨询/解释/说明性回应）
                return ExecutionResult(
                    intent_id=request.intent_id or "text",
                    trace=trace,
                    result_group=None,
                    evidence=[],
                    reflection="Agent 已给出文字说明但未形成结果图层/成果卡（咨询、解释或执行未落成图层）。",
                    final_answer=llm_executor._last_text(trace),
                )
            return None
        evidence: list[str] = []
        for widget in group.widgets:
            evidence.extend(widget.evidence)
        llm_executor._register_generated(generated)
        MEMORY.record(request.intent_id, group, evidence)
        return ExecutionResult(
            intent_id=request.intent_id,
            trace=trace,
            result_group=group,
            evidence=evidence,
            final_answer=conclusion or llm_executor._last_text(trace),
        )
    except Exception:
        logger.exception("pydai assemble failed run=%s", deps.run_id)
        return None  # 组装异常 → 回退规则版


def pa_try_execute(
    request: ExecuteRequest,
    *,
    ctx: ToolContext | None = None,
    emit: Any | None = None,  # EventEmitter.emit | None
) -> ExecutionResult | None:
    """PydanticAI 版 try_execute；失败/无 key 返回 None（回退规则版 executor）。

    deps 必须是 ToolContext（deps_type=ToolContext）；ctx=None 时建空上下文
    （与旧 loop 的 get_current_context() 兜底语义一致）。每 run 独立事件循环
    用 asyncio.run 在 worker 线程内跑，不碰 uvicorn 主循环。
    """
    deps = ctx if ctx is not None else ToolContext()
    if deps.meta.get("_pa_calls") is None:
        deps.meta["_pa_calls"] = []
    if deps.meta.get("_pa_generated") is None:
        deps.meta["_pa_generated"] = []
    try:
        return asyncio.run(_pa_run(request, deps, emit))
    except Exception:
        logger.exception("pydai pa_try_execute failed run=%s", deps.run_id)
        return None
