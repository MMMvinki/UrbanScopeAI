import { useMemo, useState } from 'react'
import { useAppStore } from '../../store/useAppStore'
import { buildSections } from '../analyze/analysisMapper'
import type { LegendGroup } from '../analyze/analysisMapper'
import { formatInt, formatNumber } from '../analyze/analysisMapper'

/**
 * 图上图例浮层（D2/D5）：右下角可收起，展示「当前分析模块」的图例组。
 * 数据与右侧成果栏同源（analysisMapper.buildSections → legendGroups），不改数字来源。
 * 无综合分析或当前模块无图例时不渲染。
 */
export function MapLegendOverlay() {
  const analysis = useAppStore((s) => s.lastComprehensiveAnalysis)
  const activeModuleId = useAppStore((s) => s.analysisMapModuleId)
  const [open, setOpen] = useState(true)

  const { title, groups } = useMemo<{ title?: string; groups: LegendGroup[] }>(() => {
    if (!analysis) return { groups: [] }
    const sections = buildSections(analysis)
    const completed = sections.filter((s) => s.status === 'completed')
    const target =
      (activeModuleId ? sections.find((s) => s.id === activeModuleId) : undefined) ??
      completed[completed.length - 1]
    return { title: target?.title, groups: target?.legendGroups ?? [] }
  }, [analysis, activeModuleId])

  if (!title || groups.length === 0) return null

  const foldLabel = open ? '收起图例' : '展开图例'
  return (
    <div
      style={{
        position: 'absolute',
        right: 12,
        bottom: 12,
        zIndex: 20,
        maxWidth: 236,
        minWidth: 160,
        background: 'rgba(255,255,255,0.94)',
        border: '1px solid #e2e8f0',
        borderRadius: 8,
        boxShadow: '0 1px 3px rgba(15,23,42,0.14)',
        padding: '6px 10px',
        fontSize: 12,
        color: '#334155',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 8,
          marginBottom: open ? 6 : 0,
        }}
      >
        <strong style={{ fontSize: 12, fontWeight: 600, color: '#0f172a' }}>{title}</strong>
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-label={foldLabel}
          title={foldLabel}
          style={{
            border: 'none',
            background: 'transparent',
            cursor: 'pointer',
            color: '#64748b',
            fontSize: 14,
            lineHeight: 1,
            padding: 2,
          }}
        >
          {open ? '▾' : '▸'}
        </button>
      </div>
      {open &&
        groups.map((group) => <LegendBlock key={group.title} group={group} />)}
    </div>
  )
}

function LegendBlock({ group }: { group: LegendGroup }) {
  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ fontWeight: 600, color: '#475569', marginBottom: 2 }}>{group.title}</div>
      {group.gradient ? (
        <>
          <div
            style={{
              height: 8,
              borderRadius: 4,
              background: `linear-gradient(to right, ${group.gradient.palette.join(',')})`,
            }}
            aria-hidden="true"
          />
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: 10,
              color: '#64748b',
              marginTop: 2,
            }}
          >
            <span>
              {group.gradient.minValue != null
                ? `${formatNumber(group.gradient.minValue)}${group.gradient.unit ?? ''}`
                : ''}
            </span>
            <span>
              {group.gradient.maxValue != null
                ? `${formatNumber(group.gradient.maxValue)}${group.gradient.unit ?? ''}`
                : ''}
            </span>
          </div>
        </>
      ) : null}
      {group.rows.map((row) => (
        <div
          key={row.code}
          style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 2 }}
        >
          <span
            style={{
              width: 14,
              height: 10,
              borderRadius: 2,
              flexShrink: 0,
              backgroundColor: row.color,
            }}
            aria-hidden="true"
          />
          <span style={{ flex: 1, color: '#334155' }}>{row.label}</span>
          {(row.cells ?? row.points) != null ? (
            <span style={{ color: '#64748b', fontVariantNumeric: 'tabular-nums' }}>
              {formatInt((row.cells ?? row.points) as number)}
            </span>
          ) : null}
        </div>
      ))}
    </div>
  )
}
