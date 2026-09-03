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
PUBLIC_SERVICE_MODULE = "public_service"
POPULATION_MODULE = "population_profile"
RAIL_ACCESSIBILITY_MODULE = "commercial_rail_accessibility"
RAIL_NETWORK_MODULE = "rail_network_reachability"

ACTIVITY_SKILL = "activity-temporal-profile"
COMMERCIAL_SKILL = "commercial-supply-profile"
DIAGNOSIS_SKILL = "commercial-vitality"
PUBLIC_SERVICE_SKILL = "public-service-profile"
POPULATION_SKILL = "population-workforce-profile"
RAIL_ACCESSIBILITY_SKILL = "commercial-rail-accessibility"
RAIL_NETWORK_SKILL = "rail-network-reachability"
COMPREHENSIVE_INTENT = "project-comprehensive-analysis"


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
    PUBLIC_SERVICE_MODULE: ModuleSpec(
        PUBLIC_SERVICE_MODULE,
        "公共服务设施资源画像",
        PUBLIC_SERVICE_SKILL,
        "L2",
        ("候选研究范围", "深圳市 POI 全量原始总表（含 bigType/midType/smallType 与 WGS-84 坐标字段）"),
    ),
    POPULATION_MODULE: ModuleSpec(
        POPULATION_MODULE,
        "人口·就业画像",
        POPULATION_SKILL,
        "L2",
        ("候选研究范围", "常住数量（居住/工作）+ 常住画像（居住/工作）原始 TXT（GCJ-02 网格中心坐标）"),
    ),
    RAIL_ACCESSIBILITY_MODULE: ModuleSpec(
        RAIL_ACCESSIBILITY_MODULE,
        "商业供给 × 地铁真实步行可达",
        RAIL_ACCESSIBILITY_SKILL,
        "L3",
        ("候选研究范围", "含有效记录标记的商业点数据", "地铁出入口点（OSM 提取，WGS84）", "MAPBOX_TOKEN（真实路网等时圈）"),
    ),
    RAIL_NETWORK_MODULE: ModuleSpec(
        RAIL_NETWORK_MODULE,
        "全区地铁步行可达率",
        RAIL_NETWORK_SKILL,
        "L3",
        ("候选研究范围", "地铁出入口点（OSM 提取，WGS84）", "含有效记录标记的商业点数据", "MAPBOX_TOKEN（真实路网等时圈）"),
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
    # C11：口语问句也命中人口·就业（skill 用仓库内真实默认源即可算，勿被通用门2 拦走）
    "人口就业", "职住比", "职住", "人口分布",
    # C11 补漏（Codex 建议的有限短语；不加过宽「人口」以免误抢语义）
    "人口分析", "人群分析", "人群分布", "人口情况", "人群情况", "居民画像", "就业画像",
)
_COMPREHENSIVE_TERMS = (
    "综合现状", "全面现状", "项目综合", "项目全面", "整体分析", "全部现状", "现状分析",
    # 0904 补口语：评委口头「全面分析一下」类（未加裸「全面」，防误抢单板块问法）
    "全面分析一下",
    "项目现状", "现有数据能做什么",
    "当前数据能做什么", "能做什么", "请根据当前已有数据，对项目各方面进行一次尽可能完整的现状分析。",
)
_PUBLIC_SERVICE_TERMS = (
    "公共服务", "公共设施", "科教文化", "教育设施", "医疗设施", "体育设施",
    "便民设施", "应急设施", "教育", "医疗", "体育", "便民", "应急",
    # 0904 补口语：评委口头「学校和医院」「学校怎么样」类问法也进公服（不加「商业」周边句防护，
    # 已知副作用：含「学校+商业」的句子会先进公服——见演示准备包 P1-3 说明）
    "学校", "医院",
)
_RAIL_ACCESSIBILITY_TERMS = (
    "地铁", "轨交", "轨道交通", "出入口", "等时圈", "步行可达", "步行", "可达",
)
# 全区地铁可达（全网口径）：与 single-corridor 的「≤5 个代表出入口」区分
_RAIL_NETWORK_TERMS = (
    "全区地铁可达", "全区地铁", "全网地铁", "全网可达", "所有地铁站", "地铁覆盖", "可达率", "难达区", "难达",
)


# ── 「主题组」全家桶（A）：区域语义 + 主题名词 → 命中对应组按序跑多个模块 ──
_AREA_TERMS = (
    "区域", "研究范围", "片区", "这个区", "当地", "候选范围", "范围内", "这片", "区内", "本区", "这里",
)
_THEME_COMMERCIAL = ("商业", "商铺", "店铺", "商店", "网点")
_THEME_COMMERCIAL_PRECISE = ("供给", "数量", "密度", "集聚", "聚集", "格局", "分布", "画像")
_THEME_PUBLIC = ("公共服务", "公共设施")
_THEME_PUBLIC_PRECISE = ("设施", "资源分布", "画像", "分布")
_THEME_POPULATION = ("人口", "人群", "居民")
_THEME_POPULATION_PRECISE = _POPULATION_TERMS  # 精确问法（画像/结构/分布/职住比…）仍走单板块
_THEME_ACTIVITY = ("活动", "活力")
_THEME_ACTIVITY_PRECISE = (
    "时序", "客流", "匹配", "昼夜", "画像", "数量", "白天", "晚上", "工作日", "周末", "规律", "错位",
)
_THEME_RAIL = ("地铁", "轨交", "轨道交通", "可达")
_THEME_RAIL_PRECISE = _RAIL_NETWORK_TERMS + ("等时圈", "出入口", "步行可达", "覆盖率", "覆盖", "站")


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _rail_overlay_focus(question: str | None) -> list[str] | None:
    """从问句里判断「地铁可达 × 特定资产」的聚焦类型；未提到任何资产返回 None（默认全叠）。

    优先级：明确提到绿地/公共空间/公园 → 聚焦 green；提到文化 → culture；提到公共服务/设施 → public；
    多个都提到 → 各自列出（如 ['green','culture']）。只用于向 rail skill 注入 overlay_types，不改路由判定。
    """
    q = str(question or "")
    if not q:
        return None
    focus: list[str] = []
    if any(t in q for t in ("绿地", "公共空间", "公园", "广场", "绿廊")):
        focus.append("green")
    if any(t in q for t in ("文化", "文博", "博物馆", "图书馆", "美术馆")):
        focus.append("culture")
    if any(t in q for t in ("公共服务", "公共设施", "学校", "医院", "医疗", "体育设施", "便民")):
        focus.append("public")
    return focus or None


def _theme_route(q: str) -> AnalysisRoute | None:
    """区域语义 + 主题名词 → 主题组路由；含精确单板块标记时放行（不误伤精确问法）。"""
    if not _contains_any(q, _AREA_TERMS):
        return None
    themes = (
        (
            "commercial",
            "R-THEME-COMMERCIAL",
            _THEME_COMMERCIAL,
            _THEME_COMMERCIAL_PRECISE,
            (COMMERCIAL_MODULE, DIAGNOSIS_MODULE, RAIL_NETWORK_MODULE),
            "商业",
        ),
        (
            "public_service",
            "R-THEME-PUBLIC-SERVICE",
            _THEME_PUBLIC,
            _THEME_PUBLIC_PRECISE,
            (PUBLIC_SERVICE_MODULE,),
            "公共服务",
        ),
        (
            "population",
            "R-THEME-POPULATION",
            _THEME_POPULATION,
            _THEME_POPULATION_PRECISE,
            (POPULATION_MODULE,),
            "人口",
        ),
        (
            "activity",
            "R-THEME-ACTIVITY",
            _THEME_ACTIVITY,
            _THEME_ACTIVITY_PRECISE,
            (ACTIVITY_MODULE,),
            "活动",
        ),
        (
            "reachability",
            "R-THEME-REACHABILITY",
            _THEME_RAIL,
            _THEME_RAIL_PRECISE,
            (RAIL_NETWORK_MODULE,),
            "地铁可达",
        ),
    )
    for key, rule, broad, precise, modules, name in themes:
        if _contains_any(q, broad) and not _contains_any(q, precise):
            label = "，".join(MODULE_SPECS[m].label for m in modules if m in MODULE_SPECS)
            return AnalysisRoute(
                intent_id=f"theme-{key}",
                route_rule=rule,
                route_reason=f"命中区域语义 + 「{name}」主题，选择{name}主题组按序执行：{label}（有数据才跑、缺前置按板块阻断）。",
                requested_modules=modules,
                executable_modules=modules,
            )
    return None


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
            route_reason="命中项目级‘综合现状/全面分析/现有数据能做什么’表达，按活动、商业、综合诊断、公共服务与人口·就业画像五个板块检查并执行。",
            requested_modules=(
                ACTIVITY_MODULE, COMMERCIAL_MODULE, DIAGNOSIS_MODULE, PUBLIC_SERVICE_MODULE, POPULATION_MODULE,
            ),
            executable_modules=(
                ACTIVITY_MODULE, COMMERCIAL_MODULE, DIAGNOSIS_MODULE, PUBLIC_SERVICE_MODULE, POPULATION_MODULE,
            ),
        )

    # 主题组（全家桶）先于纯单板块：区域语义 + 主题名词 → 整组按序跑；精确问法不含区域/含
    # 单板块标记时放行到下面单板块，不误伤（“这个区域的商业”→“商业组”，而非 R-COMMERCIAL-SUPPLY）。
    _theme = _theme_route(q)
    if _theme is not None:
        return _theme

    # 人口·就业画像：有真实 Skill，按常住数量 + 画像口径执行（不降级）。
    if _contains_any(q, _POPULATION_TERMS):
        return AnalysisRoute(
            intent_id=POPULATION_SKILL,
            route_rule="R-POPULATION-PROFILE",
            route_reason="命中人口画像/人口结构/常住就业语义，选择人口·就业画像（202604 常住数量+画像口径）。",
            requested_modules=(POPULATION_MODULE,),
            executable_modules=(POPULATION_MODULE,),
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

    # 全区地铁可达（全网口径）：可达率/难达区/覆盖/所有地铁站/全区。放在 single-corridor
    # 规则之前，避免「全区/全网/可达率」被 5 个代表出入口口径截走。
    if _contains_any(q, _RAIL_NETWORK_TERMS):
        return AnalysisRoute(
            intent_id=RAIL_NETWORK_SKILL,
            route_rule="R-RAIL-NETWORK-REACHABILITY",
            route_reason="命中全区/全网地铁可达、可达率、难达区或覆盖语义，选择全区地铁步行可达率（全部出入口聚类成簇 → 真实路网 5/10/15 分钟并集）。",
            requested_modules=(RAIL_NETWORK_MODULE,),
            executable_modules=(RAIL_NETWORK_MODULE,),
        )

    # 商业 + 地铁/轨交/可达/等时圈语义 → 商业 × 轨交真实步行可达性。
    # 放在纯商业单板块规则之前，确保“商业设施与地铁出入口的步行可达关系”不会被纯商业供给截走。
    if has_commercial and _contains_any(q, _RAIL_ACCESSIBILITY_TERMS):
        return AnalysisRoute(
            intent_id=RAIL_ACCESSIBILITY_SKILL,
            route_rule="R-COMMERCIAL-RAIL-ACCESSIBILITY",
            route_reason="同时命中商业与地铁/轨交/可达/等时圈语义，选择商业供给 × 地铁真实步行可达（Mapbox walking 5/10/15 分钟等时圈）。",
            requested_modules=(RAIL_ACCESSIBILITY_MODULE,),
            executable_modules=(RAIL_ACCESSIBILITY_MODULE,),
        )

    # 用户明确提及公共服务/公共设施/教育/医疗/体育/便民/应急语义 → 公共服务设施资源画像。
    # 放在纯商业、纯活动单板块规则之前，确保“教育、医疗、体育与便民设施分布”不会被截去。
    if _contains_any(q, _PUBLIC_SERVICE_TERMS):
        return AnalysisRoute(
            intent_id=PUBLIC_SERVICE_SKILL,
            route_rule="R-PUBLIC-SERVICE",
            route_reason="命中公共服务/公共设施/教育/医疗/体育/便民语义，选择公共服务设施资源画像。",
            requested_modules=(PUBLIC_SERVICE_MODULE,),
            executable_modules=(PUBLIC_SERVICE_MODULE,),
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
    if intent_id == ACTIVITY_SKILL:
        return route_analysis_request("活动时序")
    if intent_id == COMMERCIAL_SKILL:
        return route_analysis_request("商业供给")
    if intent_id == DIAGNOSIS_SKILL:
        return route_analysis_request("商业与活动活力匹配")
    if intent_id == PUBLIC_SERVICE_SKILL:
        return route_analysis_request("分析公共服务设施资源分布")
    if intent_id == POPULATION_SKILL:
        return route_analysis_request("人口画像")
    if intent_id == RAIL_ACCESSIBILITY_SKILL:
        return route_analysis_request("商业与地铁出入口步行可达")
    if intent_id == RAIL_NETWORK_SKILL:
        return route_analysis_request("全区地铁可达率与难达区")
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
        achieved_level="L1",
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
    output_layer_ids = [
        str(item.get("layer_id"))
        for item in layers
        if isinstance(item, dict) and item.get("layer_id")
    ]
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
        # 主图层在前端「在地图上查看」只开该模块；完整成果组（含辅助层）供图层收尾纪律使用。
        output_layer_ids=output_layer_ids,
    )


def _result_group(route: AnalysisRoute, modules: list[AnalysisModuleResult]) -> ResultGroup:
    widgets: list[ResultWidget] = []
    for index, module in enumerate(modules):
        widgets.append(
            ResultWidget(
                id=f"w-analysis-{index}",
                title=MODULE_SPECS[module.module_id].label if module.module_id in MODULE_SPECS else module.module_id,
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
    lines.append("边界：活动强度不是人口数量或人口结构；商业点数量/密度不是营业额、经营质量或消费等级；商业与活动空间共现不代表因果；公共服务设施点位数只描述相对空间分布，不代表服务容量、可达性或短缺；人口总数为 202604 单月快照口径的估算值且为候选范围筛选后，画像为网格汇总条件分布、非个人调查，未经供应商授权不输出权威总量。")
    lines.append("人口网格中心坐标按 GCJ-02→WGS84 换算（已核验），活动数据的 gcj02_to_wgs84 仍是候选坐标版本；常住（202604）与客流/OD（2025 各日）日期口径不同，不横比。")
    return "\n".join(lines)


SkillRunner = Callable[..., dict[str, Any]]


def execute_analysis_route(
    route: AnalysisRoute,
    *,
    ctx: ToolContext | None = None,
    emit: Any | None = None,
    run_skill: SkillRunner | None = None,
    params_by_skill: dict[str, dict[str, Any]] | None = None,
    question: str | None = None,
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

    # 语义聚焦：问句提到特定资产（绿地/公共空间/文化/设施）→ 只对 rail 模块注入 overlay_types，
    # 让「地铁可达 × 绿地」这类问法聚焦到对应资产叠加，而非默认全叠。
    rail_focus = _rail_overlay_focus(question)

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
        if module_id == RAIL_NETWORK_MODULE and rail_focus:
            params["overlay_types"] = rail_focus
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
            required_data=list(MODULE_SPECS[module.module_id].required_data) if module.module_id in MODULE_SPECS else ["相关数据"],
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
    return execute_analysis_route(route, ctx=ctx, emit=emit, question=request.question)
