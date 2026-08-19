"""Executor：按计划执行分析（LLM 优先，失败不兜底）。

样式/地图控制/闲聊类请求走确定性守卫（产品功能，不是兜底）；其余请求由
LLM 工具循环执行真计算。LLM 不可用或执行失败时**不降级到规则引擎**——规则版
按词表查表产出的结果往往不是用户想要的（如「学校好远」会被猜成覆盖率），
宁可真实验报告知失败与重试建议，也不产出偏离意图的分析。
"""

from typing import Any

from app.agent.llm_executor import _try_style_request, try_execute as llm_try_execute
from app.agent.reflection import reflect
from app.models.agent import ExecuteRequest, TraceEvent
from app.models.events import EventType
from app.models.result import ExecutionResult
from app.tools.context import ToolContext


def _missing_choice(
    request: ExecuteRequest,
    missing: list[str],
    *,
    emit: Any | None = None,
) -> ExecutionResult:
    """门3 护栏：分析对象数据缺失且未获用户授权 → 停下给选择，不静默执行。

    正常 UI 路径由前端门2（缺失面板）拦截；这里兜住直连 API / 前端漏门的情况。
    缺失分析对象（unmet_targets）未在 accept_missing 里 → 不产出结果、不调工具。
    """
    task = (request.question or request.summary or "该任务").strip().replace("\n", " ")
    if len(task) > 50:
        task = task[:50] + "…"
    missing_text = "、".join(missing)
    trace = [
        TraceEvent(type="text", content=f"要完成「{task}」，还缺这些数据：{missing_text}。"),
        TraceEvent(type="text", content="请选择：跳过该项（用现有数据继续）/ 上传数据 / 换个问法。"),
        TraceEvent(type="text", content="我不会用其他数据源代替缺失的数据来分析，避免给你失真结论。"),
    ]
    if emit is not None:
        for event in trace:
            emit(EventType.THOUGHT_DELTA, {"text": event.content})
    return ExecutionResult(
        intent_id=request.intent_id or "missing",
        trace=trace,
        result_group=None,
        evidence=[],
        reflection="分析对象数据缺失且未获用户授权：停下让用户选择，未伪造结果。",
        used_fallback=False,
        final_answer="\n".join(event.content for event in trace),
    )


def _execution_failure(
    request: ExecuteRequest,
    *,
    emit: Any | None = None,
) -> ExecutionResult:
    """LLM 执行失败 / 不可用时的诚实回复：不产出分析结果，不伪造。"""
    task = (request.question or request.summary or "该任务").strip().replace("\n", " ")
    if len(task) > 50:
        task = task[:50] + "…"
    trace = [
        TraceEvent(type="text", content=f"抱歉，我这次没能完成「{task}」这个任务。"),
        TraceEvent(type="text", content="执行环节没能产出可用的分析结果——可能是这个问法卡住了中间的分析步骤，也可能是执行服务临时不稳定。"),
        TraceEvent(type="text", content="建议换个更明确的问法（比如指定街道、图层和网格大小），或稍后重试。"),
    ]
    if emit is not None:
        for event in trace:
            emit(EventType.THOUGHT_DELTA, {"text": event.content})
    return ExecutionResult(
        intent_id=request.intent_id or "failed",
        trace=trace,
        result_group=None,
        evidence=[],
        reflection="AI 执行未产出可用结果且未启用规则兜底：如实告知用户（不误导为服务宕机），未伪造分析结果。",
        used_fallback=True,
        final_answer="\n".join(event.content for event in trace),
    )


def execute(
    request: ExecuteRequest,
    *,
    ctx: ToolContext | None = None,
    emit: Any | None = None,  # EventEmitter.emit | None
) -> ExecutionResult:
    """优先用 LLM Agent 循环执行；失败不兜底，如实告知。

    - 样式 / 地图控制类请求：确定性守卫直接改图层样式，不出成果卡；
    - 闲聊类请求：不进入工具循环，直接回一段文字；
    - 其余：LLM 工具循环执行；LLM 不可用 / 执行失败 → 失败话术。
    结果附带 reflection（LLM 反思；失败路径为固定文案）。
    """
    style_result = _try_style_request(request, emit=emit)
    if style_result is not None:
        style_result.used_fallback = False
        return style_result
    if request.operation == "chat":
        reply = request.summary or "你好，请告诉我你想分析什么。"
        return ExecutionResult(
            intent_id=request.intent_id or "chat",
            trace=[TraceEvent(type="text", content=reply)],
            result_group=None,
            evidence=[],
            reflection="闲聊类回复，未触发空间分析。",
            final_answer=reply,
        )

    # 门3 护栏：分析对象数据缺失且未获用户授权 → 停下给选择，不静默执行。
    # 只对 unmet_targets（解析不到数据的分析对象）生效；known 标签（乱停乱放等）
    # 不阻塞。用户点「跳过该项」后 accept_missing 携带已授权项，放行。
    unmet = list(request.unmet_targets or [])
    if unmet and request.operation != "chat":
        accepted = set(request.accept_missing or [])
        unaccepted = [item for item in unmet if item not in accepted]
        if unaccepted:
            return _missing_choice(request, unaccepted, emit=emit)

    llm_result = llm_try_execute(request, ctx=ctx, emit=emit)
    if llm_result is not None:
        llm_result.reflection = reflect(request, llm_result)
        llm_result.used_fallback = False
        return llm_result

    # LLM 不可用 / 执行失败：不兜底，如实告知
    return _execution_failure(request, emit=emit)
