import { useState } from 'react'
import type { ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'

interface RowCollapseProps {
  title: string
  badge?: string
  children: ReactNode
}

/**
 * 细折叠行（对齐流式消息的轻量 trace）：无边框卡片，
 * 只有一行「› 标题 + 徽章」，点击展开正文。默认收起，不喧宾夺主。
 */
export function RowCollapse({ title, badge, children }: RowCollapseProps) {
  const [open, setOpen] = useState(false)

  return (
    <section className={open ? 'chat-collapse is-open' : 'chat-collapse'}>
      <button
        type="button"
        className="chat-collapse__head"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <ChevronRight className="chat-collapse__chevron" aria-hidden="true" />
        <span className="chat-collapse__title">{title}</span>
        {badge && <span className="badge badge--neutral">{badge}</span>}
      </button>
      {open && <div className="chat-collapse__body">{children}</div>}
    </section>
  )
}
