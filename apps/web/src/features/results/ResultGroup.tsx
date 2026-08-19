import { useEffect, useRef, useState } from 'react'
import { ChevronRight, FolderOpen, History, Trash2 } from 'lucide-react'
import type { ResultGroup as ResultGroupModel } from './resultTypes'
import { ResultWidget } from './ResultWidget'
import { RunArchiveModal } from '../agent/RunArchiveModal'
import { useAppStore } from '../../store/useAppStore'

/**
 * 一次分析的成果组：可折叠，内含若干成果卡片。
 * 头 = 折叠开关（左）+ 操作区（右：运行记录 / 删除分组），避免 button 嵌套非法结构。
 */
export function ResultGroup({
  group,
  highlighted = false,
}: {
  group: ResultGroupModel
  highlighted?: boolean
}) {
  const [open, setOpen] = useState(true)
  const [runArchiveOpen, setRunArchiveOpen] = useState(false)
  const removeResultGroup = useAppStore((s) => s.removeResultGroup)
  const groupRef = useRef<HTMLElement>(null)

  // 对话 chip 跳转：滚动到视野中央并闪烁高亮
  useEffect(() => {
    if (!highlighted) return
    const frame = requestAnimationFrame(() => {
      groupRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    })
    return () => cancelAnimationFrame(frame)
  }, [highlighted])

  return (
    <section
      ref={groupRef}
      className={highlighted ? 'result-group is-highlighted' : 'result-group'}
    >
      <div className="result-group__head">
        <button
          type="button"
          className="result-group__head-toggle"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          <ChevronRight
            className={open ? 'result-group__chevron is-open' : 'result-group__chevron'}
            aria-hidden="true"
          />
          <span className="result-group__icon">
            <FolderOpen aria-hidden="true" />
          </span>
          <span className="result-group__title">{group.title}</span>
          <span className="panel-header__count">{group.widgets.length}</span>
        </button>
        <div className="result-group__ops">
          {group.runId && (
            <button
              type="button"
              className="mini-action"
              onClick={() => setRunArchiveOpen(true)}
              title="查看本次执行的完整运行记录"
            >
              <History aria-hidden="true" />
              运行记录
            </button>
          )}
          <button
            type="button"
            className="mini-action"
            onClick={() => removeResultGroup(group.id)}
            title="删除该次分析的全部成果"
          >
            <Trash2 aria-hidden="true" />
            删除
          </button>
        </div>
      </div>

      {open && (
        <div className="result-group__body">
          {group.widgets.map((widget) => (
            <ResultWidget key={widget.id} widget={widget} />
          ))}
        </div>
      )}

      {runArchiveOpen && group.runId && (
        <RunArchiveModal runId={group.runId} onClose={() => setRunArchiveOpen(false)} />
      )}
    </section>
  )
}
