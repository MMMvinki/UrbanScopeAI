import { useEffect, useMemo, useRef, useState } from 'react'
import { X } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'
import { patchLayerStyle } from '../../lib/api'
import { ensureLayerGeojson, getLayerGeojson, hasLayerGeojson } from '../map/mapLayers'
import type { LayerStyle, VisualVariables } from './layerTypes'
import type { DemoFeatureCollection } from '../map/mapLayers'

interface StylePanelProps {
  layerId: string
  onClose: () => void
}

/** 色带预设（镜像后端 layer_style._COLOR_RAMPS，前端手动分档用） */
const PALETTES: Array<{ name: string; label: string; colors: string[] }> = [
  { name: 'viridis', label: 'Viridis', colors: ['#fde725', '#5ec962', '#21918c', '#3b528b', '#440154'] },
  { name: 'oranges', label: '暖橙 Oranges', colors: ['#feedde', '#fdbe85', '#fd8d3c', '#e6550d', '#a63603'] },
  { name: 'reds', label: '红 Reds', colors: ['#fee5d9', '#fcae91', '#fb6a4a', '#de2d26', '#a50f15'] },
  { name: 'blues', label: '蓝 Blues', colors: ['#eff3ff', '#bdd7e7', '#6baed6', '#3182bd', '#08519c'] },
  { name: 'greens', label: '绿 Greens', colors: ['#edf8e9', '#bae4b3', '#74c476', '#31a354', '#006d2c'] },
  { name: 'purples', label: '紫 Purples', colors: ['#f3e8ff', '#d8b4fe', '#a855f7', '#7e22ce', '#581c87'] },
  { name: 'spectral', label: '光谱 Spectral', colors: ['#9e0142', '#f46d43', '#ffffbf', '#66c2a5', '#5e4fa2'] },
]

/** quantile 分位数（升序数组） */
function quantile(sorted: number[], q: number): number {
  const pos = (sorted.length - 1) * q
  const base = Math.floor(pos)
  const rest = pos - base
  return base + 1 < sorted.length ? sorted[base] + rest * (sorted[base + 1] - sorted[base]) : sorted[base]
}

/** 确定性步长采样：大图层只取真实值子集，避免 Jenks O(k·n²) 卡死交互（镜像后端 _sample_values） */
function sampleValues(values: number[], limit: number): number[] {
  const n = values.length
  if (n <= limit) return values
  const step = Math.max(1, Math.floor(n / limit))
  const out: number[] = []
  for (let i = 0; i < n; i += step) out.push(values[i])
  return out
}

/** 自然间断点（Fisher-Jenks 一维，镜像后端 _jenks_breaks）：升序切 k 类，类内 SSE 最小 */
function jenksBreaks(values: number[], k: number): number[] {
  if (k < 2) return []
  const data = [...values].sort((a, b) => a - b)
  const n = data.length
  if (n < k) return []
  const uniq = Array.from(new Set(data))
  if (uniq.length <= k) return uniq.slice(0, -1) // 唯一值不足 k 类 → 返回唯一值边界
  const cum: number[] = [0]
  const cum2: number[] = [0]
  for (const v of data) {
    cum.push(cum[cum.length - 1] + v)
    cum2.push(cum2[cum2.length - 1] + v * v)
  }
  const sse = (left: number, right: number): number => {
    const count = right - left
    if (count <= 0) return Infinity
    const s = cum[right] - cum[left]
    const s2 = cum2[right] - cum2[left]
    return s2 - (s * s) / count
  }
  const INF = Infinity
  const dp: number[][] = Array.from({ length: k + 1 }, () => Array<number>(n + 1).fill(INF))
  const parent: number[][] = Array.from({ length: k + 1 }, () => Array<number>(n + 1).fill(0))
  for (let i = 1; i <= n; i++) dp[1][i] = sse(0, i)
  for (let j = 2; j <= k; j++) {
    for (let i = j; i <= n; i++) {
      let best = INF
      let bestIdx = 0
      for (let p = j - 1; p < i; p++) {
        const cand = dp[j - 1][p] + sse(p, i)
        if (cand < best) {
          best = cand
          bestIdx = p
        }
      }
      dp[j][i] = best
      parent[j][i] = bestIdx
    }
  }
  const boundaries: number[] = []
  let end = n
  for (let j = k; j >= 2; j--) {
    const start = parent[j][end]
    boundaries.push(data[start])
    end = start
  }
  boundaries.reverse()
  const result: number[] = []
  for (const b of boundaries) {
    if (result.length === 0 || b > result[result.length - 1]) result.push(b)
  }
  return result
}

/** 前端本地算分档断点（镜像后端 layer_style.compute_breaks：quantile/equal-interval/jenks/geometric-interval/stddev） */
function computeBreaks(values: number[], method: string, classes: number): number[] {
  const sorted = [...values].sort((a, b) => a - b)
  const n = Math.max(2, Math.min(classes, 12))
  if (sorted.length === 0) return []
  const lo = sorted[0]
  const hi = sorted[sorted.length - 1]
  if (method === 'equal-interval') {
    return Array.from({ length: n - 1 }, (_, i) => lo + ((hi - lo) * (i + 1)) / n)
  }
  // jenks O(k·n²)：样本上限收紧到 1000（与后端一致）
  const sample = sampleValues(sorted, method === 'jenks' ? 1000 : 2000)
  if (method === 'geometric-interval') {
    if (lo > 0) {
      const ratio = Math.pow(hi / lo, 1 / n)
      return Array.from({ length: n - 1 }, (_, i) => lo * Math.pow(ratio, i + 1))
    }
    // 含非正值 → 回落分位数
  }
  if (method === 'stddev') {
    const mean = sample.reduce((a, b) => a + b, 0) / sample.length
    const variance = sample.reduce((a, b) => a + (b - mean) ** 2, 0) / sample.length
    const std = Math.sqrt(variance)
    if (std > 0) {
      return Array.from({ length: n - 1 }, (_, i) =>
        Math.min(hi, Math.max(lo, mean + std * (i + 1 - n / 2))),
      )
    }
    // 常数分布（std=0）→ 回落等距
    return Array.from({ length: n - 1 }, (_, i) => lo + ((hi - lo) * (i + 1)) / n)
  }
  if (method === 'jenks') {
    const brk = jenksBreaks(sample, n)
    if (brk.length > 0) return brk
    // Fisher-Jenks 退化 → 回落分位数
  }
  return Array.from({ length: n - 1 }, (_, i) => quantile(sorted, (i + 1) / n))
}

/** 从图层真实几何读某字段的数值序列（绝不编造演示数据；无数值返回空数组） */
function numericValues(features: DemoFeatureCollection['features'], field: string): number[] {
  const out: number[] = []
  for (const feat of features) {
    const props = (feat.properties ?? {}) as Record<string, unknown>
    const v = props[field]
    if (typeof v === 'number') {
      if (Number.isFinite(v)) out.push(v)
    } else if (typeof v === 'string' && v.trim() !== '') {
      const num = Number(v)
      if (Number.isFinite(num)) out.push(num)
    }
  }
  return out
}

/** 图层样式面板：改色 / 填充+描边不透明度分离（改动 4）/ 描边 / 点半径 + 按字段分层设色（改动 3，真数据+新分档方法），改动即时生效 */
export function StylePanel({ layerId, onClose }: StylePanelProps) {
  const layer = useAppStore((s) => s.layers.find((item) => item.id === layerId))
  const style = useAppStore((s) => s.layerStyles[layerId])
  const geojsonTick = useAppStore((s) => s.geojsonTick)
  const updateLayerStyle = useAppStore((s) => s.updateLayerStyle)

  const [field, setField] = useState<string>(style?.visualVariables?.graduated?.field ?? '')
  const [method, setMethod] = useState<string>(style?.visualVariables?.graduated?.method ?? 'quantile')
  const [classes, setClasses] = useState<number>(style?.visualVariables?.graduated?.classes ?? 5)
  // 持久化：样式改动防抖 300ms 后 PATCH 后端。
  // 只持久化「本次用户改动的字段」增量，绝不把 store 里从后端合并来的历史残留
  // （如 opacity:0）原样写回侧车 —— 那是「改样式地图不变 + 清完又复活」的根因。
  const persistTimer = useRef<number | null>(null)
  const pendingRef = useRef<Record<string, unknown>>({})
  /** 统一入口：更新 store（地图即时重绘）+ 把改动并入待持久化增量 */
  const applyStyle = (changes: Partial<LayerStyle>) => {
    updateLayerStyle(layerId, changes)
    // undefined → null：JSON.stringify 会丢弃 undefined，必须显式 null 才能清掉后端旧值
    for (const [key, value] of Object.entries(changes)) {
      pendingRef.current[key] = value === undefined ? null : value
    }
    if (persistTimer.current) window.clearTimeout(persistTimer.current)
    persistTimer.current = window.setTimeout(() => {
      const patch = pendingRef.current
      pendingRef.current = {}
      if (Object.keys(patch).length === 0) return
      void patchLayerStyle(layerId, patch).catch(() => {
        /* 持久化失败不打断编辑 */
      })
    }, 300)
  }
  // 色带下拉默认值：优先匹配当前已应用的 palette 数组；匹配不到才用默认 viridis
  const appliedPalette = style?.visualVariables?.graduated?.palette
  const [paletteName, setPaletteName] = useState<string>(
    () => (appliedPalette && PALETTES.find((p) => p.colors.join() === appliedPalette.join())?.name) ?? 'viridis',
  )

  // 真实几何 + geojsonTick 驱动重算：惰性平台图层按需加载后，数值/字段立即刷新
  const geojson = useMemo(
    () => (layer ? getLayerGeojson(layer) : { type: 'FeatureCollection' as const, features: [] }),
    [layer, geojsonTick],
  )
  const features = geojson.features

  // 可用分档字段：声明字段（数值型）+ 从 geojson 首个要素推断 + 确有数值的字段
  const numericFields = useMemo(() => {
    const candidates = new Map<string, string>() // 字段名 → 类型标注
    for (const f of layer?.fields ?? []) candidates.set(f.name, f.type)
    const first = features[0]
    for (const key of Object.keys((first?.properties ?? {}) as Record<string, unknown>)) {
      if (!candidates.has(key)) candidates.set(key, 'number')
    }
    const out: Array<{ name: string; type: string }> = []
    for (const [name, type] of candidates) {
      // 声明为数值 或 推断字段确有数值 → 可分档
      if (/int|float|number|real|double|numeric/i.test(type) || numericValues(features, name).length > 0) {
        out.push({ name, type })
      }
    }
    return out
  }, [layer, features])

  // 打开样式面板时确保图层几何已加载：懒加载平台图层未点亮时地图为空，
  // 此时改填充色/透明度在地图上"没反应"，会误判成调不了。（栅格图层走瓦片，无 GeoJSON 可拉；
  // Table 图层无几何、不进地图渲染，只在打开属性表 PreviewModal 时按需取，不在样式面板误取）
  useEffect(() => {
    if (layer?.lazy && layer.kind !== 'raster' && layer.geometry !== 'Table' && !hasLayerGeojson(layerId)) {
      void ensureLayerGeojson(layerId).then((ok) => {
        if (ok) useAppStore.setState((s) => ({ geojsonTick: s.geojsonTick + 1 }))
      })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layerId])

  if (!layer) return null
  const isPoint = layer.geometry === 'Point'
  // 栅格图层（如 DEM 高程）：只有不透明度有意义，填充/描边颜色对栅格无效，隐藏避免"改了没反应"
  const isRaster = layer.kind === 'raster'
  const fillColor = style?.fillColor ?? layer.color
  const strokeColor = style?.strokeColor ?? layer.color
  // 改动 4：填充/描边不透明度分离；旧值 opacity 兼容（fill ?? opacity ?? 默认，stroke 默认跟随 fill）
  const baseOpacity = 0.8
  const fillOpacity = style?.fillOpacity ?? style?.opacity ?? baseOpacity
  const strokeOpacity = style?.strokeOpacity ?? style?.opacity ?? fillOpacity
  const rasterOpacity = style?.opacity ?? 0.85
  const strokeWidth = style?.strokeWidth ?? 1
  const radius = style?.radius ?? 5
  const isGraduated = style?.styleMode === 'graduated'

  const applyGraduated = () => {
    if (!field) return
    const palette = PALETTES.find((p) => p.name === paletteName)?.colors ?? PALETTES[1].colors
    const values = numericValues(features, field)
    if (values.length === 0) return
    const breaks = computeBreaks(values, method, classes)
    const visualVariables: VisualVariables = {
      renderer: 'graduated',
      graduated: { field, method, classes, palette, breaks },
    }
    applyStyle({ styleMode: 'graduated', visualVariables })
  }

  const clearGraduated = () => {
    applyStyle({ styleMode: 'single', visualVariables: undefined })
  }

  /** 手动改填充色 = 退出分级（ArcGIS 语义：单一符号化）；分档下 fillColor 会被档位色覆盖，改不动会让人困惑 */
  const applyFillColor = (value: string) => {
    if (isGraduated) applyStyle({ fillColor: value, styleMode: 'single', visualVariables: undefined })
    else applyStyle({ fillColor: value })
  }

  /**
   * 手动调不透明度（填充/描边各自独立）：滑块值必须真正生效，若此前 Agent 设过「透明度渐变」
   * （opacity_variable），渲染走字段渐变、整体不透明度被忽略，会出现「拖滑块地图不变」。
   * 这里手动调整时清掉透明度渐变，保留 graduated/size_variable，让滑块值接管。
   */
  const applyFillOpacity = (value: number) => {
    const vv = style?.visualVariables
    const changes: Partial<LayerStyle> = { fillOpacity: value }
    if (vv?.opacity_variable) {
      const { opacity_variable, ...rest } = vv
      changes.visualVariables = Object.keys(rest).length > 0 ? rest : undefined
    }
    applyStyle(changes)
  }

  const applyStrokeOpacity = (value: number) => {
    const vv = style?.visualVariables
    const changes: Partial<LayerStyle> = { strokeOpacity: value }
    if (vv?.opacity_variable) {
      const { opacity_variable, ...rest } = vv
      changes.visualVariables = Object.keys(rest).length > 0 ? rest : undefined
    }
    applyStyle(changes)
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal modal--sm" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="图层样式">
        <header className="modal__header">
          <div>
            <h3 className="modal__title">图层样式</h3>
            <p className="modal__desc">{layer.name}</p>
          </div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭">
            <X aria-hidden="true" />
          </button>
        </header>
        <div className="modal__body">
          {!isRaster && (
            <div className="form-field">
              <label className="form-field__label">填充颜色</label>
              <div className="color-row">
                <input
                  type="color"
                  className="color-input"
                  value={fillColor}
                  onChange={(event) => applyFillColor(event.target.value)}
                />
                <code className="color-value">{fillColor.toUpperCase()}</code>
              </div>
            </div>
          )}
          {isRaster ? (
            <div className="form-field">
              <label className="form-field__label">不透明度 {Math.round(rasterOpacity * 100)}%</label>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={rasterOpacity}
                onChange={(event) => applyStyle({ opacity: Number(event.target.value) })}
              />
            </div>
          ) : (
            <>
              <div className="form-field">
                <label className="form-field__label">填充不透明度 {Math.round(fillOpacity * 100)}%</label>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={fillOpacity}
                  onChange={(event) => applyFillOpacity(Number(event.target.value))}
                />
              </div>
              <div className="form-field">
                <label className="form-field__label">描边不透明度 {Math.round(strokeOpacity * 100)}%</label>
                <input
                  type="range"
                  min={0}
                  max={1}
                  step={0.05}
                  value={strokeOpacity}
                  onChange={(event) => applyStrokeOpacity(Number(event.target.value))}
                />
              </div>
            </>
          )}
          {!isRaster && (
            <>
              <div className="form-field">
                <label className="form-field__label">描边颜色</label>
                <div className="color-row">
                  <input
                    type="color"
                    className="color-input"
                    value={strokeColor}
                    onChange={(event) => applyStyle({ strokeColor: event.target.value })}
                  />
                  <code className="color-value">{strokeColor.toUpperCase()}</code>
                </div>
              </div>
              <div className="form-field">
                <label className="form-field__label">描边宽度 {strokeWidth}px（0 = 无边框）</label>
                <input
                  type="range"
                  min={0}
                  max={8}
                  step={0.5}
                  value={strokeWidth}
                  onChange={(event) => applyStyle({ strokeWidth: Number(event.target.value) })}
                />
              </div>
              {isPoint && (
                <div className="form-field">
                  <label className="form-field__label">点半径 {radius}px</label>
                  <input
                    type="range"
                    min={2}
                    max={20}
                    step={1}
                    value={radius}
                    onChange={(event) => applyStyle({ radius: Number(event.target.value) })}
                  />
                </div>
              )}
            </>
          )}

          {/* Sprint 2：按字段分层设色（graduated，改动 3：任意图层可用 + 多种分档方法），栅格图层无字段分档 */}
          {!isRaster && (
          <div className="form-field">
            <label className="form-field__label">按字段分层设色</label>
            {numericFields.length > 0 ? (
              <>
                <select
                  className="form-select"
                  value={field}
                  onChange={(event) => setField(event.target.value)}
                  aria-label="分档字段"
                >
                  <option value="">选择字段…</option>
                  {numericFields.map((f) => (
                    <option key={f.name} value={f.name}>
                      {f.name}（{f.type}）
                    </option>
                  ))}
                </select>
                <div className="graduated-row">
                  <select className="form-select" value={method} onChange={(e) => setMethod(e.target.value)} aria-label="分档方法">
                    <option value="quantile">分位数</option>
                    <option value="equal-interval">等距</option>
                    <option value="jenks">自然间断点（Jenks）</option>
                    <option value="geometric-interval">几何间隔</option>
                    <option value="stddev">标准差</option>
                  </select>
                  <select className="form-select" value={classes} onChange={(e) => setClasses(Number(e.target.value))} aria-label="分档数">
                    {[3, 4, 5, 6, 7].map((n) => (
                      <option key={n} value={n}>
                        {n} 级
                      </option>
                    ))}
                  </select>
                </div>
                <div className="graduated-row">
                  <select className="form-select" value={paletteName} onChange={(e) => setPaletteName(e.target.value)} aria-label="色带">
                    {PALETTES.map((p) => (
                      <option key={p.name} value={p.name}>
                        {p.label}
                      </option>
                    ))}
                  </select>
                  {isGraduated ? (
                    <button type="button" className="button button--ghost" onClick={clearGraduated}>
                      清除分档
                    </button>
                  ) : (
                    <button type="button" className="button" onClick={applyGraduated} disabled={!field}>
                      应用分档
                    </button>
                  )}
                </div>
                {isGraduated && style?.visualVariables?.graduated && (
                  <p className="form-field__hint">
                    已按「{style.visualVariables.graduated.field}」分 {style.visualVariables.graduated.classes} 级
                    （断点：{style.visualVariables.graduated.breaks.map((b) => Math.round(b * 100) / 100).join(' / ')}）
                  </p>
                )}
              </>
            ) : (
              <p className="form-field__hint">该图层没有可用的数值字段，无法分档设色（可让 Agent 按属性设色）。</p>
            )}
          </div>
          )}
        </div>
      </div>
    </div>
  )
}
