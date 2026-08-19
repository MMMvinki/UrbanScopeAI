import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'
import type { GraduatedSpec } from './layerTypes'

const methodLabel: Record<string, string> = {
  quantile: '分位数',
  'equal-interval': '等距',
  jenks: '自然间断点',
  'geometric-interval': '几何间隔',
  stddev: '标准差',
  manual: '手动',
}

/** 数值格式化：整数直接千分位，小数保留 2 位 */
function fmt(value: number): string {
  return Number.isInteger(value) ? value.toLocaleString('en-US') : String(Math.round(value * 100) / 100)
}

/**
 * ArcGIS 式分级图例：图层应用了按字段分层设色时，在图层行下方展开色块 + 数值区间。
 * 点击表头可收起/展开（「下拉」）。纯前端，读 store 的 layerStyles。
 */
export function LayerLegend({ layerId }: { layerId: string }) {
  const layer = useAppStore((s) => s.layers.find((item) => item.id === layerId))
  const style = useAppStore((s) => s.layerStyles[layerId])
  const [open, setOpen] = useState(true)

  const graduated: GraduatedSpec | undefined =
    style?.styleMode === 'graduated' ? style.visualVariables?.graduated : undefined

  if (!layer || !graduated) return null

  const { field, method, classes, palette, breaks } = graduated
  // 级数 = 断点数 + 1；palette 可能比级数短，末档复用最后一个色
  const count = Math.min(breaks.length + 1, classes)
  const rows = Array.from({ length: count }, (_, i) => {
    let range: string
    if (i === 0) range = `≤ ${fmt(breaks[0])}`
    else if (i === count - 1) range = `> ${fmt(breaks[breaks.length - 1])}`
    else range = `${fmt(breaks[i - 1])} – ${fmt(breaks[i])}`
    const color = palette[i] ?? palette[palette.length - 1] ?? layer.color
    return { color, range }
  })

  return (
    <div className="layer-legend">
      <button
        type="button"
        className="layer-legend__head"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label={`${layer.name} 分级图例`}
      >
        {open ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
        <span>
          按「{field}」分 {count} 级（{methodLabel[method] ?? method}）
        </span>
      </button>
      {open && (
        <ul className="layer-legend__rows">
          {rows.map((row, index) => (
            <li key={index}>
              <span className="layer-legend__swatch" style={{ backgroundColor: row.color }} aria-hidden="true" />
              <span className="layer-legend__range">{row.range}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
