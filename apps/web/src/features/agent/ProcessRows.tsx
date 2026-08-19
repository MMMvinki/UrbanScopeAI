import { useState } from 'react'
import {
  BarChart3,
  ChevronDown,
  CircleCheck,
  Database,
  FileCode2,
  Globe,
  Layers,
  Loader2,
  MapPin,
  PlayCircle,
  Search,
  Table2,
  Wrench,
} from 'lucide-react'
import type { AgentBlock, EventKind } from './agentTypes'

/** 工具名 → 中文标签（ToolRow 标题 + TypingFooter 忙碌文案共用） */
export const TOOL_LABELS: Record<string, string> = {
  list_datasets: '查看数据集',
  get_dataset_schema: '读取结构',
  buffer: '缓冲区分析',
  coverage: '服务范围分析',
  spatial_join: '空间连接',
  nearest_distance: '最近邻分析',
  density: '密度分析',
  supply_demand: '供需分析',
  statistics: '统计分析',
  spatial_clip: '空间裁剪',
  osm_call: 'OSM 取数',
  datasource_call: '数据源',
  webfetch: '网页抓取',
  websearch: '网络搜索',
  register_layer: '注册图层',
  csv_to_geojson: 'CSV 转图层',
  execute_code: '执行代码',
  community_health_check: '城市体检',
  geocoding: '地理编码',
  rule_based_analysis: '规则分析',
}

const EVENT_LABELS: Record<EventKind, string> = {
  'ran-command': '运行了命令',
  created: '已创建',
  ran: '已运行',
  generated: '已生成',
}

const TOOL_ICONS: Record<string, typeof Wrench> = {
  buffer: PlayCircle,
  coverage: PlayCircle,
  spatial_join: Layers,
  nearest_distance: MapPin,
  density: BarChart3,
  supply_demand: BarChart3,
  statistics: BarChart3,
  spatial_clip: Layers,
  osm_call: Globe,
  datasource_call: Database,
  webfetch: Search,
  websearch: Search,
  register_layer: Layers,
  csv_to_geojson: Table2,
  execute_code: FileCode2,
  list_datasets: Database,
  get_dataset_schema: Table2,
}

function toolIcon(name: string): typeof Wrench {
  return TOOL_ICONS[name] ?? Wrench
}

/** 工具调用细行：图标 + 中文名 + 状态徽章 + 参数摘要（对齐典型工具调用行） */
export function ToolRow({ block }: { block: Extract<AgentBlock, { type: 'tool' }> }) {
  const Icon = toolIcon(block.name)
  const label = TOOL_LABELS[block.name] ?? block.name
  return (
    <div className="chat-row chat-row--tool">
      <span className="chat-row__icon" aria-hidden="true">
        {block.status === 'running' ? <Loader2 className="spinner" /> : <Icon />}
      </span>
      <span className="chat-row__label">
        {label}
        <code>{block.name}()</code>
      </span>
      {block.status === 'running' && (
        <span className="chat-row__status chat-row__status--run">进行中</span>
      )}
      {block.status === 'error' && (
        <span className="chat-row__status chat-row__status--err">失败</span>
      )}
      {block.detail && block.detail.length > 0 && (
        <span className="chat-row__detail">{block.detail}</span>
      )}
    </div>
  )
}

/** 成果事件细行：✓ 已生成/已创建 + 图层名 + 要素数 */
export function EventRow({ block }: { block: Extract<AgentBlock, { type: 'event' }> }) {
  const label = EVENT_LABELS[block.kind]
  return (
    <div className="chat-row chat-row--event">
      <span className="chat-row__icon chat-row__icon--ok" aria-hidden="true">
        <CircleCheck />
      </span>
      <span className="chat-row__label">
        {label} {block.content}
      </span>
      {block.meta && <span className="chat-row__detail">{block.meta}</span>}
    </div>
  )
}

/** 代码执行细行：标题行（可点击展开代码/结果），对齐典型代码执行步骤行的轻量感 */
export function CodeRow({ block }: { block: Extract<AgentBlock, { type: 'code' }> }) {
  const [open, setOpen] = useState(false)
  const running = block.status === 'running'
  return (
    <div className={open ? 'chat-row chat-row--code is-open' : 'chat-row chat-row--code'}>
      <button type="button" className="chat-row__head" onClick={() => setOpen((v) => !v)}>
        <span className="chat-row__icon" aria-hidden="true">
          {running ? <Loader2 className="spinner" /> : <FileCode2 />}
        </span>
        <span className="chat-row__label">
          执行代码<code>execute_code()</code>
        </span>
        {block.status === 'running' && (
          <span className="chat-row__status chat-row__status--run">进行中</span>
        )}
        {block.status === 'error' && (
          <span className="chat-row__status chat-row__status--err">失败</span>
        )}
        {block.status === 'done' && !running && (
          <span className="chat-row__status chat-row__status--ok">完成</span>
        )}
        <ChevronDown className="chat-row__chevron" aria-hidden="true" />
      </button>
      {open && (
        <div className="chat-row__code-body">
          {block.code && <pre className="chat-row__code-pre">{block.code}</pre>}
          {block.result && (
            <pre className="chat-row__code-pre chat-row__code-result">{block.result}</pre>
          )}
        </div>
      )}
    </div>
  )
}
