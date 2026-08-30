import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ChangeEvent, ReactNode } from 'react'
import {
  Boxes,
  ChevronRight,
  FileUp,
  Play,
  RefreshCw,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import { fetchSkills, type SkillSpec } from '../../lib/api'
import { useAppStore } from '../../store/useAppStore'
import { PanelCollapseButton } from '../../components/PanelCollapseButton'
import type { PlanningSkill } from './planningSkills'
import type { SkillDetail } from './planningSkills'

/* ---------------- 统一技能卡片模型（可执行 + 提示型合并，UI 不区分形态） ---------------- */

interface SkillCard {
  id: string
  name: string
  description: string
  /** 发起分析时发给 Agent 的触发语 */
  prompt: string
  /** SKILL.md 方法论正文（无则用 detail 渲染） */
  body?: string
  detail?: PlanningSkill['detail']
  /** 是否前端本地创建（仅本地可删除） */
  local?: boolean
}

/* ---------------- 轻量 Markdown 渲染（SKILL.md 方法论正文） ---------------- */

function renderInline(text: string): ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
  return parts.map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) return <strong key={index}>{part.slice(2, -2)}</strong>
    if (part.startsWith('`') && part.endsWith('`')) return <code key={index}>{part.slice(1, -1)}</code>
    return part
  })
}

function MarkdownView({ text }: { text: string }) {
  const blocks = text.trim().split(/\n{2,}/)
  return (
    <div className="op-md">
      {blocks.map((block, index) => {
        const trimmed = block.trim()
        if (!trimmed) return null
        const lines = trimmed.split('\n')
        // 表格（| 分隔行块）
        if (lines.every((line) => line.trim().startsWith('|')) && trimmed.includes('|')) {
          const cells = lines
            .filter((line) => line.trim())
            .map((row) =>
              row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim()),
            )
          const head = cells[0] ?? []
          return (
            <table key={index} className="op-md__table">
              <thead>
                <tr>
                  {head.map((cell, j) => (
                    <th key={j}>{renderInline(cell)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {cells.slice(1).map((row, i) => (
                  <tr key={i}>
                    {row.map((cell, j) => (
                      <td key={j}>{renderInline(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          )
        }
        // 无序列表
        if (/^\s*[-*]\s/.test(trimmed)) {
          return (
            <ul key={index}>
              {lines
                .filter((line) => /^\s*[-*]\s/.test(line.trim()))
                .map((line, i) => (
                  <li key={i}>{renderInline(line.trim().replace(/^\s*[-*]\s/, ''))}</li>
                ))}
            </ul>
          )
        }
        // 标题
        const heading = trimmed.match(/^(#{1,4})\s+(.*)/)
        if (heading) {
          const Tag = `h${Math.min(heading[1].length, 4)}` as 'h2' | 'h3' | 'h4' | 'h5'
          return <Tag key={index}>{renderInline(heading[2])}</Tag>
        }
        return <p key={index}>{renderInline(trimmed)}</p>
      })}
    </div>
  )
}

/* ---------------- 统一技能卡片 ---------------- */

function SkillCardItem({
  card,
  onView,
  onLaunch,
}: {
  card: SkillCard
  onView: (card: SkillCard) => void
  onLaunch: (prompt: string) => void
}) {
  return (
    <div className="op-prompt-card">
      <button type="button" className="op-prompt-card__main" onClick={() => onView(card)}>
        <span className="op-prompt-card__name">{card.name}</span>
        <span className="op-prompt-card__desc">{card.description}</span>
        <ChevronRight className="op-prompt-card__arrow" aria-hidden="true" />
      </button>
      <div className="op-prompt-card__actions">
        <button type="button" className="mini-action" onClick={() => onLaunch(card.prompt)}>
          <Play aria-hidden="true" /> 发起分析
        </button>
      </div>
    </div>
  )
}

/* ---------------- 统一详情：方法论正文（或结构化 detail）+ 发起分析 ---------------- */

function SkillDetailView({
  card,
  onClose,
  onLaunch,
  onDelete,
}: {
  card: SkillCard
  onClose: () => void
  onLaunch: (prompt: string) => void
  onDelete?: () => void
}) {
  const [confirming, setConfirming] = useState(false)
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={card.name}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="modal__header">
          <div className="modal__titles">
            <h3 className="modal__title">{card.name}</h3>
            {card.description ? <p className="modal__desc">{card.description}</p> : null}
          </div>
          <button type="button" className="icon-button icon-button--sm" onClick={onClose} aria-label="关闭">
            <X aria-hidden="true" />
          </button>
        </header>

        <div className="modal__body">
          {card.body ? (
            <MarkdownView text={card.body} />
          ) : card.detail ? (
            <>
              <section className="modal__section">
                <h4 className="modal__section-title">所需数据</h4>
                <div className="check-chip-list">
                  {card.detail.dataRequirements.map((item) => (
                    <span className="badge badge--neutral" key={item}>
                      {item}
                    </span>
                  ))}
                </div>
              </section>
              <section className="modal__section">
                <h4 className="modal__section-title">工作流</h4>
                <ol className="modal__workflow">
                  {card.detail.workflow.map((step, index) => (
                    <li key={step}>
                      <span className="modal__step-index">{index + 1}</span>
                      {step}
                    </li>
                  ))}
                </ol>
              </section>
              <section className="modal__section">
                <h4 className="modal__section-title">指标</h4>
                <ul className="modal__list">
                  {card.detail.indicators.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </section>
              <section className="modal__section">
                <h4 className="modal__section-title">使用工具</h4>
                <div className="trace-list">
                  {card.detail.tools.map((tool) => (
                    <div className="trace-row" key={tool}>
                      <code>{tool}()</code>
                    </div>
                  ))}
                </div>
              </section>
            </>
          ) : null}
        </div>

        <footer className="modal__footer">
          <button type="button" className="button button--ghost button--sm" onClick={onClose}>
            关闭
          </button>
          {onDelete &&
            (confirming ? (
              <>
                <span className="op-prompt-card__confirm-text">确定删除「{card.name}」？</span>
                <button type="button" className="mini-action mini-action--danger" onClick={onDelete}>
                  确认
                </button>
                <button type="button" className="mini-action" onClick={() => setConfirming(false)}>
                  取消
                </button>
              </>
            ) : (
              <button type="button" className="mini-action mini-action--danger" onClick={() => setConfirming(true)}>
                <Trash2 aria-hidden="true" /> 删除
              </button>
            ))}
          <span className="modal__footer-spacer" />
          <button
            type="button"
            className="button button--primary button--sm"
            onClick={() => onLaunch(card.prompt)}
          >
            <Play aria-hidden="true" />
            发起分析
          </button>
        </footer>
      </div>
    </div>
  )
}

/* ---------------- 列表：技能（统一一组） ---------------- */

function SkillList({
  cards,
  onView,
  onLaunch,
  onAiCreate,
  onImport,
}: {
  cards: SkillCard[]
  onView: (card: SkillCard) => void
  onLaunch: (prompt: string) => void
  onAiCreate: () => void
  onImport: () => void
}) {
  return (
    <div className="op-list">
      <div className="op-list__section">
        <div className="op-section-head">
          <h3 className="op-section-head__title">
            <Boxes aria-hidden="true" /> 技能
          </h3>
          <div className="op-section-head__actions">
            <button type="button" className="op-list__new" onClick={onAiCreate} title="让 Agent 按 skill-creator 方法论引导创建技能">
              <Sparkles aria-hidden="true" /> 创建技能
            </button>
            <button type="button" className="op-list__new" onClick={onImport} title="从 JSON 文件导入技能">
              <FileUp aria-hidden="true" /> 导入
            </button>
          </div>
        </div>
        <p className="op-section-head__hint">可复用分析能力 · 查看方法论，发起分析或 @ 提及</p>
        {cards.length === 0 ? (
          <p className="empty-state__hint">暂无技能。点击「创建技能」交给 Agent 引导创建，或「导入」已有的技能 JSON。</p>
        ) : (
          cards.map((card) => (
            <SkillCardItem key={card.id} card={card} onView={onView} onLaunch={onLaunch} />
          ))
        )}
      </div>
    </div>
  )
}

/** 校验并归一化一个导入的 JSON 技能定义（单对象或数组中的一项）。
 *  仅要求 name 非空即可导入；其余字段缺省时给出合理默认值。返回 null 表示该项不是有效技能。 */
function normalizeImportedSkill(raw: unknown): Omit<PlanningSkill, 'id'> | null {
  if (!raw || typeof raw !== 'object') return null
  const record = raw as Record<string, unknown>
  const name = typeof record.name === 'string' ? record.name.trim() : ''
  if (!name) return null
  const detailRaw = (record.detail && typeof record.detail === 'object' ? record.detail : {}) as Record<string, unknown>
  const strArr = (value: unknown): string[] =>
    Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
  const dataRequirements = strArr(detailRaw.dataRequirements)
  const detail: SkillDetail = {
    dataRequirements,
    workflow: strArr(detailRaw.workflow),
    indicators: strArr(detailRaw.indicators),
    tools: strArr(detailRaw.tools),
  }
  return {
    name,
    description: typeof record.description === 'string' ? record.description : '暂无描述',
    prompt: typeof record.prompt === 'string' ? record.prompt : `帮我执行 ${name}。`,
    requiredData:
      typeof record.requiredData === 'string'
        ? record.requiredData
        : dataRequirements.join('，') || '待补充',
    detail,
  }
}

/** Skills 面板：统一「技能」分组（可执行 + 提示型同卡片） */
export function SkillPanel() {
  const [skills, setSkills] = useState<SkillSpec[]>([])
  const [detailSkill, setDetailSkill] = useState<SkillCard | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const promptSkills = useAppStore((s) => s.skills)
  const addSkill = useAppStore((s) => s.addSkill)
  const removeSkill = useAppStore((s) => s.removeSkill)
  const setDraftMessage = useAppStore((s) => s.setDraftMessage)
  const toggleAgent = useAppStore((s) => s.toggleAgent)
  const newConversation = useAppStore((s) => s.newConversation)
  const sendMessage = useAppStore((s) => s.sendMessage)

  const load = useCallback(async () => {
    try {
      setSkills(await fetchSkills())
    } catch {
      // 后端未启动：保持空态，下方提示
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  /** 合并后端 skill（SkillSpec）与前端本地 skill（PlanningSkill），统一卡片，按名称排序 */
  const cards: SkillCard[] = useMemo(() => {
    const backend = skills.map((skill) => ({
      id: skill.id,
      name: skill.name,
      description: skill.description ?? '',
      prompt: skill.core_question?.trim() || `帮我用「${skill.name}」技能做分析`,
      body: skill.body,
      local: false,
    }))
    const backendIds = new Set(skills.map((skill) => skill.id))
    const local = promptSkills
      .filter((p) => !backendIds.has(p.id))
      .map((p) => ({
        id: p.id,
        name: p.name,
        description: p.description,
        prompt: p.prompt,
        detail: p.detail,
        local: true,
      }))
    return [...backend, ...local].sort((a, b) => a.name.localeCompare(b.name, 'zh'))
  }, [skills, promptSkills])

  /** 发起分析：把触发语发给 Agent。可执行技能命中后若缺参数，由 Agent 追问补齐（不再前端填参）。 */
  const launch = (prompt: string) => {
    setDraftMessage(prompt)
    toggleAgent(true) // Agent 浮窗化：发起对话 = 弹起浮窗（不切左栏）
  }

  /** 「AI 创建」：开新会话 → 发简洁触发语，让 Planner 命中 skill-creator 并按方法论引导 */
  const aiCreate = () => {
    const creator = promptSkills.find((s) => s.id === 'skill-creator')
    const trigger = creator?.prompt
      ? '我想创建一个可复用的新技能，请按 skill-creator 方法论引导我。开始吧——先问我第一个问题。'
      : '我想创建一个可复用的新技能，请引导我完成技能创建。'
    newConversation() // 开新会话，避免污染当前会话上下文
    toggleAgent(true) // 弹起 Agent 浮窗
    void sendMessage(trigger)
  }

  const deleteLocal = (id: string) => {
    removeSkill(id)
    if (detailSkill?.id === id) setDetailSkill(null)
  }

  /** 「导入」：触发隐藏的文件选择框 */
  const importSkill = () => fileInputRef.current?.click()

  /** 读取用户选择的 JSON 文件，逐个归一化为本地技能并写入 store */
  const onImportFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = '' // 允许再次选择同一文件
    if (!file) return
    file
      .text()
      .then((text) => {
        const parsed: unknown = JSON.parse(text)
        const rawList = Array.isArray(parsed) ? parsed : [parsed]
        let imported = 0
        for (const raw of rawList) {
          const skill = normalizeImportedSkill(raw)
          if (skill) {
            addSkill(skill)
            imported += 1
          }
        }
        if (imported === 0) {
          window.alert('导入失败：未找到有效技能。JSON 需包含 name 字段。')
        } else {
          window.alert(`已导入 ${imported} 个技能。`)
        }
      })
      .catch(() => window.alert('导入失败：无法解析 JSON 文件。'))
  }

  return (
    <div className="panel-content skills-panel">
      <header className="panel-header">
        <h2 className="panel-header__title">技能</h2>
        <div className="panel-header__actions">
          <button type="button" className="panel-refresh" onClick={() => void load()} title="刷新">
            <RefreshCw aria-hidden="true" />
          </button>
          <PanelCollapseButton />
        </div>
      </header>

      <div className="tools-scroll scroll-area">
        <SkillList
          cards={cards}
          onView={setDetailSkill}
          onLaunch={launch}
          onAiCreate={aiCreate}
          onImport={importSkill}
        />
      </div>

      <input
        ref={fileInputRef}
        type="file"
        accept="application/json,.json"
        className="hidden-file-input"
        onChange={onImportFile}
        aria-hidden="true"
        tabIndex={-1}
      />

      {detailSkill && (
        <SkillDetailView
          card={detailSkill}
          onClose={() => setDetailSkill(null)}
          onLaunch={launch}
          onDelete={detailSkill.local ? () => deleteLocal(detailSkill.id) : undefined}
        />
      )}
    </div>
  )
}
