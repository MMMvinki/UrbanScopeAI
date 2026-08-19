from enum import Enum

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
