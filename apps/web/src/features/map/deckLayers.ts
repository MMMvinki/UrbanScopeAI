import { BitmapLayer, GeoJsonLayer, ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import type { BitmapLayerProps, GeoJsonLayerProps, ScatterplotLayerProps, TextLayerProps } from '@deck.gl/layers'
import { TileLayer } from '@deck.gl/geo-layers'
import type { TileLayerProps } from '@deck.gl/geo-layers'
import type { Layer } from '@deck.gl/core'
import type { LayerMetadata, LayerStyle, VisualVariables } from '../layers/layerTypes'
import { API_BASE } from '../../lib/api'
import { getLayerGeojson } from './mapLayers'

/**
 * deck.gl 图层构建：把项目图层（含后端真实几何）映射为 deck.gl 图层。
 * - 点图层 → ScatterplotLayer（data 取 features 数组；deck.gl createIterable 不认 FeatureCollection 对象）
 * - 面图层 → GeoJsonLayer（浅填充 + 描边，原生支持 FeatureCollection）
 * 样式来自 layerStyles（Sprint 1 本地 + Sprint 2 后端 layer_style 事件/PATCH），缺省回退 layer.color。
 *
 * Sprint 2（S2-2）graduated 渲染：styleMode==='graduated' 时按 visualVariables 分档，
 * 后端已算好 breaks/palette，前端按要素属性取档位色；opacity_variable / size_variable 做渐变。
 * Sprint 2 补（S2-3）：categorized 分类配色、filter 图层过滤、highlight 高亮、label 标注。
 */

type Rgba = [number, number, number, number]

/** 十六进制颜色 → [r,g,b] */
function hexToRgb(hex: string): [number, number, number] {
  const value = hex.replace('#', '')
  const full = value.length === 3 ? value.split('').map((c) => c + c).join('') : value
  const num = parseInt(full, 16)
  if (Number.isNaN(num)) return [15, 23, 42]
  return [(num >> 16) & 255, (num >> 8) & 255, num & 255]
}

/** hex + alpha(0-1) → [r,g,b,a] */
function rgba(hex: string, alpha: number): Rgba {
  const [r, g, b] = hexToRgb(hex)
  const a = Math.round(Math.max(0, Math.min(1, alpha)) * 255)
  return [r, g, b, a]
}

const TRANSPARENT: Rgba = [0, 0, 0, 0]

/** 要素最小结构：读属性字段用 */
interface FeatureLike {
  geometry?: { type?: string; coordinates?: unknown }
  properties?: Record<string, unknown>
}

/** 数值 → 分档索引（breaks 升序；<=break 归该档；无数值/非数值返回 -1） */
function classIndexFor(value: unknown, breaks: number[]): number {
  const num = typeof value === 'number' ? value : Number(value)
  if (Number.isNaN(num) || !breaks.length) return -1
  for (let i = 0; i < breaks.length; i++) if (num <= breaks[i]) return i
  return breaks.length
}

/** 数值 → 线性映射到 [out[0], out[1]]（范围取 breaks 首尾；越界夹取） */
function rangeMap(value: unknown, breaks: number[], out: [number, number]): number {
  const num = typeof value === 'number' ? value : Number(value)
  if (Number.isNaN(num) || breaks.length < 2 || out[1] <= out[0]) return out[1]
  const lo = breaks[0]
  const hi = breaks[breaks.length - 1]
  if (hi <= lo) return out[1]
  const t = Math.max(0, Math.min(1, (num - lo) / (hi - lo)))
  return out[0] + t * (out[1] - out[0])
}

/** 单条过滤条件（与后端 extract_features._match_one 同语义） */
function matchOne(value: unknown, op: string, target: unknown): boolean {
  switch (op) {
    case 'eq':
      return String(value ?? '') === String(target ?? '')
    case 'ne':
      return String(value ?? '') !== String(target ?? '')
    case 'contains':
      return (
        typeof value === 'string' &&
        typeof target === 'string' &&
        target.length > 0 &&
        value.toLowerCase().includes(target.toLowerCase())
      )
    case 'startswith':
      return (
        typeof value === 'string' &&
        typeof target === 'string' &&
        value.toLowerCase().startsWith(target.toLowerCase())
      )
    default: {
      const numV = Number(value)
      const numT = Number(target)
      if (Number.isNaN(numV) || Number.isNaN(numT)) return false
      if (op === 'gt') return numV > numT
      if (op === 'gte') return numV >= numT
      if (op === 'lt') return numV < numT
      if (op === 'lte') return numV <= numT
      return false
    }
  }
}

/** 整组过滤条件：全部命中才返回 true */
function matchesFilter(props: Record<string, unknown>, conditions: Record<string, unknown>): boolean {
  for (const [field, spec] of Object.entries(conditions)) {
    const value = props[field]
    let op = 'eq'
    let target: unknown = spec
    if (spec && typeof spec === 'object' && !Array.isArray(spec)) {
      const obj = spec as Record<string, unknown>
      if ('op' in obj || 'value' in obj) {
        op = String(obj.op ?? 'eq')
        target = obj.value
      }
    }
    if (!matchOne(value, op, target)) return false
  }
  return true
}

function styleFor(layer: LayerMetadata, styles: Record<string, LayerStyle>): LayerStyle | undefined {
  return styles[layer.id]
}

/** 要素上的标注位置：点取坐标；线取首点；面取首环首点 */
function labelPositionFor(feature: FeatureLike): [number, number] {
  const coords = feature.geometry?.coordinates
  if (Array.isArray(coords)) {
    const first = Array.isArray(coords[0]) ? coords[0] : coords
    const firstOfFirst = Array.isArray(first) && Array.isArray(first[0]) ? first[0] : first
    if (
      Array.isArray(firstOfFirst) &&
      typeof firstOfFirst[0] === 'number' &&
      typeof firstOfFirst[1] === 'number'
    ) {
      return [firstOfFirst[0] as number, firstOfFirst[1] as number]
    }
  }
  return [0, 0]
}

/**
 * 栅格图层 → TileLayer（deck.gl v9 已移除 RasterTileLayer）：
 * getTileData 拉 PNG 为 ImageBitmap，renderSubLayers 用 BitmapLayer 贴到瓦片 bbox。
 * bounds 用图层 bbox 限定请求范围（视口不在深圳时不请求瓦片）。
 */
function buildRasterLayer(
  layer: LayerMetadata,
  styles: Record<string, LayerStyle> = {},
): TileLayer[] {
  if (!layer.tileUrl || !layer.bbox) return []
  const url = `${API_BASE}${layer.tileUrl}`
  const opacity = styles[layer.id]?.opacity ?? 0.85
  const bbox = layer.bbox as [number, number, number, number]
  return [
    new TileLayer({
      id: `deck-${layer.id}`,
      data: url,
      minZoom: 10,
      maxZoom: 14,
      tileSize: 256,
      bounds: [bbox[0], bbox[1], bbox[2], bbox[3]],
      opacity,
      pickable: false,
      getTileData: async ({ url: tileUrl }) => {
        if (!tileUrl) return null
        try {
          const res = await fetch(tileUrl)
          if (!res.ok) return null
          return await createImageBitmap(await res.blob())
        } catch {
          return null
        }
      },
      renderSubLayers: (props) => {
        const tile = (props as TileLayerProps & { tile?: { boundingBox?: number[][] } }).tile
        const [min, max] = tile?.boundingBox ?? []
        if (!min || !max || !props.data) return null
        return new BitmapLayer(props as unknown as BitmapLayerProps, {
          data: undefined,
          image: props.data as ImageBitmap,
          bounds: [min[0], min[1], max[0], max[1]],
        })
      },
    }),
  ]
}

/** 坐标对是否为合理经纬度（有限且范围合法；坏几何绝不进 deck，防 invalid latitude 崩溃） */
function isFiniteLngLat(x: number, y: number): boolean {
  return Number.isFinite(x) && Number.isFinite(y) && x >= -180 && x <= 180 && y >= -90 && y <= 90
}

/** 递归检查几何坐标树：任一坐标对越界/非有限即视为坏几何 */
function geometryHasValidCoords(coords: unknown): boolean {
  if (!Array.isArray(coords)) return false
  if (coords.length >= 2 && typeof coords[0] === 'number' && typeof coords[1] === 'number') {
    return isFiniteLngLat(coords[0], coords[1])
  }
  for (const child of coords) {
    if (!geometryHasValidCoords(child)) return false
  }
  return true
}

export function buildDeckLayers(
  layers: LayerMetadata[],
  styles: Record<string, LayerStyle> = {},
): Layer[] {
  return layers
    .filter((layer) => layer.visible)
    .flatMap((layer): Layer[] => {
      if (layer.kind === 'raster') return buildRasterLayer(layer, styles) as Layer[]
      const collection = getLayerGeojson(layer)
      // 坏几何（投影坐标/NaN/越界）直接丢弃：绝不喂给 deck.gl 崩地图
      const features: FeatureLike[] = ((collection?.features as FeatureLike[]) ?? []).filter((f) =>
        geometryHasValidCoords(f.geometry?.coordinates),
      )
      const style = styleFor(layer, styles)
      const vv: VisualVariables | undefined = style?.visualVariables
      const graduated = style?.styleMode === 'graduated' ? vv?.graduated : undefined
      const categorized = style?.styleMode === 'categorized' ? vv?.categorized : undefined
      const opacityVar = vv?.opacity_variable
      const sizeVar = vv?.size_variable
      const layerFilter = vv?.filter
      const highlight = vv?.highlight
      const hasFilter = layerFilter ? Object.keys(layerFilter).length > 0 : false
      const labelField = style?.labelField ?? layer.labelField

      const baseFill = style?.fillColor ?? layer.color
      const baseStroke = style?.strokeColor ?? layer.color
      // 改动 4：填充/描边不透明度分离。`?? style.opacity ?? 默认` 向后兼容旧样式
      // （fillOpacity/strokeOpacity 为 null 或缺失时仍走整体 opacity）。
      // 默认不透明度 0.8（用户要求：不要太透明，约 80%）。生成/普通图层一视同仁。
      const defaultOpacity = 0.8
      const baseFillOpacity = style?.fillOpacity ?? style?.opacity ?? defaultOpacity
      const baseStrokeOpacity = style?.strokeOpacity ?? style?.opacity ?? baseFillOpacity
      const strokeWidth = style?.strokeWidth ?? 1

      /** 图层过滤：只渲染满足条件的要素 */
      const visibleFeatures = hasFilter
        ? features.filter((f) => matchesFilter(f.properties ?? {}, layerFilter as Record<string, unknown>))
        : features

      /** 要素填充透明度：opacity_variable 渐变，否则整体填充不透明度 */
      const fillOpacityFor = (feature: FeatureLike): number => {
        if (!opacityVar) return baseFillOpacity
        return rangeMap(feature.properties?.[opacityVar.field], opacityVar.breaks, opacityVar.range)
      }
      /** 要素填充色：高亮 → 分类 → 分档 → 固定色 */
      const fillFor = (feature: FeatureLike): Rgba => {
        const props = feature.properties ?? {}
        if (highlight && matchesFilter(props, highlight.filter)) {
          return rgba(highlight.color, fillOpacityFor(feature))
        }
        if (categorized) {
          const cat = categorized.classes.find((c) => c.value === String(props[categorized.field] ?? ''))
          if (cat) return rgba(cat.color, fillOpacityFor(feature))
        }
        if (graduated) {
          const idx = classIndexFor(props[graduated.field], graduated.breaks)
          if (idx < 0) return rgba(baseFill, fillOpacityFor(feature))
          const color = graduated.palette[Math.min(idx, graduated.palette.length - 1)] ?? baseFill
          return rgba(color, fillOpacityFor(feature))
        }
        return rgba(baseFill, fillOpacityFor(feature))
      }
      /** 点半径：size_variable 渐变，否则固定 radius */
      const radiusFor = (feature: FeatureLike): number => {
        if (sizeVar) return rangeMap(feature.properties?.[sizeVar.field], sizeVar.breaks, sizeVar.range)
        return style?.radius ?? 5
      }

      /** 描边色：描边不透明度独立于填充（改动 4）。
       *  opacity_variable 渐变只作用于填充，不影响描边（视觉上渐变只调面，线保持整体透明度）。
       *  描边宽度 0 时全透明。 */
      const strokeFor = (): Rgba => (strokeWidth > 0 ? rgba(baseStroke, baseStrokeOpacity) : TRANSPARENT)

      const mainLayer =
        layer.geometry === 'Point'
          ? new ScatterplotLayer({
              id: `deck-${layer.id}`,
              // 必须传 features 数组：createIterable 对 FeatureCollection 对象返回空
              data: visibleFeatures as unknown as ScatterplotLayerProps['data'],
              pickable: true,
              stroked: true,
              filled: true,
              radiusUnits: 'pixels',
              radiusMinPixels: 2,
              radiusMaxPixels: 30,
              lineWidthUnits: 'pixels',
              lineWidthMinPixels: strokeWidth > 0 ? 1 : 0,
              getRadius: (feature: unknown) => radiusFor(feature as FeatureLike),
              getFillColor: (feature: unknown) => fillFor(feature as FeatureLike),
              getLineColor: () => strokeFor(),
              getLineWidth: strokeWidth,
              getPosition: (feature: unknown) => {
                const f = feature as { geometry: { type: string; coordinates: [number, number] } }
                return f.geometry.coordinates
              },
            })
          : new GeoJsonLayer({
              id: `deck-${layer.id}`,
              data: { type: 'FeatureCollection', features: visibleFeatures } as unknown as GeoJsonLayerProps['data'],
              pickable: true,
              stroked: true,
              filled: true,
              getFillColor: (feature: unknown) => fillFor(feature as FeatureLike),
              getLineColor: () => strokeFor(),
              lineWidthUnits: 'pixels',
              lineWidthMinPixels: strokeWidth > 0 ? 1 : 0,
              getLineWidth: strokeWidth,
            })

      // 标注层：要素边上显示 labelField 取值（点图层最合适，线/面取首点近似）
      if (!labelField) return [mainLayer]
      const textLayer = new TextLayer<{ properties: Record<string, unknown> }>({
        id: `deck-label-${layer.id}`,
        data: visibleFeatures as unknown as TextLayerProps<{ properties: Record<string, unknown> }>['data'],
        pickable: false,
        getText: (feature) => String(feature.properties?.[labelField] ?? ''),
        getPosition: (feature: unknown) => labelPositionFor(feature as FeatureLike),
        getSize: 12,
        getColor: [40, 40, 45, 255],
        backgroundColor: [255, 255, 255, 200],
        background: true,
        getBorderWidth: 1,
        getBorderColor: [255, 255, 255, 255],
        characterSet: 'auto',
        fontFamily: 'system-ui, "Microsoft YaHei", sans-serif',
        sizeUnits: 'pixels',
      })
      return [mainLayer, textLayer]
    })
}
