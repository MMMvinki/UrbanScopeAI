import { create } from 'zustand'
import type { AgentBlock, AgentMessage, PendingApproval, StepStatus } from '../features/agent/agentTypes'
import type { FieldSpec, GeometryType, LayerGroup, LayerMetadata, LayerStyle } from '../features/layers/layerTypes'
import type { AutoUnitConfig } from '../features/map/geodesic'
import { injectAutoFields } from '../features/map/geodesic'
import type { PlanningSkill } from '../features/skills/planningSkills'
import type { ResultGroup } from '../features/results/resultTypes'
import { defaultSkills } from '../features/skills/planningSkills'
import {
  API_BASE,
  createProject as apiCreateProject,
  deleteLayer as apiDeleteLayer,
  deleteProject as apiDeleteProject,
  drawLayer,
  executePlanStream,
  fetchActiveProject,
  fetchAllGeojson,
  fetchLayers,
  fetchProjectDetail,
  fetchProjects,
  fetchSkills,
  fetchTools,
  planQuestion,
  patchLayerStyle,
  switchProject as apiSwitchProject,
  toDataCheck,
  updateLayerGeojson,
  toLayer,
  toPlanningSkill,
  toResultGroup,
  toToolTrace,
} from '../lib/api'
import type { AgentEvent, PlanResult, ProjectDetail, ProjectInfo, SkillSpec, ToolSpec } from '../lib/api'
import {
  DEFAULT_BASEMAP_ID,
  ensureLayerGeojson,
  getLayerGeojson,
  hasLayerGeojson,
  removeGeojsonEntry,
  setGeojsonCache,
  setGeojsonEntry,
} from '../features/map/mapLayers'
import type { ChatTurn } from '../features/agent/agentTypes'
import { nextBlockId } from '../features/agent/agentTypes'

/** 会话（Codex 式）基础设施：localStorage 多会话 + 每会话独立消息。
 *
 *  会话 id 与消息均按会话持久化 → 后端 conversation memory 按 conversation_id
 *  分区（.workdir/memory/<id>.json），跨刷新稳定、多会话天然隔离。首访或旧版
 *  （单一会话）数据会自动迁移成第一条会话。 */
export interface ConversationMeta {
  id: string
  projectId: string
  title: string
  createdAt: string
  updatedAt: string
  pinned: boolean
}

const CONVERSATIONS_STORAGE_KEY = 'urbanscope.conversations'
const ACTIVE_CONVERSATION_KEY = 'urbanscope.activeConversationId'
// 兼容迁移：旧版单一会话 key
const LEGACY_CONVERSATION_KEY = 'urbanscope.conversationId'
const LEGACY_MESSAGES_KEY = 'urbanscope.messages'
const MESSAGES_STORAGE_KEY = 'urbanscope.messages'
const MAX_STORED_MESSAGES = 60
const STORED_CODE_MAX_CHARS = 2000
const STORED_RESULT_MAX_CHARS = 500

function conversationMessagesKey(convId: string): string {
  return `${MESSAGES_STORAGE_KEY}.${convId}`
}

function newConversationId(): string {
  return (
    (globalThis.crypto && 'randomUUID' in globalThis.crypto && globalThis.crypto.randomUUID()) ||
    `conv-${Date.now().toString(36)}`
  )
}

function nowIso(): string {
  return new Date().toISOString()
}

/** 会话默认标题：首条用户消息前 20 字；空会话 → 「新会话」 */
function defaultConversationTitle(messages: AgentMessage[]): string {
  const firstUser = messages.find((m) => m.role === 'user' && m.content)
  if (!firstUser?.content) return '新会话'
  const text = firstUser.content.replace(/\s+/g, ' ').trim()
  return text.length > 20 ? `${text.slice(0, 20)}…` : text
}

/** 写盘前的消息瘦身：截断大代码/结果块、只留最近 N 条，控制 localStorage 体积 */
function sanitizeForStorage(messages: AgentMessage[]): AgentMessage[] {
  return messages.slice(-MAX_STORED_MESSAGES).map((message) => {
    if (!message.blocks?.length) return message
    const blocks = message.blocks.map((block) => {
      if (block.type === 'code') {
        const code =
          block.code.length > STORED_CODE_MAX_CHARS ? block.code.slice(0, STORED_CODE_MAX_CHARS) : block.code
        const result =
          block.result && block.result.length > STORED_RESULT_MAX_CHARS
            ? block.result.slice(0, STORED_RESULT_MAX_CHARS)
            : block.result
        return { ...block, code, result }
      }
      return block
    })
    return { ...message, blocks }
  })
}

/** 从 localStorage 恢复某会话的消息；流式/加载中消息恢复为完成态（避免永久「思考中」） */
function loadMessagesFromStorage(key: string): AgentMessage[] {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return []
    const parsed = JSON.parse(raw) as AgentMessage[]
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter((m) => m && typeof m === 'object' && (m.role === 'user' || m.role === 'agent'))
      .map((m) =>
        m.status === 'streaming' || m.status === 'loading' ? { ...m, status: 'done' as const } : m,
      )
  } catch {
    return []
  }
}

function persistConversationMessages(convId: string, messages: AgentMessage[]): void {
  try {
    localStorage.setItem(conversationMessagesKey(convId), JSON.stringify(sanitizeForStorage(messages)))
  } catch {
    /* 存储满 / 不可用：忽略，会话仍可用 */
  }
}

/** 初始化会话状态：已有会话列表 → 恢复；否则迁移旧版单一会话 → 第一条会话 */
function loadConversationState(): {
  conversations: ConversationMeta[]
  activeConversationId: string
  messages: AgentMessage[]
} {
  try {
    const raw = localStorage.getItem(CONVERSATIONS_STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as ConversationMeta[]
      if (Array.isArray(parsed) && parsed.length > 0) {
        const activeId = localStorage.getItem(ACTIVE_CONVERSATION_KEY) || parsed[parsed.length - 1].id
        const target = parsed.find((c) => c.id === activeId) ?? parsed[parsed.length - 1]
        return {
          conversations: parsed,
          activeConversationId: target.id,
          messages: loadMessagesFromStorage(conversationMessagesKey(target.id)),
        }
      }
    }
  } catch {
    /* 解析失败 → 走迁移/空态 */
  }
  // 旧版单一会话迁移：urbanscope.conversationId + urbanscope.messages
  try {
    const legacyId = localStorage.getItem(LEGACY_CONVERSATION_KEY)
    const legacyMessages = loadMessagesFromStorage(LEGACY_MESSAGES_KEY)
    if (legacyId || legacyMessages.length > 0) {
      const convId = legacyId || newConversationId()
      const conversation: ConversationMeta = {
        id: convId,
        projectId: 'default',
        title: defaultConversationTitle(legacyMessages),
        createdAt: nowIso(),
        updatedAt: nowIso(),
        pinned: false,
      }
      persistConversationMessages(convId, legacyMessages)
      try {
        localStorage.setItem(CONVERSATIONS_STORAGE_KEY, JSON.stringify([conversation]))
        localStorage.setItem(ACTIVE_CONVERSATION_KEY, convId)
      } catch {
        /* 忽略 */
      }
      return { conversations: [conversation], activeConversationId: convId, messages: legacyMessages }
    }
  } catch {
    /* 忽略 */
  }
  return { conversations: [], activeConversationId: newConversationId(), messages: [] }
}

const initialConversationState = loadConversationState()

/** 单个 code 块展示上限（字符）：超过后丢弃/截断增量，防止 LLM 或子进程把超大文本灌进前端内存 */
const CODE_BLOCK_MAX_CHARS = 200_000
/** 单个 text 块（正文流）上限（字符）：超过后丢弃增量，防止 LLM 无限叙述撑爆前端 */
const TEXT_BLOCK_MAX_CHARS = 200_000

/**
 * 后端图层元数据 → 可种子化的 LayerStyle（Sprint 2 持久化样式）。
 * 仅当图层确实带样式字段（fillColor/strokeColor/opacity/strokeWidth/radius/styleMode/visualVariables）时返回，
 * 否则返回 null（演示图层无样式字段时不改变默认渲染，避免 opacity 默认值回归）。
 */
function layerStyleFromMetadata(layer: LayerMetadata): Partial<LayerStyle> | null {
  const hasStyle =
    layer.fillColor != null ||
    layer.strokeColor != null ||
    layer.opacity != null ||
    layer.fillOpacity != null ||
    layer.strokeOpacity != null ||
    layer.strokeWidth != null ||
    layer.radius != null ||
    layer.styleMode != null ||
    layer.visualVariables != null
  if (!hasStyle) return null
  const style: Partial<LayerStyle> = {
    color: layer.color,
    fillColor: layer.fillColor ?? layer.color,
    strokeColor: layer.strokeColor ?? layer.color,
    opacity: layer.opacity ?? 0.6,
    fillOpacity: layer.fillOpacity ?? null,
    strokeOpacity: layer.strokeOpacity ?? null,
    // 分类配色默认无描边：categorized 图层当 layer.strokeWidth 未显式设置时给 0，
    // 否则 metadata 合并会硬塞 1，把 deck/maplibre 的分类默认描边覆盖成"橙边"（密集建筑轮廓噪音）。
    strokeWidth: layer.strokeWidth ?? (layer.styleMode === 'categorized' ? 0 : 1),
    radius: layer.radius ?? 5,
  }
  if (layer.styleMode === 'single' || layer.styleMode === 'graduated' || layer.styleMode === 'categorized') {
    style.styleMode = layer.styleMode
  }
  if (layer.visualVariables) style.visualVariables = layer.visualVariables
  return style
}

/**
 * 把后端图层的持久化样式合并进 layerStyles 种子。
 * 只填尚未有本地样式的图层：已存在的 session 级用户改动不被后端元数据覆盖。
 */
function mergeStylesFromLayers(
  existing: Record<string, LayerStyle>,
  layers: LayerMetadata[],
): Record<string, LayerStyle> {
  const merged = { ...existing }
  for (const layer of layers) {
    const meta = layerStyleFromMetadata(layer)
    if (meta && !merged[layer.id]) merged[layer.id] = { ...meta } as LayerStyle
  }
  return merged
}

/**
 * 增量刷新图层：更新图层清单后只对「新增图层」单取真实几何，不再全量 fetchAllGeojson。
 *
 * 启用/上传/删除等改动只影响少数图层；原先每次全量拉取所有图层几何（大图层 JSON
 * 单条就 20~50MB，几条叠加就是「启用慢」的根因）。改为只拉新增图层（单取端点
 * /layers/{id}/geojson 对目录数据图层同样返回：启用时几何已 set_geojson 进缓存）。
 * 已删除的图层（旧有、新无）顺带清缓存，避免地图残留。失败不阻塞（保持会话可用）。
 */
export async function refreshLayersIncremental(): Promise<void> {
  const prev = useAppStore.getState().layers
  const prevIds = new Set(prev.map((l) => l.id))
  let fresh: LayerMetadata[]
  try {
    fresh = (await fetchLayers()).map(toLayer)
  } catch {
    return
  }
  const freshIds = new Set(fresh.map((l) => l.id))
  const added = fresh.filter((l) => !prevIds.has(l.id))
  const removedIds = prev.filter((l) => !freshIds.has(l.id)).map((l) => l.id)
  removedIds.forEach((id) => removeGeojsonEntry(id))
  // 只预取「新增且可见、非 lazy、非 Table、非 raster」的图层几何：
  // 隐藏的抽稀预览（visible=False, lazy=True）与 Table 图层不得在此被误预取（默认不预览的真语义）。
  const fetchable = added.filter(
    (l) => l.visible && !l.lazy && l.geometry !== 'Table' && l.kind !== 'raster',
  )
  const results = await Promise.all(fetchable.map((l) => ensureLayerGeojson(l.id)))
  if (results.some(Boolean)) {
    // ensureLayerGeojson 成功后已写缓存；这里自增 geojsonTick 驱动 buildDeckLayers 重跑上屏
    const { geojsonTick } = useAppStore.getState()
    useAppStore.setState({ geojsonTick: geojsonTick + 1 })
  }
  useAppStore.setState((state) => ({
    layers: fresh,
    layerStyles: mergeStylesFromLayers(state.layerStyles, fresh),
  }))
}


/** 向后搜索最后一个指定类型的 block 下标（-1 表示不存在） */
function lastIndexByType(blocks: AgentBlock[], type: string): number {
  for (let i = blocks.length - 1; i >= 0; i--) {
    if (blocks[i].type === type) return i
  }
  return -1
}

/** lastIndexByType 只保证下标，TS 无法据此收窄联合类型；取 code 块时显式断言 */
function codeBlockAt(blocks: AgentBlock[], index: number): Extract<AgentBlock, { type: 'code' }> {
  return blocks[index] as Extract<AgentBlock, { type: 'code' }>
}

/** 替换指定下标的 block，返回新数组 */
function withReplaced<T>(blocks: T[], index: number, block: T): T[] {
  return [...blocks.slice(0, index), block, ...blocks.slice(index + 1)]
}

/**
 * 计划步骤按执行进度实时更新状态（计划卡逐项打勾，而非等 execution 一次性全 done）。
 * - start：工具开始 → 第一条 pending 步骤置 running
 * - result/code：工具完成 → 第一条 running 步骤置 done（失败置 warning）
 * - final：execution 终结载荷 → 兜底把仍未 done 的步骤全部置 done
 *
 * 严格按清单顺序自上而下推进，不拿工具名对号入座：计划步骤标的工具名
 * （save_plot/emit_chart 等）常与实际工具调用对不上（很多图是 execute_code
 * 内部的辅助函数画的，从不作为独立工具触发），按名匹配会跳步/乱序划勾。
 * 按顺序推进保证永远按 1→2→3 逐项点亮，不出现"第 2 步没划、第 3 步先划"。
 */
function planStepMarked(
  plan: AgentMessage['plan'],
  kind: 'start' | 'result' | 'code' | 'final',
  _tool?: string,
  ok?: boolean,
): AgentMessage['plan'] {
  if (!plan) return plan
  if (kind === 'final') {
    return plan.map((step) => (step.status === 'done' ? step : { ...step, status: 'done' as const }))
  }
  const index =
    kind === 'start'
      ? plan.findIndex((step) => step.status === 'pending')
      : plan.findIndex((step) => step.status === 'running')
  if (index < 0) return plan
  const status: StepStatus = kind === 'start' ? 'running' : ok === false ? 'warning' : 'done'
  const next = plan.slice()
  next[index] = { ...plan[index], status }
  return next
}

/** 从现有消息构建多轮对话历史（agent 结论优先取最终回复 finalAnswer，否则取最后一个 text 块 / evidence 摘要） */
function buildHistory(messages: AgentMessage[]): ChatTurn[] {
  const turns: ChatTurn[] = []
  for (const message of messages) {
    if (message.role === 'user' && message.content) {
      turns.push({ role: 'user', content: message.content })
    } else if (message.role === 'agent' && message.status === 'done') {
      const texts = message.blocks?.filter((block) => block.type === 'text')
      const conclusion =
        message.finalAnswer ?? (texts?.length ? texts[texts.length - 1].content : undefined)
      const summary =
        conclusion ??
        (message.evidence && message.evidence.length > 0
          ? `分析结果：${message.evidence.join('；')}`
          : undefined)
      if (summary) turns.push({ role: 'assistant', content: summary })
    }
  }
  return turns.slice(-8)
}

/** 工具参数 → 单行摘要（tool 块 detail） */
function formatArgs(args: Record<string, unknown>): string {
  try {
    const entries = Object.entries(args)
    if (entries.length === 0) return ''
    return entries
      .map(([key, value]) => {
        const s = typeof value === 'object' ? JSON.stringify(value) : String(value)
        return `${key}=${s}`
      })
      .join(', ')
  } catch {
    return ''
  }
}

/** 完成耗时：从 startedAt 打点到现在的毫秒数（未打点则 undefined，不展示） */
function elapsedFrom(msg: AgentMessage): number | undefined {
  return msg.startedAt != null ? Date.now() - msg.startedAt : undefined
}

/**
 * 把一条 SSE AgentEvent 增量折叠进目标消息；需要跨消息的副作用
 * （成果组入栏、权限弹窗）写入 side，由调用方一并 set。
 */
function reduceEvent(msg: AgentMessage, event: AgentEvent, side: Partial<AppState>): AgentMessage {
  const blocks = msg.blocks ?? []

  switch (event.type) {
    case 'run_start': {
      const runId = String(event.data.run_id ?? '')
      return runId ? { ...msg, runId } : msg
    }
    case 'thought_delta': {
      const text = String(event.data.text ?? '')
      if (!text) return msg
      const last = blocks[blocks.length - 1]
      if (last && last.type === 'text') {
        // 防御：正文流同样封顶，防止 LLM 无限叙述撑爆前端
        if ((last.content?.length ?? 0) > TEXT_BLOCK_MAX_CHARS) return msg
        return { ...msg, blocks: [...blocks.slice(0, -1), { ...last, content: last.content + text }] }
      }
      return { ...msg, blocks: [...blocks, { type: 'text', content: text, id: nextBlockId() }] }
    }
    case 'data_check': {
      const ready = Array.isArray(event.data.ready) ? event.data.ready.map(String) : []
      const missing = Array.isArray(event.data.missing) ? event.data.missing.map(String) : []
      return { ...msg, dataCheck: { ready, missing } }
    }
    case 'tool_start': {
      const name = String(event.data.tool ?? '')
      const args = (event.data.args as Record<string, unknown>) ?? {}
      // 计划步骤：工具开始 → 对应步骤置 running（计划卡逐项点亮）
      const plan = planStepMarked(msg.plan, 'start', name)
      // execute_code 由 code 块承载（code_delta/code_block_start/code_result），
      // 不再单独建 tool 行，避免与 code 行重复（对齐流式消息的 code part 语义）
      if (name === 'execute_code') return { ...msg, plan }
      return {
        ...msg,
        plan,
        blocks: [...blocks, { type: 'tool', name, status: 'running', detail: formatArgs(args), id: nextBlockId() }],
      }
    }
    case 'tool_result': {
      const name = String(event.data.tool ?? '')
      const ok = event.data.ok !== false
      const detail = typeof event.data.message === 'string' ? event.data.message : undefined
      // 计划步骤：工具完成 → 对应 running 步骤置 done / warning
      const plan = planStepMarked(msg.plan, 'result', name, ok)
      // 匹配最后一条 running 同名 tool 块（同工具多次调用时按顺序闭合）
      let replaced = false
      const next = blocks.map((block) => {
        if (!replaced && block.type === 'tool' && block.name === name && block.status === 'running') {
          replaced = true
          return { ...block, status: ok ? ('done' as const) : ('error' as const), detail }
        }
        return block
      })
      return replaced ? { ...msg, plan, blocks: next } : { ...msg, plan }
    }
    case 'generated_layer': {
      const name = String(event.data.name ?? '')
      const count = event.data.feature_count
      return {
        ...msg,
        blocks: [
          ...blocks,
          {
            type: 'event',
            kind: 'generated' as const,
            content: name,
            meta: count != null ? `+${String(count)} 要素` : undefined,
            id: nextBlockId(),
          },
        ],
      }
    }
    case 'code_delta': {
      const text = String(event.data.text ?? '')
      if (!text) return msg
      // code 相关事件统一定位「最后一条 code 块」，不依赖它恰好是最后一块
      const codeIdx = lastIndexByType(blocks, 'code')
      if (event.data.kind === 'code') {
        // LLM 流式写代码：追加到最后一个 code 块（或新建）。
        // 防御：LLM 可能把超大 GeoJSON 原样嵌入代码，累积超过阈值就丢弃增量，防 OOM
        if (codeIdx >= 0) {
          const last = codeBlockAt(blocks, codeIdx)
          if ((last.code?.length ?? 0) > CODE_BLOCK_MAX_CHARS) return msg
          return { ...msg, blocks: withReplaced(blocks, codeIdx, { ...last, code: (last.code ?? '') + text }) }
        }
        return { ...msg, blocks: [...blocks, { type: 'code', code: text, status: 'running', id: nextBlockId() }] }
      }
      // kind=stdout：子进程输出，并入最后一个 code 块的 result 区（同样封顶）
      if (codeIdx >= 0) {
        const last = codeBlockAt(blocks, codeIdx)
        if ((last.result?.length ?? 0) > CODE_BLOCK_MAX_CHARS) return msg
        return { ...msg, blocks: withReplaced(blocks, codeIdx, { ...last, result: (last.result ?? '') + text }) }
      }
      return msg
    }
    case 'code_block_start': {
      // 流式片段已展示过（LLM 打字效果），这里用完整 code 替换；超限则截断展示
      const rawCode = String(event.data.code ?? '')
      const code = rawCode.length > CODE_BLOCK_MAX_CHARS ? rawCode.slice(0, CODE_BLOCK_MAX_CHARS) : rawCode
      const codeIdx = lastIndexByType(blocks, 'code')
      if (codeIdx >= 0) {
        return { ...msg, blocks: withReplaced(blocks, codeIdx, { ...codeBlockAt(blocks, codeIdx), code }) }
      }
      return { ...msg, blocks: [...blocks, { type: 'code', code, status: 'running', id: nextBlockId() }] }
    }
    case 'code_result': {
      const codeIdx = lastIndexByType(blocks, 'code')
      const ok = event.data.ok !== false
      // 计划步骤：execute_code 完成 → 对应 running 步骤置 done
      const plan = planStepMarked(msg.plan, 'code', 'execute_code', ok)
      if (codeIdx >= 0) {
        const last = codeBlockAt(blocks, codeIdx)
        const logs = Array.isArray(event.data.logs) ? event.data.logs.join('\n') : ''
        const error = typeof event.data.error === 'string' && event.data.error ? event.data.error : undefined
        const result = last.result ?? (ok ? logs || undefined : error ?? (logs || undefined))
        return {
          ...msg,
          plan,
          blocks: withReplaced(blocks, codeIdx, { ...last, status: ok ? ('done' as const) : ('error' as const), result }),
        }
      }
      return plan ? { ...msg, plan } : msg
    }
    case 'artifact': {
      const path = typeof event.data.path === 'string' ? event.data.path : undefined
      const caption = typeof event.data.caption === 'string' ? event.data.caption : '生成产物'
      return {
        ...msg,
        blocks: [
          ...blocks,
          { type: 'event', kind: 'created' as const, content: caption, meta: path, id: nextBlockId() },
        ],
      }
    }
    case 'layer_style': {
      // 样式类请求：后端解析出样式改动（含 graduated 的 styleMode/visualVariables）
      // → 直接更新本地图层样式（地图即时重绘），并让 layerStyles 与图层元数据同步
      const layerId = String(event.data.layer_id ?? '')
      const style = (event.data.style as Partial<LayerStyle> | undefined) ?? {}
      if (!layerId || Object.keys(style).length === 0) return msg
      const current = useAppStore.getState().layerStyles
      side.layerStyles = { ...current, [layerId]: { ...(current[layerId] ?? {}), ...style } }
      return msg
    }
    case 'layer_visibility': {
      // 地图控制：显示/隐藏图层（show_only 时仅显示目标）
      const layerId = String(event.data.layer_id ?? '')
      if (!layerId) return msg
      const store = useAppStore.getState()
      const visible = Boolean(event.data.visible)
      side.layers =
        event.data.show_only === true
          ? store.layers.map((layer) => ({ ...layer, visible: layer.id === layerId }))
          : store.layers.map((layer) => (layer.id === layerId ? { ...layer, visible } : layer))
      return msg
    }
    case 'zoom_to_layer': {
      // 地图控制：缩放到图层（MapWorkspace 已消费 focusTarget → fitBounds）
      const layerId = String(event.data.layer_id ?? '')
      if (!layerId) return msg
      side.focusTarget = { layerId, ts: Date.now() }
      return msg
    }
    case 'layer_removed': {
      // 地图控制：移除图层（remove_layer 工具）→ 图层列表/样式/几何同步清理
      const layerId = String(event.data.layer_id ?? '')
      if (!layerId) return msg
      const store = useAppStore.getState()
      const { layerStyles } = store
      const nextStyles = { ...layerStyles }
      delete nextStyles[layerId]
      side.layers = store.layers.filter((layer) => layer.id !== layerId)
      side.groups = store.groups.map((group) => ({
        ...group,
        layerIds: group.layerIds.filter((id) => id !== layerId),
      }))
      side.layerStyles = nextStyles
      removeGeojsonEntry(layerId)
      return msg
    }
    case 'layer_reorder': {
      // 地图控制：调整图层顺序（set_layer_order 工具）→ 复用拖拽同一套可见序列逻辑
      const layerId = String(event.data.layer_id ?? '')
      const toIndex = Number(event.data.to_index ?? 0)
      if (!layerId || !Number.isFinite(toIndex)) return msg
      useAppStore.getState().moveLayer(layerId, toIndex)
      return msg
    }
    case 'basemap_changed': {
      // 地图控制：切换底图（set_basemap 工具）
      const basemapId = String(event.data.basemap_id ?? '')
      if (basemapId) useAppStore.getState().setBasemapId(basemapId)
      return msg
    }
    case 'permission_request': {
      side.pendingApproval = {
        requestId: String(event.data.request_id ?? ''),
        tool: String(event.data.tool ?? ''),
        arguments: (event.data.arguments as Record<string, unknown>) ?? {},
        reason: String(event.data.reason ?? ''),
      }
      return msg
    }
    case 'permission_result': {
      const current = useAppStore.getState().pendingApproval
      if (current?.requestId === String(event.data.request_id ?? '')) {
        side.pendingApproval = null
      }
      return msg
    }
    case 'execution': {
      const group = toResultGroup((event.data.result_group as Parameters<typeof toResultGroup>[0]) ?? null)
      if (group) {
        // 成果组与 run 归属：信封级 run_id 打标，供成果页「运行记录」与对话 chip 跳转
        group.runId = String(event.run_id ?? (event.data as { run_id?: unknown }).run_id ?? '') || undefined
        side.resultGroups = [group, ...useAppStore.getState().resultGroups]
      }
      // 诚实拒绝等无成果组的运行：把 trace 里的 text 事件补为正文，确保用户能看到回复
      let finalBlocks = blocks
      if (!group) {
        const traceEvents = (event.data.trace as Array<{ type?: string; content?: string }> | null) ?? []
        const texts = traceEvents
          .filter((item) => item?.type === 'text' && typeof item.content === 'string' && item.content.trim())
          .map((item) => String(item.content))
        if (texts.length > 0 && !blocks.some((block) => block.type === 'text')) {
          finalBlocks = [
            ...blocks,
            ...texts.map((content) => ({ type: 'text' as const, content, id: nextBlockId() })),
          ]
        }
      }
      // 计划步骤随执行完成全部置 done（计划卡进度条 0→N 动画）：
      // 平时工具事件已逐项打勾，这里兜底把仍未 done 的步骤收尾
      const plan = planStepMarked(msg.plan, 'final')
      // 最终回复：后端下发的结论文本（不含思考过程叙述）；无则回退正文
      const finalAnswer =
        typeof event.data.final_answer === 'string' && event.data.final_answer.trim()
          ? event.data.final_answer
          : undefined
      return {
        ...msg,
        blocks: finalBlocks,
        status: 'done' as const,
        plan,
        finalAnswer,
        // 完成耗时冻结：执行结束当下定值，后续重渲染不再增长
        elapsedMs: elapsedFrom(msg),
        // 本运行对应的成果组 id：对话消息下方渲染「成果产物」chip 跳转用
        resultGroupId: group ? group.id : undefined,
        evidence: Array.isArray(event.data.evidence)
          ? event.data.evidence.map(String)
          : msg.evidence,
        reflection:
          typeof event.data.reflection === 'string' && event.data.reflection
            ? event.data.reflection
            : msg.reflection,
        usedFallback: event.data.used_fallback === true,
        trace: toToolTrace((event.data.trace as Parameters<typeof toToolTrace>[0]) ?? []),
      }
    }
    case 'stream_end':
      return msg.status === 'done' ? msg : { ...msg, status: 'done' as const, elapsedMs: elapsedFrom(msg) }
    case 'error':
      return {
        ...msg,
        blocks: [
          ...blocks,
          { type: 'text', content: `⚠️ ${String(event.data.message ?? '执行出错')}`, id: nextBlockId() },
        ],
      }
    default:
      return msg
  }
}

/** 把事件折叠进 agentId 对应消息，并统一提交副作用 */
function applyEvent(agentId: string, event: AgentEvent): void {
  const side: Partial<AppState> = {}
  const messages = useAppStore.getState().messages.map((message) =>
    message.id === agentId ? reduceEvent(message, event, side) : message,
  )
  useAppStore.setState({ messages, ...side })
  // Agent 生成图层 → 立即并图入库：地图即时显示该图层（颜色由随后的 layer_style 事件上色）
  if (event.type === 'generated_layer') {
    const layerId = String(event.data.layer_id ?? '')
    if (layerId) void useAppStore.getState().addGeneratedLayer(layerId)
  }
}

/** 当前执行中的 Agent 运行（Codex 式「停止」按钮）：abortAgentRun 据此中断 SSE 流 */
let activeRunController: AbortController | null = null

/** 停止收尾：保留已流出的 blocks，把消息落成 done（不显示「后端不可用」误报） */
function markAgentStopped(agentId: string): void {
  useAppStore.setState((state) => ({
    messages: state.messages.map((m) => {
      if (m.id !== agentId) return m
      const blocks = m.blocks?.length
        ? m.blocks
        : [{ type: 'text' as const, content: '已停止执行。', id: nextBlockId() }]
      return { ...m, status: 'done' as const, streamMode: 'done' as const, blocks, elapsedMs: elapsedFrom(m) }
    }),
  }))
}

/**
 * 执行管线：挂上计划元信息 → SSE 流式执行 → 刷新图层。
 * sendMessage（正常路径）与 skipMissing（门2「跳过该项」）共用。
 * acceptMissing：门2 授权跳过的缺失项，随请求传给后端门3 放行。
 * signal：外部 AbortController（停止按钮），abort 时 executePlanStream 抛 AbortError 由调用方收尾。
 */
async function runExecute(
  agentId: string,
  plan: PlanResult,
  question: string,
  history: ChatTurn[],
  acceptMissing?: string[],
  signal?: AbortSignal,
): Promise<void> {
  useAppStore.setState((state) => ({
    messages: state.messages.map((message) =>
      message.id === agentId
        ? {
            ...message,
            status: 'streaming',
            streamMode: 'live',
            blocks: [],
            thinking: plan.thinking || undefined,
            dataCheck: toDataCheck(plan.data_check),
            plan: plan.plan.map((step) => ({
              step_id: step.step_id,
              label: step.label,
              tool: step.tool,
              status: 'pending' as const,
            })),
          }
        : message,
    ),
  }))

  await executePlanStream(
    plan,
    question,
    history,
    (event) => applyEvent(agentId, event),
    signal,
    useAppStore.getState().activeConversationId,
    acceptMissing,
  )

  // 执行会生成新图层，增量刷新（只拉新增图层几何）；失败不阻塞对话
  try {
    await refreshLayersIncremental()
    void ensureVisibleGeojson() // 可见的惰性平台图层按需补拉几何
  } catch {
    // 图层刷新失败保持现状
  }
}

export type NavKey = 'project' | 'agent' | 'data' | 'tools' | 'skills' | 'results' | 'eval'

// 本地演示图层已随北京示例数据移除（2026-08-17）：地图图层全部由后端 /layers 下发。
// 后端未启动时地图保持空（诚实呈现，绝不显示编造的演示假数据）。
const initialLayers: LayerMetadata[] = []

/**
 * 把当前可见且缺几何的惰性图层逐个按需拉取（平台数据 lazy 图层），
 * 拉取完成后自增 geojsonTick 驱动地图重绘。无缺几何图层时立即返回。
 */
async function ensureVisibleGeojson(): Promise<void> {
  const { layers, geojsonTick } = useAppStore.getState()
  const pending = layers.filter(
    (layer) =>
      layer.visible &&
      layer.lazy &&
      layer.kind !== 'raster' &&
      layer.geometry !== 'Table' && // Table 不进可见性兜底拉取，只在打开属性表（PreviewModal）时按需取
      !hasLayerGeojson(layer.id),
  )
  if (pending.length === 0) return
  const results = await Promise.all(pending.map((layer) => ensureLayerGeojson(layer.id)))
  if (results.some(Boolean)) {
    useAppStore.setState({ geojsonTick: geojsonTick + 1 })
  }
}

/** 地图定位目标：成果卡「定位地图」写入，MapWorkspace 监听后飞至该图层 */
export interface FocusTarget {
  layerId: string
  ts: number
}

interface AppState {
  activeNav: NavKey
  /** Agent 浮窗是否打开（悬浮球/浮窗 X 切换；默认收起只显示悬浮球，点球弹出浮窗） */
  agentOpen: boolean
  /** 左侧功能面板是否展开（TopBar 开关；收起时地图占满，导航/成果跳转会重新展开） */
  leftPanelOpen: boolean
  messages: AgentMessage[]
  /** 会话（Codex 式）：项目 → 会话树；activeConversationId 即后端 conversation_id */
  conversations: ConversationMeta[]
  activeConversationId: string
  layers: LayerMetadata[]
  skills: PlanningSkill[]
  /** @ 提及数据源：全部后端 skill（含可执行），skills 只留 prompt 过滤 */
  allSkills: SkillSpec[]
  /** 工具货架（/tools，7 类分类目录）：给人看的全部能力，非 AI 权限开关 */
  tools: ToolSpec[]
  /** 一次分析的成果组（成果页，左槽面板） */
  resultGroups: ResultGroup[]
  /** 成果面板高亮定位的目标分组 id（点击对话「成果产物」chip 写入，面板滚动定位后自动清除） */
  highlightGroupId: string | null
  /** 图层本地样式（按 layerId，session 级；Sprint 2 随 P3 升级后端持久化） */
  layerStyles: Record<string, LayerStyle>
  /** 惰性几何加载版本：平台数据按需拉取完成时自增，驱动地图重绘 */
  geojsonTick: number
  /** 当前底图 id（Agent set_basemap / 工具栏下拉共享） */
  basemapId: string
  /** 数据源版本号：链接/取消链接/下载目录保存/重扫后自增，「文件路径视图」据此重新拉取 sources */
  folderSourcesTick: number
  bumpFolderSourcesTick: () => void
  /** 图层分组（前端本地） */
  groups: LayerGroup[]
  /** 上传弹窗开关 + 上传成功后的待发 prompt（引导案例 2 用） */
  uploadModalOpen: boolean
  pendingPromptAfterUpload: string | null
  /** 弹窗当前 tab：上传文件 / 链接目录（统一「添加数据」入口） */
  uploadModalTab: 'upload' | 'link'
  /** 项目（每间独立厨房）：列表 / 当前项目 / 详情 */
  projects: ProjectInfo[]
  currentProject: ProjectInfo | null
  projectDetail: ProjectDetail | null
  projectLoading: boolean
  /** 地图定位目标 */
  focusTarget: FocusTarget | null
  /** 手绘请求信号：面板「新建绘制」点击递增，MapWorkspace 监听后 startDraw */
  drawRequestTick: number
  requestDraw: () => void
  /** 手绘图层编辑请求信号：图层面板「编辑」点击写入，MapWorkspace 监听后进入编辑会话 */
  editLayerRequest: { layerId: string; ts: number } | null
  requestEditDrawnLayer: (layerId: string) => void
  /** 分析页点击 Skill / Pattern 后生成待发送草稿 */
  draftMessage: string
  /** 待审批权限请求（execute_code 等 ASK 操作） */
  pendingApproval: PendingApproval | null
  setActiveNav: (nav: NavKey) => void
  setBasemapId: (id: string) => void
  /** 弹/收 Agent 浮窗；传 open 布尔时强制置为指定状态（如工具/分析页「发起对话」）
   *   ——若用 toggle 语义，浮窗已开时反而会被收起。 */
  toggleAgent: (open?: boolean) => void
  setDraftMessage: (content: string) => void
  sendMessage: (content: string) => Promise<void>
  /** 门2「跳过该项」：带 accept_missing 直接执行暂存计划（不再重新 plan） */
  skipMissing: (agentId: string) => Promise<void>
  /** 停止当前 Agent 运行（发送按钮切「停止」时点击；abort SSE 流，保留已流出的内容） */
  abortAgentRun: () => void
  /** 审批一个 ASK 权限请求：POST /agent/approve（remember 时持久化规则） */
  resolveApproval: (allowed: boolean, remember: boolean) => Promise<void>
  /** 挂载时拉取后端图层与 Skill，后端未启动则保留本地演示数据 */
  hydrateData: () => Promise<void>
  /** 项目操作：拉列表 / 新建 / 删除 / 切换（切完重拉图层） / 打开详情 */
  refreshProjects: () => Promise<void>
  createProject: (name: string) => Promise<void>
  deleteProject: (projectId: string) => Promise<void>
  switchProject: (projectId: string) => Promise<void>
  openProjectDetail: (projectId: string) => Promise<void>
  /** 会话管理：新建 / 切换 / 重命名 / 置顶 / 删除（轻量前端版，localStorage 持久化） */
  newConversation: () => void
  switchConversation: (id: string) => void
  renameConversation: (id: string, title: string) => void
  pinConversation: (id: string) => void
  deleteConversation: (id: string) => void
  toggleLayerVisibility: (id: string) => void
  setLayerVisible: (id: string, visible: boolean) => void
  showOnlyLayer: (id: string) => void
  /** 彻底删除图层（后端清磁盘物化文件 + 项目 manifest）；成功后前端清理，失败抛错 */
  removeLayer: (id: string) => Promise<void>
  /** Agent 生成图层事件到达时立即并入 store（无需等整个 run 结束），真实几何上屏 */
  addGeneratedLayer: (layerId: string) => Promise<void>
  /** 手绘点/线/面 → 注册为图层并上屏（persist 时经后端 POST /layers/draw 持久化）。返回规范后的图层 id */
  addDrawnLayer: (input: {
    id: string
    name: string
    geometry: GeometryType
    color: string
    features: GeoJSON.Feature[]
    fields?: FieldSpec[]
    crs?: string
    persist?: boolean
    units?: AutoUnitConfig | null
  }) => Promise<{ layerId: string } | null>
  /** 编辑已建手绘图层：持久化编辑后的几何（PUT /layers/{id}/geojson），成功后更新 store 几何/字段/单位并重绘 */
  updateDrawnLayerGeojson: (input: {
    id: string
    features: GeoJSON.Feature[]
    fields?: FieldSpec[]
    units?: AutoUnitConfig | null
  }) => Promise<{ layerId: string } | null>
  renameLayer: (id: string, name: string) => void
  duplicateLayer: (id: string) => void
  updateLayerStyle: (id: string, style: Partial<LayerStyle>) => void
  /** 拖拽重排：把 fromId 移动到可见序列的 toVisibleIndex 处（按显示顺序的索引，0 起始） */
  moveLayer: (fromId: string, toVisibleIndex: number) => void
  createGroup: (name: string, layerId?: string) => void
  moveLayerToGroup: (layerId: string, groupId: string | null) => void
  removeGroup: (groupId: string) => void
  openUploadModal: (promptAfterUpload?: string, tab?: 'upload' | 'link') => void
  closeUploadModal: () => void
  addSkill: (skill: Omit<PlanningSkill, 'id'>) => void
  removeSkill: (id: string) => void
  addResultGroup: (group: ResultGroup) => void
  clearResultGroups: () => void
  removeResultGroup: (groupId: string) => void
  setHighlightGroupId: (id: string | null) => void
  /** 跳转成果页并高亮定位某次运行的分组（对话 chip 点击） */
  focusResultGroup: (groupId: string) => void
  setLeftPanelOpen: (open: boolean) => void
  toggleLeftPanel: () => void
  /** Agent 悬浮球拖拽位置（相对 workspace 的像素；null=默认右下角）。持久化 urbanscope.fabPos */
  fabPos: { x: number; y: number } | null
  /** 传 null 表示清除持久化位置、回退默认右下角（如检测到非法/越界遗留坐标） */
  setFabPos: (pos: { x: number; y: number } | null) => void
  focusLayer: (layerId: string) => void
}

export const useAppStore = create<AppState>((set) => ({
  // Agent 移出左栏成浮窗后，默认左侧展示数据图层
  activeNav: 'data',
  // Agent 默认收起：右下角悬浮球为常态入口，点球弹浮窗、X 关闭回到球
  agentOpen: false,
  // 左栏面板折叠态持久化（mirror「我的数据」折叠 key 的约定）
  leftPanelOpen: (() => {
    try {
      return localStorage.getItem('urbanscope.leftPanelCollapsed') !== '1'
    } catch {
      return true
    }
  })(),
  // Agent 悬浮球拖拽位置持久化（相对 workspace 像素；null=默认右下角）
  fabPos: (() => {
    try {
      const raw = localStorage.getItem('urbanscope.fabPos')
      if (!raw) return null
      const parsed = JSON.parse(raw)
      if (typeof parsed.x === 'number' && typeof parsed.y === 'number') return { x: parsed.x, y: parsed.y }
      return null
    } catch {
      return null
    }
  })(),
  // 会话与对话从 localStorage 恢复（刷新不丢；首访为空的「新会话」）
  messages: initialConversationState.messages,
  conversations: initialConversationState.conversations,
  activeConversationId: initialConversationState.activeConversationId,
  layers: initialLayers,
  skills: defaultSkills,
  allSkills: [],
  tools: [],
  resultGroups: [],
  highlightGroupId: null,
  layerStyles: {},
  geojsonTick: 0,
  basemapId: DEFAULT_BASEMAP_ID,
  folderSourcesTick: 0,
  groups: [],
  uploadModalOpen: false,
  pendingPromptAfterUpload: null,
  uploadModalTab: 'upload',
  projects: [],
  currentProject: null,
  projectDetail: null,
  projectLoading: false,
  focusTarget: null,
  drawRequestTick: 0,
  editLayerRequest: null,
  draftMessage: '',
  pendingApproval: null,
  // 切导航 = 想看对应面板 → 顺带展开左栏（面板被收起时点导航项能重新唤出）
  setActiveNav: (nav) => set({ activeNav: nav, leftPanelOpen: true }),
  setBasemapId: (id) => set({ basemapId: id }),
  bumpFolderSourcesTick: () =>
    set((state) => ({ folderSourcesTick: state.folderSourcesTick + 1 })),
  // 只认布尔参数：防止 onClick 把 MouseEvent 当 truthy open 误判成「强制打开」，导致浮窗关不掉
  toggleAgent: (open) =>
    set((state) => ({ agentOpen: typeof open === 'boolean' ? open : !state.agentOpen })),
  setLeftPanelOpen: (open) => set({ leftPanelOpen: open }),
  toggleLeftPanel: () => {
    const next = !useAppStore.getState().leftPanelOpen
    try {
      localStorage.setItem('urbanscope.leftPanelCollapsed', next ? '0' : '1')
    } catch {
      // localStorage 不可用时忽略（仅不持久化，不影响交互）
    }
    set({ leftPanelOpen: next })
  },
  setFabPos: (pos) => {
    try {
      localStorage.setItem('urbanscope.fabPos', JSON.stringify(pos))
    } catch {
      // localStorage 不可用时忽略（仅不持久化，不影响拖拽）
    }
    set({ fabPos: pos })
  },
  setDraftMessage: (content) => set({ draftMessage: content }),
  sendMessage: async (content) => {
    const controller = new AbortController()
    activeRunController = controller
    const userMessage: AgentMessage = { id: `m-${Date.now()}`, role: 'user', content }
    const agentId = `m-${Date.now() + 1}`
    // startedAt 打点：整个「思考/处理」周期从发送即开始，实时计时「已思考 Xs」据此走
    const placeholder: AgentMessage = { id: agentId, role: 'agent', status: 'loading', startedAt: Date.now() }
    set((state) => ({
      messages: [...state.messages, userMessage, placeholder],
      // 首条 user 消息 → 用其内容给会话命名（「新会话」占位名更新为真实问题）
      conversations: state.conversations.map((c) =>
        c.id === state.activeConversationId && c.title === '新会话'
          ? {
              ...c,
              title: defaultConversationTitle([...state.messages, userMessage]),
              updatedAt: nowIso(),
            }
          : c,
      ),
      draftMessage: '',
    }))

    try {
      // 0. 多轮上下文记忆：把既有对话作为 history 传给 Planner/Executor
      const history = buildHistory(useAppStore.getState().messages)
      // 1. Planner：动态读取当前 skill/tool，生成计划与思考过程 + 就绪检查
      const plan = await planQuestion(
        content,
        history,
        useAppStore.getState().activeConversationId,
        controller.signal,
      )
      if (controller.signal.aborted) {
        // 规划期用户已点「停止」：不再进入执行，直接收尾
        markAgentStopped(agentId)
        return
      }

      // 门1：意图澄清 → 追问气泡（问题 + 选项按钮 + 可打字），不执行。
      // 后端提示词已强制「历史有追问问答对就不再追问」；这里兜底：连续 2 次仍追问
      // 就按普通计划走，防止前端陷入无限追问。
      if (plan.clarify?.question) {
        const clarifyCount = useAppStore
          .getState()
          .messages.filter((m) => m.role === 'agent' && m.clarify).length
        if (clarifyCount < 2) {
          set((state) => ({
            messages: state.messages.map((message): AgentMessage =>
              message.id === agentId
                ? {
                    ...message,
                    status: 'done',
                    blocks: [{ type: 'text', content: plan.clarify!.question, id: nextBlockId() }],
                    clarify: {
                      question: plan.clarify!.question,
                      choices: plan.clarify!.choices,
                      needs: plan.clarify!.needs ?? undefined,
                    },
                  }
                : message,
            ),
          }))
          return
        }
      }

      // 门2：数据缺失 → 缺失面板（跳过 / 上传 / 换问法），不执行
      if ((plan.unmet_targets ?? []).length > 0) {
        set((state) => ({
          messages: state.messages.map((message) =>
            message.id === agentId
              ? {
                  ...message,
                  status: 'done',
                  unmetTargets: plan.unmet_targets,
                  planResult: plan,
                  originalQuestion: content,
                  dataCheck: toDataCheck(plan.data_check),
                  plan: plan.plan.map((step) => ({
                    step_id: step.step_id,
                    label: step.label,
                    tool: step.tool,
                    status: 'pending' as const,
                  })),
                }
              : message,
          ),
        }))
        return
      }

      // 正常：流式执行 + 刷新图层
      await runExecute(agentId, plan, content, history, undefined, controller.signal)
    } catch (error) {
      if (controller.signal.aborted) {
        // 用户点「停止」：保留已流出的内容收尾为 done，不误报「后端不可用」
        markAgentStopped(agentId)
        return
      }
      // 真实失败（后端不可用等）：落成 done 并追加错误块，避免消息永远处于 streaming 卡住「停止」按钮
      const errorBlock = {
        type: 'text' as const,
        content: `后端服务不可用：${(error as Error).message}。请确认 apps/api 已在 http://127.0.0.1:8011 启动。`,
        id: nextBlockId(),
      }
      set((state) => ({
        messages: state.messages.map((message) =>
          message.id === agentId
            ? {
                ...message,
                status: 'done',
                streamMode: 'done',
                blocks: [...(message.blocks ?? []), errorBlock],
                elapsedMs: elapsedFrom(message),
              }
            : message,
        ),
      }))
    } finally {
      if (activeRunController === controller) activeRunController = null
    }
  },
  skipMissing: async (agentId) => {
    // 门2「跳过该项」：用暂存的完整计划直接执行，跳过用户同意的缺失项（不再重新 plan）
    const message = useAppStore.getState().messages.find((m) => m.id === agentId)
    const plan = message?.planResult
    if (!plan) return
    const controller = new AbortController()
    activeRunController = controller
    // 门2「跳过该项」是延迟执行：重置计时起点，避免把用户盯着缺失面板的空等时间算进「已处理」
    useAppStore.setState((state) => ({
      messages: state.messages.map((m) =>
        m.id === agentId ? { ...m, startedAt: Date.now(), elapsedMs: undefined } : m,
      ),
    }))
    try {
      const history = buildHistory(useAppStore.getState().messages)
      const accept = [...new Set(plan.unmet_targets)]
      await runExecute(agentId, plan, message.originalQuestion ?? plan.summary, history, accept, controller.signal)
    } catch (error) {
      if (controller.signal.aborted) {
        markAgentStopped(agentId)
        return
      }
      throw error
    } finally {
      if (activeRunController === controller) activeRunController = null
    }
  },
  /** 停止当前 Agent 运行（发送按钮切「停止」）：abort SSE 流，已流出的内容保留 */
  abortAgentRun: () => {
    activeRunController?.abort()
  },
  resolveApproval: async (allowed, remember) => {
    const pending = useAppStore.getState().pendingApproval
    if (!pending) return
    useAppStore.setState({ pendingApproval: null })
    try {
      const response = await fetch(`${API_BASE}/agent/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: pending.requestId, allowed, remember }),
      })
      if (!response.ok) throw new Error(`approve ${response.status}`)
    } catch {
      // 审批失败：恢复弹窗让用户重试
      useAppStore.setState({ pendingApproval: pending })
    }
  },
  hydrateData: async () => {
    try {
      const [backendLayers, backendSkills, backendGeojson, backendTools] = await Promise.all([
        fetchLayers(),
        fetchSkills(),
        fetchAllGeojson(),
        fetchTools().catch(() => [] as ToolSpec[]),
      ])
      const freshLayers = backendLayers.map(toLayer)
      set((state) => ({
        layers: freshLayers,
        // 分析页 planningSkills 只消费 SKILL.md 驱动 skill（可执行 skill 无 indicators，进详情弹窗会崩）
        skills: backendSkills.filter((s) => s.kind === 'prompt').map(toPlanningSkill),
        // @ 提及数据源：保留全部后端 skill（含可执行 kernel-density 等）
        allSkills: backendSkills,
        tools: backendTools,
        layerStyles: mergeStylesFromLayers(state.layerStyles, freshLayers),
      }))
      setGeojsonCache(backendGeojson)
      void ensureVisibleGeojson() // 可见的惰性平台图层按需补拉几何
    } catch {
      // 后端未启动：保留本地演示数据，UI 仍可浏览
    }
  },
  refreshProjects: async () => {
    try {
      const [projects, active] = await Promise.all([fetchProjects(), fetchActiveProject()])
      set({ projects, currentProject: active })
    } catch {
      // 后端未启动：保持空列表
    }
  },
  createProject: async (name) => {
    try {
      await apiCreateProject(name)
      await useAppStore.getState().refreshProjects()
    } catch {
      // 后端失败：保持原状
    }
  },
  deleteProject: async (projectId) => {
    const wasCurrent = useAppStore.getState().currentProject?.id === projectId
    try {
      await apiDeleteProject(projectId)
    } catch {
      return
    }
    if (wasCurrent) {
      await useAppStore.getState().switchProject('default')
    }
    await useAppStore.getState().refreshProjects()
  },
  switchProject: async (projectId) => {
    try {
      await apiSwitchProject(projectId)
    } catch {
      return
    }
    // 重拉图层：换项目 = 换一套用户图层（底座 demo/platform 全局共享）
    try {
      const freshLayers = (await fetchLayers()).map(toLayer)
      set((state) => ({
        layers: freshLayers,
        layerStyles: mergeStylesFromLayers(state.layerStyles, freshLayers),
      }))
    } catch {
      // 忽略：图层重拉失败不阻断项目切换
    }
    await useAppStore.getState().refreshProjects()
    await useAppStore.getState().openProjectDetail(projectId)
  },
  openProjectDetail: async (projectId) => {
    set({ projectLoading: true })
    try {
      const detail = await fetchProjectDetail(projectId)
      set({ projectDetail: detail })
    } catch {
      // 后端失败：保留旧详情
    }
    set({ projectLoading: false })
  },
  newConversation: () =>
    set((state) => {
      // 运行中（有流式/加载消息）不切换会话，避免 SSE 事件写错会话
      if (state.messages.some((m) => m.status === 'streaming' || m.status === 'loading')) return state
      persistConversationMessages(state.activeConversationId, state.messages)
      const id = newConversationId()
      const conversation: ConversationMeta = {
        id,
        projectId: state.currentProject?.id ?? 'default',
        title: '新会话',
        createdAt: nowIso(),
        updatedAt: nowIso(),
        pinned: false,
      }
      try {
        localStorage.setItem(ACTIVE_CONVERSATION_KEY, id)
      } catch {
        /* 忽略 */
      }
      return {
        conversations: [...state.conversations, conversation],
        activeConversationId: id,
        messages: [],
      }
    }),
  switchConversation: (id) =>
    set((state) => {
      if (id === state.activeConversationId) return state
      if (state.messages.some((m) => m.status === 'streaming' || m.status === 'loading')) return state
      // 切走前保存当前会话快照 → 换 active → 恢复目标会话消息
      persistConversationMessages(state.activeConversationId, state.messages)
      const messages = loadMessagesFromStorage(conversationMessagesKey(id))
      try {
        localStorage.setItem(ACTIVE_CONVERSATION_KEY, id)
      } catch {
        /* 忽略 */
      }
      return {
        activeConversationId: id,
        messages,
        conversations: state.conversations.map((c) =>
          c.id === id ? { ...c, updatedAt: nowIso() } : c,
        ),
      }
    }),
  renameConversation: (id, title) =>
    set((state) => ({
      conversations: state.conversations.map((c) =>
        c.id === id ? { ...c, title: title.trim() || c.title } : c,
      ),
    })),
  pinConversation: (id) =>
    set((state) => ({
      conversations: state.conversations.map((c) =>
        c.id === id ? { ...c, pinned: !c.pinned } : c,
      ),
    })),
  deleteConversation: (id) =>
    set((state) => {
      const rest = state.conversations.filter((c) => c.id !== id)
      try {
        localStorage.removeItem(conversationMessagesKey(id))
      } catch {
        /* 忽略 */
      }
      if (id !== state.activeConversationId) return { conversations: rest }
      // 删除的是当前会话：切到同项目下最近的会话，否则新建空会话
      const sameProject = rest.filter((c) => c.projectId === state.currentProject?.id)
      const candidates = sameProject.length > 0 ? sameProject : rest
      const next = candidates[candidates.length - 1]
      const activeId = next ? next.id : newConversationId()
      const conversations = next
        ? rest
        : [
            ...rest,
            {
              id: activeId,
              projectId: state.currentProject?.id ?? 'default',
              title: '新会话',
              createdAt: nowIso(),
              updatedAt: nowIso(),
              pinned: false,
            } as ConversationMeta,
          ]
      try {
        localStorage.setItem(ACTIVE_CONVERSATION_KEY, activeId)
      } catch {
        /* 忽略 */
      }
      return {
        conversations,
        activeConversationId: activeId,
        messages: next ? loadMessagesFromStorage(conversationMessagesKey(next.id)) : [],
      }
    }),
  toggleLayerVisibility: (id) => {
    const layer = useAppStore.getState().layers.find((item) => item.id === id)
    const nextVisible = !layer?.visible
    set((state) => ({
      layers: state.layers.map((item) =>
        item.id === id ? { ...item, visible: nextVisible } : item,
      ),
    }))
    // folder 图层眼睛开关经 PATCH 持久化（重启/刷新后保持）；请求失败回滚本地状态。
    // 暂不改变平台/手绘/其他图层的既有持久化行为，控制影响面。
    if (layer?.source === 'folder') {
      void patchLayerStyle(id, { visible: nextVisible }).catch(() => {
        set((state) => ({
          layers: state.layers.map((item) =>
            item.id === id ? { ...item, visible: !nextVisible } : item,
          ),
        }))
      })
    }
    // 点亮时由 focusLayer 负责「加载一次真实几何 + 定位」（内部对 lazy 非 Table 图层
    // ensureLayerGeojson），不再额外 ensureVisibleGeojson —— 二者会在缓存写入前并发请求
    // 同一 /layers/{id}/geojson。隐藏时不加载几何。
    if (nextVisible) void useAppStore.getState().focusLayer(id)
  },
  setLayerVisible: (id, visible) => {
    const layer = useAppStore.getState().layers.find((item) => item.id === id)
    set((state) => ({
      layers: state.layers.map((item) => (item.id === id ? { ...item, visible } : item)),
    }))
    if (layer?.source === 'folder') {
      void patchLayerStyle(id, { visible }).catch(() => {
        set((state) => ({
          layers: state.layers.map((item) =>
            item.id === id ? { ...item, visible: !visible } : item,
          ),
        }))
      })
    }
    // 同 toggleLayerVisibility：点亮由 focusLayer 加载 + 定位，隐藏不加载，避免并发重复请求
    if (visible) void useAppStore.getState().focusLayer(id)
  },
  showOnlyLayer: (id) => {
    set((state) => ({
      layers: state.layers.map((layer) => ({ ...layer, visible: layer.id === id })),
    }))
    void ensureVisibleGeojson()
  },
  removeLayer: async (id) => {
    // 后端彻底删除（同步清磁盘物化文件 + 项目 manifest，跨重启不复活），成功才清理前端。
    // 平台数据只读图层后端返回 400 → 这里抛错由调用方（LayerPanel）alert。
    await apiDeleteLayer(id)
    set((state) => ({
      layers: state.layers.filter((layer) => layer.id !== id),
      groups: state.groups.map((group) => ({
        ...group,
        layerIds: group.layerIds.filter((layerId) => layerId !== id),
      })),
    }))
  },
  addGeneratedLayer: async (layerId) => {
    // Agent 生成图层（GENERATED_LAYER 事件）到达时立即刷新：把新图层并入 store，
    // 让地图立刻显示、面板立刻出现该行；颜色由随后的 layer_style 事件即时上色。
    // 只合并目标图层，不整段替换 layers（保留用户运行中已做的显隐等本地改动）。
    // 关键时序：先拉真实几何写入缓存，再并入 store —— 否则 getLayerGeojson 对
    // generated 图层会 demo 兜底，真实数据上屏前闪一下假数据（诚实呈现原则）。
    // 失败保持现状：run 结束后的整段刷新（runExecute）仍会兜底补上。
    // 先拉 metadata 再依据它决定是否拉几何：隐藏预览（visible=False, lazy）、Table、raster
    // 不能因为 GENERATED_LAYER 事件被无条件下载 —— 默认不预览必须是真不请求几何。
    try {
      const fresh = await fetchLayers()
      const freshLayers = fresh.map(toLayer)
      const target = freshLayers.find((layer) => layer.id === layerId)
      if (!target) return
      const shouldFetch =
        target.visible && !target.lazy && target.geometry !== 'Table' && target.kind !== 'raster'
      const geoOk = shouldFetch ? await ensureLayerGeojson(layerId) : false
      set((state) => {
        const exists = state.layers.some((layer) => layer.id === layerId)
        const layers = exists
          ? state.layers.map((layer) => (layer.id === layerId ? target : layer))
          : [...state.layers, target]
        return {
          layers,
          layerStyles: mergeStylesFromLayers(state.layerStyles, layers),
          ...(geoOk ? { geojsonTick: state.geojsonTick + 1 } : {}),
        }
      })
    } catch {
      // 忽略：run 结束后的整段刷新会补齐
    }
  },
  addDrawnLayer: async (input) => {
    // 手绘图层：本地几何 + 元数据直接并入 store 上屏；persist 时先经后端注册拿规范 id。
    // 与 addGeneratedLayer 不同，几何已在手（无需 fetch/ensureLayerGeojson）：
    // 直接 setGeojsonEntry 写缓存 + geojsonTick+1，驱动 MapWorkspace 重跑 buildDeckLayers。
    try {
      const features = input.features
      if (features.length === 0) return null
      const fc: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features }
      // id 去重：撞已有图层 id 时追加后缀（仿 duplicateLayer 的 while exists 规则）
      let layerId = input.id
      let n = 2
      while (useAppStore.getState().layers.some((layer) => layer.id === layerId)) {
        layerId = `${input.id}_${n++}`
      }
      // persist：后端注册（清洗内部 properties 键、推断 fields、物化到磁盘）→ 取回规范 layer_id
      let fields = input.fields
      if (input.persist) {
        const res = await drawLayer({
          // 传去重后的 layer_id：后端 clean_layer_id 以此为 id，避免同名重复创建时互相覆盖
          layer_id: layerId,
          name: input.name,
          crs: input.crs,
          color: input.color,
          geojson: fc,
          fields,
          units: input.units ?? undefined,
        })
        layerId = res.layer_id
        fields = undefined // 后端已算好 fields（以注册结果为准）
      }
      const collection = { ...fc, features }
      setGeojsonEntry(layerId, collection)
      const mapped: LayerMetadata = {
        id: layerId,
        name: input.name || layerId,
        source: 'drawn',
        geometry: input.geometry,
        color: input.color,
        visible: true,
        featureCount: features.length,
        crs: input.crs ?? 'EPSG:4326',
        fields,
        readonly: false,
        lazy: false,
        kind: 'vector',
        bbox: null,
        units: input.units ?? null,
      }
      set((state) => {
        const layers = [...state.layers, mapped]
        return {
          layers,
          layerStyles: mergeStylesFromLayers(state.layerStyles, layers),
          geojsonTick: state.geojsonTick + 1, // 必需：驱动 deck 重绘上屏
        }
      })
      return { layerId }
    } catch (error) {
      // 后端注册失败（如网络/校验）：不上屏，让调用方提示用户
      console.error('[addDrawnLayer] 失败：', error)
      return null
    }
  },
  updateDrawnLayerGeojson: async (input) => {
    // 编辑已建手绘图层：注入自动字段（按该图层 units 重算长度/面积/经纬度），
    // PUT 持久化 → 校验成功再更新 store 几何/字段/单位并自增 geojsonTick 驱动重绘。
    const layer = useAppStore.getState().layers.find((item) => item.id === input.id)
    if (!layer) return null
    const units = input.units ?? layer.units ?? undefined
    const features = injectAutoFields(input.features, units)
    if (features.length === 0) return null
    const fc: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features }
    try {
      const updated = await updateLayerGeojson(input.id, {
        geojson: fc,
        fields: input.fields,
        units,
      })
      setGeojsonEntry(input.id, fc)
      set((state) => ({
        layers: state.layers.map((item) =>
          item.id === input.id
            ? {
                ...item,
                geometry: updated.geometry,
                featureCount: features.length,
                fields: updated.fields ?? item.fields,
                units: updated.units ?? units ?? null,
              }
            : item,
        ),
        geojsonTick: state.geojsonTick + 1, // 驱动 deck 重绘新几何
      }))
      return { layerId: input.id }
    } catch (error) {
      // 后端拒绝（空几何/只读平台图层/网络）：不回写本地，让调用方提示用户
      console.error('[updateDrawnLayerGeojson] 失败：', error)
      return null
    }
  },
  renameLayer: (id, name) =>
    set((state) => ({
      layers: state.layers.map((layer) => (layer.id === id ? { ...layer, name: name.trim() || layer.name } : layer)),
    })),
  moveLayer: (fromId, toVisibleIndex) =>
    set((state) => {
      // 可见顺序 = 分组内成员（按 groups 顺序）+ 未分组（按 layers 数组序）
      const groupedIds = new Set(state.groups.flatMap((group) => group.layerIds))
      let visible: string[] = []
      for (const group of state.groups) visible.push(...group.layerIds)
      visible = visible.concat(state.layers.filter((layer) => !groupedIds.has(layer.id)).map((layer) => layer.id))

      const fromIndex = visible.indexOf(fromId)
      if (fromIndex < 0) return state
      visible.splice(fromIndex, 1)
      const to = Math.max(0, Math.min(toVisibleIndex, visible.length))
      visible.splice(to, 0, fromId)

      // 按新可见顺序重建 layers（其余元素保持原有相对顺序）
      const byId = new Map(state.layers.map((layer) => [layer.id, layer]))
      const newLayers = visible.map((id) => byId.get(id)).filter((layer): layer is NonNullable<typeof layer> => Boolean(layer))
      // 同步各分组内成员顺序（跟随可见顺序，保持分组内相对位置）
      const newGroups = state.groups.map((group) => ({
        ...group,
        layerIds: visible.filter((id) => group.layerIds.includes(id)),
      }))
      return { layers: newLayers, groups: newGroups }
    }),
  duplicateLayer: (id) => {
    const layer = useAppStore.getState().layers.find((item) => item.id === id)
    if (!layer) return
    let newId = `${id}_copy`
    let n = 2
    while (useAppStore.getState().layers.some((item) => item.id === newId)) newId = `${id}_copy${n++}`
    // 把源图层几何带过去，地图上立即可见
    setGeojsonEntry(newId, getLayerGeojson(layer))
    set((state) => ({
      layers: [
        ...state.layers,
        {
          ...layer,
          id: newId,
          name: `${layer.name} 副本`,
          visible: true,
          // 副本是用户自己的图层：解除只读/惰性，可自由修改删除
          readonly: false,
          lazy: false,
        },
      ],
      layerStyles: state.layerStyles[id] ? { ...state.layerStyles, [newId]: { ...state.layerStyles[id] } } : state.layerStyles,
    }))
  },
  updateLayerStyle: (id, style) => {
    set((state) => ({
      layerStyles: { ...state.layerStyles, [id]: { ...(state.layerStyles[id] ?? {}), ...style } },
    }))
    // 样式改动必须保证图层几何已加载：getLayerGeojson 对懒加载平台图层在几何缺失时返回空
    // （绝不编造演示数据），几何缺失时改颜色/透明度在地图上没有可见对象，
    // 表现为「改样式地图不变，隐藏再显示才更新」（toggle 会加载几何）。
    // 这里在任意样式改动（面板滑块 / Agent 设样式 / 分档）时补齐几何并驱动重绘。
    const target = useAppStore.getState().layers.find((item) => item.id === id)
    if (target?.lazy && target.kind !== 'raster' && target.geometry !== 'Table' && !hasLayerGeojson(id)) {
      void ensureLayerGeojson(id).then((ok) => {
        if (ok) useAppStore.setState((s) => ({ geojsonTick: s.geojsonTick + 1 }))
      })
    }
  },
  createGroup: (name, layerId) =>
    set((state) => {
      const clean = name.trim()
      if (!clean) return state
      const group: LayerGroup = { id: `group-${Date.now().toString(36)}`, name: clean, layerIds: layerId ? [layerId] : [] }
      return { groups: [...state.groups, group] }
    }),
  moveLayerToGroup: (layerId, groupId) =>
    set((state) => {
      const groups = state.groups.map((group) => ({
        ...group,
        layerIds: group.layerIds.filter((item) => item !== layerId),
      }))
      if (groupId) {
        const index = groups.findIndex((group) => group.id === groupId)
        if (index >= 0) groups[index] = { ...groups[index], layerIds: [...groups[index].layerIds, layerId] }
      }
      return { groups }
    }),
  removeGroup: (groupId) =>
    set((state) => ({ groups: state.groups.filter((group) => group.id !== groupId) })),
  openUploadModal: (promptAfterUpload, tab) =>
    set({
      uploadModalOpen: true,
      pendingPromptAfterUpload: promptAfterUpload ?? null,
      uploadModalTab: tab ?? 'upload',
    }),
  closeUploadModal: () =>
    set({ uploadModalOpen: false, pendingPromptAfterUpload: null, uploadModalTab: 'upload' }),
  addSkill: (skill) =>
    set((state) => ({
      skills: [...state.skills, { ...skill, id: `skill-${Date.now()}` }],
    })),
  removeSkill: (id) =>
    set((state) => ({ skills: state.skills.filter((skill) => skill.id !== id) })),
  addResultGroup: (group) =>
    set((state) => ({ resultGroups: [group, ...state.resultGroups] })),
  clearResultGroups: () => set({ resultGroups: [] }),
  removeResultGroup: (groupId) =>
    set((state) => ({ resultGroups: state.resultGroups.filter((group) => group.id !== groupId) })),
  setHighlightGroupId: (id) => set({ highlightGroupId: id }),
  focusResultGroup: (groupId) =>
    set({ activeNav: 'results', leftPanelOpen: true, highlightGroupId: groupId }),
  focusLayer: async (layerId) => {
    // 平台数据惰性图层：先按需拉几何，再飞过去（fitBounds 需要真实 bbox）
    // Table 图层无几何：不得在定位/点亮时误取整表（只在打开属性表 PreviewModal 时按需取）
    const layer = useAppStore.getState().layers.find((item) => item.id === layerId)
    if (layer?.lazy && layer.geometry !== 'Table' && !hasLayerGeojson(layerId)) {
      const ok = await ensureLayerGeojson(layerId)
      if (ok) set((state) => ({ geojsonTick: state.geojsonTick + 1 }))
    }
    set({ focusTarget: { layerId, ts: Date.now() } })
  },
  requestDraw: () => set((state) => ({ drawRequestTick: state.drawRequestTick + 1 })),
  requestEditDrawnLayer: (layerId) => set({ editLayerRequest: { layerId, ts: Date.now() } }),
}))

/* ---------------- 对话持久化 ----------------
 * 消息变化 300ms 防抖写回 localStorage：流式期每帧增量都在 setState，不直接写盘，
 * 等停下来才批量落一次。消息按会话分区（urbanscope.messages.<convId>），
 * 会话元数据（含首条消息自动命名）同步持久化。恢复后的流式消息在
 * loadMessagesFromStorage 已改为 done，不会出现永久「思考中」。 */
let _persistTimer: ReturnType<typeof setTimeout> | null = null
useAppStore.subscribe((state) => {
  if (_persistTimer) clearTimeout(_persistTimer)
  _persistTimer = setTimeout(() => {
    try {
      localStorage.setItem(
        conversationMessagesKey(state.activeConversationId),
        JSON.stringify(sanitizeForStorage(state.messages)),
      )
      localStorage.setItem(CONVERSATIONS_STORAGE_KEY, JSON.stringify(state.conversations))
    } catch {
      /* 存储满 / 不可用：忽略，不影响对话 */
    }
  }, 300)
})
