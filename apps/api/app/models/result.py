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


class ResultMetric(BaseModel):
    """结构化指标：名称 + 数值 + 单位 + 空间单元 + 数据来源（供指标条 / 导出 / 追溯）。

    params 记录该指标的计算参数（如聚合单元/半径），便于复现与质量核查。
    """
    name: str
    value: float | int | str
    unit: str | None = None
    params: dict[str, object] = Field(default_factory=dict)
    spatial_unit: str | None = None
    source: str | None = None


class ResultGroup(BaseModel):
    id: str
    title: str
    widgets: list[ResultWidget] = Field(default_factory=list)
    created_at: str | None = None
    # ── 统一结果格式（文档 §4.4/§9.4）：全部默认值，向后兼容 ─────────────
    metrics: list[ResultMetric] = Field(default_factory=list)      # 结构化指标
    findings: list[str] = Field(default_factory=list)              # 发现/结论
    warnings: list[str] = Field(default_factory=list)              # 质量警告
    params: dict[str, object] = Field(default_factory=dict)        # 方法参数（复现）
    rules: list[str] = Field(default_factory=list)                 # 判断规则
    source: str | None = None                                      # 数据来源
    next_questions: list[str] = Field(default_factory=list)        # 推荐追问
    stop_reason: str | None = None                                 # completed/data_missing/tool_call_limit


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
    # 停止原因（冗余于 result_group.stop_reason）：completed/data_missing/tool_call_limit。
    # 缺省 None 时读 result_group 内的 stop_reason。
    stop_reason: str | None = None
