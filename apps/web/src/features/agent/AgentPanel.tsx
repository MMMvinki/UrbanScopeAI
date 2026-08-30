import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { HeartPulse, MapPin, Paperclip, Send, Square, Upload, X } from 'lucide-react'
import { ChatMessage } from './ChatMessage'
import { TypingFooter } from './TypingFooter'
import { TOOL_LABELS } from './ProcessRows'
import { useAppStore } from '../../store/useAppStore'
import { stageUpload } from '../../lib/api'

interface AgentPanelProps {
  /** 浮窗模式的关闭按钮（左栏底部 Agent 按钮 / 浮窗右上角 X 关闭） */
  onClose?: () => void
}

/** 引导态案例：3 个可一键运行的真实验证场景（P0） */
const GUIDED_CASES = [
  {
    id: 'osm',
    icon: MapPin,
    title: 'OSM 取数',
    desc: '从 OpenStreetMap 下载深圳福田会展中心周边建筑，加载为地图图层',
    action: 'send',
    prompt:
      '帮我从 OpenStreetMap 下载深圳福田会展中心周边的建筑数据，并加载为地图图层。',
  },
  {
    id: 'upload',
    icon: Upload,
    title: '上传 CSV 转图层',
    desc: '上传一份 CSV / GeoJSON，转成地图图层并自动做概览分析',
    action: 'upload',
    prompt:
      '请对刚上传的数据图层做概览分析（要素数、字段、几何类型），并加载为地图图层。',
  },
  {
    id: 'health',
    icon: HeartPulse,
    title: '城市体检',
    desc: '对当前研究范围做城市体检，输出体检报告',
    action: 'send',
    prompt: '对当前研究范围做城市体检分析。',
  },
] as const

/** @ 提及目标：合并可执行 + SKILL.md 驱动 skill，去重按 id */
interface MentionTarget {
  id: string
  name: string
  description: string
  kind: 'prompt' | 'executable'
  promptText: string
}

/** 光标前的 @token（token 为字母/数字/下划线/中文），要求 @ 前不是单词字符（避免邮箱误触发） */
const MENTION_RE = /@([\p{L}\p{N}_-]*)$/u
const MENTION_FULL_RE = /@([\p{L}\p{N}_-]+)/gu

const ACCEPTED = '.csv,.xlsx,.tsv,.txt,.geojson,.json,.gpkg,.shp,.zip'
const SUPPORTED_EXT = ['csv', 'xlsx', 'tsv', 'txt', 'geojson', 'json', 'gpkg', 'shp', 'zip']

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function AgentPanel({ onClose }: AgentPanelProps = {}) {
  const messages = useAppStore((s) => s.messages)
  const sendMessage = useAppStore((s) => s.sendMessage)
  const draft = useAppStore((s) => s.draftMessage)
  const setDraft = useAppStore((s) => s.setDraftMessage)
  const openUploadModal = useAppStore((s) => s.openUploadModal)
  const allSkills = useAppStore((s) => s.allSkills)
  const promptSkills = useAppStore((s) => s.skills)
  const abortAgentRun = useAppStore((s) => s.abortAgentRun)

  // 是否有 Agent 运行中（loading=规划 / streaming=执行）：发送按钮切「停止」
  const isRunning = useMemo(
    () => messages.some((m) => m.status === 'streaming' || m.status === 'loading'),
    [messages],
  )

  // 底部工作状态（TypingFooter）：只标「进行中工具/代码」这类气泡细行没单独呈现的活动。
  // 「正在理解问题… / 思考中…」这类通用思考状态已由消息气泡的思考行承担（含实时计时），
  // 底部不再重复，避免一屏出现两个「正在思考」。
  const activeLabel = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const msg = messages[i]
      if (msg.role !== 'agent') continue
      if (msg.status === 'loading') continue // 气泡思考行已显示「正在理解问题…」
      if (msg.status !== 'streaming') continue
      const blocks = msg.blocks ?? []
      for (let j = blocks.length - 1; j >= 0; j--) {
        const block = blocks[j]
        if (block.type === 'tool' && block.status === 'running') {
          return `正在${TOOL_LABELS[block.name] ?? block.name}…`
        }
        if (block.type === 'code' && block.status === 'running') return '正在执行代码…'
      }
      // 无进行中的工具/代码：气泡思考行已显示「思考中…」，底部不重复
    }
    return null
  }, [messages])

  const runCase = (entry: (typeof GUIDED_CASES)[number]) => {
    if (entry.action === 'upload') {
      // 案例 2：先打开上传弹窗，上传成功后自动发起概览分析
      openUploadModal(entry.prompt)
      return
    }
    sendMessage(entry.prompt)
  }

  const [input, setInput] = useState('')
  const inputRef = useRef<HTMLTextAreaElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  // 已上传但尚未发送的附件（Codex 式：上传不立刻发送，可补文字后点发送一起发出；
  // 上传只暂存文件，不注册图层/不上地图，等 Agent 真正要用时才经 register_uploaded_file 创建）
  const [attachments, setAttachments] = useState<
    Array<{ stagedId: string; name: string; path: string }>
  >([])

  // @ 提及状态：光标前正在输入的 token；picker=true 为工具栏 @ 按钮呼出的全量面板（无 @ 前缀）
  const [mention, setMention] = useState<{ token: string; start: number; picker?: boolean } | null>(null)
  const [mentionActive, setMentionActive] = useState(0)
  const mentionPopRef = useRef<HTMLDivElement>(null)
  const mentionBtnRef = useRef<HTMLButtonElement>(null)

  // 点击弹窗以外（含空白、输入区、其他控件）→ 关闭 @ 弹窗；弹窗与 @ 按钮自身除外
  useEffect(() => {
    if (!mention) return
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Node
      if (mentionPopRef.current?.contains(target)) return
      if (mentionBtnRef.current?.contains(target)) return
      setMention(null)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [mention])

  // 输入区高度：随内容自动增高/收缩（min 42px ~ max 200px，超出内部滚动）
  const autosizeInput = useCallback(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.max(42, Math.min(el.scrollHeight, 200))}px`
  }, [])
  // useLayoutEffect 在 React commit 后、paint 前测量，值已提交无竞态（rAF 在清空/快速输入时测得偏大）
  useLayoutEffect(() => {
    autosizeInput()
  }, [input, autosizeInput])

  // @ 提及数据源：allSkills（含可执行）+ store.skills（SKILL.md 驱动，toPlanningSkill 后的 prompt）
  const mentionSkills = useMemo<MentionTarget[]>(() => {
    const map = new Map<string, MentionTarget>()
    for (const skill of allSkills) {
      map.set(skill.id, {
        id: skill.id,
        name: skill.name,
        description: skill.description ?? '',
        kind: skill.kind,
        promptText: skill.core_question ?? '',
      })
    }
    for (const skill of promptSkills) {
      const existing = map.get(skill.id)
      if (existing) {
        if (!existing.promptText) existing.promptText = skill.prompt
        continue
      }
      map.set(skill.id, {
        id: skill.id,
        name: skill.name,
        description: skill.description,
        kind: 'prompt',
        promptText: skill.prompt,
      })
    }
    return [...map.values()]
  }, [allSkills, promptSkills])

  const mentionCandidates = useMemo(() => {
    if (!mention) return []
    const q = mention.token.toLowerCase()
    return mentionSkills
      .filter(
        (skill) =>
          !q ||
          skill.id.toLowerCase().includes(q) ||
          skill.name.toLowerCase().includes(q) ||
          skill.description.toLowerCase().includes(q),
      )
      .slice(0, 8)
  }, [mention, mentionSkills])

  // 来自分析页的 Skill / Pattern 草稿
  useEffect(() => {
    if (draft) {
      setInput(draft)
      setDraft('')
      inputRef.current?.focus()
    }
  }, [draft, setDraft])

  // 流式输出期间监听对话区 DOM 变化，持续滚动到底部
  useEffect(() => {
    const list = listRef.current
    if (!list) return
    const scrollToBottom = () => list.scrollTo({ top: list.scrollHeight })
    const observer = new MutationObserver(scrollToBottom)
    observer.observe(list, { childList: true, subtree: true, characterData: true })
    scrollToBottom()
    return () => observer.disconnect()
  }, [])

  /** 检测光标前的 @token */
  const detectMention = (value: string, caret: number): { token: string; start: number } | null => {
    if (caret <= 0) return null
    const before = value.slice(0, caret)
    const m = MENTION_RE.exec(before)
    if (!m) return null
    const prevChar = before[m.index - 1]
    if (prevChar && /[\p{L}\p{N}]/u.test(prevChar)) return null // 邮箱等误触发
    return { token: m[1], start: m.index + 1 }
  }

  /** 把消息里的 @token 解析为技能路由：SKILL.md 驱动前置方法论，可执行追加执行提示 */
  const resolveMentions = (content: string): string => {
    const found: MentionTarget[] = []
    let m: RegExpExecArray | null
    MENTION_FULL_RE.lastIndex = 0
    while ((m = MENTION_FULL_RE.exec(content))) {
      const token = m[1]
      const target = mentionSkills.find((skill) => skill.id === token || skill.name === token)
      if (target && !found.some((f) => f.id === target.id)) found.push(target)
    }
    if (found.length === 0) return content
    let cleaned = content
    for (const target of found) {
      cleaned = cleaned
        .replace(new RegExp(`@${escapeRegExp(target.id)}`, 'g'), '')
        .replace(new RegExp(`@${escapeRegExp(target.name)}`, 'g'), '')
        .trim()
    }
    const prompts = found
      .filter((target) => target.kind === 'prompt' && target.promptText)
      .map((target) => target.promptText.trim())
    const routes = found
      .filter((target) => target.kind === 'executable')
      .map(
        (target) =>
          `用户 @ 了可执行技能「${target.name}」（${target.id}），请优先用 execute_skill_script 执行它。`,
      )
    const parts: string[] = []
    if (prompts.length > 0) parts.push(prompts.join('\n\n'))
    if (cleaned) parts.push(cleaned)
    if (routes.length > 0) parts.push(routes.join('\n'))
    return parts.join('\n\n')
  }

  /** 工具栏 @ 按钮（toggle）：光标移到末尾，弹出全量可 @ 技能面板；已弹窗时再点关闭 */
  const openMentionPicker = () => {
    if (mention) {
      setMention(null)
      return
    }
    const el = inputRef.current
    if (el) {
      el.focus()
      const len = input.length
      el.setSelectionRange(len, len)
    }
    setMention({ token: '', start: input.length, picker: true })
    setMentionActive(0)
  }

  const selectMention = (target: MentionTarget) => {
    if (!mention) return
    let next: string
    let pos: number
    if (mention.picker) {
      // 工具栏 @ 按钮呼出：直接在末尾插入 @名称
      next = `${input}@${target.name} `
      pos = next.length
    } else {
      // 行内 @token：替换光标前的 token 为 @名称
      const before = input.slice(0, mention.start - 1) // 含 @ 前的部分
      const after = input.slice(mention.start + mention.token.length)
      next = `${before}@${target.name} ${after}`
      pos = mention.start - 1 + 1 + target.name.length + 1
    }
    setInput(next)
    setMention(null)
    requestAnimationFrame(() => {
      const el = inputRef.current
      if (el) {
        el.focus()
        el.setSelectionRange(pos, pos)
      }
    })
  }

  const handleSend = () => {
    const content = input.trim()
    if (!content && attachments.length === 0) return
    // Codex 式：附件 + 文字一起组成待发送内容，附件上下文在前、用户文字在后。
    // 文件只是暂存（data/uploads/），尚未注册图层——需要 Agent 真正用时再按需创建。
    const uploadParts = attachments.map(
      (attachment) =>
        `我上传了文件「${attachment.name}」，已暂存到工作区 ${attachment.path}（尚未注册为地图图层）。` +
        `请先读取该文件了解数据结构；如果要用它做地图分析，请先用 register_uploaded_file 把它注册为图层，再继续：`,
    )
    const resolved = resolveMentions(content)
    const parts = [...uploadParts]
    if (resolved) parts.push(resolved)
    sendMessage(parts.join('\n\n'))
    setInput('')
    setAttachments([])
    setMention(null)
  }

  const removeAttachment = (stagedId: string) => {
    setAttachments((prev) => prev.filter((attachment) => attachment.stagedId !== stagedId))
  }

  /** 附件暂存：只挂到待发送附件列表（不注册图层/不上地图，无需刷新图层）；不支持类型内联提示 */
  const handleFileChosen = async (file: File | undefined) => {
    if (!file || uploading) return
    const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
    if (!SUPPORTED_EXT.includes(ext)) {
      setUploadError(
        `暂不支持 ${ext.toUpperCase()} 文件。支持 CSV/XLSX/GeoJSON/GPKG/SHP(zip)；Word/PDF 请转 CSV/GeoJSON 再上传。`,
      )
      if (fileRef.current) fileRef.current.value = ''
      return
    }
    setUploading(true)
    setUploadError(null)
    try {
      const result = await stageUpload(file)
      setAttachments((prev) => [
        ...prev,
        { stagedId: result.staged_id, name: result.original_name, path: result.path },
      ])
    } catch (error) {
      setUploadError((error as Error).message)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  return (
    <div className="panel-content agent-panel">
      <header className="panel-header">
        <h2 className="panel-header__title">Agent</h2>
        <div className="panel-header__actions">
          {onClose && (
            <button
              type="button"
              className="icon-button icon-button--sm"
              onClick={() => onClose?.()}
              aria-label="关闭 Agent 浮窗"
              title="关闭浮窗"
            >
              <X aria-hidden="true" />
            </button>
          )}
          <span className="badge badge--success">已就绪</span>
        </div>
      </header>

      <div className="chat-list scroll-area" ref={listRef}>
        {messages.length === 0 ? (
          <div className="guided-state">
            <p className="guided-state__title">你好，我是城市分析 Agent</p>
            <p className="guided-state__hint">
              可以指挥我取数、分析并直接操作地图。输入 @ 可提及技能。选择一个场景开始：
            </p>
            <div className="guided-cases">
              {GUIDED_CASES.map((entry) => {
                const Icon = entry.icon
                return (
                  <button
                    type="button"
                    key={entry.id}
                    className="guided-case"
                    onClick={() => runCase(entry)}
                  >
                    <span className="guided-case__icon">
                      <Icon aria-hidden="true" />
                    </span>
                    <span className="guided-case__text">
                      <span className="guided-case__title">{entry.title}</span>
                      <span className="guided-case__desc">{entry.desc}</span>
                    </span>
                  </button>
                )
              })}
            </div>
          </div>
        ) : (
          messages.map((message) => <ChatMessage key={message.id} message={message} />)
        )}
        <TypingFooter label={activeLabel} />
      </div>

      {uploadError && <p className="chat-input__error">{uploadError}</p>}

      {/* 待发送附件（上传后不立刻发送，点击发送才随消息一起发出） */}
      {attachments.length > 0 && (
        <div className="chat-attachments">
          {attachments.map((attachment) => (
            <span key={attachment.stagedId} className="chat-attachment">
              <Paperclip aria-hidden="true" />
              <span className="chat-attachment__name" title={attachment.name}>
                {attachment.name}
              </span>
              <button
                type="button"
                className="chat-attachment__remove"
                onClick={() => removeAttachment(attachment.stagedId)}
                aria-label={`移除附件 ${attachment.name}`}
                title="移除附件"
              >
                <X aria-hidden="true" />
              </button>
            </span>
          ))}
        </div>
      )}

      <div className="chat-input">
        {mention && mentionCandidates.length > 0 && (
          <div className="mention-pop" ref={mentionPopRef} role="listbox" aria-label="提及技能">
            {mentionCandidates.map((skill, index) => (
              <button
                type="button"
                key={skill.id}
                role="option"
                aria-selected={index === mentionActive}
                className={
                  index === mentionActive
                    ? 'mention-pop__item is-active'
                    : 'mention-pop__item'
                }
                onMouseEnter={() => setMentionActive(index)}
                onClick={() => selectMention(skill)}
              >
                <span className="mention-pop__name">{skill.name}</span>
                <span className="mention-pop__desc">{skill.description}</span>
                <span className="mention-pop__kind">
                  {skill.kind === 'executable' ? '可执行' : '提示'}
                </span>
              </button>
            ))}
          </div>
        )}
        <textarea
          ref={inputRef}
          className="chat-input__field"
          placeholder="请输入想研究的规划问题，例如“帮我分析这个片区公共服务有什么问题”。输入 @ 或点下方 @ 可提及技能。"
          value={input}
          rows={1}
          onChange={(e) => {
            const value = e.target.value
            setInput(value)
            setMention(detectMention(value, e.target.selectionStart ?? value.length))
            setMentionActive(0)
          }}
          onKeyDown={(e) => {
            if (mention && mentionCandidates.length > 0) {
              if (e.key === 'ArrowDown') {
                e.preventDefault()
                setMentionActive((a) => (a + 1) % mentionCandidates.length)
                return
              }
              if (e.key === 'ArrowUp') {
                e.preventDefault()
                setMentionActive((a) => (a - 1 + mentionCandidates.length) % mentionCandidates.length)
                return
              }
              if (e.key === 'Enter') {
                e.preventDefault()
                selectMention(mentionCandidates[mentionActive])
                return
              }
              if (e.key === 'Escape') {
                setMention(null)
                return
              }
            }
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSend()
            }
          }}
        />
        {/* Codex 式工具栏：左 链接/@，右 发送/停止 */}
        <div className="chat-input__toolbar">
          <div className="chat-input__toolbar-left">
            <button
              type="button"
              className="chat-input__tool-button"
              aria-label="上传文件"
              title="上传文件（CSV/XLSX/GeoJSON/GPKG/SHP/zip）"
              disabled={uploading}
              onClick={() => fileRef.current?.click()}
            >
              <Paperclip aria-hidden="true" />
              <span>链接</span>
            </button>
            <button
              type="button"
              ref={mentionBtnRef}
              className="chat-input__tool-button chat-input__mention-btn"
              aria-label="提及技能"
              title="查看可 @ 的技能，选择后插入输入框"
              onClick={openMentionPicker}
            >
              <span className="chat-input__mention-glyph" aria-hidden="true">
                @
              </span>
            </button>
          </div>
          <div className="chat-input__toolbar-right">
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPTED}
              className="visually-hidden"
              onChange={(e) => void handleFileChosen(e.target.files?.[0])}
            />
            <button
              type="button"
              className={`button button--primary chat-input__send${isRunning ? ' is-stop' : ''}`}
              onClick={isRunning ? abortAgentRun : handleSend}
              disabled={!isRunning && ((!input.trim() && attachments.length === 0) || uploading)}
            >
              {isRunning ? <Square aria-hidden="true" /> : <Send aria-hidden="true" />}
              {isRunning ? '停止' : '发送'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
