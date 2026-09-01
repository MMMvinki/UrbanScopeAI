import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'
import type { CategorizedSpec, GraduatedSpec } from './layerTypes'

const methodLabel: Record<string, string> = {
  quantile: '分位数',
  'equal-interval': '等距',
  jenks: '自然间断点',
  'geometric-interval': '几何间隔',
  stddev: '标准差',
  manual: '手动',
}

/** 双变量 LISA 簇码 → 语义中文（lisa_cluster 字段专用） */
const LISA_LABELS: Record<string, string> = {
  HH: '高-高集聚',
  HL: '高-低异常',
  LH: '低-高异常',
  LL: '低-低集聚',
  NS: '不显著',
}

/** 分类值 → 图例文本：LISA 簇带语义（「HH 高-高集聚」），其余字段直显值 */
function categoryLabel(field: string, value: string): string {
  if (field === 'lisa_cluster') {
    const meaning = LISA_LABELS[value]
    return meaning ? `${value} ${meaning}` : value
  }
  return value
}

/** 数值格式化：整数直接千分位，小数保留 2 位 */
function fmt(value: number): string {
  return Number.isInteger(value) ? value.toLocaleString('en-US') : String(Math.round(value * 100) / 100)
}

/**
 * ArcGIS 式分级图例：图层应用了按字段分层设色（graduated）或分类配色（categorized）时，
 * 在图层行下方展开色块 + 数值区间 / 类别说明。点击表头可收起/展开（「下拉」）。
 * 纯前端，读 store 的 layerStyles。
 */
export function LayerLegend({ layerId }: { layerId: string }) {
  const layer = useAppStore((s) => s.layers.find((item) => item.id === layerId))
  const style = useAppStore((s) => s.layerStyles[layerId])
  const [open, setOpen] = useState(true)

  const graduated: GraduatedSpec | undefined =
    style?.styleMode === 'graduated' ? style.visualVariables?.graduated : undefined
  const categorized: CategorizedSpec | undefined =
    style?.styleMode === 'categorized' ? style.visualVariables?.categorized : undefined

  if (!layer || (!graduated && !categorized)) return null

  // ── 分类配色图例（双变量 LISA / 按类别字段设色）──
  if (categorized) {
    const { field, classes } = categorized
    return (
      <div className="layer-legend">
        <button
          type="button"
          className="layer-legend__head"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          aria-label={`${layer.name} 分类图例`}
        >
          {open ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />}
          <span>
            按「{field}」分 {classes.length} 类
            {field === 'lisa_cluster' && ' · 双变量 LISA 簇'}
          </span>
        </button>
        {open && (
          <ul className="layer-legend__rows">
            {classes.map((item) => (
              <li key={item.value}>
                <span className="layer-legend__swatch" style={{ backgroundColor: item.color }} aria-hidden="true" />
                <span className="layer-legend__range">{categoryLabel(field, item.value)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    )
  }

  // 走到这里 categorized 分支已 return，但 TS 无法从「!graduated && !categorized」的提前返回收窄
  // graduated，显式兜底（避免解构 undefined 报 TS18048）
  if (!graduated) return null
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
