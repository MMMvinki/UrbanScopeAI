/** 图层来源：平台数据 / 用户上传 / 分析生成 / 手绘 / 本地数据目录 */
export type LayerSource = 'platform' | 'uploaded' | 'generated' | 'drawn' | 'folder'

export type GeometryType = 'Polygon' | 'Point' | 'LineString' | 'Table'

export interface FieldSpec {
  name: string
  type: string
  description?: string | null
}

export interface LayerMetadata {
  id: string
  name: string
  source: LayerSource
  geometry: GeometryType
  color: string
  visible: boolean
  featureCount: number
  crs: string
  description?: string
  /** 生成图层的来源工具，例如 coverage 工具 */
  generatedBy?: string
  /** 图层字段（样式面板分档设色用） */
  fields?: FieldSpec[]
  /** 平台数据只读：数据内容不可改，样式/名字可改（🔒 角标，禁止删除） */
  readonly?: boolean
  /** 源数据磁盘路径（folder 目录/uploaded 数据），便于回文件夹定位 */
  dataPath?: string | null
  /** folder 源图层的源内相对路径（如 交通/e.csv），文件路径视图按它细分文件夹 */
  relPath?: string | null
  /** 源文件格式（csv/shp/gpkg/geojson…），图层角标用；非文件型为空 */
  dataType?: string | null
  /** 几何按需加载：重图层不进 bulk 端点，显隐时惰性拉取 /layers/{id}/geojson */
  lazy?: boolean
  /** 图层类型：vector（默认）| raster（栅格瓦片渲染） */
  kind?: 'vector' | 'raster'
  /** raster 图层瓦片 URL 模板（{z}/{x}/{y} 占位） */
  tileUrl?: string
  /** 真实范围 [minx,miny,maxx,maxy] */
  bbox?: number[] | null
  /** 样式模型（Sprint 2：随后端 /layers 下发，PATCH 持久化） */
  opacity?: number | null
  /** 填充不透明度 0-1（独立于描边；None=跟随 opacity） */
  fillOpacity?: number | null
  /** 描边不透明度 0-1（独立于填充；None=跟随 opacity） */
  strokeOpacity?: number | null
  strokeColor?: string | null
  strokeWidth?: number | null
  radius?: number | null
  fillColor?: string | null
  styleMode?: string | null
  visualVariables?: VisualVariables | null
  labelField?: string | null
  /** 手绘图层自动字段单位偏好：{length:'m'|'km', area:'m2'|'hm2'|'km2'} */
  units?: { length?: 'm' | 'km'; area?: 'm2' | 'hm2' | 'km2' } | null
}

export type LayerAction =
  | 'zoom'
  | 'show-only'
  | 'style'
  | 'duplicate'
  | 'rename'
  | 'group'
  | 'preview'
  | 'edit'
  | 'data-path'
  | 'delete'

/** 分档设色描述（后端算 breaks，前端按要素取色） */
export interface GraduatedSpec {
  field: string
  method: string
  classes: number
  palette: string[]
  breaks: number[]
}

/** 分类配色描述：字段唯一值 → 颜色映射（后端算好，前端按值查色） */
export interface CategorizedSpec {
  field: string
  classes: Array<{ value: string; color: string }>
}

/** 视觉变量（透明度/点大小按字段渐变） */
export interface VisualVariableSpec {
  field: string
  method: string
  classes: number
  range: [number, number]
  breaks: number[]
}

/** visualVariables 合并描述：graduated/categorized renderer + size/opacity 渐变 + filter/highlight 叠加 */
export interface VisualVariables {
  renderer?: 'graduated' | 'categorized'
  graduated?: GraduatedSpec
  categorized?: CategorizedSpec
  size_variable?: VisualVariableSpec
  opacity_variable?: VisualVariableSpec
  /** 图层过滤叠加：只渲染满足条件的要素（与后端 filter 语法一致） */
  filter?: Record<string, unknown>
  /** 高亮叠加：满足条件的要素用 color 标出 */
  highlight?: { filter: Record<string, unknown>; color: string }
}

/** 图层样式（Sprint 1 前端本地，session 级；Sprint 2 随后端 layer_style 事件/图层下发） */
export interface LayerStyle {
  color: string
  /** 整体透明度 0-1（兼容旧值；新样式用 fillOpacity/strokeOpacity 分别控制） */
  opacity: number
  /** 填充不透明度 0-1（None/缺省=跟随 opacity） */
  fillOpacity?: number | null
  /** 描边不透明度 0-1（None/缺省=跟随 opacity） */
  strokeOpacity?: number | null
  strokeColor: string
  /** 描边宽度（px） */
  strokeWidth: number
  /** 点图层半径（px），仅点图层生效 */
  radius: number
  fillColor: string
  /** 渲染模式：single | graduated（categorized 预留） */
  styleMode?: 'single' | 'graduated' | 'categorized'
  /** 分档/渐变描述（styleMode=graduated 时生效） */
  visualVariables?: VisualVariables
  /** 标注字段（预留） */
  labelField?: string
}

/** 图层分组（前端本地，session 级） */
export interface LayerGroup {
  id: string
  name: string
  layerIds: string[]
}
