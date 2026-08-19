import { ShieldAlert } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'

/**
 * 最小权限审批弹窗：execute_code / pip install 等写操作默认 ASK。
 * 用户允许 / 拒绝 / 允许并记住 → POST /agent/approve 解阻塞后端 worker 线程。
 */
export function ApprovalModal() {
  const pending = useAppStore((s) => s.pendingApproval)
  const resolveApproval = useAppStore((s) => s.resolveApproval)

  if (!pending) return null

  const argsText = (() => {
    try {
      const text = JSON.stringify(pending.arguments, null, 2)
      return text === '{}' ? '（无参数）' : text
    } catch {
      return String(pending.arguments)
    }
  })()

  return (
    <div className="approval-overlay" role="dialog" aria-modal="true" aria-label="权限审批">
      <div className="approval-modal">
        <header className="approval-modal__header">
          <ShieldAlert className="approval-modal__icon" aria-hidden="true" />
          <div>
            <h3 className="approval-modal__title">需要你的授权</h3>
            <p className="approval-modal__subtitle">Agent 请求执行以下操作</p>
          </div>
        </header>

        <div className="approval-modal__tool">
          <span className="approval-modal__tool-label">工具</span>
          <code className="approval-modal__tool-name">{pending.tool}</code>
        </div>

        <div className="approval-modal__args">
          <span className="approval-modal__args-label">参数</span>
          <pre className="approval-modal__args-body">{argsText}</pre>
        </div>

        {pending.reason && <p className="approval-modal__reason">{pending.reason}</p>}

        <footer className="approval-modal__actions">
          <button
            type="button"
            className="button button--ghost"
            onClick={() => resolveApproval(false, false)}
          >
            拒绝
          </button>
          <button
            type="button"
            className="button button--secondary"
            onClick={() => resolveApproval(true, false)}
          >
            允许
          </button>
          <button
            type="button"
            className="button button--primary"
            onClick={() => resolveApproval(true, true)}
          >
            允许并记住
          </button>
        </footer>
      </div>
    </div>
  )
}
