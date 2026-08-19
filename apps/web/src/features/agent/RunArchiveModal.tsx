import { useEffect, useState } from 'react'
import { History, X } from 'lucide-react'
import { fetchRun, type RunArchiveDetail } from '../../lib/api'

interface RunArchiveModalProps {
  runId: string
  onClose: () => void
}

/** 折叠块：标题 + 子项列表（meta/events/tool_calls/artifacts 共用） */
function Group({ title, badge, items }: { title: string; badge: string; items: Array<Record<string, unknown>> }) {
  if (items.length === 0) return null
  return (
    <details className="run-group" open>
      <summary className="run-group__head">
        <span>{title}</span>
        <span className="run-group__badge">{badge}</span>
      </summary>
      <ol className="run-group__list">
        {items.map((item, index) => (
          <li key={index}>
            <pre>{JSON.stringify(item, null, 2)}</pre>
          </li>
        ))}
      </ol>
    </details>
  )
}

/** Run Archive（S2-4）：读取 /runs/{id} 只读展示 —— 元信息 + 事件时间线 + 工具调用 + 产物 */
export function RunArchiveModal({ runId, onClose }: RunArchiveModalProps) {
  const [detail, setDetail] = useState<RunArchiveDetail | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    fetchRun(runId)
      .then((data) => {
        if (!cancelled) setDetail(data)
      })
      .catch(() => {
        if (!cancelled) setError('运行记录读取失败')
      })
    return () => {
      cancelled = true
    }
  }, [runId])

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal modal--lg"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="运行记录"
      >
        <header className="modal__header">
          <div>
            <h3 className="modal__title">运行记录</h3>
            <p className="modal__desc">
              <code>{runId}</code>
            </p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭">
            <X aria-hidden="true" />
          </button>
        </header>
        <div className="modal__body">
          {error ? (
            <p className="preview-empty">{error}</p>
          ) : !detail ? (
            <div className="run-loading">
              <History aria-hidden="true" />
              <span>正在读取运行记录…</span>
            </div>
          ) : (
            <div className="run-archive">
              {detail.meta && Object.keys(detail.meta).length > 0 && (
                <Group title="元信息" badge={`${Object.keys(detail.meta).length} 项`} items={[detail.meta]} />
              )}
              <Group title="事件时间线" badge={`${(detail.events ?? []).length} 条`} items={detail.events ?? []} />
              <Group title="工具调用" badge={`${(detail.tool_calls ?? []).length} 次`} items={detail.tool_calls ?? []} />
              <Group title="产物" badge={`${(detail.artifacts ?? []).length} 项`} items={detail.artifacts ?? []} />
              <Group title="LLM 用量" badge={`${(detail.llm_usage ?? []).length} 次`} items={detail.llm_usage ?? []} />
              <Group title="步骤" badge={`${(detail.steps ?? []).length} 步`} items={detail.steps ?? []} />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
