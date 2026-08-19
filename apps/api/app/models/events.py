"""Agent 事件协议：SSE 流式 + RunArchive 事件溯源共享的契约。

一次 agent run 产生一条 AgentEventEnvelope 流：
  run_start → data_check → plan(可选) → (thought_delta / tool_start / tool_result /
  code_block_start / code_delta / code_result / generated_layer / permission_request /
  permission_result)* → execution(终结载荷) → stream_end

前端据此增量渲染（真流式）；RunArchive 将每个事件追加落盘（events.jsonl）。
ExecutionResult 作为 execution 事件的 data 全量下发，向后兼容旧前端映射。
"""

from enum import Enum

from pydantic import BaseModel, Field


class EventType(str, Enum):
    """Agent 事件类型（SSE 帧的 event: 名）。"""

    RUN_START = "run_start"  # run_id 广播（前端关联 /runs/{id}）
    THOUGHT_DELTA = "thought_delta"  # LLM 文本增量
    DATA_CHECK = "data_check"  # 就绪检查
    PLAN = "plan"  # 计划（可选）
    TOOL_START = "tool_start"
    TOOL_RESULT = "tool_result"
    CODE_BLOCK_START = "code_block_start"
    CODE_DELTA = "code_delta"
    CODE_RESULT = "code_result"
    GENERATED_LAYER = "generated_layer"
    LAYER_STYLE = "layer_style"  # 图层样式更新（含 graduated 的 styleMode/visualVariables，Sprint 2）
    LAYER_VISIBILITY = "layer_visibility"  # 图层可见性更新 {layer_id, visible, show_only?}
    ZOOM_TO_LAYER = "zoom_to_layer"  # 地图缩放到图层 {layer_id}
    LAYER_REMOVED = "layer_removed"  # 图层移除 {layer_id}（remove_layer 工具）
    LAYER_REORDER = "layer_reorder"  # 图层顺序调整 {layer_id, to_index}（set_layer_order 工具）
    BASEMAP_CHANGED = "basemap_changed"  # 底图切换 {basemap_id}（set_basemap 工具）
    ARTIFACT = "artifact"  # 产物引用（plot / GeoJSON / CSV ...）
    PERMISSION_REQUEST = "permission_request"
    PERMISSION_RESULT = "permission_result"
    RUN_ARCHIVE = "run_archive"  # archive open/close
    ERROR = "error"
    EXECUTION = "execution"  # 终结载荷（ExecutionResult 全量）
    STREAM_END = "stream_end"


class ToolTrace(BaseModel):
    """单次工具调用记录（与 TraceEvent type='ran' 对应，带结构化字段）。"""

    seq: int
    tool: str
    args: dict[str, object] = Field(default_factory=dict)
    ok: bool = True
    output: dict[str, object] = Field(default_factory=dict)
    message: str | None = None
    duration_ms: float | None = None
    run_id: str | None = None


class AgentEventEnvelope(BaseModel):
    """SSE 帧载荷：类型 + 数据 + 归属 run。"""

    id: str  # uuid4().hex
    type: EventType
    data: dict[str, object] = Field(default_factory=dict)
    run_id: str | None = None
    ts: str  # isoformat
