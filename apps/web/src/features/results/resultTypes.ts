/** 成果卡片类型：与后端 ResultWidget.WidgetType 对齐 */
export type WidgetType = 'metric' | 'chart' | 'list' | 'table' | 'map' | 'image'

export interface WidgetSeries {
  name: string
  value: number
}

/** 成果卡片：右侧成果栏里的一个结果单元 */
export interface ResultWidget {
  id: string
  title: string
  widgetType: WidgetType
  /** 主数值 / 文本，如 "82%"、"已生成" */
  value?: string
  subtitle?: string
  unit?: string
  /** 结构性数据：图表系列 / 表格行 / 清单项 / 结果图层 id / 图片静态路由路径 / ECharts option */
  data?: {
    series?: WidgetSeries[]
    rows?: Record<string, string | number>[]
    items?: string[]
    layerIds?: string[]
    url?: string
    option?: Record<string, unknown>
    height?: number
  }
  /** 结果依据（证据链） */
  evidence?: string[]
  /** 关联图层 id：用于「查看图层 / 定位地图」 */
  layerId?: string
}

/** 一次分析产出的成果组 */
export interface ResultGroup {
  id: string
  title: string
  widgets: ResultWidget[]
  createdAt?: string
  /** 归属 run id：成果页「运行记录」打开 RunArchiveModal 用 */
  runId?: string
}
