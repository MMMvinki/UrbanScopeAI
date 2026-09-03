import type { LayerMetadata, LayerStyle } from '../features/layers/layerTypes'
import type { PlanningSkill } from '../features/skills/planningSkills'
import type { ResultGroup } from '../features/results/resultTypes'
import type {
  EvalReportDetail,
  EvalReportSummary,
  EvalRunStatus,
  EvalSetDetail,
  EvalSetSummary,
} from '../features/eval/evalTypes'
import type {
  AgentBlock,
  ChatTurn,
  DataCheckResult as ChatDataCheck,
  ToolCallRecord,
} from '../features/agent/agentTypes'

/**
 * UrbanScope 后端 API 封装：类型镜像 + 请求函数 + 后端模型 → 前端模型映射。
 * 后端契约（FastAPI / Pydantic）：所有请求响应均为 JSON。
 */

export const API_BASE = import.meta.env.VITE_API_URL ?? 'http://127.0.0.1:8011'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!response.ok) {
    throw new Error(`API ${response.status} ${path}`)
  }
  return (await response.json()) as T
}

/**
 * PATCH 图层样式：把前端样式改动持久化到后端（样式侧车，重启后保留）。
 * body 用 camelCase 字段（fillColor/strokeColor/…），与后端 LayerPatch 一致；
 * styleMode/visualVariables 传 null 表示清除分级。
 */
export const patchLayerStyle = (layerId: string, patch: Record<string, unknown>) =>
  request<BackendLayer>(`/layers/${layerId}`, { method: 'PATCH', body: JSON.stringify(patch) })

/* ---------------- 后端模型（镜像 Pydantic） ---------------- */

export interface BackendLayer {
  id: string
  name: string
  source: 'platform' | 'uploaded' | 'generated' | 'drawn' | 'folder'
  geometry: 'Polygon' | 'Point' | 'LineString' | 'Table'
  color: string
  visible?: boolean
  feature_count: number
  crs: string
  description?: string
  generated_by?: string
  fields?: Array<{ name: string; type: string; description?: string | null }>
  /* 平台数据（2026-08-17）：只读底座 + 惰性几何 */
  readonly?: boolean
  /* 源数据磁盘路径（folder 目录/uploaded 数据；后端 model 字段 data_path） */
  data_path?: string | null
  /* folder 源图层源内相对路径（后端 model 字段 rel_path） */
  rel_path?: string | null
  /* 源文件格式（后端 model 字段 data_type） */
  data_type?: string | null
  lazy?: boolean
  kind?: 'vector' | 'raster'
  tile_url?: string
  bbox?: number[] | null
  /* 热力图层加权字段（后端 model 字段 weightField，Pydantic 按字段名序列化） */
  weightField?: string | null
  /* DEM 3D 地形（2026-08-30） */
  dem?: boolean
  terrain_url?: string
  /* Sprint 2 样式字段（随后端 /layers 下发；Pydantic 序列化为 camelCase） */
  opacity?: number | null
  fillOpacity?: number | null
  strokeOpacity?: number | null
  strokeColor?: string | null
  strokeWidth?: number | null
  radius?: number | null
  fillColor?: string | null
  styleMode?: string | null
  visualVariables?: Record<string, unknown> | null
  labelField?: string | null
  /* 手绘图层自动字段单位偏好 */
  units?: { length?: 'm' | 'km'; area?: 'm2' | 'hm2' | 'km2' } | null
}

export interface ToolSpec {
  name: string
  category: string
  description: string
  status: 'planned' | 'ready' | 'blocked'
  parameters: Record<string, string>
  /** 工具卡「发起对话」预置提示词（无则前端由 name/description 兜底生成） */
  prompt_hint?: string | null
}

export interface SkillIndicator {
  name: string
  data: string
  mvp_depth: string
  status: 'planned' | 'ready' | 'blocked'
  steps: string[]
}

/** 统一 Skill（Anthropic Agent Skill）：SKILL.md 驱动 + 可执行两种形态。
 *  kind 决定形态；字段按形态分两组，后端 /skills 合并下发（kind=prompt 组 / kind=executable 组）。 */
export interface SkillSpec {
  id: string
  kind: 'prompt' | 'executable'
  name: string
  /** Anthropic L1 触发词：描述「干什么 + 何时用」，Planner 命中依据 */
  description?: string
  /* ── SKILL.md 驱动字段（kind=prompt） ── */
  core_question?: string
  keywords?: string[]
  required_data?: string[]
  outputs?: string[]
  indicators?: SkillIndicator[]
  body?: string
  plan?: { operation: string; targets: string[]; target_distances: Record<string, number> } | null
  /* ── 可执行字段（kind=executable，skill.json 契约） ── */
  scope?: 'builtin' | 'workspace'
  version?: string
  revision?: number
  status?: 'draft' | 'validated' | 'failed'
  entry?: string
  runtime?: { language: string; python?: string | null; dependencies: string[] }
  input_schema?: {
    type: 'object'
    properties?: Record<string, { type?: string; description?: string; default?: unknown }>
    required?: string[]
  }
  output_schema?: { type: 'object'; properties?: Record<string, unknown> }
  validation?: {
    protocol_ok: boolean
    syntax_ok: boolean
    direct_param_access: string[]
    warnings: string[]
    errors: string[]
  }
  provenance?: {
    run_count: number
    last_run_at?: string | null
    promoted_from?: string | null
    created_by?: string | null
  }
  created_at?: string
  updated_at?: string
}

export interface PlanStep {
  step_id: string
  label: string
  tool: string | null
  detail: string | null
  status: string
}

export interface DataCheckResult {
  state: string
  ready: string[]
  missing: string[]
  details: { item: string; ready: boolean; source: string | null; suggestions: string[] }[]
}

export interface PlanResult {
  intent_id: string
  summary: string
  /** Planner 思考过程（skill/tool 选择依据） */
  thinking: string
  operation: string
  targets: string[]
  distance: number
  target_distances: Record<string, number>
  required_data: string[]
  plan: PlanStep[]
  data_check: DataCheckResult | null
  /** 门1 意图澄清：非空时前端渲染追问气泡，不执行 */
  clarify?: { needs?: string | null; question: string; choices: string[] } | null
  /** 门2 数据缺失：解析不到数据的分析对象原文，非空时前端渲染缺失面板，不执行 */
  unmet_targets: string[]
}

export type TraceEventType = 'text' | 'ran_command' | 'created' | 'ran' | 'generated'

export interface TraceEvent {
  type: TraceEventType
  content: string
  meta?: string | null
  step_id?: string | null
}

export interface ResultWidgetBackend {
  id: string
  title: string
  widget_type: 'metric' | 'chart' | 'list' | 'table' | 'map' | 'image'
  value?: string | null
  subtitle?: string | null
  unit?: string | null
  data: Record<string, unknown>
  evidence: string[]
  layer_id?: string | null
}

export interface ResultGroupBackend {
  id: string
  title: string
  widgets: ResultWidgetBackend[]
  created_at?: string | null
  // 统一结果格式（§4.4）：结构化指标/发现/警告/参数/规则/来源/追问/停止原因（缺省为空）
  metrics?: { name: string; value: number | string; unit?: string | null; params?: Record<string, unknown>; spatial_unit?: string | null; source?: string | null }[]
  findings?: string[]
  warnings?: string[]
  params?: Record<string, unknown>
  rules?: string[]
  source?: string | null
  next_questions?: string[]
  stop_reason?: string | null
}

export interface ExecutionResult {
  intent_id: string
  trace: TraceEvent[]
  result_group: ResultGroupBackend | null
  evidence: string[]
  /** 反思：执行结果复核说明 */
  reflection: string
  /** 是否走了规则兜底 */
  used_fallback: boolean
  /** 最终回复：给用户的结论文本（不含思考过程叙述） */
  final_answer?: string | null
}

/* ---------------- 请求函数 ---------------- */

export const fetchLayers = () => request<BackendLayer[]>('/layers')
export const fetchSkills = () => request<SkillSpec[]>('/skills')
export const fetchTools = () => request<ToolSpec[]>('/tools')
export const fetchLatestResults = () => request<ResultGroupBackend | null>('/agent/results')

/** 彻底删除图层（后端同步清磁盘物化文件 + 项目 manifest，跨重启不复活）。
 *  平台数据只读图层后端拒绝（400）；缺失返回 404。 */
export const deleteLayer = (layerId: string) =>
  request<{ deleted: string }>(`/layers/${encodeURIComponent(layerId)}`, { method: 'DELETE' })

/** 全部图层真实几何（{layer_id: FeatureCollection}），后端真计算数据源 */
export const fetchAllGeojson = () => request<Record<string, GeoJSON.FeatureCollection>>('/layers/geojson')

/** 单个图层真实几何（平台数据 lazy 图层按需拉取，避免首屏拉几百 MB） */
export const fetchLayerGeojson = (layerId: string) =>
  request<GeoJSON.FeatureCollection>(`/layers/${encodeURIComponent(layerId)}/geojson`)

/* ---------------- Run Archive（Sprint 2 S2-4） ---------------- */

export interface RunIndex {
  run_id: string
  prompt: string
  status: string
  started_at: string
  duration_ms?: number
}

/** 项目（每间独立厨房）：列表项 / 详情 / 运行历史条目 */
export interface ProjectInfo {
  id: string
  name: string
  description?: string
  created_at?: string
  layer_count?: number
  run_count?: number
  /** 置顶时间（ISO）；有值 = 已置顶 */
  pinned_at?: string | null
}

export interface ProjectFile {
  name: string
  type: string
  size: number
  on_map: boolean
  /** 文件对应的图层 id（= clean 后的文件名 stem），用于删除图层 */
  layer_id?: string
  /** 文件中真实要素数（后端从 GeoJSON 实读，非编造） */
  feature_count?: number
}

export interface ProjectRun {
  run_id: string
  question: string
  status: string
  created_at: string
  tool_count: number
}

export interface ProjectDetail extends ProjectInfo {
  files: ProjectFile[]
  runs: ProjectRun[]
}

export interface RunArchiveDetail {
  meta: Record<string, unknown>
  events: Array<Record<string, unknown>>
  tool_calls: Array<Record<string, unknown>>
  artifacts: Array<Record<string, unknown>>
  llm_usage: Array<Record<string, unknown>>
  steps: Array<Record<string, unknown>>
}

export const fetchRuns = () => request<RunIndex[]>('/runs?limit=30')

/* ---------------- 项目（每间独立厨房） ---------------- */

export const fetchProjects = () => request<ProjectInfo[]>('/projects')
export const fetchActiveProject = () => request<ProjectInfo & { current: boolean }>('/projects/active')
export const fetchProjectDetail = (projectId: string) =>
  request<ProjectDetail>(`/projects/${encodeURIComponent(projectId)}`)
export const createProject = (name: string, description?: string) =>
  request<ProjectInfo>('/projects', {
    method: 'POST',
    body: JSON.stringify({ name, description: description ?? '' }),
  })
export const deleteProject = (projectId: string) =>
  request<{ deleted: string }>(`/projects/${encodeURIComponent(projectId)}`, { method: 'DELETE' })
/** 改名 / 改描述 / 置顶：只更新传了的字段 */
export const renameProject = (
  projectId: string,
  body: { name?: string; description?: string; pinned?: boolean },
) =>
  request<ProjectInfo>(`/projects/${encodeURIComponent(projectId)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
/** 在资源管理器打开项目目录（仅本机 win32 生效，返回 {path, opened}） */
export const openProjectFolder = (projectId: string) =>
  request<{ path: string; opened: boolean }>(`/projects/${encodeURIComponent(projectId)}/open-folder`, {
    method: 'POST',
  })
export const switchProject = (projectId: string) =>
  request<{ project_id: string; layers: string[] }>(
    `/projects/${encodeURIComponent(projectId)}/active`,
    { method: 'PUT' },
  )
export const fetchRun = (runId: string) => request<RunArchiveDetail>(`/runs/${runId}`)

/** 上传 CSV/GeoJSON → 注册为地图图层（multipart）。返回后端图层元数据。 */
export async function uploadLayer(
  file: File,
  layerName?: string,
): Promise<{ layer_id: string; name: string; feature_count: number; geometry_types: string[] }> {
  const form = new FormData()
  form.append('file', file)
  if (layerName) form.append('layer_name', layerName)
  const response = await fetch(`${API_BASE}/layers/upload`, { method: 'POST', body: form })
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail ? `上传失败：${detail}` : `API ${response.status} /layers/upload`)
  }
  return (await response.json()) as {
    layer_id: string
    name: string
    feature_count: number
    geometry_types: string[]
  }
}

/** 暂存上传文件的返回结构（不解析/不注册图层，仅落盘到工作区 data/uploads/） */
export interface StagedUpload {
  staged_id: string
  original_name: string
  /** workspace 相对路径（如 data/uploads/oct_poi.csv），Agent 消息引用此路径 */
  path: string
  size: number
}

/** 手绘点/线/面 → 注册为图层（POST /layers/draw）。返回后端图层元数据。 */
export async function drawLayer(body: {
  layer_id?: string
  name: string
  crs?: string
  color?: string
  geojson: GeoJSON.FeatureCollection
  fields?: Array<{ name: string; type: string; description?: string | null }>
  units?: { length?: 'm' | 'km'; area?: 'm2' | 'hm2' | 'km2' } | null
}): Promise<{ layer_id: string; name: string; feature_count: number; geometry_types: string[]; bbox: number[] | null }> {
  const response = await fetch(`${API_BASE}/layers/draw`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail ? `创建失败：${detail}` : `API ${response.status} /layers/draw`)
  }
  return (await response.json()) as {
    layer_id: string
    name: string
    feature_count: number
    geometry_types: string[]
    bbox: number[] | null
  }
}

/** 保存编辑后的手绘图层几何（PUT /layers/{id}/geojson）。返回后端图层元数据。 */
export async function updateLayerGeojson(
  layerId: string,
  body: {
    geojson: GeoJSON.FeatureCollection
    fields?: Array<{ name: string; type: string; description?: string | null }>
    units?: { length?: 'm' | 'km'; area?: 'm2' | 'hm2' | 'km2' }
  },
): Promise<BackendLayer> {
  const response = await fetch(`${API_BASE}/layers/${layerId}/geojson`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail ? `保存失败：${detail}` : `API ${response.status} /layers/${layerId}/geojson`)
  }
  return (await response.json()) as BackendLayer
}

/** 暂存上传文件：只挂附件 chip，不上图层/不上地图。Agent 需要用时经 register_uploaded_file 创建图层。 */
export async function stageUpload(file: File): Promise<StagedUpload> {
  const form = new FormData()
  form.append('file', file)
  const response = await fetch(`${API_BASE}/uploads/stage`, { method: 'POST', body: form })
  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(detail ? `上传失败：${detail}` : `API ${response.status} /uploads/stage`)
  }
  return (await response.json()) as StagedUpload
}

/** Planner：把问题 + 多轮对话历史转成计划（history 作上下文记忆）；signal 支持「停止」即时中断 */
export const planQuestion = (
  question: string,
  history: ChatTurn[] = [],
  conversationId?: string,
  signal?: AbortSignal,
) =>
  request<PlanResult>('/agent/plan', {
    method: 'POST',
    body: JSON.stringify({ question, history, conversation_id: conversationId }),
    signal,
  })

/* ---------------- SSE 真流式 ---------------- */

/** 后端 Agent 事件类型（镜像 EventType 枚举） */
export type AgentEventType =
  | 'run_start'
  | 'thought_delta'
  | 'data_check'
  | 'plan'
  | 'tool_start'
  | 'tool_result'
  | 'code_block_start'
  | 'code_delta'
  | 'code_result'
  | 'generated_layer'
  | 'layer_style'
  | 'layer_visibility'
  | 'zoom_to_layer'
  | 'layer_removed'
  | 'layer_reorder'
  | 'basemap_changed'
  | 'artifact'
  | 'permission_request'
  | 'permission_result'
  | 'run_archive'
  | 'error'
  | 'execution'
  | 'stream_end'

/** 前端事件信封（镜像 AgentEventEnvelope）；payload 在 data 字段 */
export interface AgentEvent {
  id: string
  type: AgentEventType
  data: Record<string, unknown>
  run_id?: string | null
  ts: string
}

export type StreamEventHandler = (event: AgentEvent) => void

/** Executor（SSE 流式）：POST /agent/execute，逐帧解析 event:/data: 并回调 onEvent */
export async function executePlanStream(
  plan: PlanResult,
  question: string,
  history: ChatTurn[] = [],
  onEvent: StreamEventHandler,
  signal?: AbortSignal,
  conversationId?: string,
  acceptMissing?: string[],
): Promise<void> {
  const {
    intent_id,
    summary,
    operation,
    targets,
    distance,
    target_distances,
    required_data,
    plan: steps,
    data_check,
    unmet_targets,
  } = plan
  const response = await fetch(`${API_BASE}/agent/execute`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      intent_id,
      summary,
      operation,
      targets,
      distance,
      target_distances,
      required_data,
      plan: steps,
      data_check,
      unmet_targets,
      accept_missing: acceptMissing ?? [],
      question,
      history,
      conversation_id: conversationId,
    }),
    signal,
  })
  if (!response.ok) throw new Error(`API ${response.status} /agent/execute`)
  if (!response.body) throw new Error('SSE 响应无 body')

  const reader = response.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''
  let eventType = ''
  let dataLines: string[] = []

  const dispatch = () => {
    if (!eventType) return
    const dataStr = dataLines.join('\n')
    let envelope: Record<string, unknown> = {}
    if (dataStr) {
      try {
        envelope = JSON.parse(dataStr)
      } catch {
        return // 不完整帧丢弃，等下一段补齐
      }
    }
    onEvent({
      id: String(envelope.id ?? ''),
      type: eventType as AgentEventType,
      data: (envelope.data as Record<string, unknown>) ?? {},
      run_id: envelope.run_id as string | null,
      ts: String(envelope.ts ?? ''),
    })
    eventType = ''
    dataLines = []
  }

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx: number
    while ((idx = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, idx).replace(/\r$/, '')
      buffer = buffer.slice(idx + 1)
      if (line === '') {
        dispatch()
      } else if (line.startsWith('event: ')) {
        eventType = line.slice(7)
      } else if (line.startsWith('data: ')) {
        dataLines.push(line.slice(6))
      }
    }
  }
  dispatch() // 冲刷尾部帧（连接关闭前未以空行结尾的最后一帧）
}

/** Executor：按计划执行（把多轮对话历史一并作为上下文） */
export const executePlan = (
  plan: PlanResult,
  question: string,
  history: ChatTurn[] = [],
  acceptMissing?: string[],
) => {
  const {
    intent_id,
    summary,
    operation,
    targets,
    distance,
    target_distances,
    required_data,
    plan: steps,
    data_check,
    unmet_targets,
  } = plan
  return request<ExecutionResult>('/agent/execute', {
    method: 'POST',
    body: JSON.stringify({
      intent_id,
      summary,
      operation,
      targets,
      distance,
      target_distances,
      required_data,
      plan: steps,
      data_check,
      unmet_targets,
      accept_missing: acceptMissing ?? [],
      question,
      history,
    }),
  })
}

/* ---------------- 后端 → 前端映射 ---------------- */

export function toLayer(layer: BackendLayer): LayerMetadata {
  return {
    id: layer.id,
    name: layer.name,
    source: layer.source,
    geometry: layer.geometry,
    color: layer.color,
    visible: layer.visible ?? true,
    featureCount: layer.feature_count,
    crs: layer.crs,
    description: layer.description,
    generatedBy: layer.generated_by,
    fields: layer.fields,
    readonly: layer.readonly ?? false,
    dataPath: layer.data_path ?? null,
    relPath: layer.rel_path ?? null,
    dataType: layer.data_type ?? null,
    lazy: layer.lazy ?? false,
    kind: layer.kind ?? 'vector',
    tileUrl: layer.tile_url,
    bbox: layer.bbox ?? null,
    dem: layer.dem ?? false,
    terrainUrl: layer.terrain_url,
    opacity: layer.opacity ?? null,
    fillOpacity: layer.fillOpacity ?? null,
    strokeOpacity: layer.strokeOpacity ?? null,
    strokeColor: layer.strokeColor ?? null,
    strokeWidth: layer.strokeWidth ?? null,
    radius: layer.radius ?? null,
    fillColor: layer.fillColor ?? null,
    styleMode: layer.styleMode ?? null,
    visualVariables: (layer.visualVariables as LayerStyle['visualVariables']) ?? null,
    labelField: layer.labelField ?? null,
    units: layer.units ?? null,
    weightField: layer.weightField ?? null,
  }
}

/** 后端 TraceEvent → 前端过程块（ran_command 映射到 ran-command） */
export function toAgentBlock(event: TraceEvent): AgentBlock {
  if (event.type === 'text') return { type: 'text', content: event.content }
  const kind = event.type === 'ran_command' ? 'ran-command' : event.type
  return { type: 'event', kind, content: event.content, meta: event.meta ?? undefined }
}

/** 执行 trace → 前端 Tool Trace 记录 */
export function toToolTrace(trace: TraceEvent[]): ToolCallRecord[] {
  return trace
    .filter((event) => event.type === 'ran' || event.type === 'ran_command' || event.type === 'generated')
    .map((event) => ({ name: event.content, detail: event.meta ?? '' }))
}

/** 后端就绪检查 → 前端数据检查（可折叠区块） */
export function toDataCheck(check: DataCheckResult | null): ChatDataCheck {
  return { ready: check?.ready ?? [], missing: check?.missing ?? [] }
}

/** 后端成果组 → 前端成果组 */
export function toResultGroup(group: ResultGroupBackend | null): ResultGroup | null {
  if (!group) return null
  return {
    id: group.id,
    title: group.title,
    createdAt: group.created_at ?? undefined,
    // 统一结果格式映射（snake→camel；缺省即空，向后兼容）
    metrics: (group.metrics ?? []).map((m) => ({
      name: m.name,
      value: m.value,
      unit: m.unit ?? undefined,
      params: m.params ?? undefined,
      spatialUnit: m.spatial_unit ?? undefined,
      source: m.source ?? undefined,
    })),
    findings: group.findings ?? [],
    warnings: group.warnings ?? [],
    params: group.params ?? {},
    rules: group.rules ?? [],
    source: group.source ?? undefined,
    nextQuestions: group.next_questions ?? [],
    stopReason: group.stop_reason ?? undefined,
    widgets: group.widgets.map((widget) => {
      const data = widget.data as {
        series?: { name: string; value: number }[]
        rows?: Record<string, string | number>[]
        layer_ids?: string[]
        url?: string
        option?: Record<string, unknown>
        height?: number
      }
      return {
        id: widget.id,
        title: widget.title,
        widgetType: widget.widget_type,
        value: widget.value ?? undefined,
        subtitle: widget.subtitle ?? undefined,
        unit: widget.unit ?? undefined,
        evidence: widget.evidence,
        layerId: widget.layer_id ?? undefined,
        data: {
          ...(Array.isArray(data.series) ? { series: data.series } : {}),
          ...(Array.isArray(data.rows) ? { rows: data.rows } : {}),
          ...(Array.isArray(data.layer_ids) ? { layerIds: data.layer_ids } : {}),
          ...(typeof data.url === 'string' ? { url: data.url } : {}),
          ...(data.option && typeof data.option === 'object' ? { option: data.option } : {}),
          ...(typeof data.height === 'number' ? { height: data.height } : {}),
        },
      }
    }),
  }
}

/** 后端 Skill → 前端 PlanningSkill（分析页详情弹窗）。
 *  只对 SKILL.md 驱动 skill 调用（可执行 skill 无 indicators，消费方已按 kind 过滤）。 */
export function toPlanningSkill(skill: SkillSpec): PlanningSkill {
  const indicators = skill.indicators ?? []
  const requiredData = skill.required_data ?? []
  const outputs = skill.outputs ?? []
  const toolCalls = new Set<string>()
  for (const indicator of indicators) {
    for (const step of indicator.steps) {
      const name = step.split('(')[0].trim()
      if (step.includes('(') && /^[a-z_]+$/i.test(name)) toolCalls.add(name)
    }
  }
  return {
    id: skill.id,
    name: skill.name,
    description:
      outputs.length > 0
        ? `产出：${outputs.join('、')}`
        : `包含 ${indicators.length} 项指标`,
    prompt: skill.core_question ?? '',
    requiredData: requiredData.join(' · '),
    detail: {
      dataRequirements: requiredData,
      workflow: ['数据准备', '指标计算 / 统计', '空间诊断 / 审查', '报告片段生成'],
      indicators: indicators.map((indicator) => `${indicator.name}（${indicator.mvp_depth}）`),
      tools: [...toolCalls],
    },
  }
}

/* ---------------- Skills（Anthropic Agent Skill：可执行技能 + 脚本货架） ---------------- */

export interface SkillRunResult {
  success: boolean
  skill_id: string
  run_id: string
  returncode?: number | null
  stdout_tail?: string
  stderr_tail?: string
  output?: {
    success: boolean
    summary?: string
    layer_id?: string
    stats?: Record<string, unknown>
    error?: string
  }
  layers: Array<{ layer_id: string; name: string; feature_count: number }>
  plot_files: string[]
}

export interface ScriptInfo {
  name: string
  path: string
  size: number
  run_id?: string
  step?: number
  semantic_name?: string
  description?: string
  has_error?: boolean
  archived_at?: string
}

export const fetchSkill = (id: string) =>
  request<SkillSpec>(`/skills/${encodeURIComponent(id)}`)
export const executeSkillScript = (id: string, params: Record<string, unknown>) =>
  request<SkillRunResult>(`/skills/${encodeURIComponent(id)}/run`, {
    method: 'POST',
    body: JSON.stringify({ params }),
  })
export const createSkill = (body: {
  skill_id: string
  name?: string
  description?: string
  code?: string
  requirements?: string[]
}) => request<SkillSpec>('/skills', { method: 'POST', body: JSON.stringify(body) })
export const editSkill = (
  id: string,
  patch: Record<string, unknown>,
) => request<SkillSpec>(`/skills/${encodeURIComponent(id)}`, {
  method: 'PATCH',
  body: JSON.stringify(patch),
})
export const copySkill = (id: string) =>
  request<SkillSpec>(`/skills/${encodeURIComponent(id)}/copy`, { method: 'POST' })
export const validateSkill = (id: string) =>
  request<SkillSpec['validation']>(`/skills/${encodeURIComponent(id)}/validate`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
export const promoteScriptToSkill = (body: { script_name: string; name?: string; description?: string }) =>
  request<SkillSpec>('/skills/promote-script', {
    method: 'POST',
    body: JSON.stringify(body),
  })
export const fetchScripts = () => request<{ scripts: ScriptInfo[]; count: number }>('/scripts')
export const fetchScript = (name: string) =>
  request<{ name: string; path: string; content: string }>(`/scripts/${encodeURIComponent(name)}`)

/* ---------------- eval 评测管理（后台 /eval/*） ---------------- */

export const fetchEvalSets = () => request<EvalSetSummary[]>('/eval/sets')
export const fetchEvalSet = (name: string) =>
  request<EvalSetDetail>(`/eval/sets/${encodeURIComponent(name)}`)
export const fetchEvalReports = () => request<EvalReportSummary[]>('/eval/reports')
export const fetchEvalReport = (id: string) =>
  request<EvalReportDetail>(`/eval/reports/${encodeURIComponent(id)}`)
export const triggerEvalRun = (body: { set: string; tier: string; exclude_dep?: boolean }) =>
  request<{ run_id: string }>('/eval/run', { method: 'POST', body: JSON.stringify(body) })
export const fetchEvalRun = (runId: string) =>
  request<EvalRunStatus>(`/eval/runs/${encodeURIComponent(runId)}`)

/* ---------------- 本地数据目录接入（第一步「只读发现」） ---------------- */

export type DatasetStatus = 'discovered' | 'enableable' | 'query_only' | 'unavailable'

export interface FolderField {
  name: string
  type: string
}

export interface FolderDataset {
  dataset_id: string
  name: string
  rel_path: string
  format: string
  size: number
  fields: FolderField[]
  crs: string | null
  coord_columns: string[] | null
  geometry: string | null
  feature_count: number | null
  status: DatasetStatus
  reason: string | null
  warnings: string[]
  enabled: boolean
  layer_id: string | null
  /** 栅格影像元数据（tif 探测结果） */
  raster?: {
    width: number
    height: number
    bands: number
    dtype?: string | null
    crs?: string | null
    res_x?: number | null
    res_y?: number | null
    bounds?: number[] | null
    nodata?: number | null
    color_interpretation?: string[] | null
    preview_image?: string | null
    preview_note?: string | null
  } | null
}

export interface FolderSource {
  source_id: string
  alias: string
  root_path: string
  created_at: string
  last_scanned_at: string
  datasets: FolderDataset[]
}

export const fetchDataSources = () =>
  request<{ sources: FolderSource[]; allowlist: string[] }>('/api/data-sources')

export const rescanDataFolder = (sourceId: string) =>
  request<FolderSource>(`/api/data-sources/${encodeURIComponent(sourceId)}/rescan`, { method: 'POST' })

export const unlinkDataFolder = (sourceId: string) =>
  request<{ ok: boolean }>(`/api/data-sources/${encodeURIComponent(sourceId)}`, { method: 'DELETE' })

export const addAllowlistRoot = (rootPath: string) =>
  request<{ roots: string[] }>('/api/data-sources/allowlist', {
    method: 'POST',
    body: JSON.stringify({ root_path: rootPath }),
  })

/** 弹出系统原生文件夹选择器，返回用户选中的目录路径（取消返回 null）。 */
export const browseFolder = () =>
  request<{ path: string | null; cancelled: boolean }>('/api/data-sources/browse-folder', {
    method: 'POST',
  })

/** 把「可直接启用」的数据集启用为项目图层（第二步「小数据启用」）。 */
export const enableDataset = (sourceId: string, datasetId: string) =>
  request<{ layer_id: string; name: string; feature_count: number; geometry_types: string[]; crs: string }>(
    `/api/data-sources/${encodeURIComponent(sourceId)}/datasets/${encodeURIComponent(datasetId)}/enable`,
    { method: 'POST' },
  )

/** 停用一个已启用数据集（移除其图层，幂等）。 */
export const disableDataset = (sourceId: string, datasetId: string) =>
  request<{ ok: boolean; already_disabled: boolean; message: string }>(
    `/api/data-sources/${encodeURIComponent(sourceId)}/datasets/${encodeURIComponent(datasetId)}/disable`,
    { method: 'POST' },
  )

/** 文件夹级全部启用（硬上限 20 + 512MB 软预算）。folderPath 非空时仅启用该子目录前缀下数据集。
 *  dryRun=true 只返回后端算好的计划（planned_count/estimated_features/capped），不真正启用，
 *  供确认框展示与后端一致的数字（不得臆断一定计划 20 个）。 */
export const enableAllDatasets = (sourceId: string, folderPath?: string, dryRun = false) => {
  const params = new URLSearchParams()
  if (folderPath) params.set('folder_path', folderPath)
  if (dryRun) params.set('dry_run', '1')
  const qs = params.toString()
  return request<{
    enabled: number
    count: number
    failed: Array<{ dataset_id: string; error: string }>
    total_candidates: number
    planned_count: number
    omitted_count: number
    estimated_features: number
    estimated_bytes: number
    capped: boolean
    capped_by_limit: boolean
    capped_by_bytes: boolean
    dry_run?: boolean
  }>(
    `/api/data-sources/${encodeURIComponent(sourceId)}/enable-all${qs ? `?${qs}` : ''}`,
    { method: 'POST' },
  )
}

/** 文件夹级全部停用。folderPath 非空时仅停用该子目录前缀下已启用数据集。 */
export const disableAllDatasets = (sourceId: string, folderPath?: string) => {
  const params = new URLSearchParams()
  if (folderPath) params.set('folder_path', folderPath)
  const qs = params.toString()
  return request<{ disabled: number; count: number }>(
    `/api/data-sources/${encodeURIComponent(sourceId)}/disable-all${qs ? `?${qs}` : ''}`,
    { method: 'POST' },
  )
}

/** 获取当前下载目录（联网取数默认落盘位置）。 */
export const fetchDownloadFolder = () =>
  request<{ path: string | null }>('/api/data-sources/download-folder')

/** 设置下载目录：联网取数（OSM/高德/datasource）默认落盘到该本地文件夹。 */
export const setDownloadFolder = (rootPath: string) =>
  request<{ path: string; source_id: string }>('/api/data-sources/download-folder', {
    method: 'POST',
    body: JSON.stringify({ root_path: rootPath }),
  })

/** 链接本地数据目录：失败时抛出带后端中文 detail 的错误（含 403 白名单拒绝）。 */
export async function linkDataFolder(rootPath: string, alias?: string): Promise<FolderSource> {
  const response = await fetch(`${API_BASE}/api/data-sources/link`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ root_path: rootPath, alias }),
  })
  if (!response.ok) {
    let detail = ''
    try {
      const body = await response.json()
      detail = (body as { detail?: string }).detail ?? ''
    } catch {
      /* 忽略非 JSON 响应体 */
    }
    const msg = detail || `API ${response.status} /api/data-sources/link`
    const err = new Error(msg) as Error & { status?: number }
    err.status = response.status
    throw err
  }
  return (await response.json()) as FolderSource
}
