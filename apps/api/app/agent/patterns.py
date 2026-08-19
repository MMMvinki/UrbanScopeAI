"""分析模式与通用引擎词汇表。

Planner **不做意图分类**，而是对任意问题做两件事的动态抽取：
1. 数据抽取：问题中提到了哪些图层（DATA_VOCABULARY，任意组合）；
2. 操作抽取：问题想要哪种分析（OPERATION_VOCABULARY）以及距离参数。

然后按"操作"选用方法模板（METHOD_TEMPLATES）把计划步骤组装出来。
命名 Skill（如街区城市体检）走 skill 路由，其余一切问题都走这套通用引擎。
"""

import re
from dataclasses import dataclass

# 数据关键词：命中任一关键词 → 对应图层 id
DATA_VOCABULARY: list[tuple[tuple[str, ...], str]] = [
    (("学校", "中学", "教育", "学区", "托幼", "幼儿园"), "schools"),
    (("公园", "绿地", "绿化", "游园"), "parks"),
    (("文化", "活动中心", "图书馆", "体育"), "culture_centers"),
    (("产业", "工业", "园区", "厂房", "制造业"), "industry"),
    (("轨道", "地铁", "轻轨", "站点", "公交"), "transit_stations"),
    (("人口", "居民", "住户", "常住"), "population_grid"),
    (("居住", "住宅", "住区", "小区", "住房"), "residential"),
]

# 注意：study_area 不进入目标操作数（它是空间上下文，始终在 required_data 基线里），
# 否则 "这个片区人口薄弱区" 会把研究范围也当成分析对象，产出语义奇怪的结果卡片。

# 操作关键词：命中任一关键词 → 分析操作
OPERATION_VOCABULARY: list[tuple[tuple[str, ...], str]] = [
    (("覆盖", "可达", "服务范围", "服务半径", "辐射", "配套"), "coverage"),
    (("距离", "最近", "邻近", "周边", "通勤"), "nearest_distance"),
    (("叠加", "重叠", "相交", "结合", "薄弱"), "overlay"),
    (("密度", "集中", "疏密"), "density"),
    (("供需", "匹配", "错配", "需求", "缺口"), "supply_demand"),
]

DEFAULT_OPERATION = "coverage"
DEFAULT_DISTANCE = 800
BASELINE_LAYERS = ("study_area", "residential")
# 注意：不提供"未提及数据图层"时的兜底目标——把"下载餐饮POI"之类请求硬映射到
# 居住用地分析会产生无关结果。未提取到图层时保持空，由执行器在规则兜底时诚实拒绝。
FACILITY_LAYERS = ("schools", "parks", "culture_centers", "industry", "transit_stations")

_DISTANCE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(米|公里|m|km)", re.IGNORECASE)


@dataclass(frozen=True)
class PlanStepSpec:
    label: str
    tool: str | None = None
    detail: str | None = None


# 方法模板：按"操作"组织的通用计划步骤，与具体问题无关
METHOD_TEMPLATES: dict[str, tuple[PlanStepSpec, ...]] = {
    "coverage": (
        PlanStepSpec("理解规划问题"),
        PlanStepSpec("检查研究范围与基础数据", "list_datasets"),
        PlanStepSpec("判断可执行指标", "readiness"),
        PlanStepSpec("生成服务覆盖图层", "coverage", "按服务半径生成"),
        PlanStepSpec("统计覆盖人口与指标", "statistics"),
        PlanStepSpec("输出结果卡片与证据链", "results"),
    ),
    "overlay": (
        PlanStepSpec("理解规划问题"),
        PlanStepSpec("检查研究范围与基础数据", "list_datasets"),
        PlanStepSpec("判断可执行指标", "readiness"),
        PlanStepSpec("叠加目标图层", "spatial_join", "空间叠加"),
        PlanStepSpec("统计空间关系指标", "statistics"),
        PlanStepSpec("输出结果卡片与证据链", "results"),
    ),
    "nearest_distance": (
        PlanStepSpec("理解规划问题"),
        PlanStepSpec("检查研究范围与基础数据", "list_datasets"),
        PlanStepSpec("判断可执行指标", "readiness"),
        PlanStepSpec("计算最近距离", "nearest_distance", "最近设施距离"),
        PlanStepSpec("统计距离分布", "statistics"),
        PlanStepSpec("输出结果卡片与证据链", "results"),
    ),
    "density": (
        PlanStepSpec("理解规划问题"),
        PlanStepSpec("检查研究范围与基础数据", "list_datasets"),
        PlanStepSpec("判断可执行指标", "readiness"),
        PlanStepSpec("计算核密度分布", "execute_skill_script", "核密度网格图层（execute_skill_script kernel-density）"),
        PlanStepSpec("统计密度指标", "statistics"),
        PlanStepSpec("输出结果卡片与证据链", "results"),
    ),
    "supply_demand": (
        PlanStepSpec("理解规划问题"),
        PlanStepSpec("检查研究范围与基础数据", "list_datasets"),
        PlanStepSpec("判断可执行指标", "readiness"),
        PlanStepSpec("计算供需匹配", "supply_demand", "设施与人口匹配"),
        PlanStepSpec("统计错配与缺口", "statistics"),
        PlanStepSpec("输出结果卡片与证据链", "results"),
    ),
    "general": (
        PlanStepSpec("理解规划问题"),
        PlanStepSpec("检查研究范围与基础数据", "list_datasets"),
        PlanStepSpec("判断可执行指标", "readiness"),
        PlanStepSpec("执行空间分析", "analysis"),
        PlanStepSpec("统计分析指标", "statistics"),
        PlanStepSpec("输出结果卡片与证据链", "results"),
    ),
}


def extract_distance(question: str) -> int:
    """从问题中抽取距离参数（米），未提及用默认值。"""
    match = _DISTANCE_RE.search(question)
    if not match:
        return DEFAULT_DISTANCE
    value = float(match.group(1))
    unit = match.group(2).lower()
    if unit in ("km", "公里"):
        value *= 1000
    return int(value)
