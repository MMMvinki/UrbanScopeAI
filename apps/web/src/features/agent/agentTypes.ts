export type MessageRole = 'user' | 'agent'

export type StepStatus = 'done' | 'warning' | 'running' | 'pending'

export type MessageStatus = 'done' | 'loading' | 'streaming'

/** Agent 过程事件类型：运行命令 / 已创建 / 已运行 / 已生成 */
export type EventKind = 'ran-command' | 'created' | 'ran' | 'generated'

/** 内容块稳定 key（增量更新时匹配同一条块） */
let _blockSeq = 0
export function nextBlockId(): string {
  _blockSeq += 1
  return `b-${_blockSeq}`
}

/** 流式输出的内容块：自然语言文本 / 过程事件 / 代码块 / 工具调用 */
export type AgentBlock =
  | { type: 'text'; content: string; id?: string }
  | { type: 'event'; kind: EventKind; content: string; meta?: string; id?: string }
  | { type: 'code'; code: string; status: 'running' | 'done' | 'error'; result?: string; id?: string }
  | { type: 'tool'; name: string; status: 'running' | 'done' | 'error'; detail?: string; id?: string }

export interface DataCheckResult {
  ready: string[]
  missing: string[]
}

export interface PlanStep {
  step_id?: string
  label: string
  /** 后端计划里标注的执行工具（tool_start/tool_result 事件据此逐项打勾） */
  tool?: string | null
  status: StepStatus
}

export interface ToolCallRecord {
  name: string
  detail: string
}

/** 一条 Agent 对话消息。用户消息靠右，Agent 消息靠左。 */
export interface AgentMessage {
  id: string
  role: MessageRole
  /** 用户消息的文本；Agent 消息也可用作纯文本回退 */
  content?: string
  /** Agent 消息的流式过程内容块 */
  blocks?: AgentBlock[]
  status?: MessageStatus
  /** 数据检查（可折叠） */
  dataCheck?: DataCheckResult
  /** 分析计划（可折叠） */
  plan?: PlanStep[]
  /** Planner 思考过程（可折叠，展示 skill/工具选择依据） */
  thinking?: string
  /** Tool Trace（可折叠） */
  trace?: ToolCallRecord[]
  /** 结果依据（可折叠） */
  evidence?: string[]
  /** 反思：执行结果的复核说明（可折叠） */
  reflection?: string
  /** 是否走了规则兜底（LLM 不可用 / 执行失败） */
  usedFallback?: boolean
  /** 最终回复：给用户的结论文本（不含思考过程叙述；无则前端回退 bodyText） */
  finalAnswer?: string
  /** 流式模式：live=真实 SSE 增量渲染；demo=初始演示消息的伪播放 */
  streamMode?: 'demo' | 'live'
  /** 归属 run id（/runs/{id} 追溯） */
  runId?: string
  /** 本运行对应的成果组 id：回复下方「成果产物」chip 跳转成果页用 */
  resultGroupId?: string
  /** 门1 意图澄清：追问气泡（问题 + 选项按钮 + 可打字），不执行 */
  clarify?: ClarifyData
  /** 门2 数据缺失：解析不到数据的分析对象原文，渲染缺失面板（跳过/上传/换问法），不执行 */
  unmetTargets?: string[]
  /** 门2 暂存完整计划：点「跳过该项」直接 execute，无需重新 plan */
  planResult?: import('../../lib/api').PlanResult
  /** 门2 暂存原问题：上传数据后「用它继续」重 plan */
  originalQuestion?: string
}

/** 门1 意图澄清：Planner 追问，前端渲染追问气泡（选项按钮 + 可打字） */
export interface ClarifyData {
  needs?: string
  question: string
  choices: string[]
}

/** 待审批权限请求（permission_request 事件置起，ApprovalModal 消费） */
export interface PendingApproval {
  requestId: string
  tool: string
  arguments: Record<string, unknown>
  reason: string
}

/** 多轮对话记录（发送给后端作为上下文记忆） */
export interface ChatTurn {
  role: 'user' | 'assistant'
  content: string
}
