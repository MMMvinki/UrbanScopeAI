import { useEffect } from 'react'
import { FolderOutput, Trash2 } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'
import { PanelCollapseButton } from '../../components/PanelCollapseButton'
import { ResultGroup } from './ResultGroup'

/**
 * 成果页（改动 4）：左侧 360px 槽位渲染，替代原右侧常驻成果栏。
 * 数据 = 内存态（本次会话；刷新即清，历史走各组「运行记录」RunArchiveModal）。
 */
export function ResultsPanel() {
  const groups = useAppStore((s) => s.resultGroups)
  const clearResultGroups = useAppStore((s) => s.clearResultGroups)
  const highlightGroupId = useAppStore((s) => s.highlightGroupId)
  const setHighlightGroupId = useAppStore((s) => s.setHighlightGroupId)
  const widgetCount = groups.reduce((count, group) => count + group.widgets.length, 0)

  // chip 跳转高亮 2.4s 后自动清除
  useEffect(() => {
    if (!highlightGroupId) return
    const timer = setTimeout(() => setHighlightGroupId(null), 2400)
    return () => clearTimeout(timer)
  }, [highlightGroupId, setHighlightGroupId])

  return (
    <div className="panel-content results-panel">
      <header className="panel-header">
        <h2 className="panel-header__title">成果</h2>
        <div className="panel-header__actions">
          <span className="panel-header__count">{widgetCount}</span>
          {groups.length > 0 && (
            <button
              type="button"
              className="icon-button icon-button--sm"
              onClick={clearResultGroups}
              aria-label="清空成果"
              title="清空成果"
            >
              <Trash2 aria-hidden="true" />
            </button>
          )}
          <PanelCollapseButton />
        </div>
      </header>

      {groups.length === 0 ? (
        <div className="empty-state">
          <FolderOutput className="empty-state__icon" aria-hidden="true" />
          <p className="empty-state__title">暂无成果</p>
          <p className="empty-state__hint">完成一次分析后，结果会显示在这里。</p>
        </div>
      ) : (
        <div className="result-groups scroll-area">
          {groups.map((group) => (
            <ResultGroup
              key={group.id}
              group={group}
              highlighted={group.id === highlightGroupId}
            />
          ))}
        </div>
      )}
    </div>
  )
}
