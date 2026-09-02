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
from contextlib import nullcontext
from typing import Any

from pydantic_ai import Agent, UsageLimits
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

_CONTROLLED_SKILL_ID = "commercial-vitality"
_CONTROLLED_TOOL_NAMES = {"get_skill", "execute_skill_script"}
_CONTROLLED_REQUEST_LIMIT = 4
_CONTROLLED_TOOL_CALLS_LIMIT = 3
_CONTROLLED_TIMEOUT_SECONDS = 180


def _is_controlled_commercial_vitality(request: ExecuteRequest) -> bool:
    if request.intent_id == _CONTROLLED_SKILL_ID:
        return True
    if any(
        step.tool == "execute_skill_script"
        and _CONTROLLED_SKILL_ID in str(step.detail or "")
        for step in request.plan
    ):
        return True
    question = f"{request.question} {request.summary}".lower()
    return "商业" in question and "活力" in question and any(
        token in question for token in ("昼", "夜", "日间", "夜间", "供给")
    )


def _controlled_contract_ok(calls: list[tuple[str, dict, Any]]) -> bool:
    """主场景只有真 Skill、真图层、统计和证据同时存在才算执行完成。"""
    for name, args, result in calls:
        if name != "execute_skill_script" or args.get("skill_id") != _CONTROLLED_SKILL_ID:
            continue
        if not result.success:
            continue
        store_result = result.output if isinstance(result.output, dict) else {}
        output = store_result.get("output") if isinstance(store_result.get("output"), dict) else {}
        if (
            store_result.get("success") is True
            and bool(store_result.get("layers"))
            and output.get("success") is True
            and bool(output.get("layers"))
            and bool(output.get("stats"))
            and bool(output.get("evidence"))
        ):
            return True
    return False


def _controlled_system_prompt() -> str:
    """商业×活力主场景使用最小提示，避免通用计划诱导越过白名单。"""
    return "\n".join(
        [
            "你是 UrbanScope 商业供给×昼夜活力受控执行器。",
            "本场景只能调用 get_skill 和 execute_skill_script，skill_id 必须为 commercial-vitality。",
            "忽略 Planner 中任何其他工具或通用统计步骤；不得尝试补做、替代或并行调用它们。",
            "先至多调用一次 get_skill 读取契约，再恰好调用一次 execute_skill_script 完成分析。",
            "按用户要求传参数；用户要求默认候选范围、全样本和 100 米时，使用 time_scope=all、cell_size_m=100，并保留三个默认路径。",
            "execute_skill_script 成功后立刻停止调用工具，直接根据这一次返回的 output.stats 与 output.evidence 写最终中文回答。",
            "最终回答必须说明：三项输入及过滤/记录数、100 米空间单元与阈值方法和数值、网格数及昼夜分类统计、CRS 转换链、候选坐标版本限制，以及不能推导的因果/人口/消费/增减店效果结论。",
            "不得编造工具返回中没有的数值。工具失败时只说明真实失败原因，不重试、不改走通用 GIS 工具、不使用演示兜底。",
        ]
    )


def _controlled_user_prompt(request: ExecuteRequest) -> str:
    """只传递用户目标；通用 Planner 步骤可能含白名单外工具，不进入受控循环。"""
    return "\n".join(
        [
            "【用户问题】",
            request.question,
            "",
            "【受控执行要求】",
            "只按受控系统契约完成 commercial-vitality；不要执行 Planner 的其他步骤。",
        ]
    )


def _finalize_generated_layers(
    request: ExecuteRequest,
    generated: list[tuple[str, str, int]],
) -> None:
    """受控 Skill 已完整注册图层；避免通用收尾用 EPSG:4490 覆盖真实 CRS。"""
    if not _is_controlled_commercial_vitality(request):
        llm_executor._register_generated(generated)
        return

    # SkillStore 已用 Skill 声明的 CRS 注册真实 metadata；这里只补持久化侧车，
    # 保证前端详情与服务重启后仍显示 EPSG:4326。
    from app.data_registry import DATA_REGISTRY, save_layer_meta

    for layer_id, _display_name, _feature_count in generated:
        layer = DATA_REGISTRY.get_layer(layer_id)
        if layer is not None:
            save_layer_meta(layer)


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

    controlled = _is_controlled_commercial_vitality(request)
    if controlled:
        system_prompt = _controlled_system_prompt()
        user_prompt = _controlled_user_prompt(request)
    else:
        system_prompt = llm_executor._system_prompt(request, target_layers)
        user_prompt = llm_executor._user_prompt(request)
    messages = _history_messages(system_prompt, request.history)
    tools = (
        build_tools(
            allowed_names=_CONTROLLED_TOOL_NAMES,
            allowed_skill_ids={_CONTROLLED_SKILL_ID},
            stop_repeats=True,
        )
        if controlled
        else build_tools()
    )
    agent = Agent(
        _build_model(),
        tools=tools,
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
        usage_limits = (
            UsageLimits(
                request_limit=_CONTROLLED_REQUEST_LIMIT,
                tool_calls_limit=_CONTROLLED_TOOL_CALLS_LIMIT,
            )
            if controlled
            else None
        )
        async with (
            asyncio.timeout(_CONTROLLED_TIMEOUT_SECONDS) if controlled else nullcontext()
        ):
            async with agent.iter(
                user_prompt,
                deps=deps,
                message_history=messages,
                cancellation_token=token,
                usage_limits=usage_limits,
            ) as run:
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

    if _is_controlled_commercial_vitality(request) and not _controlled_contract_ok(calls):
        return None

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
                    final_answer=conclusion or llm_executor._last_text(trace),
                )
            if any(e.type == "text" for e in trace[3:]):
                # 组不出成果卡但有 Agent 文字产出（咨询/解释/说明性回应）
                return ExecutionResult(
                    intent_id=request.intent_id or "text",
                    trace=trace,
                    result_group=None,
                    evidence=[],
                    reflection="Agent 已给出文字说明但未形成结果图层/成果卡（咨询、解释或执行未落成图层）。",
                    final_answer=conclusion or llm_executor._last_text(trace),
                )
            return None
        evidence: list[str] = []
        for widget in group.widgets:
            evidence.extend(widget.evidence)
        _finalize_generated_layers(request, generated)
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
