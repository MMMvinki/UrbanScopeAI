import { Bot, CircleCheck, History, TriangleAlert } from 'lucide-react'
import { useState } from 'react'
import type { AgentMessage, DataCheckResult } from './agentTypes'
import { useAppStore } from '../../store/useAppStore'
import { LiquidOrb } from '../../components/LiquidOrb'
import { ThinkingRow, formatDuration } from './ThinkingRow'
import { PlanCard } from './PlanCard'
import { MarkdownText } from './MarkdownText'
import { CodeRow, EventRow, ToolRow } from './ProcessRows'
import { RowCollapse } from './RowCollapse'
import { RunArchiveModal } from './RunArchiveModal'
import { ResultChip } from '../results/ResultChip'

/** 数据就绪：一行 badges（就绪 ✓ / 缺失 ⚠），不再是面板 */
function DataCheckLine({ result }: { result: DataCheckResult }) {
  if (result.ready.length === 0 && result.missing.length === 0) return null
  return (
    <div className="chat-badges">
      {result.ready.map((item) => (
        <span className="badge badge--success" key={item}>
          <CircleCheck aria-hidden="true" />
          {item}
        </span>
      ))}
      {result.missing.map((item) => (
        <span className="badge badge--warning" key={item}>
          <TriangleAlert aria-hidden="true" />
          {item}
        </span>
      ))}
    </div>
  )
}

/** 门1 意图澄清：Planner 追问，用户点选项或直接打字回答。回答作为新用户消息走 sendMessage。 */
function ClarifyBox({ message }: { message: AgentMessage }) {
  const sendMessage = useAppStore((s) => s.sendMessage)
  const [draft, setDraft] = useState('')
  const clarify = message.clarify
  if (!clarify) return null
  const submit = (text: string) => {
    const value = text.trim()
    if (value) void sendMessage(value)
  }
  return (
    <div className="chat-clarify">
      {clarify.choices.length > 0 && (
        <div className="chat-actions-row">
          {clarify.choices.map((choice) => (
            <button
              type="button"
              key={choice}
              className="button button--sm chat-choice-btn"
              onClick={() => submit(choice)}
            >
              {choice}
            </button>
          ))}
        </div>
      )}
      <div className="chat-clarify__input">
        <input
          type="text"
          className="form-field__input"
          value={draft}
          placeholder="或直接输入你的回答，回车发送"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              submit(draft)
              setDraft('')
            }
          }}
        />
      </div>
    </div>
  )
}

/** 门2 数据缺失：列出缺失项，让用户选「跳过该项 / 上传数据 / 换个问法」。 */
function MissingPanel({ message }: { message: AgentMessage }) {
  const skipMissing = useAppStore((s) => s.skipMissing)
  const openUploadModal = useAppStore((s) => s.openUploadModal)
  const sendMessage = useAppStore((s) => s.sendMessage)
  const [draft, setDraft] = useState('')
  const missing = message.unmetTargets ?? []
  if (missing.length === 0) return null
  const uploadPrompt = `我上传了新的图层数据，请用它继续完成分析：${message.originalQuestion ?? ''}`
  const rephrase = (text: string) => {
    const value = text.trim()
    if (value) void sendMessage(value)
  }
  return (
    <div className="chat-missing">
      <p className="chat-missing__title">要完成这个分析，还缺这些数据：</p>
      <ul className="chat-missing-list">
        {missing.map((item) => (
          <li key={item}>
            <TriangleAlert aria-hidden="true" />
            {item}
          </li>
        ))}
      </ul>
      <div className="chat-actions-row">
        <button
          type="button"
          className="button button--sm button--primary"
          onClick={() => void skipMissing(message.id)}
        >
          跳过该项，用现有数据继续
        </button>
        <button type="button" className="button button--sm" onClick={() => openUploadModal(uploadPrompt)}>
          上传数据
        </button>
      </div>
      <div className="chat-missing__input">
        <input
          type="text"
          className="form-field__input"
          value={draft}
          placeholder="换个问法，重新描述你的需求，回车发送"
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              rephrase(draft)
              setDraft('')
            }
          }}
        />
      </div>
    </div>
  )
}

/**
 * Agent 回复 = 行式流（对齐主流 Agent 消息观感）。
 *
 * 顺序对齐流式消息的 part 数组（最终结论在最后）：
 *   流式期间 → 顶部思考行（自动展开跟随）；结束后 → 顶部思考收起
 *   计划 → 进度卡；数据检查 → 一行 badges
 *   过程块（工具/事件/代码）→ 细行
 *   trace/依据/反思 → 细折叠行（默认收起）
 *   最终回复/结论 → 底部 markdown 正文（流式消息的最后一个 text part）
 */
function AgentBubble({ message }: { message: AgentMessage }) {
  const streaming = message.status === 'streaming'
  const blocks = message.blocks ?? []
  const textBlocks = blocks.filter((block) => block.type === 'text')
  const bodyText = textBlocks.map((block) => block.content).join('\n\n').trim()
  const rows = blocks.filter((block) => block.type !== 'text')
  const [runArchiveOpen, setRunArchiveOpen] = useState(false)

  // 改动 5：最终回复不含思考过程叙述。结束后 replyText = 后端下发的结论文本
  // （finalAnswer），无则回退整个正文。思考正文若与结论不同 → 折叠成「思考过程」行。
  const replyText = (message.finalAnswer ?? bodyText).trim()
  const hasSeparateThinking = !streaming && bodyText && bodyText !== replyText

  return (
    <div className="chat-message chat-message--agent">
      <div className="chat-avatar" aria-hidden="true">
        <LiquidOrb size={28} fallback={<Bot />} />
      </div>
      <div className="chat-bubble chat-bubble--agent chat-bubble--flow">
        {message.usedFallback && (
          <span
            className="chat-bubble__flag"
            title="LLM 不可用或执行失败时，由规则引擎兜底完成分析"
          >
            <TriangleAlert aria-hidden="true" />
            规则兜底
          </span>
        )}

        {/* 流式期间：顶部思考行 = 正在生成的叙事（流结束自动收起）；实时「已思考 Xs」 */}
        {streaming && <ThinkingRow text={bodyText} streaming startedAt={message.startedAt} />}

        {message.plan && message.plan.length > 0 && <PlanCard steps={message.plan} />}
        {message.dataCheck && <DataCheckLine result={message.dataCheck} />}

        {/* 门1 意图澄清 / 门2 数据缺失：追问交互，不触发执行 */}
        {message.clarify && <ClarifyBox message={message} />}
        {message.unmetTargets && message.unmetTargets.length > 0 && <MissingPanel message={message} />}

        {rows.map((block, index) => {
          const key = block.id ?? index
          if (block.type === 'tool') return <ToolRow key={key} block={block} />
          if (block.type === 'event') return <EventRow key={key} block={block} />
          return <CodeRow key={key} block={block} />
        })}

        {message.trace && message.trace.length > 0 && (
          <RowCollapse title="工具调用" badge={`${message.trace.length} 次`}>
            <div className="trace-list">
              {message.trace.map((call) => (
                <div className="trace-row" key={call.name}>
                  <code>{call.name}()</code>
                  <span>{call.detail}</span>
                </div>
              ))}
            </div>
          </RowCollapse>
        )}

        {message.evidence && message.evidence.length > 0 && (
          <RowCollapse title="结果依据" badge="可追溯">
            <ul className="evidence-list">
              {message.evidence.map((item) => (
                <li key={item}>
                  <CircleCheck aria-hidden="true" />
                  <span>{item}</span>
                </li>
              ))}
            </ul>
          </RowCollapse>
        )}

        {message.reflection && (
          <RowCollapse title="反思" badge="执行复核">
            <p className="thinking-text">{message.reflection}</p>
          </RowCollapse>
        )}

        {/* 结束后：思考正文与结论不同 → 折叠成可展开的「思考过程」行（改动 5），标题后固定「已处理 Xs」 */}
        {hasSeparateThinking && (
          <ThinkingRow
            text={bodyText}
            streaming={false}
            collapsible
            startedAt={message.startedAt}
            elapsedMs={message.elapsedMs}
          />
        )}

        {/* 最终回复/结论：结论文本（finalAnswer），放最底部；无则回退整个正文 */}
        {!streaming && replyText && <MarkdownText text={replyText} />}

        {/* 改动 6：成果产物 chip —— 一行摘要，点击跳成果页并高亮该 run 分组 */}
        {!streaming && message.resultGroupId && <ResultChip groupId={message.resultGroupId} />}

        {/* S2-4：运行记录入口（run archive 后端就绪，纯前端只读展示） */}
        {!streaming && message.runId && (
          <button
            type="button"
            className="run-entry"
            onClick={() => setRunArchiveOpen(true)}
            title="查看本次执行的完整运行记录"
          >
            <History aria-hidden="true" />
            查看运行记录
          </button>
        )}

        {/* 兜底：完成后无独立思考行（正文即结论）时，在底部标出处理耗时 */}
        {!streaming && !hasSeparateThinking && message.elapsedMs != null && (
          <div className="chat-elapsed">已处理 {formatDuration(message.elapsedMs)}</div>
        )}
      </div>
      {runArchiveOpen && message.runId && (
        <RunArchiveModal runId={message.runId} onClose={() => setRunArchiveOpen(false)} />
      )}
    </div>
  )
}

interface ChatMessageProps {
  message: AgentMessage
}

export function ChatMessage({ message }: ChatMessageProps) {
  if (message.role === 'user') {
    return (
      <div className="chat-message chat-message--user">
        <div className="chat-bubble chat-bubble--user">{message.content}</div>
      </div>
    )
  }

  if (message.status === 'loading') {
    return (
      <div className="chat-message chat-message--agent">
        <div className="chat-avatar" aria-hidden="true">
          <LiquidOrb size={28} fallback={<Bot />} />
        </div>
        <div className="chat-bubble chat-bubble--agent chat-bubble--flow">
          <ThinkingRow text="" streaming title="正在理解问题…" startedAt={message.startedAt} />
        </div>
      </div>
    )
  }

  return <AgentBubble message={message} />
}
