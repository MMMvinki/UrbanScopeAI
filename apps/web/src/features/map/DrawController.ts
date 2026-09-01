import type { Map as MapLibreMap } from 'maplibre-gl'
import {
  TerraDraw,
  TerraDrawLineStringMode,
  TerraDrawPointMode,
  TerraDrawPolygonMode,
  TerraDrawSelectMode,
  TerraDrawModeUndoRedo,
  TerraDrawSessionUndoRedo,
  ValidateMinAreaSquareMeters,
  ValidateNotSelfIntersecting,
} from 'terra-draw'
import { TerraDrawMapLibreGLAdapter } from 'terra-draw-maplibre-gl-adapter'
import type { GeometryType } from '../layers/layerTypes'

/** 绘制模式 key（与 TerraDraw mode 实例的 mode 名一致） */
export type DrawMode = 'point' | 'linestring' | 'polygon' | 'select'

/** 从要素集合推断图层 geometry（与后端 geometry_type_for 同优先级：Polygon > Line > Point）。
 *  混合绘制时优先面/线，保证 deck 走 GeoJsonLayer（对点/线/面皆渲染友好）。 */
export function geometryTypeFromFeatures(features: GeoJSON.Feature[]): GeometryType {
  const types = new Set(features.map((feature) => feature.geometry?.type).filter(Boolean))
  if (types.has('Polygon') || types.has('MultiPolygon')) return 'Polygon'
  if (types.has('LineString') || types.has('MultiLineString')) return 'LineString'
  return 'Point'
}

/** 几何类型 → TerraDraw 绘制模式名（用于 addFeatures 补 properties.mode）。
 *  仅处理手绘会出现的单几何类型（Point/LineString/Polygon）。Multi* 不在 TerraDraw
 *  validator 允许的几何类型集内（Polygon/LineString/Point），且手绘不会产生，返回 null 由调用方丢弃。 */
function drawModeForGeometry(type: string | undefined): DrawMode | null {
  if (type === 'Polygon') return 'polygon'
  if (type === 'LineString') return 'linestring'
  if (type === 'Point') return 'point'
  return null
}

/** 保留键集合：TerraDraw 在 properties 里写入的中间态键，收集 FC 时需剔除 */
const RESERVED_PROPS: ReadonlySet<string> = new Set([
  'mode',
  'midpoint',
  'midPoint',
  'selectionpoint',
  'selectionPoint',
  'selected',
  'id',
  'currentlyDrawing',
  'committedCoordinateCount',
  'provisionalCoordinateCount',
])

/** 剔除 TerraDraw 内部属性，得到用户真正画的要素 */
export function stripTerraDrawProps(properties: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(properties ?? {})) {
    if (key && !RESERVED_PROPS.has(key)) out[key] = value
  }
  return out
}

/** guidance 判定：绘制期 TerraDraw 生成的辅助点（顶点提示/closing/选中），收集时一律丢弃。
 *  仅纳入实际在 snapshot 出现过的键（closingPoint/midPoint/selection*），避免误伤用户自有属性。 */
const GUIDANCE_PROPS: ReadonlySet<string> = new Set([
  'midpoint',
  'midPoint',
  'selectionpoint',
  'selectionPoint',
  'closingPoint',
])

function isGuidanceFeature(properties: Record<string, unknown>): boolean {
  for (const key of GUIDANCE_PROPS) if (properties[key] === true) return true
  return false
}

/** 环形归一化：去掉连续重复顶点 + 若未闭合则补上首顶点，产出标准闭合环。
 *  TerraDraw 在「逐顶点增长」时会在尾部留下一个临时重复点，且用户未闭合点「完成」时环是开的。 */
function normalizeRing(ring: number[][]): number[][] {
  const out: number[][] = []
  for (const coord of ring) {
    const last = out[out.length - 1]
    if (!last || last[0] !== coord[0] || last[1] !== coord[1]) out.push(coord)
  }
  const first = out[0]
  const last = out[out.length - 1]
  if (first && last && (first[0] !== last[0] || first[1] !== last[1])) out.push(first)
  return out
}

/** 归一化几何：Polygon/MultiPolygon 重写为去重且闭合的环；其余几何原样返回。 */
function normalizeGeometry(geometry: GeoJSON.Geometry): GeoJSON.Geometry {
  if (geometry.type === 'Polygon') {
    return { type: 'Polygon', coordinates: [normalizeRing(geometry.coordinates[0])] }
  }
  if (geometry.type === 'MultiPolygon') {
    return {
      type: 'MultiPolygon',
      coordinates: geometry.coordinates.map((polygon) => [normalizeRing(polygon[0])]),
    }
  }
  return geometry
}

/** 取 Polygon/MultiPolygon 的"首个环"，用于统计有效顶点数（绘制产物恒为单 Polygon，这里兼容一下） */
function firstRingOf(geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon): number[][] {
  if (geometry.type === 'MultiPolygon') return geometry.coordinates[0][0]
  return geometry.coordinates[0]
}

/** 归一化后有效顶点数（不同坐标数量，去重后判断至少 3 个点才能构成面） */
function polygonDistinctVertexCount(geometry: GeoJSON.Polygon | GeoJSON.MultiPolygon): number {
  const seen = new Set<string>()
  for (const coord of firstRingOf(geometry)) seen.add(`${coord[0]},${coord[1]}`)
  return seen.size
}

/**
 * Terra Draw 生命周期封装：地图手绘点/线/面，adapter 负责在地图上叠加渲染。
 *
 * - 构造时传入 maplibre 实例 + 三种绘制 mode + 辅助 select mode。
 * - `start()` 启用（进入指定 mode）；`stop()` 清空并注销 adapter（避免与 deck 真实图层双渲染）。
 * - `collect()`：getSnapshot() 返回 Feature[]，剔除中间态后包成 FeatureCollection，供 addDrawnLayer 用。
 * - 完成后调用方应 stop() 释放；「取消」= clear() 丢弃全部已绘要素。
 */
export class TerraDrawController {
  private draw: TerraDraw
  private currentMode: DrawMode = 'select'
  private onChange?: (fc: GeoJSON.FeatureCollection) => void
  private onHistory?: (canUndo: boolean, canRedo: boolean) => void

  constructor(
    map: MapLibreMap,
    onChange?: (fc: GeoJSON.FeatureCollection) => void,
    onHistory?: (canUndo: boolean, canRedo: boolean) => void,
  ) {
    this.onChange = onChange
    this.onHistory = onHistory
    this.draw = new TerraDraw({
      adapter: new TerraDrawMapLibreGLAdapter({ map }),
      // 撤销/重做：TerraDraw 1.32.3 内置（modeLevel 单步 + sessionLevel 会话级）：
      // 实例方法 undo()/redo()/canUndo()。不用内置 keyboardShortcuts——由 MapWorkspace 自建 keydown
      // 监听 Ctrl+Z / Ctrl+Y，避免与自定义绑定重复触发。
      undoRedo: {
        modeLevel: new TerraDrawModeUndoRedo(),
        sessionLevel: new TerraDrawSessionUndoRedo(),
      },
      modes: [
        new TerraDrawPointMode(),
        new TerraDrawLineStringMode({ validation: (feature) => {
          // 线必须至少 2 个点，且不自相交（返回 { valid, reason } 与 Validation 类型一致）
          const coords = feature.geometry.coordinates as unknown as unknown[][]
          if (coords.length < 2) return { valid: false, reason: '线需要至少 2 个点' }
          return ValidateNotSelfIntersecting(feature)
        } }),
        new TerraDrawPolygonMode({ validation: (feature) => {
          // TerraDraw 每次「点击加顶点」都会跑一次 validation：逐顶点增长的中途，环还只有 2 个不同点、面积恒为 0。
          // 若直接用 ValidateMinAreaSquareMeters 会把这些中间态判为非法 → updatePolygon 返回 null →
          // onLeftClick 提前 return，currentCoordinate 卡死，面永远画不出来。所以必须先感知「仍在绘制中」。
          const props = (feature.properties ?? {}) as Record<string, unknown>
          const ring = (feature.geometry as { coordinates?: number[][][] }).coordinates?.[0] ?? []
          const distinctVertices = new Set(ring.map((c) => `${c[0]},${c[1]}`)).size
          const drawing = props.currentlyDrawing === true
          // 绘制中（currentlyDrawing 为真）放行，允许环自由增长到任意顶点数；未完成且顶点过少也放行。
          if (drawing || distinctVertices < 3) return { valid: true }
          // 仅在真正完成时校验：拒绝过小（<1 m²）退化环 + 自相交。
          const area = ValidateMinAreaSquareMeters(feature, 1)
          if (!area.valid) return area
          return ValidateNotSelfIntersecting(feature)
        } }),
        // select 模式 flags 必须按绘制模式显式开启，否则 select() 里 flags[mode].feature 为 undefined，
        // 要素永远选不中、坐标手柄也不出现（「点不中/拖不动顶点」的根源）。
        // polygon/linestring 开 coordinates.draggable（拖单个顶点）+ feature.draggable（整体拖），
        // point 只需 feature.draggable（点无顶点，整体拖即移动）；linestring 额外开启中点插入。
        new TerraDrawSelectMode({
          flags: {
            polygon: {
              feature: { draggable: true, coordinates: { draggable: true } },
            },
            linestring: {
              feature: { draggable: true, coordinates: { draggable: true, midpoints: { draggable: true } } },
            },
            point: {
              feature: { draggable: true },
            },
          },
          // 顶点手柄样式：默认 6px 蓝点太容易看不见（尤其在大面/深色底图上）。
          // 改成白色实心 + 橙色描边环，选中的顶点用黄色，任何背景下都能一眼看到。
          styles: {
            selectionPointWidth: 9,
            selectionPointColor: '#ffffff',
            selectionPointOpacity: 1,
            selectionPointOutlineColor: '#d9480f',
            selectionPointOutlineWidth: 2.5,
            selectionPointOutlineOpacity: 1,
            selectedPointWidth: 12,
            selectedPointColor: '#ffd43b',
            selectedPointOpacity: 1,
            selectedPointOutlineColor: '#d9480f',
            selectedPointOutlineWidth: 2.5,
            selectedPointOutlineOpacity: 1,
            midPointWidth: 7,
            midPointColor: '#ffffff',
            midPointOpacity: 1,
            midPointOutlineColor: '#1971c2',
            midPointOutlineWidth: 2,
            midPointOutlineOpacity: 1,
          },
        }),
      ],
    })

    // `finish`：某个要素绘制完成 / select 模式完成选择；`change`：几何有变更则回调快照
    this.draw.on('finish', () => this.onChange?.(this.collect()))
    this.draw.on('change', (_ids, type) => {
      // 'styling' 是广告牌色调等非几何变更，不触发快照回传
      if (type !== 'styling') this.onChange?.(this.collect())
    })
    // 撤销栈变化 → 通知 toolbar 刷新可撤销/可重做状态
    this.draw.on('history', () => {
      this.onHistory?.(this.canUndo(), this.canRedo())
    })
  }

  /** 启用绘制：进入指定模式（自动 stop 上一个模式并 start 当前） */
  setMode(mode: DrawMode): void {
    this.currentMode = mode
    if (!this.draw.enabled) this.draw.start()
    this.draw.setMode(mode)
  }

  /** 当前处于哪个绘制模式（select=只选不画/空闲） */
  getMode(): DrawMode {
    return this.currentMode
  }

  /** 收集当前全部已绘要素，剔除中间态后包成 FeatureCollection */
  collect(): GeoJSON.FeatureCollection {
    const features = this.draw
      .getSnapshot()
      // 丢弃绘制期辅助点（closing/midpoint/selection 等），避免混入真实图层
      .filter((feature) => !isGuidanceFeature((feature.properties ?? {}) as Record<string, unknown>))
      .map((feature) => {
        const geometry = normalizeGeometry(feature.geometry)
        const properties = stripTerraDrawProps(feature.properties ?? {})
        // 仍处于绘制中（未闭合）的面：归一化后若不足 3 个不同顶点，无法构成面，丢弃
        if (
          (geometry.type === 'Polygon' || geometry.type === 'MultiPolygon') &&
          polygonDistinctVertexCount(geometry) < 3
        ) {
          return null
        }
        return { ...feature, geometry, properties } as GeoJSON.Feature
      })
      .filter((feature): feature is GeoJSON.Feature => feature !== null)
    return { type: 'FeatureCollection', features }
  }

  /** 丢弃全部已绘要素（取消） */
  clear(): void {
    this.draw.clear()
  }

  /** 注销：清空 store + 移除 adapter 渲染的图层，释放地图交互 */
  stop(): void {
    try {
      this.draw.clear()
      this.draw.stop()
    } catch {
      // stop 在未 start 状态下可能抛错，忽略
    }
  }

  /** 载入已有图层几何（编辑已建手绘图层）：清空 store 后 addFeatures，供 select 模式选中改顶点。
   *  TerraDraw 的 clear()/addFeatures()/clearUndoRedoHistory() 都要求先 start()（checkEnabled 抛错），
   *  故先插桩 start。载入即重置撤销栈——编辑会话的 undo 只追踪会话内改动，不追踪「载入」本身。 */
  load(features: GeoJSON.Feature[]): void {
    if (!this.draw.enabled) this.draw.start()
    this.draw.clear()
    // TerraDraw addFeatures 要求每个要素 properties.mode 指向已实例化模式，并经该模式 validator 校验。
    // 但 collect() 持久化时已 strip 掉 mode（RESERVED_PROPS），重逢的几何没有 mode → _modes[undefined]
    // 一律判 invalid → 0 个载入。故须按几何类型补回 mode（点/线/面各自 draw 模式，仅手绘单几何类型）。
    const withMode = features
      .map((feature) => {
        const mode = drawModeForGeometry(feature.geometry?.type)
        if (!mode) return null
        return { ...feature, properties: { ...(feature.properties ?? {}), mode } } as GeoJSON.Feature
      })
      .filter((f): f is GeoJSON.Feature => f !== null)
    if (withMode.length > 0) this.draw.addFeatures(withMode as never[])
    this.draw.clearUndoRedoHistory()
  }

  /** 重置撤销/重做历史（新建绘制会话或编辑会话开始时调用，丢弃上一会话残留的栈） */
  resetHistory(): void {
    if (!this.draw.enabled) this.draw.start()
    this.draw.clearUndoRedoHistory()
  }

  /** 撤销上一步（清空/重绘由 TerraDraw adapter 自动处理） */
  undo(): void {
    this.draw.undo()
  }

  /** 重做下一步 */
  redo(): void {
    this.draw.redo()
  }

  canUndo(): boolean {
    return this.draw.canUndo()
  }

  canRedo(): boolean {
    return this.draw.canRedo()
  }

  /** 程序化选中某要素（编辑已建图层时进入 select 后聚焦它） */
  selectFeature(id: string | undefined): void {
    if (id && this.draw.hasFeature(id)) this.draw.selectFeature(id)
  }

  /** 载入后自动选中首个要素：进「编辑顶点」不再需要手动点形状，直接显示顶点手柄。
   *  要素 id 在快照顶层 feature.id（不在 properties.id——那会被 RESERVED_PROPS 剥掉），
   *  故从顶层取首个 id 选中。 */
  selectFirstFeature(): void {
    const snapshot = this.draw.getSnapshot()
    const id = snapshot[0]?.id as string | undefined
    if (id) this.selectFeature(id)
  }
}
