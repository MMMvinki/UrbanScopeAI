from enum import Enum

from pydantic import BaseModel, Field


class MessageRole(str, Enum):
    user = "user"
    agent = "agent"


class StepStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    blocked = "blocked"


class PlanStep(BaseModel):
    step_id: str
    label: str
    tool: str | None = None
    detail: str | None = None
    status: StepStatus = StepStatus.pending


class DataCheckItem(BaseModel):
    item: str
    ready: bool
    source: str | None = None
    suggestions: list[str] = Field(default_factory=list)


class DataCheckResult(BaseModel):
    """就绪检查结果。state: ready | partial | blocked。"""

    state: str
    ready: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    details: list[DataCheckItem] = Field(default_factory=list)


class TraceEvent(BaseModel):
    """执行过程事件，与前端 Agent 消息里的 blocks 对齐。

    type 取值：text | ran_command | created | ran | generated
    """

    type: str
    content: str
    meta: str | None = None
    step_id: str | None = None


class Clarify(BaseModel):
    """门1 意图澄清：Planner 不确定用户意图时停下来追问，不产出计划。"""

    needs: str | None = None  # 还缺什么信息
    question: str  # 一句话追问
    choices: list[str] = Field(default_factory=list)  # 可选理解，用户可直接点选


class PlanRequest(BaseModel):
    question: str
    # 多轮对话历史：与当前问题构成完整上下文
    history: list[dict] = Field(default_factory=list)
    # 会话 id：同一前端会话内稳定生成，用于持久化 conversation memory
    conversation_id: str | None = None


class PlanResult(BaseModel):
    """Planner 输出：不预设意图，动态抽取数据图层与分析操作。

    - intent_id：由操作+目标生成的稳定 id（skill 路径则用 skill id）
    - operation：抽取到的分析操作（coverage / overlay / nearest_distance / density / supply_demand / general）
    - targets：问题中涉及的目标图层 id
    - distance：抽取到的服务半径/距离（米），未提及则用默认值
    - thinking：Planner 的思考过程（skill/tool 选择依据），前端展示
    """

    intent_id: str
    summary: str
    # Planner 思考过程：动态注入的 skill/tool 是如何命中与挑选的
    thinking: str = ""
    operation: str = "general"
    targets: list[str] = Field(default_factory=list)
    distance: int = 800
    # 各目标图层的独立距离（如 城市体检：学校 800m、公园 500m）；缺省用 distance
    target_distances: dict[str, int] = Field(default_factory=dict)
    required_data: list[str] = Field(default_factory=list)
    plan: list[PlanStep] = Field(default_factory=list)
    data_check: DataCheckResult | None = None
    # 门1：意图模糊 → clarify 非空，operation="clarify"，其余字段为空，不触发执行
    clarify: Clarify | None = None
    # 门2：解析不到数据的分析对象原文（如「人群画像」），前端据此弹「跳过/上传/换问法」
    unmet_targets: list[str] = Field(default_factory=list)


class ExecuteRequest(BaseModel):
    intent_id: str
    question: str = ""
    summary: str = ""  # Planner 的一句话总结，供 LLM Executor 提示词使用
    operation: str = "general"
    targets: list[str] = Field(default_factory=list)
    distance: int = 800
    target_distances: dict[str, int] = Field(default_factory=dict)
    required_data: list[str] = Field(default_factory=list)
    plan: list[PlanStep] = Field(default_factory=list)
    data_check: DataCheckResult | None = None
    # 门2 透传：解析不到数据的分析对象原文（前端据此展示缺失面板）
    unmet_targets: list[str] = Field(default_factory=list)
    # 门2 授权：用户已同意跳过的缺失项（前端点「跳过该项」后携带）
    accept_missing: list[str] = Field(default_factory=list)
    # 多轮对话历史（问题已从中抽离，仅作上下文）
    history: list[dict] = Field(default_factory=list)
    # 会话 id：同一前端会话内稳定生成，用于持久化 conversation memory
    conversation_id: str | None = None


class AgentMessage(BaseModel):
    """会话消息（前后端通用 DTO）。"""

    id: str
    role: MessageRole
    content: str | None = None
    blocks: list[TraceEvent] = Field(default_factory=list)
    status: str = "done"  # done | streaming | loading
