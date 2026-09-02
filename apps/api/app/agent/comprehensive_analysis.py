"""三项已验证规划分析能力的确定性路由与受控编排。

这里不做 GIS 计算；所有地图、统计和证据都来自既有 SkillStore 真运行。
规则路由先于自由模型规划，既允许普通用户使用模糊问法，也让选择依据可测试、
可解释。综合请求按固定依赖顺序批量执行，不占用或放宽通用 Agent 工具额度。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.models.agent import ExecuteRequest, PlanResult, PlanStep, StepStatus, TraceEvent
from app.models.events import EventType
from app.models.result import (
    AnalysisModuleResult,
    AnalysisReadinessItem,
    ExecutionResult,
    ResultGroup,
    ResultWidget,
    StructuredAnalysisResult,
    WidgetType,
)
from app.skills.store import STORE
from app.tools.context import ToolContext


ACTIVITY_MODULE = "activity_temporal"
COMMERCIAL_MODULE = "commercial_supply"
DIAGNOSIS_MODULE = "commercial_vitality"
POPULATION_MODULE = "population_profile"

ACTIVITY_SKILL = "activity-temporal-profile"
COMMERCIAL_SKILL = "commercial-supply-profile"
DIAGNOSIS_SKILL = "commercial-vitality"
COMPREHENSIVE_INTENT = "project-comprehensive-analysis"
POPULATION_DEGRADED_INTENT = "population-profile-degraded"


@dataclass(frozen=True)
class ModuleSpec:
    module_id: str
    label: str
    skill_id: str
    achieved_level: str
    required_data: tuple[str, ...]


MODULE_SPECS: dict[str, ModuleSpec] = {
    ACTIVITY_MODULE: ModuleSpec(
        ACTIVITY_MODULE,
        "活动时序画像",
        ACTIVITY_SKILL,
        "L2",
        ("候选研究范围", "含日/夜及工作日/周末字段的活动数据"),
    ),
    COMMERCIAL_MODULE: ModuleSpec(
        COMMERCIAL_MODULE,
        "商业供给格局",
        COMMERCIAL_SKILL,
        "L2",
        ("候选研究范围", "含有效记录标记和明确坐标系的商业点数据"),
    ),
    DIAGNOSIS_MODULE: ModuleSpec(
        DIAGNOSIS_MODULE,
        "商业供给 × 昼夜活力综合诊断",
        DIAGNOSIS_SKILL,
        "L3",
        ("候选研究范围", "商业点数据", "含可比时段字段的活动数据"),
    ),
}


@dataclass(frozen=True)
class AnalysisRoute:
    intent_id: str
    route_rule: str
    route_reason: str
    requested_modules: tuple[str, ...]
    executable_modules: tuple[str, ...]
    initially_blocked: dict[str, str] = field(default_factory=dict)


_ACTIVITY_TERMS = (
    "活动", "活力", "昼夜", "日间", "夜间", "白天", "晚上", "工作日", "周末", "时序", "客流",
)
_COMMERCIAL_TERMS = (
    "商业", "商铺", "店铺", "商店", "网点", "供给", "密度", "集聚", "聚集",
)
_RELATION_TERMS = (
    "错位", "匹配", "关系", "供需", "共现", "结合", "关联", "对照", "比较", "×", "x",
)
_POPULATION_TERMS = (
    "人口画像", "人口结构", "常住人口", "就业人口", "年龄画像", "学历画像", "收入画像", "人群画像",
)
_COMPREHENSIVE_TERMS = (
    "综合现状", "全面现状", "项目综合", "项目全面", "整体分析", "全部现状", "现有数据能做什么",
    "当前数据能做什么", "能做什么",
)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def route_analysis_request(question: str) -> AnalysisRoute | None:
    """按显式优先级选择能力；未命中时返回 None，交回原通用 Planner。"""
    q = "".join(str(question or "").lower().split())
    if not q:
        return None

    has_activity = _contains_any(q, _ACTIVITY_TERMS)
    has_commercial = _contains_any(q, _COMMERCIAL_TERMS)

    # 项目级综合意图优先，避免“根据现有数据做全面分析”被某个偶然关键词截走。
    if _contains_any(q, _COMPREHENSIVE_TERMS) or (
        "综合分析" in q and not (has_activity or has_commercial)
    ):
        return AnalysisRoute(
            intent_id=COMPREHENSIVE_INTENT,
            route_rule="R-COMPREHENSIVE",
            route_reason="命中项目级‘综合现状/全面分析/现有数据能做什么’表达，按三个核心板块检查并执行。",
            requested_modules=(ACTIVITY_MODULE, COMMERCIAL_MODULE, DIAGNOSIS_MODULE),
            executable_modules=(ACTIVITY_MODULE, COMMERCIAL_MODULE, DIAGNOSIS_MODULE),
        )

    # 当前没有人口属性 Skill；明确降级到活动时序，并把人口画像保留为 blocked。
    if _contains_any(q, _POPULATION_TERMS):
        reason = "缺少可验证的人口数量、年龄、学历、收入等人口属性数据；活动强度不能替代人口画像。"
        return AnalysisRoute(
            intent_id=POPULATION_DEGRADED_INTENT,
            route_rule="R-POPULATION-DEGRADE",
            route_reason="命中人口画像表达；受规划语义边界约束，仅降级执行活动时序画像。",
            requested_modules=(POPULATION_MODULE, ACTIVITY_MODULE),
            executable_modules=(ACTIVITY_MODULE,),
            initially_blocked={POPULATION_MODULE: reason},
        )

    # 商业与活动同时出现，或明确提出昼夜商业/商业活力，进入二者的综合诊断。
    if (has_commercial and has_activity) or (
        has_commercial and _contains_any(q, _RELATION_TERMS) and ("人流" in q or "昼夜" in q)
    ):
        return AnalysisRoute(
            intent_id=DIAGNOSIS_SKILL,
            route_rule="R-COMMERCIAL-X-ACTIVITY",
            route_reason="同时命中商业与活动/昼夜语义，选择商业供给 × 昼夜活力综合诊断。",
            requested_modules=(DIAGNOSIS_MODULE,),
            executable_modules=(DIAGNOSIS_MODULE,),
        )

    if has_commercial:
        return AnalysisRoute(
            intent_id=COMMERCIAL_SKILL,
            route_rule="R-COMMERCIAL-SUPPLY",
            route_reason="命中商业供给、数量、密度或集聚语义，选择商业供给格局。",
            requested_modules=(COMMERCIAL_MODULE,),
            executable_modules=(COMMERCIAL_MODULE,),
        )

    if has_activity:
        return AnalysisRoute(
            intent_id=ACTIVITY_SKILL,
            route_rule="R-ACTIVITY-TEMPORAL",
            route_reason="命中活动、昼夜、日间/夜间或工作日/周末语义，选择活动时序画像。",
            requested_modules=(ACTIVITY_MODULE,),
            executable_modules=(ACTIVITY_MODULE,),
        )
    return None


def _route_from_intent(intent_id: str) -> AnalysisRoute | None:
    if intent_id == COMPREHENSIVE_INTENT:
        return route_analysis_request("根据现有数据做项目综合现状分析")
    if intent_id == POPULATION_DEGRADED_INTENT:
        return route_analysis_request("人口画像")
    if intent_id == ACTIVITY_SKILL:
        return route_analysis_request("活动时序")
    if intent_id == COMMERCIAL_SKILL:
        return route_analysis_request("商业供给")
    if intent_id == DIAGNOSIS_SKILL:
        return route_analysis_request("商业与活动活力匹配")
    return None


def plan_controlled_analysis(question: str) -> PlanResult | None:
    """把命中的自然语言请求变成固定、可解释的 Skill 计划。"""
    route = route_analysis_request(question)
    if route is None:
        return None

    steps: list[PlanStep] = []
    for module_id, reason in route.initially_blocked.items():
        steps.append(
            PlanStep(
                step_id=f"blocked-{module_id}",
                label="人口画像不可执行",
                detail=reason,
                status=StepStatus.blocked,
            )
        )
    for index, module_id in enumerate(route.executable_modules, start=1):
        spec = MODULE_SPECS[module_id]
        steps.append(
            PlanStep(
                step_id=f"module-{index}",
                label=f"检查数据并执行：{spec.label}",
                tool="execute_skill_script",
                detail=f"skill_id={spec.skill_id}；由 Skill 自身校验文件、字段、CRS 与时段条件",
            )
        )
    required = list(dict.fromkeys(data for module in route.executable_modules for data in MODULE_SPECS[module].required_data))
    return PlanResult(
        intent_id=route.intent_id,
        summary=f"{route.route_reason} 系统将按板块返回真实运行结果、阻断原因与语义限制。",
        thinking=f"受控路由 {route.route_rule}：{route.route_reason}",
        operation="controlled-analysis",
        required_data=required,
        plan=steps,
    )


def _blocked_module(module_id: str, reason: str, *, stop_code: str = "STOP_NOT_READY") -> AnalysisModuleResult:
    spec = MODULE_SPECS.get(module_id)
    return AnalysisModuleResult(
        module_id=module_id,
        status="blocked",
        skill_id=spec.skill_id if spec else None,
        achieved_level="L0" if module_id == POPULATION_MODULE else "L1",
        summary=reason,
        limitations=[reason],
        stop_code=stop_code,
        blocked_reason=reason,
    )


def _module_from_run(module_id: str, run: dict[str, Any]) -> AnalysisModuleResult:
    spec = MODULE_SPECS[module_id]
    output = run.get("output") if isinstance(run.get("output"), dict) else {}
    evidence = output.get("evidence") if isinstance(output.get("evidence"), dict) else {}
    stats = output.get("stats") if isinstance(output.get("stats"), dict) else {}
    layers = run.get("layers") if isinstance(run.get("layers"), list) else []
    contract_ok = (
        run.get("success") is True
        and output.get("success") is True
        and bool(stats)
        and bool(evidence)
        and bool(output.get("layers"))
        and bool(layers)
    )
    summary = str(output.get("summary") or run.get("stderr_tail") or "Skill 未返回摘要")
    if not contract_ok:
        reason = summary if run.get("success") is not True else "Skill 未产出完整的地图、统计和证据，不能标记成功。"
        return AnalysisModuleResult(
            module_id=module_id,
            status="blocked",
            skill_id=spec.skill_id,
            run_id=str(run.get("run_id") or "") or None,
            achieved_level="L1",
            summary=reason,
            stop_code=str(output.get("stop_code") or "STOP_INCOMPLETE_RESULT"),
            blocked_reason=reason,
            limitations=[reason],
        )
    limitations = [str(item) for item in evidence.get("limitations", []) if str(item).strip()]
    return AnalysisModuleResult(
        module_id=module_id,
        status="completed",
        skill_id=spec.skill_id,
        run_id=str(run.get("run_id") or "") or None,
        achieved_level=spec.achieved_level,
        summary=summary,
        stats=stats,
        evidence=evidence,
        limitations=limitations,
        output_layer=str(run.get("output_layer_id") or output.get("layer_id") or "") or None,
    )


def _result_group(route: AnalysisRoute, modules: list[AnalysisModuleResult]) -> ResultGroup:
    widgets: list[ResultWidget] = []
    for index, module in enumerate(modules):
        widgets.append(
            ResultWidget(
                id=f"w-analysis-{index}",
                title=MODULE_SPECS[module.module_id].label if module.module_id in MODULE_SPECS else "人口画像数据缺口",
                widget_type=WidgetType.map if module.output_layer else WidgetType.list,
                value="已完成" if module.status == "completed" else "不可执行",
                subtitle=f"{module.achieved_level} · {module.skill_id or '无可执行 Skill'}",
                data={
                    "module_id": module.module_id,
                    "status": module.status,
                    "run_id": module.run_id,
                    "layer_id": module.output_layer,
                    "blocked_reason": module.blocked_reason,
                },
                evidence=[module.summary],
                layer_id=module.output_layer,
            )
        )
    return ResultGroup(id=f"rg-{route.intent_id}", title="综合分析结果", widgets=widgets)


def _final_answer(route: AnalysisRoute, analysis: StructuredAnalysisResult) -> str:
    lines = [f"已按{route.route_rule}完成受控选择：{route.route_reason}"]
    if analysis.ready_modules:
        lines.append(f"已完成板块：{'、'.join(analysis.ready_modules)}。")
    if analysis.blocked_modules:
        lines.append(f"未就绪板块：{'、'.join(analysis.blocked_modules)}。")
    for module in analysis.modules:
        marker = "完成" if module.status == "completed" else "阻断"
        run_ref = f"；run={module.run_id}" if module.run_id else ""
        layer_ref = f"；图层={module.output_layer}" if module.output_layer else ""
        lines.append(f"[{marker}] {module.module_id}{run_ref}{layer_ref}：{module.summary}")
    lines.append("边界：活动强度不是人口数量或人口结构；商业点数量/密度不是营业额、经营质量或消费等级；商业与活动空间共现不代表因果。")
    lines.append("活动数据使用的 gcj02_to_wgs84 仍是候选坐标版本，尚待数据所有者权威确认。")
    return "\n".join(lines)


SkillRunner = Callable[..., dict[str, Any]]


def execute_analysis_route(
    route: AnalysisRoute,
    *,
    ctx: ToolContext | None = None,
    emit: Any | None = None,
    run_skill: SkillRunner | None = None,
    params_by_skill: dict[str, dict[str, Any]] | None = None,
) -> ExecutionResult:
    """顺序执行独立板块；综合板块只在活动和商业前置都成功时运行。"""
    runner = run_skill or STORE.run
    params_by_skill = params_by_skill or {}
    modules: list[AnalysisModuleResult] = [
        _blocked_module(module_id, reason, stop_code="STOP_MISSING_POPULATION_ATTRIBUTES")
        for module_id, reason in route.initially_blocked.items()
    ]
    completed: set[str] = set()
    trace = [TraceEvent(type="text", content=f"{route.route_rule}：{route.route_reason}")]

    for module_id in route.executable_modules:
        spec = MODULE_SPECS[module_id]
        if ctx is not None and ctx.is_cancelled():
            modules.append(_blocked_module(module_id, "用户已中止本次分析。", stop_code="STOP_CANCELLED"))
            continue
        if (
            route.intent_id == COMPREHENSIVE_INTENT
            and module_id == DIAGNOSIS_MODULE
            and not {ACTIVITY_MODULE, COMMERCIAL_MODULE}.issubset(completed)
        ):
            modules.append(
                _blocked_module(
                    module_id,
                    "活动时序或商业供给前置板块未就绪，综合诊断不执行，也不补造结论。",
                    stop_code="STOP_PREREQUISITE_BLOCKED",
                )
            )
            continue

        params = dict(params_by_skill.get(spec.skill_id) or {})
        if emit is not None:
            emit(EventType.TOOL_START, {"tool": "execute_skill_script", "args": {"skill_id": spec.skill_id, "params": params}})
        try:
            run = runner(spec.skill_id, params, ctx=ctx)
        except Exception as exc:
            run = {
                "success": False,
                "skill_id": spec.skill_id,
                "output": {"success": False, "summary": f"Skill 执行异常：{type(exc).__name__}: {exc}", "stop_code": "STOP_EXECUTION_ERROR"},
                "layers": [],
            }
        module = _module_from_run(module_id, run)
        modules.append(module)
        if module.status == "completed":
            completed.add(module_id)
            trace.append(TraceEvent(type="ran", content=f"execute_skill_script(skill_id={spec.skill_id})", meta=module.run_id))
            if module.output_layer:
                trace.append(TraceEvent(type="generated", content=spec.label, meta=module.output_layer))
        else:
            trace.append(TraceEvent(type="text", content=f"{spec.label}未执行：{module.blocked_reason}"))
        if emit is not None:
            emit(
                EventType.TOOL_RESULT,
                {
                    "tool": "execute_skill_script",
                    "ok": module.status == "completed",
                    "message": module.summary,
                    "output": {
                        "success": module.status == "completed",
                        "skill_id": module.skill_id,
                        "run_id": module.run_id,
                        "output_layer_id": module.output_layer,
                        "stop_code": module.stop_code,
                        "summary": module.summary,
                        "stats": module.stats,
                    },
                },
            )

    ready_modules = [module.module_id for module in modules if module.status == "completed"]
    blocked_modules = [module.module_id for module in modules if module.status == "blocked"]
    readiness = [
        AnalysisReadinessItem(
            module_id=module.module_id,
            status="ready" if module.status == "completed" else "blocked",
            required_data=list(MODULE_SPECS[module.module_id].required_data) if module.module_id in MODULE_SPECS else ["人口属性数据"],
            reason=("真实 Skill 校验通过并产出地图、统计和证据。" if module.status == "completed" else str(module.blocked_reason or module.summary)),
        )
        for module in modules
    ]
    overall = f"请求 {len(route.requested_modules)} 个板块；真实完成 {len(ready_modules)} 个，阻断 {len(blocked_modules)} 个。"
    if blocked_modules:
        overall += " 阻断板块未生成假图层或推断性结论。"
    analysis = StructuredAnalysisResult(
        route_id=route.intent_id,
        route_rule=route.route_rule,
        route_reason=route.route_reason,
        requested_modules=list(route.requested_modules),
        ready_modules=ready_modules,
        blocked_modules=blocked_modules,
        data_readiness=readiness,
        modules=modules,
        overall_summary=overall,
    )
    if emit is not None:
        emit(
            EventType.DATA_CHECK,
            {
                "state": "ready" if not blocked_modules else ("partial" if ready_modules else "blocked"),
                "ready": ready_modules,
                "missing": blocked_modules,
            },
        )
    final_answer = _final_answer(route, analysis)
    trace.append(TraceEvent(type="text", content=final_answer))
    evidence = [
        f"{module.module_id}: run={module.run_id}, layer={module.output_layer}"
        for module in modules
        if module.status == "completed"
    ]
    return ExecutionResult(
        intent_id=route.intent_id,
        trace=trace,
        result_group=_result_group(route, modules),
        evidence=evidence,
        reflection="受控规则路由和批量编排；GIS 结果均来自既有 Skill 真运行，阻断项未生成结果。",
        used_fallback=False,
        final_answer=final_answer,
        structured_analysis=analysis,
    )


def try_execute_controlled_analysis(
    request: ExecuteRequest,
    *,
    ctx: ToolContext | None = None,
    emit: Any | None = None,
) -> ExecutionResult | None:
    """识别综合入口 ExecuteRequest；非本场景返回 None，保留原执行路径。"""
    route = _route_from_intent(request.intent_id) or route_analysis_request(request.question)
    if route is None:
        return None
    return execute_analysis_route(route, ctx=ctx, emit=emit)
