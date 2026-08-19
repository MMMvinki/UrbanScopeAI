import { useRef, useState, type Ref } from 'react'
import { Check, Download, Eye, Locate, Maximize2, Table as TableIcon } from 'lucide-react'
import { API_BASE } from '../../lib/api'
import { useAppStore } from '../../store/useAppStore'
import { EChart, type EChartHandle } from './EChart'
import { WidgetPreviewModal } from './WidgetPreviewModal'
import type {
  ResultWidget as ResultWidgetModel,
  WidgetSeries,
} from './resultTypes'

export const widgetTypeLabel: Record<ResultWidgetModel['widgetType'], string> = {
  metric: '指标',
  chart: '图表',
  list: '清单',
  table: '表格',
  map: '图层',
  image: '图片',
}

function MetricValue({ widget }: { widget: ResultWidgetModel }) {
  return (
    <div className="result-widget__metric">
      <div className="result-widget__value">
        {widget.value}
        {widget.unit && <span className="result-widget__unit">{widget.unit}</span>}
      </div>
      {widget.subtitle && <div className="result-widget__subtitle">{widget.subtitle}</div>}
    </div>
  )
}

function ChartValue({
  widget,
  chartHeight,
  chartRef,
}: {
  widget: ResultWidgetModel
  chartHeight?: number
  /** 导出 PNG 用：仅在成果卡内挂载（预览弹窗不导出） */
  chartRef?: Ref<EChartHandle>
}) {
  // 交互式图表：后端给的是 ECharts option → ECharts 渲染（悬浮/缩放/动画，一卡一图）
  if (widget.data?.option) {
    return (
      <EChart
        ref={chartRef}
        option={widget.data.option}
        height={chartHeight ?? widget.data.height ?? 320}
      />
    )
  }
  // 旧版指标对比：简化 CSS 横向条形
  const series = widget.data?.series ?? []
  const max = Math.max(1, ...series.map((item) => item.value))
  return (
    <div className="result-chart">
      {series.map((item) => (
        <div className="result-chart__row" key={item.name}>
          <span className="result-chart__name">{item.name}</span>
          <div className="result-chart__bar-track">
            <div
              className="result-chart__bar"
              style={{ width: `${Math.max(12, (item.value / max) * 100)}%` }}
            >
              {item.value}%
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function TableValue({ widget }: { widget: ResultWidgetModel }) {
  const rows = widget.data?.rows ?? []
  if (rows.length === 0) return <div className="result-widget__empty">暂无表格数据</div>
  const columns = Object.keys(rows[0])
  return (
    <div className="result-table-wrap">
      <table className="result-table">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column}>{column}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column}>{row[column]}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function ImageValue({ widget }: { widget: ResultWidgetModel }) {
  const [broken, setBroken] = useState(false)
  const url = widget.data?.url
  if (!url) return <div className="result-widget__empty">暂无图片数据</div>
  if (broken) return <div className="result-widget__empty">图表加载失败（静态资源不可达）</div>
  return (
    <div className="result-image">
      <img src={`${API_BASE}${url}`} alt={widget.title} loading="lazy" onError={() => setBroken(true)} />
    </div>
  )
}

function ListValue({ widget }: { widget: ResultWidgetModel }) {
  const items = widget.data?.items ?? []
  if (items.length === 0) return <div className="result-widget__empty">暂无清单数据</div>
  return (
    <ul className="result-list">
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  )
}

function MapValue({ widget }: { widget: ResultWidgetModel }) {
  return (
    <div className="result-map">
      <div className="result-map__hint">结果图层已生成并同步到地图</div>
      <div className="result-map__layers">
        {(widget.data?.layerIds ?? []).map((layerId) => (
          <span className="mini-action" key={layerId}>
            {layerId}
          </span>
        ))}
      </div>
    </div>
  )
}

/** 各类型成果的取值渲染：成果卡与预览弹窗共用（预览弹窗传大 chartHeight） */
export function ResultWidgetValue({
  widget,
  chartHeight,
}: {
  widget: ResultWidgetModel
  chartHeight?: number
}) {
  return (
    <>
      {widget.widgetType === 'metric' && <MetricValue widget={widget} />}
      {widget.widgetType === 'chart' && <ChartValue widget={widget} chartHeight={chartHeight} />}
      {widget.widgetType === 'table' && <TableValue widget={widget} />}
      {widget.widgetType === 'list' && <ListValue widget={widget} />}
      {widget.widgetType === 'image' && <ImageValue widget={widget} />}
      {widget.widgetType === 'map' && <MapValue widget={widget} />}
    </>
  )
}

/* ---------- 导出工具 ---------- */

/** 标题 → 安全文件名（保留中文与字母数字） */
function safeFilename(title: string, ext: string): string {
  const base = title.replace(/[^\w一-龥-]+/g, '_').replace(/^_+|_+$/g, '') || 'result'
  return `${base}${ext}`
}

function triggerDownload(data: string | Blob, filename: string) {
  const url = typeof data === 'string' ? data : URL.createObjectURL(data)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  if (typeof data !== 'string') URL.revokeObjectURL(url)
}

/** CSV：BOM 前缀（Excel 中文不乱码）+ 字段引号转义 */
function toCsv(fields: string[], rows: Array<Array<string | number>>): string {
  const esc = (cell: string): string => {
    const normalized = cell.replace(/"/g, '""')
    return /[",\n]/.test(normalized) ? `"${normalized}"` : normalized
  }
  const lines = [fields.map(esc).join(',')]
  for (const row of rows) {
    lines.push(row.map((cell) => esc(String(cell ?? ''))).join(','))
  }
  return '﻿' + lines.join('\r\n')
}

/** 旧式 CSS 条形图 → 内联 SVG 字符串（导出用） */
function barsToSvg(series: WidgetSeries[]): string {
  const width = 600
  const barHeight = 36
  const gap = 14
  const padLeft = 110
  const trackWidth = width - padLeft - 16
  const max = Math.max(1, ...series.map((item) => item.value))
  const lines = series.map((item, index) => {
    const y = 24 + index * (barHeight + gap)
    const barW = Math.max(12, (item.value / max) * trackWidth)
    const name = item.name.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    const value = Math.round(item.value)
    return (
      `<text x="6" y="${y + barHeight / 2 + 4}" font-family="sans-serif" font-size="12" fill="#475569">${name}</text>` +
      `<rect x="${padLeft}" y="${y}" width="${barW}" height="${barHeight}" rx="3" fill="#0f172a"/>` +
      `<text x="${padLeft + barW + 6}" y="${y + barHeight / 2 + 4}" font-family="sans-serif" font-size="12" fill="#0f172a">${value}%</text>`
    )
  })
  const height = series.length * (barHeight + gap) + 32
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}">${lines.join('')}</svg>`
}

export function ResultWidget({ widget }: { widget: ResultWidgetModel }) {
  const setLayerVisible = useAppStore((s) => s.setLayerVisible)
  const setActiveNav = useAppStore((s) => s.setActiveNav)
  const focusLayer = useAppStore((s) => s.focusLayer)

  const [tableOpen, setTableOpen] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const chartRef = useRef<EChartHandle>(null)
  const hasLayer = widget.layerId != null
  const canExport =
    widget.widgetType === 'chart' ||
    widget.widgetType === 'table' ||
    widget.widgetType === 'list' ||
    widget.widgetType === 'image'

  const handleViewLayer = () => {
    if (!widget.layerId) return
    setLayerVisible(widget.layerId, true)
    setActiveNav('data')
  }

  const handleExport = () => {
    switch (widget.widgetType) {
      case 'chart': {
        // 优先 ECharts 实例导出 PNG；旧式 CSS 条形图 → SVG
        const dataUrl = chartRef.current?.getDataURL() ?? ''
        if (dataUrl) {
          triggerDownload(dataUrl, safeFilename(widget.title, '.png'))
        } else if (widget.data?.series?.length) {
          triggerDownload(
            new Blob([barsToSvg(widget.data.series)], { type: 'image/svg+xml' }),
            safeFilename(widget.title, '.svg'),
          )
        }
        break
      }
      case 'table': {
        const rows = widget.data?.rows ?? []
        if (rows.length === 0) return
        const columns = Object.keys(rows[0])
        triggerDownload(
          toCsv(
            columns,
            rows.map((row) => columns.map((column) => row[column])),
          ),
          safeFilename(widget.title, '.csv'),
        )
        break
      }
      case 'list': {
        const items = widget.data?.items ?? []
        if (items.length === 0) return
        triggerDownload(
          toCsv(['项'], items.map((item) => [item])),
          safeFilename(widget.title, '.csv'),
        )
        break
      }
      case 'image': {
        const url = widget.data?.url
        if (!url) return
        // fetch → blob：跨端口（web/api 不同源）也能强制下载
        void fetch(`${API_BASE}${url}`)
          .then((response) => response.blob())
          .then((blob) => triggerDownload(blob, safeFilename(widget.title, '.png')))
          .catch(() => window.open(`${API_BASE}${url}`, '_blank'))
        break
      }
    }
  }

  return (
    <article className="result-widget">
      <header className="result-widget__header">
        <h3 className="result-widget__title">{widget.title}</h3>
        <span className="badge badge--neutral">{widgetTypeLabel[widget.widgetType]}</span>
      </header>

      <div className="result-widget__body">
        {widget.widgetType === 'metric' && <MetricValue widget={widget} />}
        {widget.widgetType === 'chart' && <ChartValue widget={widget} chartRef={chartRef} />}
        {widget.widgetType === 'table' && <TableValue widget={widget} />}
        {widget.widgetType === 'list' && <ListValue widget={widget} />}
        {widget.widgetType === 'image' && <ImageValue widget={widget} />}
        {widget.widgetType === 'map' && <MapValue widget={widget} />}
      </div>

      {widget.evidence && widget.evidence.length > 0 && (
        <ul className="result-widget__evidence">
          {widget.evidence.map((item, index) => (
            <li key={index}>
              <Check aria-hidden="true" />
              {item}
            </li>
          ))}
        </ul>
      )}

      <footer className="result-widget__actions">
        <button type="button" className="mini-action" onClick={() => setPreviewOpen(true)}>
          <Maximize2 aria-hidden="true" />
          预览放大
        </button>
        {canExport && (
          <button type="button" className="mini-action" onClick={handleExport}>
            <Download aria-hidden="true" />
            导出
          </button>
        )}
        {hasLayer && (
          <>
            <button type="button" className="mini-action" onClick={handleViewLayer}>
              <Eye aria-hidden="true" />
              查看图层
            </button>
            <button
              type="button"
              className="mini-action"
              onClick={() => widget.layerId && focusLayer(widget.layerId)}
            >
              <Locate aria-hidden="true" />
              定位地图
            </button>
            <button type="button" className="mini-action" onClick={() => setTableOpen((open) => !open)}>
              <TableIcon aria-hidden="true" />
              打开表格
            </button>
          </>
        )}
      </footer>

      {tableOpen && hasLayer && <TableValue widget={widget} />}

      {previewOpen && <WidgetPreviewModal widget={widget} onClose={() => setPreviewOpen(false)} />}
    </article>
  )
}
