import { useMemo, useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  Database,
  FileText,
  FolderCog,
  Globe,
  Landmark,
  MapIcon,
  Play,
  Target,
} from 'lucide-react'
import type { ToolSpec } from '../../lib/api'
import { useAppStore } from '../../store/useAppStore'
import { PanelCollapseButton } from '../../components/PanelCollapseButton'

/** 7 类货架（tools-module-design.md §2）：顺序即展示顺序 */
const SHELF: { key: string; icon: typeof MapIcon; note?: string }[] = [
  { key: '文件与系统', icon: FolderCog },
  { key: '地图与可视化', icon: MapIcon },
  { key: '空间分析', icon: Target },
  { key: '数据与栅格', icon: Database },
  { key: '网络与搜索', icon: Globe },
  { key: '报告生成', icon: FileText, note: '规划中' },
  { key: '专业GIS分析', icon: Landmark, note: '连接 Agent 后开放' },
]

const STATUS_LABEL: Record<ToolSpec['status'], string> = {
  ready: '可用',
  planned: '规划中',
  blocked: '受限',
}

/** 发起对话提示词：优先用后端预置白话 prompt，否则由名字+描述兜底生成 */
function buildPrompt(tool: ToolSpec): string {
  if (tool.prompt_hint && tool.prompt_hint.trim()) return tool.prompt_hint
  return `请帮我使用「${tool.name}」工具：${tool.description}`
}

/** 工具货架：按 7 类分类展示产品全部能力（给人看的目录，非 AI 权限开关） */
export function ToolsPanel() {
  const tools = useAppStore((s) => s.tools)
  const setDraftMessage = useAppStore((s) => s.setDraftMessage)
  const toggleAgent = useAppStore((s) => s.toggleAgent)

  // 默认展开能力集中的货架；专业 GIS（89 个）默认收起
  const [expanded, setExpanded] = useState<Record<string, boolean>>({
    '地图与可视化': true,
    '空间分析': true,
    '数据与栅格': true,
    '网络与搜索': true,
  })
  const [selected, setSelected] = useState<ToolSpec | null>(null)

  const byShelf = useMemo(() => {
    const groups = new Map<string, ToolSpec[]>()
    for (const shelf of SHELF) groups.set(shelf.key, [])
    for (const tool of tools) {
      const list = groups.get(tool.category)
      if (list) list.push(tool)
    }
    return groups
  }, [tools])

  const readyCount = tools.filter((tool) => tool.status === 'ready').length

  const launch = (tool: ToolSpec) => {
    setDraftMessage(buildPrompt(tool))
    toggleAgent(true) // Agent 已浮窗化：发起对话 = 弹起浮窗（不切左栏）
  }

  const toggle = (key: string) => setExpanded((prev) => ({ ...prev, [key]: !prev[key] }))

  return (
    <div className="panel-content tools-panel">
      <header className="panel-header">
        <h2 className="panel-header__title">Tools</h2>
        <div className="panel-header__actions">
          <PanelCollapseButton />
        </div>
      </header>

      <div className="tools-scroll scroll-area">
        <p className="tools-intro">
          平台能力货架：看清这产品能干什么。共 {readyCount} 个可用工具
          {tools.length - readyCount > 0 ? `，${tools.length - readyCount} 个规划中` : ''}。
          点开分类看详情，点「发起对话」直接让 Agent 开工。
        </p>

        {tools.length === 0 ? (
          <div className="empty-state">
            <p className="empty-state__title">工具目录未加载</p>
            <p className="empty-state__hint">请确认后端服务已启动（http://127.0.0.1:8011），刷新后自动加载。</p>
          </div>
        ) : (
          <div className="tools-shelf">
            {SHELF.map(({ key, icon: Icon, note }) => {
              const list = byShelf.get(key) ?? []
              const isOpen = expanded[key] ?? false
              return (
                <div className="tools-category" key={key}>
                  <button
                    type="button"
                    className="tools-category__head"
                    onClick={() => toggle(key)}
                    aria-expanded={isOpen}
                  >
                    {isOpen ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
                    <Icon aria-hidden="true" />
                    <span className="tools-category__name">{key}</span>
                    <span className="tools-category__count">{list.length}</span>
                  </button>
                  {isOpen &&
                    (list.length === 0 ? (
                      <p className="tools-category__empty">
                        {note ?? '暂无工具（规划中）'}
                      </p>
                    ) : (
                      <div className="tools-category__body">
                        {list.map((tool) => (
                          <div className="tools-tool" key={tool.name}>
                            <button
                              type="button"
                              className="tools-tool__row"
                              onClick={() => setSelected(selected?.name === tool.name ? null : tool)}
                            >
                              <span className="tools-tool__name">{tool.name}</span>
                              <span className={`tools-tool__status is-${tool.status}`}>
                                {STATUS_LABEL[tool.status]}
                              </span>
                            </button>
                            {selected?.name === tool.name && (
                              <div className="tools-tool__detail">
                                <p className="tools-tool__desc">{tool.description}</p>
                                {Object.keys(tool.parameters).length > 0 && (
                                  <dl className="tools-tool__params">
                                    {Object.entries(tool.parameters).map(([key, value]) => (
                                      <div className="tools-tool__param" key={key}>
                                        <dt>{key}</dt>
                                        <dd>{value || '—'}</dd>
                                      </div>
                                    ))}
                                  </dl>
                                )}
                                <button
                                  type="button"
                                  className="button button--primary button--sm tools-tool__launch"
                                  onClick={() => launch(tool)}
                                >
                                  <Play aria-hidden="true" />
                                  发起对话
                                </button>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    ))}
                </div>
              )
            })}
          </div>
        )}

        <p className="tools-footnote">
          说明：工具目录是「给人看的能力货架」；Agent 实际能否调用由后端权限单独控制，互不干扰。
        </p>
      </div>
    </div>
  )
}
