import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Globe } from 'lucide-react'
import { LiquidOrb } from '../../components/LiquidOrb'

interface ThinkingRowProps {
  /** 当前已流出的思考/叙述文本（为空时只显示标题行） */
  text: string
  /** 是否正在流式输出（真流式期间自动展开并跟随滚动） */
  streaming: boolean
  /** 标题文案（默认「思考中…」；流式结束后默认「思考过程」） */
  title?: string
  /** 结束后可折叠模式：默认收起，点击展开看完整思考过程（改动 5） */
  collapsible?: boolean
}

/**
 * 思考行（对齐典型 Agent 思考行）：一行「🌐 思考中… / 思考过程」标题 +
 * 带左强调竖条的可滚动内容区。
 * - 流式期间：自动展开、自动滚底，标题「思考中…」；
 * - 结束后（collapsible）：默认收起成一行「思考过程」，点击展开看完整思考。
 */
export function ThinkingRow({ text, streaming, title, collapsible }: ThinkingRowProps) {
  // 折叠模式默认收起；流式/非折叠保持展开
  const [open, setOpen] = useState<boolean>(!collapsible)
  const scrollRef = useRef<HTMLDivElement>(null)

  // 文本增长时跟随滚动到底部（流式体验）
  useEffect(() => {
    if (!streaming) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [text, streaming])

  const label = title ?? (streaming ? '思考中…' : '思考过程')
  const expanded = streaming ? true : open
  const toggleable = Boolean(collapsible) && !streaming

  return (
    <div className={collapsible ? 'chat-thinking chat-thinking--collapsible' : 'chat-thinking'}>
      <button
        type="button"
        className="chat-thinking__toggle"
        disabled={!toggleable}
        onClick={() => setOpen((v) => !v)}
        aria-expanded={expanded}
      >
        {collapsible &&
          (expanded ? (
            <ChevronDown className="chat-thinking__chevron" aria-hidden="true" />
          ) : (
            <ChevronRight className="chat-thinking__chevron" aria-hidden="true" />
          ))}
        <LiquidOrb
          size={15}
          className="chat-thinking__icon"
          fallback={<Globe className="chat-thinking__icon" aria-hidden="true" />}
        />
        <span className="chat-thinking__title">{label}</span>
      </button>
      {text && expanded && (
        <div className="chat-thinking__body-wrap">
          <div className="chat-thinking__bar" aria-hidden="true" />
          <div className="chat-thinking__body" ref={scrollRef}>
            {text}
          </div>
        </div>
      )}
    </div>
  )
}
