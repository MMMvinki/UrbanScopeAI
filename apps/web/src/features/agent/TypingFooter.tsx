import { Bot } from 'lucide-react'
import { LiquidOrb } from '../../components/LiquidOrb'

interface TypingFooterProps {
  /** 当前忙碌文案（null 时不渲染，消息区无正在进行的任务） */
  label: string | null
}

/**
 * 底部工作状态（对齐典型消息流底部状态）：消息流末尾的「头像 + 呼吸点 +
 * 当前在做什么」，替代原来的「正在理解问题…」占位气泡，不占消息位。
 */
export function TypingFooter({ label }: TypingFooterProps) {
  if (!label) return null
  return (
    <div className="typing-footer">
      <div className="chat-avatar" aria-hidden="true">
        <LiquidOrb size={28} fallback={<Bot />} />
      </div>
      <span className="typing-footer__dot" aria-hidden="true" />
      <span className="typing-footer__label">{label}</span>
    </div>
  )
}
