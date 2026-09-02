from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.agent import TraceEvent


class WidgetType(str, Enum):
    metric = "metric"
    chart = "chart"
    list = "list"
    table = "table"
    map = "map"
    # 图片类产物（如 save_plot 保存的 matplotlib 图表 PNG）；data.url 为静态路由路径
    image = "image"


class ResultWidget(BaseModel):
    id: str
    title: str
    widget_type: WidgetType
    value: str | None = None
    subtitle: str | None = None
    unit: str | None = None
    data: dict[str, object] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    # 关联图层 id：前端「查看图层 / 定位地图」使用
    layer_id: str | None = None


class ResultGroup(BaseModel):
    id: str
    title: str
    widgets: list[ResultWidget] = Field(default_factory=list)
    created_at: str | None = None


class AnalysisModuleResult(BaseModel):
    """综合入口的单个分析板块；字段保持简短，避免前端解析长工具文本。"""

    module_id: str
    status: Literal["completed", "blocked"]
    skill_id: str | None = None
    run_id: str | None = None
    achieved_level: str
    summary: str
    stats: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    output_layer: str | None = None
    stop_code: str | None = None
    blocked_reason: str | None = None


class AnalysisReadinessItem(BaseModel):
    """某个板块经过真实 Skill 校验后的数据就绪结论。"""

    module_id: str
    status: Literal["ready", "blocked"]
    required_data: list[str] = Field(default_factory=list)
    reason: str


class StructuredAnalysisResult(BaseModel):
    """普通规划用户综合入口的稳定结果契约。"""

    route_id: str
    route_rule: str
    route_reason: str
    requested_modules: list[str] = Field(default_factory=list)
    ready_modules: list[str] = Field(default_factory=list)
    blocked_modules: list[str] = Field(default_factory=list)
    data_readiness: list[AnalysisReadinessItem] = Field(default_factory=list)
    modules: list[AnalysisModuleResult] = Field(default_factory=list)
    overall_summary: str


class ExecutionResult(BaseModel):
    intent_id: str
    trace: list[TraceEvent] = Field(default_factory=list)
    result_group: ResultGroup | None = None
    evidence: list[str] = Field(default_factory=list)
    # 反思：Executor 对计划执行结果的复核说明（供「反思」折痕展示）
    reflection: str = ""
    # 是否走了规则兜底（LLM 不可用 / 执行失败时）
    used_fallback: bool = False
    # 最终回复：给用户的结论文本（不含思考过程叙述）。
    # 缺省 None 时前端/worker 兜底取 trace 最后一条 text 事件作为回复。
    final_answer: str | None = None
    # 受控综合入口的结构化结果；旧分析路径保持 None，向后兼容。
    structured_analysis: StructuredAnalysisResult | None = None
