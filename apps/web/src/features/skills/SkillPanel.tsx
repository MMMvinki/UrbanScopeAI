import { useCallback, useEffect, useMemo, useState } from 'react'
import type { FormEvent, ReactNode } from 'react'
import {
  Boxes,
  ChevronRight,
  FileCode2,
  FilePlus,
  Play,
  RefreshCw,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react'
import {
  fetchScripts,
  fetchSkills,
  promoteScriptToSkill,
  type ScriptInfo,
  type SkillSpec,
} from '../../lib/api'
import { useAppStore } from '../../store/useAppStore'
import { PanelCollapseButton } from '../../components/PanelCollapseButton'
import type { PlanningSkill } from './planningSkills'

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

/* ---------------- 列表：技能（统一一组）+ 脚本货架 ---------------- */

function SkillList({
  cards,
  scripts,
  onView,
  onLaunch,
  onCreate,
  onAiCreate,
  onPromote,
  busyId,
}: {
  cards: SkillCard[]
  scripts: ScriptInfo[]
  onView: (card: SkillCard) => void
  onLaunch: (prompt: string) => void
  onCreate: () => void
  onAiCreate: () => void
  onPromote: (script: ScriptInfo) => void
  busyId: string | null
}) {
  return (
    <div className="op-list">
      <div className="op-list__section">
        <h3 className="op-list__heading">
          <Boxes aria-hidden="true" /> 技能
          <span className="op-list__hint">可复用分析能力 · 查看方法论，发起分析或 @ 提及</span>
          <button type="button" className="op-list__new" onClick={onAiCreate} title="让 Agent 按 skill-creator 方法论创建技能">
            <Sparkles aria-hidden="true" /> AI 创建
          </button>
          <button type="button" className="op-list__new" onClick={onCreate}>
            <FilePlus aria-hidden="true" /> 新建
          </button>
        </h3>
        {cards.length === 0 ? (
          <p className="empty-state__hint">
            暂无技能。点击「新建」创建一个，或让 Agent 跑一次分析后从脚本货架「提升为技能」沉淀。
          </p>
        ) : (
          cards.map((card) => (
            <SkillCardItem key={card.id} card={card} onView={onView} onLaunch={onLaunch} />
          ))
        )}
      </div>

      <div className="op-list__section">
        <h3 className="op-list__heading">
          <FileCode2 aria-hidden="true" /> 脚本货架
          <span className="op-list__hint">execute_code 每次执行自动归档，可一键提升为技能</span>
        </h3>
        {scripts.length === 0 ? (
          <p className="empty-state__hint">脚本库为空。让 Agent 用 execute_code 跑一次分析后会出现在这里。</p>
        ) : (
          scripts.map((script) => (
            <div className="op-script" key={script.name}>
              <div className="op-script__body">
                <span className="op-script__name">{script.name}</span>
                {script.description ? <span className="op-script__desc">{script.description}</span> : null}
                <span className="op-script__meta">
                  {script.semantic_name} · step {script.step ?? '-'}
                  {script.has_error ? ' · 有错误' : ''}
                </span>
              </div>
              <button
                type="button"
                className="op-script__promote"
                disabled={busyId === script.name}
                onClick={() => onPromote(script)}
                title="升级为可复用技能"
              >
                <Sparkles aria-hidden="true" /> {busyId === script.name ? '提升中…' : '提升为技能'}
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

const DEFAULT_WORKFLOW = ['数据准备', '指标计算 / 统计', '空间诊断', '结论生成']
const DEFAULT_TOOLS = ['list_datasets', 'get_dataset_schema', 'statistics']

/** 新建技能弹窗（前端本地，存 localStorage；与后端技能同卡片展示） */
function PromptSkillForm({ onClose }: { onClose: () => void }) {
  const addSkill = useAppStore((s) => s.addSkill)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [requiredData, setRequiredData] = useState('')
  const [prompt, setPrompt] = useState('')

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName) return
    addSkill({
      name: trimmedName,
      description: description.trim() || '暂无描述',
      requiredData: requiredData.trim() || '待补充',
      prompt: prompt.trim() || `帮我执行 ${trimmedName}。`,
      detail: {
        dataRequirements: requiredData
          .split(/[,，]/)
          .map((item) => item.trim())
          .filter(Boolean),
        workflow: DEFAULT_WORKFLOW,
        indicators: ['待补充'],
        tools: DEFAULT_TOOLS,
      },
    })
    onClose()
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <form
        className="modal modal--form"
        role="dialog"
        aria-modal="true"
        aria-label="新建技能"
        onClick={(event) => event.stopPropagation()}
        onSubmit={handleSubmit}
      >
        <header className="modal__header">
          <div className="modal__titles">
            <h3 className="modal__title">新建技能</h3>
            <p className="modal__desc">创建一个技能专题，将出现在技能面板与 Agent 中。</p>
          </div>
          <button type="button" className="icon-button icon-button--sm" onClick={onClose} aria-label="关闭">
            <X aria-hidden="true" />
          </button>
        </header>

        <div className="modal__body">
          <label className="form-field">
            <span className="form-field__label">名称 *</span>
            <input
              className="form-field__input"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="例如：社区养老设施评估"
              required
            />
          </label>
          <label className="form-field">
            <span className="form-field__label">描述</span>
            <textarea
              className="form-field__input"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder="这个技能做什么"
              rows={2}
            />
          </label>
          <label className="form-field">
            <span className="form-field__label">所需数据（逗号分隔）</span>
            <input
              className="form-field__input"
              value={requiredData}
              onChange={(event) => setRequiredData(event.target.value)}
              placeholder="设施点位，人口分布，研究范围"
            />
          </label>
          <label className="form-field">
            <span className="form-field__label">触发提示词</span>
            <textarea
              className="form-field__input"
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="发起分析时发送给 Agent 的问题"
              rows={2}
            />
          </label>
        </div>

        <footer className="modal__footer">
          <button type="button" className="button button--ghost button--sm" onClick={onClose}>
            取消
          </button>
          <span className="modal__footer-spacer" />
          <button type="submit" className="button button--primary button--sm">
            保存
          </button>
        </footer>
      </form>
    </div>
  )
}

/** Skills 面板：统一「技能」分组（可执行 + 提示型同卡片）+ 脚本货架 */
export function SkillPanel() {
  const [skills, setSkills] = useState<SkillSpec[]>([])
  const [scripts, setScripts] = useState<ScriptInfo[]>([])
  const [detailSkill, setDetailSkill] = useState<SkillCard | null>(null)
  const [creatingPrompt, setCreatingPrompt] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const promptSkills = useAppStore((s) => s.skills)
  const removeSkill = useAppStore((s) => s.removeSkill)
  const setDraftMessage = useAppStore((s) => s.setDraftMessage)
  const toggleAgent = useAppStore((s) => s.toggleAgent)
  const newConversation = useAppStore((s) => s.newConversation)
  const sendMessage = useAppStore((s) => s.sendMessage)

  const load = useCallback(async () => {
    try {
      const [skillsData, scriptsData] = await Promise.all([fetchSkills(), fetchScripts()])
      setSkills(skillsData)
      setScripts(scriptsData.scripts ?? [])
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

  const doPromote = async (script: ScriptInfo) => {
    setBusyId(script.name)
    try {
      await promoteScriptToSkill({ script_name: script.name, name: script.semantic_name || undefined })
      await load()
    } finally {
      setBusyId(null)
    }
  }

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
          scripts={scripts}
          onView={setDetailSkill}
          onLaunch={launch}
          onCreate={() => setCreatingPrompt(true)}
          onAiCreate={aiCreate}
          onPromote={doPromote}
          busyId={busyId}
        />
      </div>

      {detailSkill && (
        <SkillDetailView
          card={detailSkill}
          onClose={() => setDetailSkill(null)}
          onLaunch={launch}
          onDelete={detailSkill.local ? () => deleteLocal(detailSkill.id) : undefined}
        />
      )}
      {creatingPrompt && <PromptSkillForm onClose={() => setCreatingPrompt(false)} />}
    </div>
  )
}
