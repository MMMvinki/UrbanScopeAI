import { useEffect, useRef, useState } from 'react'
import { ChevronRight, FolderOpen, History, Trash2 } from 'lucide-react'
import type { ResultGroup as ResultGroupModel } from './resultTypes'
import { ResultWidget } from './ResultWidget'
import { RunArchiveModal } from '../agent/RunArchiveModal'
import { useAppStore } from '../../store/useAppStore'

/** 停止原因 → 徽标文案（缺省 null → 不渲染徽标） */
const STOP_REASON_LABELS: Record<string, string> = {
  completed: '分析完成',
  data_missing: '因缺数据未完成',
  tool_call_limit: '已达工具调用上限',
  no_data_in_range: '范围内无数据',
  engine_error: '计算异常未完成',
}

function stopReasonBadge(reason?: string): string | undefined {
  if (!reason) return undefined
  return STOP_REASON_LABELS[reason] ?? reason
}

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
  const sendMessage = useAppStore((s) => s.sendMessage)
  const groupRef = useRef<HTMLElement>(null)

  // 统一结果格式新字段（缺省即空，整块不渲染 → 完全向后兼容）
  const metrics = group.metrics ?? []
  const findings = group.findings ?? []
  const warnings = group.warnings ?? []
  const rules = group.rules ?? []
  const params = group.params ?? {}
  const source = group.source
  const nextQuestions = group.nextQuestions ?? []
  const stopReason = group.stopReason
  const stopBadge = stopReasonBadge(stopReason)
  const hasMeta =
    metrics.length > 0 || findings.length > 0 || warnings.length > 0 ||
    nextQuestions.length > 0 || Boolean(stopBadge)

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
          {stopBadge && (
            <span className={`resultset__stopbadge resultset__stopbadge--${stopReason}`}>
              {stopBadge}
            </span>
          )}
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
          {hasMeta && (
            <div className="resultset__meta">
              {metrics.length > 0 && (
                <div className="resultset__metrics">
                  {metrics.map((metric, i) => (
                    <div
                      key={i}
                      className="resultset__metric"
                      title={metric.source ? `数据来源：${metric.source}` : undefined}
                    >
                      <span className="resultset__metric-name">{metric.name}</span>
                      <span className="resultset__metric-value">
                        {metric.value}
                        {metric.unit ? (
                          <span className="resultset__metric-unit">{metric.unit}</span>
                        ) : null}
                      </span>
                    </div>
                  ))}
                </div>
              )}
              {findings.length > 0 && (
                <div className="resultset__block">
                  <div className="resultset__block-title">发现</div>
                  <ul className="resultset__list">
                    {findings.map((f, i) => (
                      <li key={i}>{f}</li>
                    ))}
                  </ul>
                </div>
              )}
              {warnings.length > 0 && (
                <div className="resultset__block resultset__block-warn">
                  <div className="resultset__block-title">质量提示</div>
                  {source && <div className="resultset__source">{source}</div>}
                  <ul className="resultset__list">
                    {warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                </div>
              )}
              {(rules.length > 0 || Object.keys(params).length > 0) && (
                <div className="resultset__block">
                  <div className="resultset__block-title">方法</div>
                  {rules.length > 0 && (
                    <ul className="resultset__list">
                      {rules.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  )}
                  {Object.keys(params).length > 0 && (
                    <div className="resultset__params">
                      {Object.entries(params).map(([k, v]) => (
                        <span key={k} className="resultset__param">
                          {k}={String(v)}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              )}
              {nextQuestions.length > 0 && (
                <div className="resultset__block">
                  <div className="resultset__block-title">继续分析</div>
                  <div className="resultset__next">
                    {nextQuestions.map((q, i) => (
                      <button
                        key={i}
                        type="button"
                        className="resultset__next-button"
                        onClick={() => sendMessage(q)}
                      >
                        {q}
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
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
