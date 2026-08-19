"""反思：Executor 对执行结果的复核。

- LLM 反思优先：基于计划、工具调用痕迹与结果指标给出事实性评估；
- 异常 / 未配置 key 回退规则反思：从真实工具数值（覆盖率等）推导复核结论；
- 反思结果写入 ExecutionResult.reflection，前端「反思」折痕展示。
"""

from app.agent import llm_client
from app.models.agent import ExecuteRequest
from app.models.result import ExecutionResult, WidgetType


def _rule_reflect(request: ExecuteRequest, result: ExecutionResult) -> str:
    """规则版反思：完全基于真实执行痕迹与指标数值，不调用 LLM。"""
    group = result.result_group
    if group is None:
        return "本次执行未产出结果卡。建议补充缺失数据，或换一种更具体的问法重试。"
    metrics = [w for w in group.widgets if w.widget_type == WidgetType.metric]
    n_calls = sum(1 for e in result.trace if e.type == "ran")
    coverage: list[float] = []
    for w in metrics:
        if "覆盖率" in w.title:
            try:
                coverage.append(float(str(w.value).rstrip("%")))
            except ValueError:
                pass

    lines = [
        f"执行复核：按计划调用 {n_calls} 次分析工具，产出 {len(metrics)} 个指标卡、"
        f"{len(result.evidence)} 条证据。"
    ]
    if coverage:
        avg = sum(coverage) / len(coverage)
        low = [v for v in coverage if v < 70]
        if low:
            lines.append(
                f"覆盖率类指标均值 {avg:.1f}%，其中 {len(low)} 项低于 70%（短板），"
                f"建议在规划层面优先补强这些设施。"
            )
        else:
            lines.append(f"覆盖率类指标均值 {avg:.1f}%，整体达标。")
    lines.append("复核结论基于真实工具计算结果，未编造数值。")
    return "\n".join(lines)


def _llm_reflect(request: ExecuteRequest, result: ExecutionResult) -> str:
    if not llm_client.is_available():
        return ""
    trace_lines = "\n".join(f"- {e.type}: {e.content}" for e in result.trace[:14])
    metrics_lines = ""
    if result.result_group:
        metrics_lines = "\n".join(
            f"- {w.title}: {w.value}"
            for w in result.result_group.widgets
            if w.widget_type == WidgetType.metric
        )
    system = (
        "你是 UrbanScope 空间分析平台的反思器。基于给定的分析计划、工具调用痕迹与结果指标，"
        "用 2-4 句中文评估执行质量：计划是否执行完整、结果是否可信、有哪些局限或下一步建议。"
        "只做事实性复核，不要编造计划之外的数据。"
    )
    user = (
        f"【分析计划】{request.summary}（operation={request.operation}，targets={request.targets}）\n"
        f"【工具调用痕迹】\n{trace_lines or '无'}\n"
        f"【结果指标】\n{metrics_lines or '无'}\n"
        "\n请输出反思。"
    )
    response = llm_client.chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.2,
        max_tokens=600,
    )
    content = response["choices"][0]["message"].get("content") or ""
    return content.strip()[:600]


def reflect(request: ExecuteRequest, result: ExecutionResult) -> str:
    """对执行结果做反思：LLM 优先，异常 / 未配置回退规则版。"""
    try:
        text = _llm_reflect(request, result)
        if text:
            return text
    except Exception:
        pass
    return _rule_reflect(request, result)
