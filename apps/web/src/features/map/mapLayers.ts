import type { LayerSpecification, StyleSpecification } from 'maplibre-gl'
import type { LayerMetadata, LayerStyle } from '../layers/layerTypes'
import { API_BASE } from '../../lib/api'

/**
 * 地图演示数据生成：为每个图层 id 确定性生成一套示意 GeoJSON。
 * MVP 阶段后端尚无真实矢量数据，地图渲染与图层开关用这套几何演示整条链路；
 * Phase 4 接入真实数据后，替换为按图层读取 GeoJSON。
 */

/** 地图中心（演示坐标：北京市区） */
export const MAP_CENTER: [number, number] = [116.34, 39.93]

/** 演示范围半宽（度，约 3–4 km） */
const EXTENT_HALF = 0.035

/**
 * 底图（basemap）定义：全部为 WGS84/Web Mercator 瓦片源，与数据 EPSG:4490（≈WGS84）对齐。
 * 不用高德/百度瓦片：其使用 GCJ-02 加密坐标，叠加会错位约 500 米。
 */
export interface BasemapStyle {
  id: string
  name: string
  /** 瓦片 URL 模板（MapLibre {z}/{x}/{y} 占位） */
  url: string
  attribution: string
  /** 瓦片未加载/失败时的背景色（PRD：底图失败仍保留矢量图层） */
  background: string
}

/** 可选底图（Esri ArcGIS Online 公共服务，无需 key，已验证可访问）。浅灰街道默认。 */
export const BASEMAPS: BasemapStyle[] = [
  {
    id: 'light',
    name: '浅灰街道',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Esri, HERE, Garmin',
    background: '#eef2f7',
  },
  {
    id: 'satellite',
    name: '卫星影像',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Esri, Maxar, Earthstar Geographics',
    background: '#0a0f14',
  },
  {
    id: 'dark',
    name: '深色街道',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Esri, HERE, Garmin',
    background: '#0d131b',
  },
  {
    id: 'streets',
    name: '街道详图',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Esri, HERE, Garmin',
    background: '#f5f1e8',
  },
  {
    id: 'topo',
    name: '地形图',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Esri, USGS, NOAA',
    background: '#e9e5d8',
  },
]

export const DEFAULT_BASEMAP_ID = BASEMAPS[0].id

/** 底图样式：背景色（瓦片未就绪/失败时可见）+ 栅格瓦片层；数据图层叠加在其上 */
export function buildBasemapStyle(basemap: BasemapStyle): StyleSpecification {
  return {
    version: 8,
    name: basemap.name,
    sources: {
      basemap: {
        type: 'raster',
        tiles: [basemap.url],
        tileSize: 256,
        maxzoom: 19,
        attribution: basemap.attribution,
      },
    },
    layers: [
      { id: 'background', type: 'background', paint: { 'background-color': basemap.background } },
      {
        id: 'basemap-raster',
        type: 'raster',
        source: 'basemap',
        paint: { 'raster-fade-duration': 0 },
      },
    ],
  }
}

/** 最小几何结构（与 GeoJSON 兼容） */
export type DemoGeometry =
  | { type: 'Point'; coordinates: [number, number] }
  | { type: 'LineString'; coordinates: [number, number][] }
  | { type: 'MultiLineString'; coordinates: [number, number][][] }
  | { type: 'Polygon'; coordinates: [number, number][][] }
  | { type: 'MultiPolygon'; coordinates: [number, number][][][] }

export interface DemoFeature {
  type: 'Feature'
  geometry: DemoGeometry
  properties: Record<string, string | number>
}

export interface DemoFeatureCollection {
  type: 'FeatureCollection'
  features: DemoFeature[]
}

/* ---------- 确定性随机（按图层 id 播种，多次渲染结果一致） ---------- */

function hashString(str: string): number {
  let hash = 2166136261
  for (let i = 0; i < str.length; i += 1) {
    hash ^= str.charCodeAt(i)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

function mulberry32(seed: number): () => number {
  let a = seed
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/* ---------- 几何构造 ---------- */

function pointFeature(lng: number, lat: number, name: string): DemoFeature {
  return {
    type: 'Feature',
    geometry: { type: 'Point', coordinates: [lng, lat] },
    properties: { name },
  }
}

function polygonFeature(ring: [number, number][], name: string): DemoFeature {
  return {
    type: 'Feature',
    geometry: { type: 'Polygon', coordinates: [ring] },
    properties: { name },
  }
}

function rectFeature(x1: number, y1: number, x2: number, y2: number, name: string): DemoFeature {
  const ring: [number, number][] = [
    [x1, y1],
    [x2, y1],
    [x2, y2],
    [x1, y2],
    [x1, y1],
  ]
  return polygonFeature(ring, name)
}

/** 以中心为圆心的圆（结果图层 / 服务范围示意） */
function circleFeature(
  center: [number, number],
  radiusDeg: number,
  name: string,
  segments = 48,
): DemoFeature {
  const [lng, lat] = center
  const latScale = Math.cos((lat * Math.PI) / 180)
  const ring: [number, number][] = []
  for (let i = 0; i <= segments; i += 1) {
    const angle = (i / segments) * Math.PI * 2
    ring.push([lng + Math.cos(angle) * radiusDeg, lat + Math.sin(angle) * radiusDeg * latScale])
  }
  return polygonFeature(ring, name)
}

/* ---------- 图层 → GeoJSON ---------- */

function buildLayerGeojson(layer: LayerMetadata): DemoFeatureCollection {
  const rnd = mulberry32(hashString(layer.id))
  const [cx, cy] = MAP_CENTER
  const halfW = EXTENT_HALF
  const halfH = EXTENT_HALF
  const features: DemoFeature[] = []

  // 研究范围：覆盖大部分范围的多边形
  if (layer.id === 'study_area') {
    features.push(rectFeature(cx - halfW * 0.9, cy - halfH * 0.85, cx + halfW * 0.8, cy + halfH * 0.9, layer.name))
    return { type: 'FeatureCollection', features }
  }

  // 生成图层：以范围中心为圆心的服务范围 / 结果图层
  if (layer.source === 'generated') {
    features.push(circleFeature(MAP_CENTER, EXTENT_HALF * (0.42 + rnd() * 0.3), layer.name))
    return { type: 'FeatureCollection', features }
  }

  // 点图层：范围内散点
  if (layer.geometry === 'Point') {
    const count = Math.min(layer.featureCount, 60)
    for (let i = 0; i < count; i += 1) {
      const lng = cx + (rnd() * 2 - 1) * halfW * 0.9
      const lat = cy + (rnd() * 2 - 1) * halfH * 0.85
      features.push(pointFeature(lng, lat, layer.name))
    }
    return { type: 'FeatureCollection', features }
  }

  // 面图层：网格地块（人口网格密一些，其余留白随机）
  const grid = Math.max(3, Math.min(8, Math.round(Math.sqrt(layer.featureCount))))
  const cellW = (halfW * 1.6) / grid
  const cellH = (halfH * 1.5) / grid
  for (let i = 0; i < grid; i += 1) {
    for (let j = 0; j < grid; j += 1) {
      if (rnd() < 0.24) continue
      const w = (0.5 + rnd() * 0.9) * cellW
      const h = (0.5 + rnd() * 0.9) * cellH
      const x = cx - halfW * 0.8 + i * cellW + rnd() * 0.1
      const y = cy - halfH * 0.75 + j * cellH + rnd() * 0.1
      features.push(rectFeature(x, y, x + w, y + h, layer.name))
    }
  }
  return { type: 'FeatureCollection', features }
}

/** 客户端演示生成缓存（按图层 id） */
const localCache = new Map<string, DemoFeatureCollection>()

/** 后端真实几何缓存（{layer_id: FeatureCollection}），由 store hydrate 时填充 */
let backendGeojson: Record<string, DemoFeatureCollection> | null = null

/** 写入后端真实几何（key 与后端 /layers/geojson 一致） */
export function setGeojsonCache(cache: Record<string, unknown>): void {
  backendGeojson = {} as Record<string, DemoFeatureCollection>
  for (const [layerId, collection] of Object.entries(cache)) {
    // 后端 geometry 坐标类型是 number[][][]，运行时兼容 tuple，做一次宽松转换
    backendGeojson[layerId] = collection as DemoFeatureCollection
  }
}

/** 写入/复制单个图层的几何（复制图层时把源图层几何带过去） */
export function setGeojsonEntry(layerId: string, collection: unknown): void {
  backendGeojson = backendGeojson ?? {}
  backendGeojson[layerId] = collection as DemoFeatureCollection
  // 惰性拉取完成后通知地图重绘（React 通过 geojsonTick 感知）
  geojsonTick += 1
}

/** 移除单个图层的几何缓存（remove_layer 工具清理用，自增版本驱动重绘） */
export function removeGeojsonEntry(layerId: string): void {
  if (backendGeojson && layerId in backendGeojson) {
    delete backendGeojson[layerId]
    geojsonTick += 1
  }
  localCache.delete(layerId)
}

/** 几何加载版本：平台数据惰性拉取完成时自增，驱动 buildDeckLayers 重跑 */
export let geojsonTick = 0

/** 该图层几何是否已就绪（有真实几何或无需几何） */
export function hasLayerGeojson(layerId: string): boolean {
  const collection = backendGeojson?.[layerId]
  return Boolean(collection && collection.features?.length)
}

/**
 * 惰性拉取单个图层真实几何（平台数据重图层：bulk 端点不含，显隐时按需取）。
 * 已缓存直接返回 true；失败返回 false（不重试风暴，用户再次切换时会再试）。
 */
export async function ensureLayerGeojson(layerId: string): Promise<boolean> {
  if (hasLayerGeojson(layerId)) return true
  try {
    const response = await fetch(`${API_BASE}/layers/${encodeURIComponent(layerId)}/geojson`)
    if (!response.ok) return false
    const data = await response.json()
    setGeojsonEntry(layerId, data)
    return true
  } catch {
    return false
  }
}

const EMPTY_FC: DemoFeatureCollection = { type: 'FeatureCollection', features: [] }

export function getLayerGeojson(layer: LayerMetadata): DemoFeatureCollection {
  // 优先后端真实几何（Phase C：真计算数据源），缺失回退客户端演示生成
  const real = backendGeojson?.[layer.id]
  if (real && real.features?.length) return real
  // 平台数据/栅格图层没有演示回退：几何未加载时渲染空（绝不显示编造的假数据）
  if (layer.readonly || layer.lazy || layer.kind === 'raster') return EMPTY_FC
  const cached = localCache.get(layer.id)
  if (cached) return cached
  const collection = buildLayerGeojson(layer)
  localCache.set(layer.id, collection)
  return collection
}

/**
 * 递归遍历任意几何的 coordinates（Point / LineString / MultiLineString / Polygon /
 * MultiPolygon，含外部数据可能出现的畸形结构），仅对「两元素数字数组」的坐标对执行
 * 回调；坏数据静默跳过 —— 绝不因单个坏要素抛 TypeError 崩掉整张地图。
 */
function forEachCoordinate(coords: unknown, visit: (lng: number, lat: number) => void): void {
  if (!Array.isArray(coords)) return
  if (coords.length >= 2 && typeof coords[0] === 'number' && typeof coords[1] === 'number') {
    visit(coords[0], coords[1])
    return
  }
  for (const child of coords) forEachCoordinate(child, visit)
}

/**
 * 要素集合包围盒（西南、东北），用于 fitBounds。
 * 注意：LineString 与 Polygon 的坐标嵌套层数不同，绝不能按固定层数展开
 * （曾把 LineString 当两层展开 → 把经度数字当坐标解构 → 「number is not iterable」崩溃）。
 */
export function featureCollectionBounds(
  collection: DemoFeatureCollection,
): [[number, number], [number, number]] {
  let minLng = Infinity
  let minLat = Infinity
  let maxLng = -Infinity
  let maxLat = -Infinity

  for (const feature of collection.features) {
    const coords = feature.geometry?.coordinates
    if (!coords) continue
    forEachCoordinate(coords, (lng, lat) => {
      if (!Number.isFinite(lng) || !Number.isFinite(lat)) return
      minLng = Math.min(minLng, lng)
      minLat = Math.min(minLat, lat)
      maxLng = Math.max(maxLng, lng)
      maxLat = Math.max(maxLat, lat)
    })
  }

  if (!Number.isFinite(minLng)) return [[MAP_CENTER[0], MAP_CENTER[1]], [MAP_CENTER[0], MAP_CENTER[1]]]
  return [
    [minLng, minLat],
    [maxLng, maxLat],
  ]
}

/* ---------- 地图图层注册 ---------- */

export const sourceIdFor = (layerId: string): string => `src-${layerId}`
export const mapLayerIdFor = (layerId: string): string => `layer-${layerId}`

/**
 * 图层 → MapLibre 样式：点用圆点，面用浅填充；生成图层更透明。
 * 传入 layerStyles 让 deck.gl 叠加不可用（回退路径）时也消费用户改的样式，
 * 否则「改颜色/不透明度地图不变」会在兜底模式下静默复发。
 * 数值字段与 deck 路径对齐：不透明度 opacity（1=完全不透明）、描边/半径/颜色同源。
 */
export function styleSpecFor(layer: LayerMetadata, style?: LayerStyle): LayerSpecification {
  const source = sourceIdFor(layer.id)
  const id = mapLayerIdFor(layer.id)
  const fill = style?.fillColor ?? layer.color
  const stroke = style?.strokeColor ?? layer.color
  const strokeWidth = style?.strokeWidth ?? 1
  const radius = style?.radius ?? 5
  const opacity = style?.opacity ?? (layer.source === 'generated' ? 0.27 : 0.51)

  if (layer.geometry === 'Point') {
    return {
      id,
      source,
      type: 'circle',
      paint: {
        'circle-radius': radius,
        'circle-color': fill,
        'circle-opacity': opacity,
        'circle-stroke-width': strokeWidth > 0 ? strokeWidth : 0,
        'circle-stroke-color': strokeWidth > 0 ? stroke : fill,
        // 描边与填充同用整体不透明度：不透明度作用于整个符号（对齐 deck 路径）
        'circle-stroke-opacity': strokeWidth > 0 ? opacity : 0,
      },
    } as LayerSpecification
  }

  return {
    id,
    source,
    type: 'fill',
    paint: {
      'fill-color': fill,
      'fill-opacity': opacity,
      'fill-outline-color': stroke,
      // 描边与填充同用整体不透明度：不透明度作用于整个符号（对齐 deck 路径）
      'fill-outline-opacity': strokeWidth > 0 ? opacity : 0,
    },
  } as LayerSpecification
}
