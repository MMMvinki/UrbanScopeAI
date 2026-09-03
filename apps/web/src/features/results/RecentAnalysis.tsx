import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { ClipboardList, Sparkles } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'
import type { ResultGroup } from './resultTypes'

/**
 * 「最近分析成果」（B demo）：分析完成时把摘要持久到 localStorage（防刷新丢），
 * 数据主视图顶部展示最近一次主题/模块成果，可一键跳回「成果」对应组；完成时有 toast。
 * 只读 structured 成果数据（resultGroups），不新起数据通道、不改 useAppStore。
 */

const LS_KEY = 'urbanscope.recent-analysis'

interface RecentSummary {
  groupId: string
  title: string
  time: string
  widgets: number
  layers: number
  charts: number
  highlight: string
}

function summarize(g: ResultGroup): RecentSummary {
  let layers = 0
  let charts = 0
  let highlight = ''
  for (const w of g.widgets ?? []) {
    if (w.widgetType === 'map') layers += (w.data?.layerIds?.length ?? 0)
    else if (w.widgetType === 'chart' || w.widgetType === 'image') charts += 1
    if (!highlight && w.widgetType === 'metric' && (w.value || w.subtitle)) {
      highlight = [w.value, w.subtitle].filter(Boolean).join(' · ')
    }
  }
  return {
    groupId: g.id,
    title: g.title || '分析成果',
    time: new Date().toLocaleString('zh-CN', { hour12: false }),
    widgets: g.widgets?.length ?? 0,
    layers,
    charts,
    highlight,
  }
}

function readStored(): RecentSummary | null {
  try {
    const raw = localStorage.getItem(LS_KEY)
    return raw ? (JSON.parse(raw) as RecentSummary) : null
  } catch {
    return null
  }
}

function storeSummary(s: RecentSummary | null): void {
  try {
    if (s) localStorage.setItem(LS_KEY, JSON.stringify(s))
    else localStorage.removeItem(LS_KEY)
  } catch {
    /* localStorage 不可用时静默（不阻塞主流程） */
  }
}

const toastStyle: CSSProperties = {
  position: 'fixed',
  right: 18,
  bottom: 24,
  zIndex: 1200,
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  padding: '10px 14px',
  background: 'rgba(15, 23, 42, 0.92)',
  color: '#fff',
  borderRadius: 10,
  boxShadow: '0 6px 24px rgba(0,0,0,0.25)',
  fontSize: 13,
  cursor: 'pointer',
}

/** 全局：分析成果产生时 toast（点它回成果组），并持久化「最近分析成果」。 */
export function AnalysisToast() {
  const groups = useAppStore((s) => s.resultGroups)
  const focusResultGroup = useAppStore((s) => s.focusResultGroup)
  const [visible, setVisible] = useState(false)
  const [top, setTop] = useState<ResultGroup | null>(null)
  const firstRun = useRef(true)

  useEffect(() => {
    const g = groups[0]
    if (firstRun.current) {
      firstRun.current = false
      if (g) storeSummary(summarize(g))
      return
    }
    if (g) {
      storeSummary(summarize(g))
      setTop(g)
      setVisible(true)
      const t = setTimeout(() => setVisible(false), 6000)
      return () => clearTimeout(t)
    }
  }, [groups])

  if (!visible || !top) return null
  return (
    <button
      type="button"
      style={toastStyle}
      onClick={() => {
        setVisible(false)
        focusResultGroup(top.id)
      }}
    >
      <Sparkles size={16} aria-hidden="true" />
      分析完成 · 已生成 {top.widgets?.length ?? 0} 项成果，点此查看
    </button>
  )
}

/** 数据主视图顶部的「最近分析成果」卡（本地持久化，刷新不丢）。 */
export function RecentAnalysisCard() {
  const groups = useAppStore((s) => s.resultGroups)
  const focusResultGroup = useAppStore((s) => s.focusResultGroup)
  const stored = readStored()
  const liveTop = groups[0] ?? null
  const summary = stored ?? (liveTop ? summarize(liveTop) : null)
  const fresh = liveTop && stored && liveTop.id !== stored.groupId

  if (!summary) {
    return (
      <div
        style={{
          margin: 10,
          padding: '10px 12px',
          borderRadius: 10,
          background: 'rgba(241,245,249,0.6)',
          fontSize: 12,
          color: '#475569',
        }}
      >
        完成一次分析后，「最近分析成果」会显示在这里。
      </div>
    )
  }

  return (
    <div
      style={{
        margin: 10,
        padding: '10px 12px',
        borderRadius: 10,
        border: fresh ? '1px solid #f59e0b' : '1px solid #e2e8f0',
        background: '#fff',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
        <ClipboardList size={14} aria-hidden="true" style={{ color: '#0f766e' }} />
        <span style={{ fontSize: 12, fontWeight: 600, color: '#0f172a' }}>最近分析成果</span>
        {fresh && (
          <span
            style={{
              marginLeft: 'auto',
              fontSize: 10,
              color: '#b45309',
              background: '#fef3c7',
              padding: '1px 6px',
              borderRadius: 999,
            }}
          >
            有更新
          </span>
        )}
      </div>
      <div style={{ fontSize: 13, fontWeight: 600, color: '#1e293b' }}>{summary.title}</div>
      <div style={{ fontSize: 12, color: '#64748b', marginTop: 4 }}>
        成果 {summary.widgets} 项（图层 {summary.layers} · 图表 {summary.charts}）
        {summary.highlight ? ` · ${summary.highlight}` : ''}
      </div>
      <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 4 }}>
        {summary.time}
        {liveTop && liveTop.id !== summary.groupId ? '（本组已不在会话内，刷新后回看以本地为准）' : ''}
      </div>
      {liveTop && liveTop.id === summary.groupId && (
        <button
          type="button"
          onClick={() => focusResultGroup(summary.groupId)}
          style={{
            marginTop: 8,
            padding: '4px 10px',
            borderRadius: 8,
            border: '1px solid #0f766e',
            background: '#0f766e',
            color: '#fff',
            fontSize: 12,
            cursor: 'pointer',
          }}
        >
          查看分析成果
        </button>
      )}
    </div>
  )
}
