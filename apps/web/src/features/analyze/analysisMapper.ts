/**
 * 综合分析板块：把后端 `structured_analysis` 映射为「板块视图模型」（纯函数，不含 UI）。
 * 所有数字均来自 structured_analysis 的 stats / evidence / summary，不解析长工具文本。
 */

import type {
  AnalysisModuleResult,
  AnalysisReadinessItem,
  StructuredAnalysisResult,
} from './analysisTypes'

/* ---------------- 视图模型 ---------------- */

export interface StatMetric {
  label: string
  value: string
  hint?: string
}

export interface LegendRow {
  code: string
  label: string
  color: string
  cells: number | null
  areaKm2: number | null
  points: number | null
}

export interface LegendGroup {
  title: string
  rows: LegendRow[]
  note?: string
}

export interface FunnelStep {
  label: string
  value: number
}

/** 证据链分组：一项「数据来源 / 方法 / 口径 / 排除」主题，下挂若干可读行 */
export interface EvidenceGroup {
  title: string
  icon?: string
  lines: string[]
}

/** 对比条形图的一条柱 */
export interface ChartBar {
  label: string
  value: number
  unit?: string
}

/** 一组对比条形图（一份 stats 的某项分类/口径对比） */
export interface ChartGroup {
  title: string
  bars: ChartBar[]
  unit?: string
  note?: string
}

export interface ReadinessRow {
  moduleId: string
  moduleLabel: string
  status: 'ready' | 'blocked'
  requiredData: string[]
  reason: string
}

export type SectionKind = 'plan' | 'module' | 'boundary'

export interface AnalysisSection {
  id: string
  index: number
  kind: SectionKind
  title: string
  lead?: string
  status?: 'completed' | 'blocked'
  achievedLevel?: string
  runId?: string
  summary?: string
  metrics?: StatMetric[]
  legendGroups?: LegendGroup[]
  funnel?: FunnelStep[]
  charts?: ChartGroup[]
  evidence?: EvidenceGroup[]
  limitations?: string[]
  outputLayer?: string
  stopCode?: string
  blockedReason?: string
  degradeHint?: string
  // plan 专属
  routeRule?: string
  routeReason?: string
  overallSummary?: string
  readiness?: ReadinessRow[]
  // boundary 专属
  boundaryItems?: string[]
}

/* ---------------- 常量：标题 / 标签 / 配色 / 降级提示 ---------------- */

const MODULE_META: Record<string, { label: string; lead: string }> = {
  activity_temporal: {
    label: '活动时序画像',
    lead: '什么时候、哪里更活跃——项目活动时序画像',
  },
  commercial_supply: {
    label: '商业供给格局',
    lead: '商业在哪里、供给有多少——商业供给与集聚格局',
  },
  commercial_vitality: {
    label: '商业供给 × 昼夜活力综合诊断',
    lead: '供给是否跟着活动走——商业供给与昼夜活力综合诊断',
  },
  public_service: {
    label: '公共服务设施资源画像',
    lead: '教育、医疗、体育、便民与应急——公共服务设施资源画像',
  },
  population_profile: {
    label: '人口·就业画像',
    lead: '居住与就业在哪里、结构与职住比——人口·就业画像',
  },
  commercial_rail_accessibility: {
    label: '商业供给 × 地铁真实步行可达',
    lead: '从地铁出入口出发，5/10/15 分钟真实步行能到多少商业——商业×轨交可达性',
  },
}

const RHYTHM_ORDER = ['D_H_N_L', 'D_L_N_H', 'D_H_N_H', 'D_L_N_L', 'ND'] as const
const RHYTHM_LABELS: Record<string, string> = {
  D_H_N_L: '昼高夜低',
  D_L_N_H: '昼低夜高',
  D_H_N_H: '全天较高',
  D_L_N_L: '持续较低',
  ND: '无数据',
}
const RHYTHM_COLORS: Record<string, string> = {
  D_H_N_L: '#f59e0b',
  D_L_N_H: '#8b5cf6',
  D_H_N_H: '#ef4444',
  D_L_N_L: '#94a3b8',
  ND: '#cbd5e1',
}

const QUADRANT_ORDER = ['S_L_V_H', 'S_H_V_L', 'S_H_V_H', 'S_L_V_L', 'ND'] as const
const QUADRANT_LABELS: Record<string, string> = {
  S_L_V_H: '高活力 · 低供给',
  S_H_V_L: '低活力 · 高供给',
  S_H_V_H: '高活力 · 高供给',
  S_L_V_L: '低活力 · 低供给',
  ND: '无数据',
}
const QUADRANT_COLORS: Record<string, string> = {
  S_L_V_H: '#f59e0b',
  S_H_V_L: '#3b82f6',
  S_H_V_H: '#ef4444',
  S_L_V_L: '#94a3b8',
  ND: '#cbd5e1',
}

const SUPPLY_COLORS: Record<string, string> = {
  high_supply: '#ef4444',
  low_supply: '#94a3b8',
}

/** 公共服务设施：资源相对分布三类颜色（与地图上的分类图例按同一相对口径描述） */
const FACILITY_CLASS_COLORS: Record<string, string> = {
  high: '#0d9488',
  low: '#f59e0b',
  none: '#cbd5e1',
}

const RICHNESS_COLORS: Record<string, string> = {
  0: '#e2e8f0',
  1: '#a5b4fc',
  2: '#6366f1',
  3: '#0ea5e9',
  4: '#0d9488',
}

/** 商业大类 → 稳定配色（与后端 commercial_poi.COMMERCIAL_CATEGORY_COLORS 同源） */
const COMMERCIAL_CATEGORY_ORDER = ['餐饮服务', '购物服务', '生活服务', '体育休闲服务', '文化娱乐服务', '其它'] as const
const COMMERCIAL_CATEGORY_COLORS: Record<string, string> = {
  餐饮服务: '#f4a261',
  购物服务: '#e76f51',
  生活服务: '#2a9d8f',
  体育休闲服务: '#457b9d',
  文化娱乐服务: '#9b5de5',
  其它: '#8a8f98',
}

/** 等时圈分钟档 → 同色系（内→外 深→浅；与后端 isochrone 配色同源） */
const ISOCHRONE_COLORS: Record<string, string> = {
  5: '#0c4a6e',
  10: '#0ea5e9',
  15: '#bae6fd',
}

/** 人口·就业画像：三个群体（居住口径）的相对集聚图例配色 */
const POPULATION_GROUP_COLORS: Record<string, string> = {
  youth: '#2563eb',
  high_edu: '#0d9488',
  mid_high_income: '#d97706',
}

/** 阻断板块的降级提示（静态口径，与《规划分析内容标准》一致） */
const DEGRADE_HINTS: Record<string, string> = {
  activity_temporal: '缺少活动数据时，仍可单独做商业供给格局（若商业数据就绪）。',
  commercial_supply: '缺少商业数据时，仍可单独做活动时序画像（若活动数据就绪）。',
  commercial_vitality:
    '综合诊断依赖活动与商业两个前置板块；任一缺失则无法交叉，可分别查看活动时序与商业供给的单项结果。',
  public_service:
    '四类 POI 分片或类别字段缺失时公共服务板块不运行；其余已就绪的活动、商业与综合诊断板块不受影响。',
  population_profile:
    '缺少常住数量或常住画像数据时人口·就业画像不运行；其余已就绪的活动、商业、综合诊断与公共服务板块不受影响。',
  commercial_rail_accessibility:
    '缺少地铁出入口数据或 MAPBOX_TOKEN 时真实步行可达不运行；仍可单独查看商业供给格局与分类商业 POI。',
}

/* ---------------- 数字格式化 ---------------- */

export function formatInt(n: number): string {
  return Math.round(n).toLocaleString('en-US')
}

export function formatNumber(n: number): string {
  if (Number.isInteger(n)) return n.toLocaleString('en-US')
  return String(Math.round(n * 100) / 100)
}

export function formatArea(km2: number): string {
  return `${formatNumber(km2)} km²`
}

/* ---------------- 安全取值 ---------------- */

function str(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function num(v: unknown): number | null {
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? n : null
}

function obj(v: unknown): Record<string, unknown> {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {}
}

function strArr(v: unknown): string[] {
  return Array.isArray(v) ? v.map((item) => String(item)) : []
}

/* ---------------- 解析后端 structured_analysis ---------------- */

function parseReadiness(raw: unknown): AnalysisReadinessItem | null {
  if (!raw || typeof raw !== 'object') return null
  const o = raw as Record<string, unknown>
  return {
    module_id: str(o.module_id),
    status: o.status === 'blocked' ? 'blocked' : 'ready',
    required_data: strArr(o.required_data),
    reason: str(o.reason),
  }
}

function parseModule(raw: unknown): AnalysisModuleResult | null {
  if (!raw || typeof raw !== 'object') return null
  const o = raw as Record<string, unknown>
  return {
    module_id: str(o.module_id),
    status: o.status === 'blocked' ? 'blocked' : 'completed',
    skill_id: typeof o.skill_id === 'string' ? o.skill_id : null,
    run_id: typeof o.run_id === 'string' ? o.run_id : null,
    achieved_level: str(o.achieved_level),
    summary: str(o.summary),
    stats: obj(o.stats),
    evidence: obj(o.evidence),
    limitations: strArr(o.limitations),
    output_layer: typeof o.output_layer === 'string' ? o.output_layer : null,
    stop_code: typeof o.stop_code === 'string' ? o.stop_code : null,
    blocked_reason: typeof o.blocked_reason === 'string' ? o.blocked_reason : null,
  }
}

/** 从 SSE execution 事件的 data.structured_analysis 安全解析；非综合分析返回 null */
export function parseStructuredAnalysis(raw: unknown): StructuredAnalysisResult | null {
  if (!raw || typeof raw !== 'object') return null
  const o = raw as Record<string, unknown>
  const parsedModules = Array.isArray(o.modules)
    ? o.modules.map(parseModule).filter((m): m is AnalysisModuleResult => m != null)
    : []
  const routeRule = str(o.route_rule)
  // 没有路由规则且没有板块结果，视为非综合分析结构，返回 null（保留上一次结果不被清空）
  if (!routeRule && parsedModules.length === 0) return null
  return {
    route_id: str(o.route_id),
    route_rule: routeRule,
    route_reason: str(o.route_reason),
    requested_modules: strArr(o.requested_modules),
    ready_modules: strArr(o.ready_modules),
    blocked_modules: strArr(o.blocked_modules),
    data_readiness: Array.isArray(o.data_readiness)
      ? o.data_readiness.map(parseReadiness).filter((r): r is AnalysisReadinessItem => r != null)
      : [],
    modules: parsedModules,
    overall_summary: str(o.overall_summary),
  }
}

/* ---------------- 板块视图模型构建 ---------------- */

/** 指标构建：只在数值存在时才加入 */
function metric(label: string, value: string | null, hint?: string): StatMetric | null {
  if (value == null || value === '') return null
  return { label, value, hint }
}

function buildMetrics(module: AnalysisModuleResult): StatMetric[] {
  const stats = module.stats
  if (module.module_id === 'activity_temporal') {
    const thr = obj(stats.thresholds)
    const list = [
      metric('100m 网格', num(stats.grid_count) != null ? formatInt(num(stats.grid_count) as number) : null),
      metric('有效活动格', num(stats.valid_cells) != null ? formatInt(num(stats.valid_cells) as number) : null),
      metric('无数据格', num(stats.nd_cells) != null ? formatInt(num(stats.nd_cells) as number) : null),
      metric('日间阈值', num(thr.day_activity) != null ? formatNumber(num(thr.day_activity) as number) : null),
      metric('夜间阈值', num(thr.night_activity) != null ? formatNumber(num(thr.night_activity) as number) : null),
      metric(
        '标准网格面积',
        num(stats.standard_grid_area_km2) != null
          ? formatArea(num(stats.standard_grid_area_km2) as number)
          : null,
      ),
    ]
    return list.filter((m): m is StatMetric => m != null)
  }
  if (module.module_id === 'commercial_supply') {
    const inputs = obj(stats.inputs)
    const thr = obj(stats.thresholds)
    const classes = obj(stats.supply_classes)
    const high = obj(classes.high_supply)
    const low = obj(classes.low_supply)
    const list = [
      metric('原始记录', num(inputs.commercial_source_records) != null ? formatInt(num(inputs.commercial_source_records) as number) : null),
      metric('有效记录', num(inputs.commercial_isdelete_0_records) != null ? formatInt(num(inputs.commercial_isdelete_0_records) as number) : null),
      metric('入格商业点', num(stats.total_commercial_points) != null ? formatInt(num(stats.total_commercial_points) as number) : null),
      metric('高供给格', num(high.grid_count) != null ? formatInt(num(high.grid_count) as number) : null),
      metric('低供给格', num(low.grid_count) != null ? formatInt(num(low.grid_count) as number) : null),
      metric(
        '密度阈值',
        num(thr.commercial_density_km2) != null
          ? `${formatNumber(num(thr.commercial_density_km2) as number)} 个/km²`
          : null,
      ),
    ]
    return list.filter((m): m is StatMetric => m != null)
  }
  if (module.module_id === 'commercial_vitality') {
    const day = obj(stats.supply_day_quadrants)
    const list = [
      metric('100m 网格', num(stats.grid_count) != null ? formatInt(num(stats.grid_count) as number) : null),
      metric('高活力·低供给（昼）', num(obj(day.S_L_V_H).grid_count) != null ? formatInt(num(obj(day.S_L_V_H).grid_count) as number) : null),
      metric('低活力·高供给（昼）', num(obj(day.S_H_V_L).grid_count) != null ? formatInt(num(obj(day.S_H_V_L).grid_count) as number) : null),
      metric('高·高（昼）', num(obj(day.S_H_V_H).grid_count) != null ? formatInt(num(obj(day.S_H_V_H).grid_count) as number) : null),
      metric('低·低（昼）', num(obj(day.S_L_V_L).grid_count) != null ? formatInt(num(obj(day.S_L_V_L).grid_count) as number) : null),
      metric('无数据（昼）', num(obj(day.ND).grid_count) != null ? formatInt(num(obj(day.ND).grid_count) as number) : null),
    ]
    return list.filter((m): m is StatMetric => m != null)
  }
  if (module.module_id === 'public_service') {
    const counts = obj(stats.counts)
    const groups = obj(stats.groups)
    const edu = obj(groups.education_culture)
    const med = obj(groups.medical_health)
    const sport = obj(groups.sports)
    const convenience = obj(groups.convenience_emergency)
    const list = [
      metric('候选范围内点', num(counts.inside_study) != null ? formatInt(num(counts.inside_study) as number) : null, '四域大类落在研究范围内的点'),
      metric('规划分析纳入', num(counts.included) != null ? formatInt(num(counts.included) as number) : null, '按类别纳入清单归入四大功能组'),
      metric('本次排除', num(counts.excluded) != null ? formatInt(num(counts.excluded) as number) : null, '明确排除或无法判定类别的点'),
      metric('教育文化', num(edu.count) != null ? formatInt(num(edu.count) as number) : null),
      metric('医疗健康', num(med.count) != null ? formatInt(num(med.count) as number) : null),
      metric('体育活动', num(sport.count) != null ? formatInt(num(sport.count) as number) : null),
      metric('便民与应急', num(convenience.count) != null ? formatInt(num(convenience.count) as number) : null),
      metric('500m 网格', num(stats.grid_count) != null ? formatInt(num(stats.grid_count) as number) : null),
    ]
    return list.filter((m): m is StatMetric => m != null)
  }
  if (module.module_id === 'population_profile') {
    const counts = obj(stats.counts)
    const groups = obj(stats.groups)
    const youth = obj(groups.youth)
    const highEdu = obj(groups.high_edu)
    const midHigh = obj(groups.mid_high_income)
    const list = [
      metric(
        '居住人口',
        num(counts.home_in_range_population) != null
          ? formatInt(num(counts.home_in_range_population) as number)
          : null,
        '202604 估算 · 候选范围筛选后',
      ),
      metric(
        '就业人口',
        num(counts.work_in_range_population) != null
          ? formatInt(num(counts.work_in_range_population) as number)
          : null,
        '202604 估算 · 候选范围筛选后',
      ),
      metric(
        '职住比',
        num(stats.jobs_resident_ratio) != null
          ? formatNumber(num(stats.jobs_resident_ratio) as number)
          : null,
        '就业 ÷ 居住',
      ),
      metric('100m 网格', num(stats.grid_count) != null ? formatInt(num(stats.grid_count) as number) : null),
      metric(
        '青年（18-34）占比',
        num(youth.proportion) != null
          ? `${formatNumber((num(youth.proportion) as number) * 100)}%`
          : null,
        '居住人群人口加权',
      ),
      metric(
        '高学历（本科及以上）',
        num(highEdu.proportion) != null
          ? `${formatNumber((num(highEdu.proportion) as number) * 100)}%`
          : null,
        '居住人群人口加权',
      ),
      metric(
        '中高收入（4000+）',
        num(midHigh.proportion) != null
          ? `${formatNumber((num(midHigh.proportion) as number) * 100)}%`
          : null,
        '居住人群人口加权',
      ),
    ]
    return list.filter((m): m is StatMetric => m != null)
  }
  if (module.module_id === 'commercial_rail_accessibility') {
    const entrances = obj(stats.entrances)
    const centers = obj(stats.centers)
    const acc = obj(stats.accessibility)
    const bands = obj(acc.bands)
    const list = [
      metric('范围内出入口', num(entrances.in_study_area) != null ? formatInt(num(entrances.in_study_area) as number) : null, '原始 1,159 个出入口中落入研究范围'),
      metric('本次计算中心', num(centers.requested) != null ? formatInt(num(centers.requested) as number) : null, '按 500m 商业 POI 数降序 + 300m 空间去重选取代表出入口'),
      metric('商业点总量', num(acc.total_commercial_points) != null ? formatInt(num(acc.total_commercial_points) as number) : null),
      metric('5 分钟内', num(obj(bands['5']).cumulative_count) != null ? formatInt(num(obj(bands['5']).cumulative_count) as number) : null, '真实步行等时圈内'),
      metric('10 分钟累计', num(obj(bands['10']).cumulative_count) != null ? formatInt(num(obj(bands['10']).cumulative_count) as number) : null, '含 5 分钟'),
      metric('15 分钟累计', num(obj(bands['15']).cumulative_count) != null ? formatInt(num(obj(bands['15']).cumulative_count) as number) : null, '含 5、10 分钟'),
    ]
    return list.filter((m): m is StatMetric => m != null)
  }
  return []
}

function quadrantRows(stats: Record<string, unknown>, key: string): LegendRow[] {
  const table = obj(stats[key])
  return QUADRANT_ORDER.map((code) => {
    const item = obj(table[code])
    return {
      code,
      label: QUADRANT_LABELS[code],
      color: QUADRANT_COLORS[code],
      cells: num(item.grid_count),
      areaKm2: num(item.area_km2),
      points: num(item.commercial_points),
    }
  })
}

function buildLegendGroups(module: AnalysisModuleResult): LegendGroup[] {
  const stats = module.stats
  if (module.module_id === 'activity_temporal') {
    const types = obj(stats.day_night_types)
    const rows = RHYTHM_ORDER.map((code) => {
      const item = obj(types[code])
      return {
        code,
        label: str(item.label) || RHYTHM_LABELS[code],
        color: RHYTHM_COLORS[code],
        cells: num(item.grid_count),
        areaKm2: num(item.area_km2),
        points: null,
      }
    })
    return [{ title: '昼夜节律五类', rows, note: '阈值以上为高、以下为低；缺活动数据为无数据（ND）' }]
  }
  if (module.module_id === 'commercial_supply') {
    const classes = obj(stats.supply_classes)
    const rows: LegendRow[] = (['high_supply', 'low_supply'] as const).map((key) => {
      const item = obj(classes[key])
      return {
        code: key,
        label: str(item.label) || (key === 'high_supply' ? '高供给' : '低供给'),
        color: SUPPLY_COLORS[key],
        cells: num(item.grid_count),
        areaKm2: num(item.area_km2),
        points: num(item.commercial_points),
      }
    })
    const groups: LegendGroup[] = [
      { title: '高低供给', rows, note: '正密度中位数以上为高供给、以下为低供给' },
    ]
    const breakdown = Array.isArray(stats.category_breakdown) ? stats.category_breakdown : []
    const catRows: LegendRow[] = breakdown
      .filter((r) => (num(obj(r).count) ?? 0) > 0)
      .map((r) => {
        const cat = str(obj(r).category)
        return {
          code: cat,
          label: cat,
          color: COMMERCIAL_CATEGORY_COLORS[cat] ?? '#8a8f98',
          cells: null,
          areaKm2: null,
          points: num(obj(r).count),
        }
      })
    if (catRows.length) {
      groups.push({ title: '商业大类', rows: catRows, note: '研究范围内分类商业点数量（isdelete=0）' })
    }
    return groups
  }
  if (module.module_id === 'commercial_vitality') {
    return [
      { title: '昼间 商业供给 × 活力', rows: quadrantRows(stats, 'supply_day_quadrants') },
      { title: '夜间 商业供给 × 活力', rows: quadrantRows(stats, 'supply_night_quadrants') },
    ]
  }
  if (module.module_id === 'public_service') {
    const cellClasses = obj(stats.cell_classes)
    const grids = obj(stats.grids)
    const classRows = (['none', 'low', 'high'] as const).map((key) => {
      const item = obj(cellClasses[key])
      return {
        code: key,
        label: str(item.label) || key,
        color: FACILITY_CLASS_COLORS[key],
        cells: num(item.grid_count),
        areaKm2: num(item.area_km2),
        points: null,
      }
    })
    const richness = Array.isArray(stats.type_richness) ? stats.type_richness : []
    const richnessRows = richness
      .filter((r) => {
        const n = num(obj(r).types)
        return n != null && n >= 1 && n <= 4
      })
      .sort((a, b) => (num(obj(a).types) ?? 0) - (num(obj(b).types) ?? 0))
      .map((r) => {
        const item = obj(r)
        const types = num(item.types) ?? 0
        return {
          code: `rich_${types}`,
          label: `同时覆盖 ${types} 类`,
          color: RICHNESS_COLORS[String(types)] ?? '#94a3b8',
          cells: num(item.grid_count),
          areaKm2: num(item.area_km2),
          points: null,
        }
      })
    const median = num(grids.relative_median)
    return [
      {
        title: '500m 格资源相对分布',
        rows: classRows,
        note: median != null
          ? `无设施格=设施数为 0；资源相对较多=设施数高于有设施格数量的中位数 ${median}；资源相对较少=有设施但不高于该中位数`
          : '无设施格与资源相对较多/较少按有设施格数量的中位数划分',
      },
      {
        title: '类型丰富度',
        rows: richnessRows,
        note: '类型丰富度=一个 500m 格覆盖四大功能组中的几类',
      },
    ]
  }
  if (module.module_id === 'population_profile') {
    const groups = obj(stats.groups)
    const rows: LegendRow[] = (['youth', 'high_edu', 'mid_high_income'] as const).map((key) => {
      const item = obj(groups[key])
      return {
        code: key,
        label: str(item.label) || key,
        color: POPULATION_GROUP_COLORS[key] ?? '#94a3b8',
        cells: num(item.high_cells),
        areaKm2: null,
        points: null,
      }
    })
    return [
      {
        title: '群体相对集聚格（高于正密度格中位数）',
        rows,
        note: '基于居住人群口径：每格群体人数 = 居住人数 × 该群体比例之和；相对集聚 = 高于正密度格中位数。占比为人口加权估算，非个人身份。',
      },
    ]
  }
  if (module.module_id === 'commercial_rail_accessibility') {
    const acc = obj(stats.accessibility)
    const bands = obj(acc.bands)
    const isoRows: LegendRow[] = [5, 10, 15].map((m) => ({
      code: String(m),
      label: `${m} 分钟`,
      color: ISOCHRONE_COLORS[String(m)],
      cells: null,
      areaKm2: null,
      points: num(obj(bands[String(m)]).cumulative_count),
    }))
    const groups: LegendGroup[] = [
      {
        title: '真实步行等时圈（累计）',
        rows: isoRows,
        note: 'Mapbox walking；累计口径（10 分钟含 5 分钟，15 分钟含前两档）',
      },
    ]
    const totalByCat = obj(acc.total_by_category)
    const catRows: LegendRow[] = COMMERCIAL_CATEGORY_ORDER.filter(
      (cat) => (num(totalByCat[cat]) ?? 0) > 0,
    ).map((cat) => ({
      code: cat,
      label: cat,
      color: COMMERCIAL_CATEGORY_COLORS[cat],
      cells: null,
      areaKm2: null,
      points: num(totalByCat[cat]),
    }))
    if (catRows.length) {
      groups.push({ title: '商业大类', rows: catRows, note: '研究范围内分类商业点数量' })
    }
    return groups
  }
  return []
}

function buildFunnel(module: AnalysisModuleResult): FunnelStep[] | undefined {
  const inputs = obj(module.stats.inputs)
  if (module.module_id === 'activity_temporal') {
    const steps = [
      { label: '活动源记录', value: num(inputs.activity_source_records) },
      { label: '落入研究范围', value: num(inputs.activity_points_in_boundary) },
      { label: '入稳定网格', value: num(inputs.activity_points_assigned_to_center_selected_grid) },
      { label: '有效活动格', value: num(inputs.activity_aggregated_grid_count) },
    ]
    return steps.every((s) => s.value != null)
      ? (steps as FunnelStep[])
      : undefined
  }
  if (module.module_id === 'commercial_supply') {
    const total = num(module.stats.total_commercial_points)
    const steps = [
      { label: '原始记录', value: num(inputs.commercial_source_records) },
      { label: 'isdelete=0', value: num(inputs.commercial_isdelete_0_records) },
      { label: '落入研究范围', value: num(inputs.commercial_points_in_boundary) },
      { label: '最终入格', value: num(inputs.commercial_points_aggregated) ?? total },
    ]
    return steps.every((s) => s.value != null)
      ? (steps as FunnelStep[])
      : undefined
  }
  if (module.module_id === 'public_service') {
    const c = obj(module.stats.counts)
    const steps = [
      { label: '全量原始点', value: num(c.source_records) },
      { label: '四域大类', value: num(c.domain_source_records) },
      { label: '候选范围内', value: num(c.inside_study) },
      { label: '规划分析纳入', value: num(c.included) },
      { label: '入 500m 稳定格', value: num(c.assigned_to_grid) },
    ]
    return steps.every((s) => s.value != null)
      ? (steps as FunnelStep[])
      : undefined
  }
  return undefined
}

/** 证据链分组说明：把后端 evidence 的已知字段拆成「数据来源 / 方法 / 口径 / 排除」可读行。只读字段，不猜数字。 */
function evidenceGroups(e: Record<string, unknown>): EvidenceGroup[] {
  const groups: EvidenceGroup[] = []
  const push = (title: string, icon: string, lines: string[]) => {
    const cleaned = lines.map((l) => l.trim()).filter(Boolean)
    if (cleaned.length) groups.push({ title, icon, lines: cleaned })
  }

  // 坐标契约 / 坐标参考系
  const crs = obj(e.crs)
  const contract = str(crs.coordinate_contract)
  const crsLines: string[] = []
  if (str(crs.analysis) || str(crs.output)) crsLines.push(`分析 ${str(crs.analysis) || '—'} → 输出 ${str(crs.output) || '—'}`)
  if (contract) crsLines.push(contract)
  push('坐标契约', '🧭', crsLines)

  // 口径（日期/快照/人口口径）
  const caliberLines: string[] = []
  if (str(e.date_caliber)) caliberLines.push(`日期/快照口径：${str(e.date_caliber)}`)
  if (str(e.population_caliber)) caliberLines.push(str(e.population_caliber))
  push('数据口径', '📏', caliberLines)

  // 数据来源与版本（input_versions）
  const versions = obj(e.input_versions)
  const versionKeys = Object.keys(versions)
  if (versionKeys.length) {
    const lines = versionKeys.map((key) => {
      const v = obj(versions[key])
      const path = str(v.path) || str(v.original_file) || key
      const file = path.split(/[\\/]/).pop() || path
      const hash = str(v.sha256)
      const size = num(v.size_bytes)
      return `${key} → ${file}${hash ? ` · ${hash.slice(0, 12)}` : ''}${size != null ? ` · ${formatInt(size)} B` : ''}`
    })
    push('数据来源与版本', '🗂️', lines)
  }

  // 原始数据血缘（source_audit）：兼容「多文件血缘」（按子文件分键）与「单文件扁平血缘」两种结构
  const audit = obj(e.source_audit)
  const auditKeys = Object.keys(audit)
  if (auditKeys.length) {
    const auditLine = (a: Record<string, unknown>, prefix = ''): string => {
      const file = str(a.original_file).split(/[\\/]/).pop() || str(a.original_file)
      const fields = Array.isArray(a.used_fields) ? a.used_fields.length : num(a.used_fields)
      const record = num(a.original_record_count) ?? num(a.original_grid_count)
      const inRange = num(a.in_range_records) ?? num(a.in_range_population)
      const bits: string[] = [file ? `${file}` : '']
      if (fields != null) bits.push(`${formatInt(fields)} 字段`)
      if (record != null) bits.push(`源记录 ${formatInt(record)}`)
      if (inRange != null) bits.push(`范围内 ${formatInt(inRange)}`)
      const label = bits.filter(Boolean).join(' · ')
      return prefix ? `${prefix}：${label}` : label
    }
    const isFlat = Object.prototype.hasOwnProperty.call(audit, 'original_file') ||
      Object.prototype.hasOwnProperty.call(audit, 'original_sha256')
    if (isFlat) {
      const label = auditLine(audit)
      if (label) push('原始数据血缘', '🧬', [label])
    } else {
      const lines = auditKeys.map((key) => auditLine(obj(audit[key]), key)).filter((l) => l.trim().length > 0)
      push('原始数据血缘', '🧬', lines)
    }
  }

  const filters = e.filters
  if (Array.isArray(filters) && filters.length) push('过滤规则', '🔍', filters.map(str))
  if (str(e.aggregation)) push('聚合方式', '🏗️', [str(e.aggregation)])
  if (str(e.classification)) push('分级口径', '🏷️', [str(e.classification)])
  if (str(e.relative_rule)) push('相对分型规则', '⚖️', [str(e.relative_rule)])

  const excluded = e.excluded_inputs
  if (Array.isArray(excluded) && excluded.length) push('排除与不可比', '🚫', excluded.map(str))

  // 交叉校验（公共服务）
  const cross = obj(e.crosscheck)
  if (cross.enabled === true || str(cross.enabled) === 'true') {
    const crossLines: string[] = []
    const inside = num(cross.inside_total)
    if (inside != null) crossLines.push(`范围内合计 ${formatInt(inside)}`)
    const counts = obj(cross.group_counts)
    const groupLines = Object.keys(counts).map((k) => `${k} ${formatInt(num(counts[k]) ?? 0)}`)
    if (groupLines.length) crossLines.push(groupLines.join(' · '))
    push('交叉校验', '✅', crossLines)
  }
  return groups
}

/** 公共服务四大功能组的读法（与 buildMetrics 口径一致，仅用于图表/图例读识） */
const FACILITY_GROUP_LABELS: Record<string, string> = {
  education_culture: '教育文化',
  medical_health: '医疗健康',
  sports: '体育活动',
  convenience_emergency: '便民与应急',
}

/** 对比条形图：只把已有 stats 数字再呈一次，便于「对照」阅读，不新增计算、不引入新结论。 */
function buildCharts(module: AnalysisModuleResult): ChartGroup[] {
  const stats = module.stats
  if (module.module_id === 'activity_temporal') {
    const types = obj(stats.day_night_types)
    const bars = RHYTHM_ORDER.map((code) => ({
      label: str(obj(types[code]).label) || RHYTHM_LABELS[code],
      value: num(obj(types[code]).grid_count),
    })).filter((b): b is ChartBar => b.value != null)
    return [{ title: '昼夜节律 · 网格数', bars, note: '阈值以上为高、以下为低；无数据（ND）单独一列' }]
  }
  if (module.module_id === 'commercial_supply') {
    const classes = obj(stats.supply_classes)
    const bars = (['high_supply', 'low_supply'] as const).map((key) => ({
      label: str(obj(classes[key]).label) || (key === 'high_supply' ? '高供给' : '低供给'),
      value: num(obj(classes[key]).grid_count),
    })).filter((b): b is ChartBar => b.value != null)
    const out: ChartGroup[] = [
      { title: '商业供给 · 格数', bars, note: '正密度中位数以上为高供给、以下为低供给' },
    ]
    const breakdown = Array.isArray(stats.category_breakdown) ? stats.category_breakdown : []
    const catBars: ChartBar[] = breakdown
      .filter((r) => (num(obj(r).count) ?? 0) > 0)
      .map((r) => ({ label: str(obj(r).category), value: num(obj(r).count) as number }))
    if (catBars.length) {
      out.push({ title: '商业大类 · 点数', bars: catBars, note: '研究范围内分类商业点（isdelete=0）' })
    }
    return out
  }
  if (module.module_id === 'commercial_vitality') {
    return (['supply_day_quadrants', 'supply_night_quadrants'] as const)
      .map((key, i) => {
        const table = obj(stats[key])
        const bars = QUADRANT_ORDER.map((code) => ({
          label: QUADRANT_LABELS[code],
          value: num(obj(table[code]).grid_count),
        })).filter((b): b is ChartBar => b.value != null)
        return { title: i === 0 ? '昼间 格数' : '夜间 格数', bars, note: '同一四象限口径下昼/夜对照' }
      })
      .filter((g) => g.bars.length)
  }
  if (module.module_id === 'public_service') {
    const groups = obj(stats.groups)
    const out: ChartGroup[] = []
    const groupBars = Object.keys(groups).map((code) => ({
      label: str(obj(groups[code]).label) || FACILITY_GROUP_LABELS[code] || code,
      value: num(obj(groups[code]).count),
    })).filter((b): b is ChartBar => b.value != null)
    if (groupBars.length) out.push({ title: '四大功能组 · 纳入点数', bars: groupBars })
    const cellClasses = obj(stats.cell_classes)
    const classBars = (['none', 'low', 'high'] as const).map((code) => ({
      label: str(obj(cellClasses[code]).label) || code,
      value: num(obj(cellClasses[code]).grid_count),
    })).filter((b): b is ChartBar => b.value != null)
    if (classBars.length) out.push({ title: '500m 格 · 资源分布', bars: classBars })
    return out
  }
  if (module.module_id === 'population_profile') {
    const groups = obj(stats.groups)
    const out: ChartGroup[] = []
    const groupBars: ChartBar[] = []
    for (const code of ['youth', 'high_edu', 'mid_high_income'] as const) {
      const p = num(obj(groups[code]).proportion)
      if (p != null) groupBars.push({ label: str(obj(groups[code]).label) || code, value: p * 100, unit: '%' })
    }
    if (groupBars.length) out.push({ title: '群体占比（居住口径）', bars: groupBars, note: '人口加权估算，非个人身份' })
    const home = obj(obj(stats.structure).home)
    const work = obj(obj(stats.structure).work)
    const eduProportion = (structure: Record<string, unknown>): number | null => {
      const dims = Array.isArray(structure.dimensions) ? structure.dimensions : []
      for (const d of dims) {
        if (str(obj(d).code) !== '教育水平') continue
        const rawCats: unknown = obj(d).categories
        const cats: Array<Record<string, unknown>> = Array.isArray(rawCats)
          ? (rawCats as unknown[]).map((c) => obj(c))
          : []
        for (const c of cats) {
          if (str(c.label) === '本科及以上') return num(c.proportion)
        }
      }
      return null
    }
    const homeEdu = eduProportion(home)
    const workEdu = eduProportion(work)
    if (homeEdu != null && workEdu != null) {
      out.push({
        title: '本科及以上占比 对照',
        bars: [
          { label: '居住人群', value: Math.round(homeEdu * 1000) / 10, unit: '%' },
          { label: '就业人群', value: Math.round(workEdu * 1000) / 10, unit: '%' },
        ],
      })
    }
    return out
  }
  if (module.module_id === 'commercial_rail_accessibility') {
    const acc = obj(stats.accessibility)
    const bands = obj(acc.bands)
    const bandBars: ChartBar[] = [5, 10, 15]
      .map((m) => ({ label: `${m} 分钟`, value: num(obj(bands[String(m)]).cumulative_count) }))
      .filter((b): b is ChartBar => b.value != null)
    const out: ChartGroup[] = []
    if (bandBars.length) {
      out.push({
        title: '各时间档商业数量（累计）',
        bars: bandBars,
        note: '累计口径：10 分钟含 5 分钟，15 分钟含前两档；来自真实路网等时圈空间统计',
      })
    }
    const totalByCat = obj(acc.total_by_category)
    const catBars: ChartBar[] = COMMERCIAL_CATEGORY_ORDER.filter(
      (cat) => (num(totalByCat[cat]) ?? 0) > 0,
    ).map((cat) => ({ label: cat, value: num(totalByCat[cat]) as number }))
    if (catBars.length) {
      out.push({ title: '商业大类 · 点数', bars: catBars, note: '研究范围内分类商业点构成' })
    }
    return out
  }
  return []
}

/* ---------------- 板块构建 ---------------- */

function buildPlanSection(analysis: StructuredAnalysisResult): AnalysisSection {
  const readiness: ReadinessRow[] = analysis.data_readiness.map((r) => ({
    moduleId: r.module_id,
    moduleLabel: MODULE_META[r.module_id]?.label ?? r.module_id,
    status: r.status,
    requiredData: r.required_data,
    reason: r.reason,
  }))
  return {
    id: 'plan',
    index: 0,
    kind: 'plan',
    title: '项目综合分析计划',
    lead: '先拆板块，再逐项判断数据是否具备；命中规则的板块才运行',
    routeRule: analysis.route_rule,
    routeReason: analysis.route_reason,
    overallSummary: analysis.overall_summary,
    metrics: [
      { label: '请求板块', value: String(analysis.requested_modules.length) },
      { label: '就绪板块', value: String(analysis.ready_modules.length) },
      { label: '阻断板块', value: String(analysis.blocked_modules.length) },
    ],
    readiness,
    limitations: [
      '不能因为「文件存在」就认定数据已获权威确认',
      '不会自动替你选定权威边界或坐标口径',
      '不据此直接给出供需缺口或规划建设建议',
    ],
  }
}

function buildModuleSection(module: AnalysisModuleResult, index: number): AnalysisSection {
  const meta = MODULE_META[module.module_id] ?? { label: module.module_id, lead: '' }
  const base = {
    id: module.module_id,
    index,
    kind: 'module' as const,
    title: meta.label,
    lead: meta.lead,
    achievedLevel: module.achieved_level,
    runId: module.run_id ?? undefined,
    limitations: module.limitations,
  }
  if (module.status === 'blocked') {
    return {
      ...base,
      status: 'blocked',
      summary: module.blocked_reason ?? module.summary,
      stopCode: module.stop_code ?? undefined,
      blockedReason: module.blocked_reason ?? module.summary,
      degradeHint: DEGRADE_HINTS[module.module_id],
    }
  }
  return {
    ...base,
    status: 'completed',
    summary: module.summary,
    metrics: buildMetrics(module),
    legendGroups: buildLegendGroups(module),
    funnel: buildFunnel(module),
    charts: buildCharts(module),
    evidence: evidenceGroups(module.evidence),
    outputLayer: module.output_layer ?? undefined,
  }
}

/** 当前不能分析的板块：阻断模块（若有）+ 固定能力边界清单 */
function buildBoundarySection(analysis: StructuredAnalysisResult, index: number): AnalysisSection {
  const blocked = analysis.modules.filter((m) => m.status === 'blocked')
  const completedCount = analysis.modules.filter((m) => m.status === 'completed').length
  const fixed = [
    '人口权威总量与个人身份画像（本次仅输出 202604 估算的居住/就业数量与网格汇总条件分布；未经供应商授权不发布权威总量，也不把画像当个人身份）',
    '常住（202604）与客流/OD（2025 各日）的横比（日期口径不同）；通勤方式/时间/距离与 OD 流向（后续 commuting-flow-profile 范畴）',
    '公共服务覆盖范围/可达性/服务容量与品质、15 分钟生活圈（本次仅做资源数量与分布盘点，不做覆盖、可达、容量或品质评价）',
    '设施公办民办属性与产权/运营主体判定（POI 类别字段无法证明，本次不做推断）',
    '交通可达性（综合五板块内未做；「商业 × 地铁真实步行可达」另走专用场景，仅覆盖选定的 1–5 个出入口，不等同全范围可达）',
    '商业与活动的因果关系、补建优先级、投资排序与效果预测（现有数据只能说明空间共现）',
    '土地、公共空间、产业与更新板块（不以现有能力替代）',
    '活动数据使用的 gcj02_to_wgs84 仍是候选坐标版本，尚未获数据所有者权威确认',
  ]
  const items = [
    ...blocked.map(
      (m) => `${MODULE_META[m.module_id]?.label ?? m.module_id}：${m.blocked_reason ?? m.summary}`,
    ),
    ...fixed,
  ]
  return {
    id: 'boundary',
    index,
    kind: 'boundary',
    title: '当前不能分析的板块',
    lead: '以下内容因数据或口径未锁定，本次未分析，也未冒充',
    summary: blocked.length
      ? '存在因缺数据被阻断的板块；下列清单同时列出平台当前不具备的能力边界。'
      : `本次共有 ${completedCount} 个板块完成运行；下列是平台当前不具备的能力边界。`,
    metrics: [{ label: '阻断板块', value: String(analysis.blocked_modules.length) }],
    boundaryItems: items,
  }
}

/** 综合分析 → 板块视图模型数组（上到下顺序阅读） */
export function buildSections(analysis: StructuredAnalysisResult): AnalysisSection[] {
  const sections: AnalysisSection[] = [buildPlanSection(analysis)]
  let index = 1
  for (const module of analysis.modules) {
    sections.push(buildModuleSection(module, index))
    index += 1
  }
  sections.push(buildBoundarySection(analysis, index))
  return sections
}
