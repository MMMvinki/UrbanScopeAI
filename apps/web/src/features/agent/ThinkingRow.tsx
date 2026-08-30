import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Globe } from 'lucide-react'
import { LiquidOrb } from '../../components/LiquidOrb'

/** 毫秒 → 「Xs」/「Xm Ys」：Codex 式处理时长文案 */
export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  if (total < 60) return `${total}s`
  return `${Math.floor(total / 60)}m ${total % 60}s`
}

interface ThinkingRowProps {
  /** 当前已流出的思考/叙述文本（为空时只显示标题行） */
  text: string
  /** 是否正在流式输出（真流式期间自动展开并跟随滚动） */
  streaming: boolean
  /** 标题文案（默认「思考中…」；流式结束后默认「思考过程」） */
  title?: string
  /** 结束后可折叠模式：默认收起，点击展开看完整思考过程（改动 5） */
  collapsible?: boolean
  /** 本轮开始时间（epoch ms）：提供且 streaming 时标题后实时跳「已思考 Xs」 */
  startedAt?: number
  /** 完成耗时（ms）：提供时标题后固定显示「已处理 Xs」（执行结束冻结） */
  elapsedMs?: number
}

/**
 * 思考行（对齐典型 Agent 思考行）：一行「🌐 思考中… / 思考过程」标题 +
 * 带左强调竖条的可滚动内容区。
 * - 流式期间：自动展开、自动滚底，标题「思考中…」+ 每秒跳动的「已思考 Xs」；
 * - 结束后（collapsible）：默认收起成一行「思考过程 · 已处理 Xs」，点击展开看完整思考。
 */
export function ThinkingRow({ text, streaming, title, collapsible, startedAt, elapsedMs }: ThinkingRowProps) {
  // 折叠模式默认收起；流式/非折叠保持展开
  const [open, setOpen] = useState<boolean>(!collapsible)
  const [now, setNow] = useState<number>(() => Date.now())
  const scrollRef = useRef<HTMLDivElement>(null)

  // 实时计时：仅「正在思考且未冻结耗时」时每秒跳动一次
  const ticking = streaming && startedAt != null && elapsedMs == null
  useEffect(() => {
    if (!ticking) return
    setNow(Date.now())
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [ticking])

  // 文本增长时跟随滚动到底部（流式体验）
  useEffect(() => {
    if (!streaming) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [text, streaming])

  const label = title ?? (streaming ? '思考中…' : '思考过程')
  // 已完成 → 冻结的「已处理 Xs」；思考中 → 实时「已思考 Xs」；其余不显示
  const durationLabel =
    elapsedMs != null
      ? `已处理 ${formatDuration(elapsedMs)}`
      : startedAt != null && streaming
        ? `已思考 ${formatDuration(now - startedAt)}`
        : null
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
        {durationLabel && <span className="chat-thinking__elapsed">{durationLabel}</span>}
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
