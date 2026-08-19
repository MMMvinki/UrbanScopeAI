import { useEffect, useRef, useState } from 'react'
import { AttributionControl, Map as MapLibreMap, NavigationControl } from 'maplibre-gl'
import { MapboxOverlay } from '@deck.gl/mapbox'
import 'maplibre-gl/dist/maplibre-gl.css'
import { Layers } from 'lucide-react'
import { useAppStore } from '../../store/useAppStore'
import {
  BASEMAPS,
  MAP_CENTER,
  buildBasemapStyle,
  featureCollectionBounds,
  getLayerGeojson,
  mapLayerIdFor,
  sourceIdFor,
  styleSpecFor,
} from './mapLayers'
import { buildDeckLayers } from './deckLayers'
import { MapSchematic } from './MapSchematic'

/**
 * WebGL2 预探测：MapLibre 在 `new Map()` 构造器内**同步**初始化 painter，
 * 失败时 error 事件在监听器注册前就被丢弃（只会 console.error），导致静默空白。
 * 因此必须在构造前探测，并在构造后立即检查 painter 状态。
 */
let cachedWebgl: boolean | null = null

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

  const layers = useAppStore((s) => s.layers)
  const layerStyles = useAppStore((s) => s.layerStyles)
  const geojsonTick = useAppStore((s) => s.geojsonTick)
  const focusTarget = useAppStore((s) => s.focusTarget)
  const basemapId = useAppStore((s) => s.basemapId)
  const setBasemapId = useAppStore((s) => s.setBasemapId)

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
      layers.find((layer) => layer.id === 'study_area') ?? layers.find((layer) => layer.bbox?.length === 4)
    if (!target) return
    const bbox = target.bbox
    if (bbox?.length === 4) {
      map.fitBounds([[bbox[0], bbox[1]], [bbox[2], bbox[3]]], { padding: 40, duration: 0 })
    } else {
      map.fitBounds(featureCollectionBounds(getLayerGeojson(target)), { padding: 40, duration: 0 })
    }
  }, [loaded]) // eslint-disable-line react-hooks/exhaustive-deps

  // 底图切换：setStyle 会清空 MapLibre 内置数据图层，等 style.load 后递增 styleTick 重同步
  // （deck.gl 路径由 MapboxOverlay 在 interleaved 模式下自动重注入，无需手动处理）
  useEffect(() => {
    const map = mapRef.current
    if (!loaded || !map?.painter) return
    const basemap = BASEMAPS.find((b) => b.id === basemapId) ?? BASEMAPS[0]
    map.setStyle(buildBasemapStyle(basemap))
    map.once('style.load', () => setStyleTick((tick) => tick + 1))
  }, [basemapId]) // eslint-disable-line react-hooks/exhaustive-deps

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
    if (bbox?.length === 4) {
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

  const visibleCount = layers.filter((layer) => layer.visible).length

  return (
    <section className="map-workspace" aria-label="地图工作区">
      <div className="map-toolbar">
        <span className="map-toolbar__title">
          <Layers aria-hidden="true" />
          地图图层
        </span>
        <label className="map-toolbar__basemap">
          <span className="map-toolbar__basemap-label">底图</span>
          <select
            className="map-toolbar__basemap-select"
            value={basemapId}
            onChange={(event) => setBasemapId(event.target.value)}
            aria-label="切换地图底图"
          >
            {BASEMAPS.map((basemap) => (
              <option key={basemap.id} value={basemap.id}>
                {basemap.name}
              </option>
            ))}
          </select>
        </label>
        <span className="map-toolbar__hint">
          {visibleCount}/{layers.length} 个图层可见 · 在「数据」面板切换
        </span>
      </div>
      <div className="map-canvas" ref={containerRef}>
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
      </div>
    </section>
  )
}
