import { useEffect, useRef, useState } from 'react'
import { AttributionControl, Map as MapLibreMap, NavigationControl } from 'maplibre-gl'
import type { StyleSpecification } from 'maplibre-gl'
import { MapboxOverlay } from '@deck.gl/mapbox'
import 'maplibre-gl/dist/maplibre-gl.css'
import { Layers } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'
import {
  BASEMAPS,
  DEFAULT_BASEMAP_ID,
  MAP_CENTER,
  buildBasemapStyle,
  featureCollectionBounds,
  getLayerGeojson,
  isValidLngLatBounds,
  mapLayerIdFor,
  sourceIdFor,
  stripBasemapLabels,
  styleSpecFor,
} from './mapLayers'
import { buildDeckLayers } from './deckLayers'
import { MapSchematic } from './MapSchematic'
import { MapLegendOverlay } from './MapLegendOverlay'
import { TerraDrawController, geometryTypeFromFeatures } from './DrawController'
import type { DrawMode } from './DrawController'
import type { AutoUnitConfig } from './geodesic'
import { MapDrawToolbar } from './MapDrawToolbar'
import { DrawnAttributesPanel } from './DrawnAttributesPanel'
import type { FieldSpec, GeometryType } from '../layers/layerTypes'

/**
 * WebGL2 预探测：MapLibre 在 `new Map()` 构造器内**同步**初始化 painter，
 * 失败时 error 事件在监听器注册前就被丢弃（只会 console.error），导致静默空白。
 * 因此必须在构造前探测，并在构造后立即检查 painter 状态。
 */
let cachedWebgl: boolean | null = null

/**
 * Mapbox 底图 public token（pk. 开头）。构建期常量：配域名白名单后放前端是 Mapbox 标准做法；
 * 服务端调用如 isochrone 的 token 另走 apps/api/.env，两者不是一回事。
 * 未配置时 Mapbox 底图不会出现在列表，地图回退免 token 的 OpenFreeMap 样式。
 */
const mapboxToken = import.meta.env.VITE_MAPBOX_TOKEN as string | undefined

/** 图层 id 随手绘名字生成：保留中英文、数字、下划线，其余替换为下划线（后端 clean_layer_id 会再次清洗） */
function slugify(value: string): string {
  const slug = value
    .trim()
    .replace(/[^\p{L}\p{N}_-]+/gu, '_')
    .replace(/^_+|_+$/g, '')
  return slug || 'drawn'
}

function webgl2Available(): boolean {
  if (cachedWebgl !== null) return cachedWebgl
  try {
    const canvas = document.createElement('canvas')
    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl')
    if (gl) {
      // 探测后立即释放上下文，避免在受限环境（软件渲染 / VM）里占用 WebGL 上下文名额
      const ext = gl.getExtension('WEBGL_lose_context')
      ext?.loseContext()
      cachedWebgl = true
      return true
    }
    cachedWebgl = false
    return false
  } catch {
    cachedWebgl = false
    return false
  }
}

/** 地图主工作区：MapLibre 实例 + 图层同步 + WebGL 兜底。 */
export function MapWorkspace() {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const deckOverlayRef = useRef<MapboxOverlay | null>(null)
  // deck.gl 叠加是否接管数据图层渲染（失败自动回退 MapLibre 内置图层）
  const [deckActive, setDeckActive] = useState(false)
  const [loaded, setLoaded] = useState(false)
  const [webglError, setWebglError] = useState(() => !webgl2Available())
  // 底图切换：setStyle 后 style.load 触发递增，驱动数据图层重新同步
  // basemapId 提升到 store：工具栏下拉与 Agent set_basemap 事件共用同一状态
  const [styleTick, setStyleTick] = useState(0)
  // 底图文字标注开关（矢量底图带大量 POI/道路标注，出图分析时常要干净底图）
  const [showLabels, setShowLabels] = useState(true)
  // 3D 建筑开关：OpenFreeMap planet 矢量建筑 fill-extrusion 挤出，叠加在任意底图上
  const [show3D, setShow3D] = useState(false)

  // —— 手绘图层（Terra Draw）状态 ——
  // drawActive：绘制工具栏已展开；drawMode：当前绘制/编辑模式；
  // drawPending：点击「完成」后收集到的要素，待属性面板确认创建；drawHasFeatures：是否有已绘要素。
  const [drawActive, setDrawActive] = useState(false)
  const [drawMode, setDrawMode] = useState<DrawMode>('point')
  const [drawPending, setDrawPending] = useState<{
    features: GeoJSON.Feature[]
    geometryType: GeometryType
    color: string
  } | null>(null)
  const [drawHasFeatures, setDrawHasFeatures] = useState(false)
  // 撤销/重做可用性：撤销栈变化时由控制器 onHistory 回调写入，供工具栏按钮判 enable
  const [drawCanUndo, setDrawCanUndo] = useState(false)
  const [drawCanRedo, setDrawCanRedo] = useState(false)
  // 编辑会话：非空=当前正在编辑已建手绘图层（完成时走 PUT 而非 POST /layers/draw）
  const [editLayerId, setEditLayerId] = useState<string | null>(null)
  const drawControllerRef = useRef<TerraDrawController | null>(null)
  const drawColorRef = useRef('#0F172A')
  const startDrawRef = useRef<() => void>(() => {})
  const editStartRef = useRef<(id: string) => void>(() => {})

  const layers = useAppStore((s) => s.layers)
  const layerStyles = useAppStore((s) => s.layerStyles)
  const geojsonTick = useAppStore((s) => s.geojsonTick)
  const focusTarget = useAppStore((s) => s.focusTarget)
  const basemapId = useAppStore((s) => s.basemapId)
  const setBasemapId = useAppStore((s) => s.setBasemapId)
  const addDrawnLayer = useAppStore((s) => s.addDrawnLayer)
  const updateDrawnLayerGeojson = useAppStore((s) => s.updateDrawnLayerGeojson)
  const focusLayer = useAppStore((s) => s.focusLayer)
  const drawRequestTick = useAppStore((s) => s.drawRequestTick)
  const editLayerRequest = useAppStore((s) => s.editLayerRequest)

  // 当前生效底图：Mapbox 底图但没配 token → 回退默认（避免无 token 的栅格 URL 白图）
  const activeBasemap = BASEMAPS.find((b) => b.id === basemapId) ?? BASEMAPS[0]
  const effectiveBasemap =
    activeBasemap.mapboxStyle && !mapboxToken
      ? (BASEMAPS.find((b) => b.id === DEFAULT_BASEMAP_ID) ?? BASEMAPS[0])
      : activeBasemap
  // 标注开关只对矢量底图有效（OpenFreeMap）；Mapbox/Esri 栅格底图标注内嵌/不存在
  const labelsCapable = Boolean(effectiveBasemap.styleUrl)

  // 初始化地图（组件卸载时销毁，StrictMode 下安全）
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    if (!webgl2Available()) {
      setWebglError(true)
      return
    }

    let map: MapLibreMap | null = null
    try {
      map = new MapLibreMap({
        container,
        style: buildBasemapStyle(BASEMAPS[0]),
        center: MAP_CENTER,
        zoom: 12,
        attributionControl: false,
      })
      // 构造后立即检查：painter 为 null 说明 WebGL2 初始化已在构造期内失败
      if (!map.painter) {
        setWebglError(true)
        map = null
        return
      }
    } catch (error) {
      console.error('[map] 初始化失败：', error)
      setWebglError(true)
      return
    }

    map.addControl(new NavigationControl({ visualizePitch: true }), 'top-right')
    map.addControl(new AttributionControl({ compact: true }), 'bottom-right')

    // deck.gl MapboxOverlay：interleaved 模式在 MapLibre 的 WebGL 上下文内叠加渲染。
    // 初始化失败（受限环境）→ 记录并回退 MapLibre 内置图层，不阻塞地图。
    try {
      const overlay = new MapboxOverlay({ interleaved: true })
      map.addControl(overlay)
      deckOverlayRef.current = overlay
      setDeckActive(true)
      console.info('[map] deck.gl 叠加就绪')
    } catch (error) {
      console.warn('[map] deck.gl 叠加初始化失败，回退 MapLibre 图层：', error)
      deckOverlayRef.current = null
    }

    // load 超时看门狗：任何未能在期限内就绪的初始化都会暴露为兜底，而非静默空白
    let loadedRef = false
    const watchdog = window.setTimeout(() => {
      if (!loadedRef) {
        console.error('[map] load 超时（8s）：地图未就绪')
        setWebglError(true)
      }
    }, 8000)

    map.on('load', () => {
      loadedRef = true
      window.clearTimeout(watchdog)
      console.info('[map] load 就绪', {
        layers: Object.keys(map.getStyle().sources).length,
      })
      // 地图已就绪 → 强制清除任何误触发的兜底（真实浏览器中探测稳定，此分支兜底保证）
      setWebglError(false)
      setLoaded(true)
    })
    map.on('error', (event) => {
      const message =
        typeof event?.error?.message === 'string' ? event.error.message : String(event.error ?? event)
      console.warn('[map] error event: loaded=', loadedRef, '|', message)
      if (loadedRef) return // 已就绪后忽略非致命错误，绝不因性能消息回退
      if (/webgl|create context|gpu|hardware/i.test(message)) {
        console.error('[map] WebGL 初始化失败：', message)
        setWebglError(true)
      }
    })
    // 会话中途 GPU context 丢失
    map.on('webglcontextlost', () => {
      console.error('[map] WebGL context lost（loaded=', loadedRef, '）')
      if (!loadedRef) setWebglError(true)
    })
    map.on('webglcontextrestored', () => {
      console.info('[map] WebGL context restored')
      setWebglError(false)
    })

    mapRef.current = map
    // DEV 探针钩子：headless/CDP 验证底图渲染用（import.meta.env.DEV 下才挂载）
    if (import.meta.env.DEV) {
      ;(window as unknown as Record<string, unknown>).__urbanscopeMap = map
      ;(window as unknown as Record<string, unknown>).__urbanscopeOverlay = deckOverlayRef.current
      // 诊断快照：控制台输入 __urbanscopeDebug('schools') 读出该图层
      // store 样式 + deck 图层实例的真实渲染属性，用于定位"改样式地图不变"断点。
      ;(window as unknown as Record<string, unknown>).__urbanscopeDebug = (layerId: string) => {
        const { layers, layerStyles } = useAppStore.getState()
        const layer = layers.find((l) => l.id === layerId)
        const style = layerStyles[layerId]
        const overlayRef = (window as unknown as Record<string, unknown>).__urbanscopeOverlay as
          | { _deck?: { layerManager?: { layers?: Array<{ id: string; props: unknown }> } } }
          | null
        const deckLayer = overlayRef?._deck?.layerManager?.layers?.find(
          (l) => l.id === `deck-${layerId}`,
        )
        const props = deckLayer?.props as
          | { visible?: boolean; getFillColor?: (f: { properties?: Record<string, unknown> }) => number[] }
          | undefined
        let fillSample: number[] | null = null
        if (props && typeof props.getFillColor === 'function') {
          fillSample = props.getFillColor({ properties: {} })
        }
        return {
          layer: layer
            ? { id: layer.id, name: layer.name, color: layer.color, visible: layer.visible, geometry: layer.geometry }
            : null,
          storeStyle: style ?? null,
          deck: deckLayer
            ? { id: deckLayer.id, visible: props?.visible ?? null, getFillColorSample: fillSample }
            : null,
        }
      }
    }

    // 左侧面板 / 成果栏开关导致容器尺寸变化时重算画布（painter 可能为 null，需防御）
    const observer = new ResizeObserver(() => {
      if (mapRef.current?.painter) mapRef.current.resize()
    })
    observer.observe(container)

    return () => {
      observer.disconnect()
      window.clearTimeout(watchdog)
      if (mapRef.current) {
        try {
          mapRef.current.remove()
        } catch {
          // painter 已为 null 时 remove 可能抛错，忽略
        }
      }
      deckOverlayRef.current = null
      setDeckActive(false)
      mapRef.current = null
      setLoaded(false)
    }
  }, [])

  // 初始定位到研究范围（study_area 已随北京示例移除 → 回退到首个有真实 bbox 的图层 = 深圳）
  useEffect(() => {
    const map = mapRef.current
    if (!loaded || !map?.painter) return
    const target =
      layers.find((layer) => layer.id === 'study_area') ??
      layers.find((layer) => isValidLngLatBounds(layer.bbox))
    if (!target) return
    const bbox = target.bbox
    if (isValidLngLatBounds(bbox) && bbox) {
      map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 40, duration: 0 })
    } else {
      map.fitBounds(featureCollectionBounds(getLayerGeojson(target)), { padding: 40, duration: 0 })
    }
  }, [loaded]) // eslint-disable-line react-hooks/exhaustive-deps

  // 底图切换 + 标注开关：setStyle 会清空 MapLibre 内置数据图层，等 style.load 后递增
  // styleTick 重同步（deck.gl 路径由 MapboxOverlay 在 interleaved 模式下自动重注入）。
  // Mapbox 底图是内联栅格样式（typeof === 'object'）直接应用；OpenFreeMap 矢量是 URL，
  // showLabels=false 时拉取样式并剥掉 symbol（文字/图标）图层再应用。
  useEffect(() => {
    const map = mapRef.current
    if (!loaded || !map?.painter) return
    const styleInput = buildBasemapStyle(effectiveBasemap) // string（矢量样式 URL）| StyleSpecification（栅格/内联）
    let cancelled = false
    const applyStyle = (target: string | StyleSpecification) => {
      map.setStyle(target)
      map.once('style.load', () => setStyleTick((tick) => tick + 1))
    }
    if (showLabels || typeof styleInput !== 'string') {
      applyStyle(styleInput)
      return () => {
        cancelled = true
      }
    }
    // 隐藏标注：拉取矢量样式 JSON → 剥掉 symbol 图层 → 应用修改后的样式
    fetch(styleInput)
      .then((res) => (res.ok ? res.json() : null))
      .then((json) => {
        if (cancelled) return
        if (json && typeof json === 'object' && Array.isArray((json as StyleSpecification).layers)) {
          applyStyle(stripBasemapLabels(json as StyleSpecification))
        } else {
          applyStyle(styleInput)
        }
      })
      .catch(() => {
        if (!cancelled) applyStyle(styleInput)
      })
    return () => {
      cancelled = true
    }
  }, [basemapId, showLabels]) // eslint-disable-line react-hooks/exhaustive-deps

  // 3D 建筑图层：OpenFreeMap planet 矢量源（building source-layer，含 render_height/colour），
  // MapLibre fill-extrusion 原生挤出。每次底图 setStyle 会清空 MapLibre 图层，靠 styleTick 递增重建。
  // 渲染层级：MapLibre 原生层在 deck.gl 数据图层之下（数据图层保持可见），建筑作为底图语境。
  useEffect(() => {
    const map = mapRef.current
    if (!loaded || !map?.painter) return
    if (!show3D) {
      if (map.getLayer('buildings-3d')) map.removeLayer('buildings-3d')
      if (map.getSource('buildings')) map.removeSource('buildings')
      return
    }
    // 样式切换瞬间可能未就绪：等 style.load 递增 styleTick 后由本 effect 重建
    if (!map.isStyleLoaded()) return
    if (!map.getSource('buildings')) {
      map.addSource('buildings', { type: 'vector', url: 'https://tiles.openfreemap.org/planet' })
    }
    if (!map.getLayer('buildings-3d')) {
      map.addLayer({
        id: 'buildings-3d',
        source: 'buildings',
        'source-layer': 'building',
        type: 'fill-extrusion',
        minzoom: 14,
        filter: ['!=', ['get', 'hide_3d'], true],
        paint: {
          'fill-extrusion-color': '#7fb3d9',
          'fill-extrusion-height': ['coalesce', ['get', 'render_height'], 5],
          'fill-extrusion-base': ['coalesce', ['get', 'render_min_height'], 0],
          'fill-extrusion-opacity': 0.75,
        },
      })
    }
  }, [loaded, styleTick, show3D])

  // 3D 轨道旋转：按住鼠标中键拖拽，绕视图中心旋转视角。
  // 横向拖 = 旋转朝向（bearing），纵向拖 = 俯仰角（pitch），类似 SketchUp / GIS 轨道视图。
  // capture 阶段监听，抢在 deck.gl 事件之前处理；中键默认不触发 MapLibre 任何内置手势，互不冲突。
  useEffect(() => {
    const map = mapRef.current
    if (!loaded || !map?.painter) return
    map.setMaxPitch(85) // 放开俯仰上限，配合轨道旋转做真 3D 观察

    const container = map.getCanvasContainer()
    let start: { x: number; y: number } | null = null
    let startBearing = 0
    let startPitch = 0

    const onMouseDown = (e: MouseEvent) => {
      if (e.button !== 1) return // 仅鼠标中键
      e.preventDefault() // 阻止浏览器中键自动滚动
      start = { x: e.clientX, y: e.clientY }
      startBearing = map.getBearing()
      startPitch = map.getPitch()
      container.style.cursor = 'grabbing'
    }
    const onMouseMove = (e: MouseEvent) => {
      if (!start) return
      const dx = e.clientX - start.x
      const dy = e.clientY - start.y
      map.setBearing(startBearing - dx * 0.3)
      map.setPitch(Math.min(Math.max(startPitch + dy * 0.2, 0), 85))
    }
    const onMouseUp = () => {
      if (!start) return
      start = null
      container.style.cursor = ''
    }

    container.addEventListener('mousedown', onMouseDown, true)
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
    return () => {
      container.removeEventListener('mousedown', onMouseDown, true)
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
      container.style.cursor = ''
    }
  }, [loaded])

  // deck.gl 数据图层：deckActive 时接管数据图层渲染（interleaved）。
  // geojsonTick：平台数据惰性几何拉取完成时自增，重跑 buildDeckLayers 让新几何上屏。
  useEffect(() => {
    const overlay = deckOverlayRef.current
    if (!deckActive || !overlay) return
    overlay.setProps({ layers: buildDeckLayers(layers, layerStyles) })
    // interleaved 模式下 setProps 只更新了 deck 图层 props（getFillColor 等已变），
    // 但不会主动触发地图画布重绘 —— 导致「改样式地图不变，隐藏再显示（触发相机飞/重建）才更新」。
    // 显式请求一次地图重绘，让样式改动即时上屏。
    mapRef.current?.triggerRepaint()
  }, [layers, layerStyles, deckActive, geojsonTick])

  // 图层同步（MapLibre 内置路径）：deck.gl 接管时不重复渲染。
  // 兜底路径也消费 layerStyles：改颜色/不透明度即时写 paint，避免回退模式下「改样式地图不变」。
  useEffect(() => {
    const map = mapRef.current
    if (!loaded || !map?.painter) return
    if (deckActive) return

    const liveIds = new Set(layers.map((layer) => layer.id))

    const style = map.getStyle()
    for (const sourceId of Object.keys(style.sources)) {
      if (!sourceId.startsWith('src-')) continue
      const layerId = sourceId.slice(4)
      if (liveIds.has(layerId)) continue
      const mapLayerId = mapLayerIdFor(layerId)
      if (map.getLayer(mapLayerId)) map.removeLayer(mapLayerId)
      map.removeSource(sourceId)
    }

    for (const layer of layers) {
      // 表格图层（无几何）不进 MapLibre 渲染链（属性表/Agent 查询用）
      if (layer.geometry === 'Table') continue
      const sourceId = sourceIdFor(layer.id)
      const mapLayerId = mapLayerIdFor(layer.id)
      const spec = styleSpecFor(layer, layerStyles[layer.id])
      if (!map.getSource(sourceId)) {
        try {
          map.addSource(sourceId, { type: 'geojson', data: getLayerGeojson(layer) })
          map.addLayer(spec)
        } catch (error) {
          console.error(`[map] 图层 ${layer.id} 添加失败：`, error)
        }
      } else {
        // 惰性几何到达后更新既有 source 数据（setData 触发重绘）
        try {
          ;(map.getSource(sourceId) as { setData?: (d: unknown) => void }).setData?.(getLayerGeojson(layer))
        } catch {
          // 某些非 geojson source 无 setData，忽略
        }
      }
      if (map.getLayer(mapLayerId)) {
        for (const [key, value] of Object.entries(spec.paint ?? {})) {
          map.setPaintProperty(mapLayerId, key, value)
        }
        map.setLayoutProperty(mapLayerId, 'visibility', layer.visible ? 'visible' : 'none')
      }
    }
  }, [layers, layerStyles, loaded, deckActive, styleTick, geojsonTick])

  // 成果栏「定位地图」/ 点亮平台数据图层：飞至目标图层
  // 图层范围已在当前视野内则不飞（避免点亮视野内图层时地图突然跳动）
  // 优先用真实 bbox fitBounds：platform 图层全 lazy，几何未就绪时 geojson bounds 无效
  useEffect(() => {
    if (!focusTarget) return
    const map = mapRef.current
    const layer = layers.find((item) => item.id === focusTarget.layerId)
    if (!map?.painter || !layer) return
    const bbox = layer.bbox
    if (isValidLngLatBounds(bbox) && bbox) {
      const bounds = map.getBounds()
      const inView =
        bounds.getWest() <= bbox[2] &&
        bounds.getEast() >= bbox[0] &&
        bounds.getSouth() <= bbox[3] &&
        bounds.getNorth() >= bbox[1]
      if (inView) return
      map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 60, duration: 1200 })
      return
    }
    const gjBounds = featureCollectionBounds(getLayerGeojson(layer))
    if (Number.isFinite(gjBounds[0][0]) && Number.isFinite(gjBounds[0][1])) {
      map.fitBounds(gjBounds, { padding: 60, duration: 1200 })
    }
  }, [focusTarget, layers])

  // —— 手绘图层交互处理 ——
  // 惰性创建控制器；onChange 更新已绘要素数（完成按钮判定），onHistory 更新撤销/重做可用态
  const ensureController = (): TerraDrawController | null => {
    const map = mapRef.current
    if (!map) return null
    if (!drawControllerRef.current) {
      drawControllerRef.current = new TerraDrawController(
        map,
        (fc) => setDrawHasFeatures(fc.features.length > 0),
        (canUndo, canRedo) => {
          setDrawCanUndo(canUndo)
          setDrawCanRedo(canRedo)
        },
      )
    }
    return drawControllerRef.current
  }

  const startDraw = () => {
    const controller = ensureController()
    if (!controller) return
    // 新建会话：重置撤销栈（上一会话若取消过会残留），避免 undo 作用到已废弃要素
    controller.resetHistory()
    controller.setMode('point')
    setDrawMode('point')
    setEditLayerId(null)
    setDrawActive(true)
    setDrawHasFeatures(false)
    setDrawCanUndo(false)
    setDrawCanRedo(false)
  }

  // 编辑已建手绘图层：载入已有几何 → 进入 select 拖拽顶点
  const startEditLayer = (layerId: string) => {
    const controller = ensureController()
    const layer = layers.find((item) => item.id === layerId)
    if (!controller || !layer) return
    controller.load(getLayerGeojson(layer).features)
    controller.setMode('select')
    // 载入后自动选中首个要素：进编辑即出手柄，省去用户先点形状这一步
    controller.selectFirstFeature()
    setDrawMode('select')
    setEditLayerId(layerId)
    setDrawActive(true)
    setDrawHasFeatures(true)
    setDrawCanUndo(false)
    setDrawCanRedo(false)
  }

  const changeDrawMode = (mode: DrawMode) => {
    // 允许 select（编辑）：会话内切「编辑」即可选中要素拖顶点
    drawControllerRef.current?.setMode(mode)
    setDrawMode(mode)
  }

  // 「完成」：收集要素 → 停止绘制（避免与 deck 真实图层双渲染）。
  // 编辑会话走 PUT 更新已建图层几何；新建会话打开属性面板（命名/单位/字段）。
  const finishDraw = () => {
    const controller = drawControllerRef.current
    if (!controller) return
    const fc = controller.collect()
    controller.stop()
    setDrawActive(false)
    setDrawCanUndo(false)
    setDrawCanRedo(false)
    if (fc.features.length === 0) return
    if (editLayerId) {
      const targetId = editLayerId
      const layer = layers.find((item) => item.id === targetId)
      // 编辑：按该图层存的单位重算自动字段，PUT 持久化。fields 交给后端按注入后 properties 重建
      void updateDrawnLayerGeojson({
        id: targetId,
        features: fc.features,
        units: layer?.units ?? undefined,
      }).then((res) => {
        setEditLayerId(null)
        if (res) {
          focusLayer(targetId)
        } else {
          window.alert('保存失败：图层几何未更新。请重试或检查图层是否已改为只读。')
        }
      })
      return
    }
    const geometryType = geometryTypeFromFeatures(fc.features)
    setDrawPending({ features: fc.features, geometryType, color: drawColorRef.current })
  }

  // 属性面板「确认创建」：经后端注册（persist）→ addDrawnLayer 上屏
  const confirmDrawnLayer = async (input: {
    name: string
    features: GeoJSON.Feature[]
    fields: FieldSpec[]
    units: AutoUnitConfig
  }) => {
    const geometryType = geometryTypeFromFeatures(input.features)
    const res = await addDrawnLayer({
      id: slugify(input.name),
      name: input.name,
      geometry: geometryType,
      color: drawColorRef.current,
      features: input.features,
      fields: input.fields,
      crs: 'EPSG:4326',
      persist: true,
      units: input.units,
    })
    setDrawPending(null)
    if (res) {
      // 创建成功：地图飞到新图层（若在视野外）
      focusLayer(res.layerId)
    }
  }

  // 「取消」（工具栏或属性面板）：清空绘制并注销控制器，不持久化
  const cancelDraw = () => {
    drawControllerRef.current?.stop()
    setDrawActive(false)
    setDrawPending(null)
    setEditLayerId(null)
    setDrawHasFeatures(false)
    setDrawCanUndo(false)
    setDrawCanRedo(false)
  }

  // 面板「新建绘制」请求：drawRequestTick 递增 → 进入绘制会话
  // startDraw 每次渲染重建，用 ref 恒指向最新闭包，避免把函数放进依赖数组触发重跑
  useEffect(() => {
    startDrawRef.current = startDraw
  })
  useEffect(() => {
    if (drawRequestTick > 0) startDrawRef.current()
  }, [drawRequestTick])

  // 图层面板「编辑」请求：editLayerRequest 写入 → 进入编辑会话
  useEffect(() => {
    editStartRef.current = startEditLayer
  })
  useEffect(() => {
    if (editLayerRequest) editStartRef.current(editLayerRequest.layerId)
  }, [editLayerRequest])

  // 撤销/重做快捷键：绘制/编辑会话内监听 Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z。
  // capture 阶段 + 非输入框才拦截，避免劫持浏览器在 input 内的撤销；未进入绘制会话不挂监听。
  useEffect(() => {
    if (!drawActive) return
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (!target) return
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable) return
      if (!(event.ctrlKey || event.metaKey)) return
      const key = event.key.toLowerCase()
      if (key === 'z') {
        event.preventDefault()
        if (event.shiftKey) drawControllerRef.current?.redo()
        else drawControllerRef.current?.undo()
      } else if (key === 'y') {
        event.preventDefault()
        drawControllerRef.current?.redo()
      }
    }
    window.addEventListener('keydown', onKey, true)
    return () => window.removeEventListener('keydown', onKey, true)
  }, [drawActive])

  // 组件卸载时注销控制器（释放 adapter 渲染的图层与地图交互）
  useEffect(() => {
    return () => {
      drawControllerRef.current?.stop()
      drawControllerRef.current = null
    }
  }, [])

  // —— 绘制会话期间保持地图「不可拖动」——
  // maplibre 在 dragPan 开启时，每次 mousemove 都会把 canvas 光标强制改回 'grab'（覆盖
  // TerraDraw 的 crosshair），且真机拖动会把点击变成平移。因此绘制期间关闭 dragPan/dragRotate，
  // 并持续兜底（TerraDraw 拖动结束会自动重新 enable dragPan）保证全程关闭，退出绘制再恢复。
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (!drawActive) {
      map.dragPan.enable()
      map.dragRotate.enable()
      map.doubleClickZoom.enable()
      return
    }
    map.dragPan.disable()
    map.dragRotate.disable()
    map.doubleClickZoom.disable()
    let raf = 0
    const keepDisabled = () => {
      if (map.dragPan.isEnabled()) map.dragPan.disable()
      if (map.dragRotate.isEnabled()) map.dragRotate.disable()
      // 双击缩放同样会劫持「双击闭合」手势，保持关闭
      if (map.doubleClickZoom.isEnabled()) map.doubleClickZoom.disable()
      raf = requestAnimationFrame(keepDisabled)
    }
    raf = requestAnimationFrame(keepDisabled)
    return () => {
      cancelAnimationFrame(raf)
    }
  }, [drawActive])

  const visibleCount = layers.filter((layer) => layer.visible).length

  return (
    <section className={`map-workspace${drawActive ? ' is-drawing' : ''}`} aria-label="地图工作区">
      <div className="map-toolbar">
        <span className="map-toolbar__title">
          <Layers aria-hidden="true" />
          地图图层
        </span>
        <span className="map-toolbar__controls">
          <MapDrawToolbar
            active={drawActive}
            mode={drawMode}
            hasFeatures={drawHasFeatures}
            canUndo={drawCanUndo}
            canRedo={drawCanRedo}
            onStart={startDraw}
            onModeChange={changeDrawMode}
            onFinish={finishDraw}
            onCancel={cancelDraw}
            onUndo={() => drawControllerRef.current?.undo()}
            onRedo={() => drawControllerRef.current?.redo()}
          />
          <label className="map-toolbar__basemap">
            <span className="map-toolbar__basemap-label">底图</span>
            <select
              className="map-toolbar__basemap-select"
              value={basemapId}
              onChange={(event) => setBasemapId(event.target.value)}
              aria-label="切换地图底图"
            >
              {BASEMAPS.filter((basemap) => !basemap.mapboxStyle || mapboxToken).map((basemap) => (
                <option key={basemap.id} value={basemap.id}>
                  {basemap.name}
                </option>
              ))}
            </select>
          </label>
          {labelsCapable && (
            <label className="map-toolbar__labels">
              <input
                type="checkbox"
                checked={showLabels}
                onChange={(event) => setShowLabels(event.target.checked)}
                aria-label="显示底图文字标注"
              />
              标注
            </label>
          )}
          <label className="map-toolbar__labels">
            <input
              type="checkbox"
              checked={show3D}
              onChange={(event) => {
                setShow3D(event.target.checked)
                // 俯视时挤出不可见，开 3D 时给俯仰角让建筑立起来
                mapRef.current?.easeTo({ pitch: event.target.checked ? 50 : 0, duration: 700 })
              }}
              aria-label="3D 建筑显示"
            />
            3D建筑
          </label>
        </span>
        <span className="map-toolbar__hint">
          {visibleCount}/{layers.length} 个图层可见 · 在「数据」面板切换
        </span>
      </div>
      <div className="map-canvas" ref={containerRef}>
        {drawPending && (
          <DrawnAttributesPanel
            features={drawPending.features}
            geometryType={drawPending.geometryType}
            color={drawPending.color}
            onCancel={cancelDraw}
            onConfirm={confirmDrawnLayer}
          />
        )}
        {webglError && (
          <div className="map-fallback">
            <MapSchematic layers={layers} styles={layerStyles} />
            <div className="map-fallback__note">
              <p className="map-fallback__title">WebGL 不可用，已切换为示意地图</p>
              <p className="map-fallback__hint">
                当前浏览器没有可用的 WebGL2（通常是被禁用硬件加速，或在虚拟机/远程桌面中）。
                可在 Chrome/Edge 设置中开启「使用硬件加速」后刷新，即可显示完整交互地图。
              </p>
              <button type="button" onClick={() => window.location.reload()}>
                重新加载
              </button>
            </div>
          </div>
        )}
        {/* 图上图例：展示当前分析模块（默认最近完成板块）的同源图例，右下角可收起 */}
        <MapLegendOverlay />
      </div>
    </section>
  )
}
