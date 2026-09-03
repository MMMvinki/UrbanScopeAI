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
  /** D2：连续渐变图例形态（单色浅→深档位）；有值时按渐变条渲染，两端取 minValue/maxValue */
  gradient?: {
    palette: string[]
    minValue: number | null
    maxValue: number | null
    unit?: string
  }
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
  /** 一句结论（D1-B）：从 stats 提炼的可读结论行；缺省时 UI 回退 summary 首句 */
  conclusion?: string
  metrics?: StatMetric[]
  legendGroups?: LegendGroup[]
  funnel?: FunnelStep[]
  charts?: ChartGroup[]
  evidence?: EvidenceGroup[]
  limitations?: string[]
  outputLayer?: string
  /** 本 run 产出全部图层 id（含辅助层）：「在地图上查看」打开整个成果组，不只主图层 */
  outputLayerIds?: string[]
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
  rail_network_reachability: {
    label: '全区地铁步行可达率',
    lead: '研究范围内所有出入口聚成簇，真实路网 5/10/15 分钟可达带与难达区——全区可达率',
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

/** 取文本首句（中文句号截断），供结论回退；无句号时按长度截断。 */
function firstSentence(text: string): string {
  const t = (text ?? '').trim()
  if (!t) return t
  const cut = t.search(/[。！？]/)
  if (cut > 0) return `${t.slice(0, cut + 1)}`
  return t.length > 140 ? `${t.slice(0, 140)}…` : t
}

/* ---------------- density_bands（D2 渐变主图层同源图例/图表） ---------------- */

/** 各模块「数值渐变主图层」的语义：标题 + 单位（读 stats.density_bands，与图层 applied 色带同源） */
const DENSITY_META: Record<string, { title: string; unit?: string }> = {
  commercial_supply: { title: '商业密度', unit: '个/km²' },
  activity_temporal: { title: '昼间活力', unit: '值' },
  commercial_vitality: { title: '昼间活力', unit: '值' },
  population_profile: { title: '居住人口数', unit: '人' },
  public_service: { title: '设施密度（平滑）', unit: '个/km²' },
}

function fmtNum(n: number): string {
  if (Number.isInteger(n)) return n.toLocaleString('en-US')
  return String(Math.round(n * 100) / 100)
}

/** 渐变档位 → 图例组（梯度条 + 每档色块/格数；值与图层同源）。字段缺时返回 null。 */
function densityLegendGroup(module: AnalysisModuleResult): LegendGroup | null {
  const meta = DENSITY_META[module.module_id]
  if (!meta) return null
  const bands = obj(module.stats.density_bands)
  const palette = Array.isArray(bands.palette) ? bands.palette.map(String) : []
  const bandRows = Array.isArray(bands.bands) ? (bands.bands as unknown[]) : []
  if (!palette.length || !bandRows.length) return null
  const rows: LegendRow[] = bandRows.map((raw, i) => {
    const band = obj(raw)
    const from = num(band.from)
    const to = num(band.to)
    const lo = from != null ? fmtNum(from) : '…'
    const hi = to != null ? fmtNum(to) : '…'
    return {
      code: `band${i}`,
      label: `${lo} – ${hi}`,
      color: palette[i] ?? '#9ca3af',
      cells: num(band.count),
      areaKm2: null,
      points: null,
    }
  })
  return {
    title: meta.title,
    rows,
    note: '按分位数切档（浅→深，低档近底退让）；断点与图层色带同源',
    gradient: {
      palette,
      minValue: num(bands.min),
      maxValue: num(bands.max),
      unit: meta.unit,
    },
  }
}

/** 渐变档位 → 右卡柱图（每档网格数）。字段缺时返回 null。 */
function densityBandChart(module: AnalysisModuleResult): ChartGroup | null {
  const meta = DENSITY_META[module.module_id]
  if (!meta) return null
  const bands = obj(module.stats.density_bands)
  const bandRows = Array.isArray(bands.bands) ? (bands.bands as unknown[]) : []
  if (!bandRows.length) return null
  const bars: ChartBar[] = []
  bandRows.forEach((raw, i) => {
    const band = obj(raw)
    const from = num(band.from)
    const to = num(band.to)
    const count = num(band.count)
    if (count == null) return
    const lo = from != null ? fmtNum(from) : '…'
    const hi = to != null ? fmtNum(to) : '…'
    bars.push({ label: `${i + 1} 档 ${lo}–${hi}`, value: count, unit: '格' })
  })
  if (!bars.length) return null
  return {
    title: `${meta.title} · 档位分布`,
    bars,
    note: '分位数档位（断点与图层色带同源）；格数=该档落入的网格数',
  }
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
    output_layer_ids: Array.isArray(o.output_layer_ids)
      ? o.output_layer_ids.map(String).filter(Boolean)
      : [],
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
      metric('100m 网格', num(stats.grid_count) != null ? formatInt(num(stats.grid_count) as number) : null),
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
  if (module.module_id === 'rail_network_reachability') {
    const ent = obj(stats.entrances)
    const clusters = obj(stats.clusters)
    const bands = obj(stats.bands)
    const hard = num(stats.hard_to_reach_km2)
    const hardRate = num(stats.hard_to_reach_rate)
    const area = num(stats.study_area_km2)
    const cov = (m: number) => {
      const b = obj(bands[String(m)])
      const km2 = num(b.cumulative_area_km2)
      const rate = num(b.coverage_rate)
      if (km2 == null || rate == null) return null
      return `${formatNumber(km2)} km² · ${formatNumber(rate)}%`
    }
    // A1：居住用地 / 人口·就业 的轨道覆盖与难达盲区（rail run 内叠加，读 stats.residential_blindspot）
    const rb = obj(stats.residential_blindspot)
    const rbKm = (m: string): number | null => num(obj(rb.coverage_km2)[m])
    const rbPct = (m: string): number | null => num(obj(rb.coverage_pct)[m])
    const fmtAreaRate = (a: number | null, b: number | null): string | null =>
      a != null && b != null ? `${formatNumber(a)} km² · ${formatNumber(b)}%` : null
    // 商业 POI × 地铁可达：reach_class 分档 counts/pct（后端 stats.commercial_reach，与原 cumulative 同源）
    const cr = obj(stats.commercial_reach)
    const crC = obj(cr.counts)
    const c5 = num(crC['5'])
    const c10 = num(crC['10'])
    const c15 = num(crC['15'])
    const cOut = num(crC['out'])
    const crTot = num(cr.total)
    const list = [
      metric('研究范围', area != null ? formatArea(area) : null),
      metric('范围内出入口', num(ent.in_study_area) != null ? formatInt(num(ent.in_study_area) as number) : null, '全部真实出入口（1,159 个原始中落入研究范围）'),
      metric('出入口簇 / 代表', num(clusters.cluster_count) != null ? `${formatInt(num(clusters.cluster_count) as number)} / ${formatInt(num(clusters.representatives) as number)}` : null, '300m 距离聚簇；代表为真实出入口，非站名'),
      metric('5 分钟覆盖', cov(5), '真实路网步行 · 面积 · 占研究区'),
      metric('10 分钟累计覆盖', cov(10)),
      metric('15 分钟累计覆盖', cov(15)),
      metric('难达区', hard != null && hardRate != null ? `${formatNumber(hard)} km² · ${formatNumber(hardRate)}%` : null, '15 分钟步行未覆盖'),
      metric('居住用地 · 研究区内', num(rb.residential_total_km2) != null ? formatArea(num(rb.residential_total_km2) as number) : null, 'Layer 含居住 / R1–R4 住宅本体（共 601 块）'),
      metric('居住 ≤5 分钟可及', fmtAreaRate(rbKm('5'), rbPct('5')), '累计 · 占研究区内居住面积'),
      metric('居住 ≤10 累计', fmtAreaRate(rbKm('10'), rbPct('10'))),
      metric('居住 ≤15 累计', fmtAreaRate(rbKm('15'), rbPct('15'))),
      metric('居住 · 难达(>15)', rb != null ? fmtAreaRate(num(rb.blind_km2), num(rb.blind_pct)) : null, '盲区居住面积 / 占居住面积'),
      metric('难达区人口/就业', num(rb.home_blind) != null && num(rb.work_blind) != null ? `${formatInt(num(rb.home_blind) as number)} / ${formatInt(num(rb.work_blind) as number)}` : null, '盲区内居住 / 就业人口'),
      metric('商业可达 ≤5 / ≤10 / ≤15', c5 != null && c10 != null && c15 != null ? `${formatInt(c5)} / ${formatInt(c10)} / ${formatInt(c15)}` : null, '研究范围内商业点在各分钟并集内的 within 计数'),
      metric('商业 区外(>15)', cOut != null && crTot != null && crTot > 0 ? `${formatInt(cOut)} 个 · ${formatNumber(Math.round((cOut / crTot) * 1000) / 10)}%` : null, '=地铁 15 分钟步行不可达'),
      // 可达 × 多类资产：公共/文化/绿地 ≤15 可及（默认四类 + overlay_layers）
      ...[
        { label: '公共服务 ≤15 可及', key: 'public', unit: '个' },
        { label: '文化设施 ≤15 可及', key: 'culture', unit: '个' },
        { label: '绿地 ≤15 可及', key: 'green', unit: '块' },
      ].flatMap((a) => {
        const r = obj(stats[`${a.key}_reach`])
        const reach15 = num(obj(r.counts)['15'])
        const tot = num(r.total)
        return [metric(a.label, reach15 != null ? formatInt(reach15) : null, `共 ${tot != null ? formatInt(tot) : '—'} ${a.unit}`)]
      }).filter((m): m is StatMetric => m != null),
    ]
    return list.filter((m): m is StatMetric => m != null)
  }
  return []
}

/** 一句结论（D1-B）：从 stats 提炼单行「结论句」，数字与 buildMetrics / 图表 / 图例同源。
 *  某字段缺失时只组装现有部分；完全无可用数字返回 null（调用方回退首句摘要）。 */
function buildConclusion(module: AnalysisModuleResult): string | null {
  const stats = module.stats
  const clause: string[] = []
  const push = (s: string | null | undefined): void => {
    if (s) clause.push(s)
  }
  if (module.module_id === 'activity_temporal') {
    const types = obj(stats.day_night_types)
    const rhythmCounts = RHYTHM_ORDER.map((code) => {
      const n = num(obj(types[code]).grid_count)
      return n != null ? `${RHYTHM_LABELS[code]} ${formatInt(n)} 格` : null
    }).filter((s): s is string => Boolean(s))
    const valid = num(stats.valid_cells)
    push(valid != null ? `范围内有效活动格 ${formatInt(valid)} 个` : null)
    if (rhythmCounts.length) clause.push(`（${rhythmCounts.join('、')}）`)
  } else if (module.module_id === 'commercial_supply') {
    const inputs = obj(stats.inputs)
    const classes = obj(stats.supply_classes)
    const inside = num(inputs.commercial_points_in_boundary)
    const high = num(obj(classes.high_supply).grid_count)
    const low = num(obj(classes.low_supply).grid_count)
    const thr = num(obj(stats.thresholds).commercial_density_km2)
    push(inside != null ? `范围内 ${formatInt(inside)} 个有效分类商业点` : null)
    if (high != null && low != null) push(`高供给 ${formatInt(high)} 格 / 低供给 ${formatInt(low)} 格`)
    push(thr != null ? `密度阈值 ${formatNumber(thr)} 个/km²` : null)
  } else if (module.module_id === 'commercial_vitality') {
    const dayMis = obj(obj(stats.supply_day_quadrants).S_L_V_H)
    const nightMis = obj(obj(stats.supply_night_quadrants).S_L_V_H)
    const dayHighHigh = obj(obj(stats.supply_day_quadrants).S_H_V_H)
    const dm = num(dayMis.grid_count)
    const nm = num(nightMis.grid_count)
    const hh = num(dayHighHigh.grid_count)
    push(dm != null && nm != null ? `昼间高活力·低供给（错配候选）${formatInt(dm)} 格、夜间 ${formatInt(nm)} 格` : null)
    push(hh != null ? `昼间高·高供给 ${formatInt(hh)} 格` : null)
  } else if (module.module_id === 'public_service') {
    const counts = obj(stats.counts)
    const groups = obj(stats.groups)
    const included = num(counts.included)
    const label: Record<string, string> = {
      education_culture: '教育文化',
      medical_health: '医疗健康',
      sports: '体育活动',
      convenience_emergency: '便民与应急',
    }
    const groupParts: string[] = []
    for (const code of ['education_culture', 'medical_health', 'sports', 'convenience_emergency'] as const) {
      const n = num(obj(groups[code]).count)
      if (n != null && n > 0) groupParts.push(`${label[code] ?? code} ${formatInt(n)}`)
    }
    push(included != null ? `规划分析纳入 ${formatInt(included)} 个设施点` : null)
    if (groupParts.length) clause.push(`（${groupParts.join('、')}）`)
  } else if (module.module_id === 'population_profile') {
    const counts = obj(stats.counts)
    const home = num(counts.home_in_range_population)
    const work = num(counts.work_in_range_population)
    const ratio = num(stats.jobs_resident_ratio)
    push(home != null && work != null ? `居住约 ${formatInt(home)} 人、就业约 ${formatInt(work)} 人` : null)
    push(ratio != null ? `职住比 ${formatNumber(ratio)}（就业 ÷ 居住）` : null)
  } else if (module.module_id === 'commercial_rail_accessibility') {
    const acc = obj(stats.accessibility)
    const bands = obj(acc.bands)
    const requested = num(obj(stats.centers).requested)
    const c5 = num(obj(bands['5']).cumulative_count)
    const c10 = num(obj(bands['10']).cumulative_count)
    const c15 = num(obj(bands['15']).cumulative_count)
    const total = num(acc.total_commercial_points)
    push(requested != null ? `从 ${formatInt(requested)} 个代表出入口出发（真实路网步行）` : null)
    if (c5 != null && c10 != null && c15 != null) {
      push(`5/10/15 分钟可及商业点 ${formatInt(c5)} / ${formatInt(c10)} / ${formatInt(c15)} 个`)
    } else if (c15 != null && total != null) {
      push(`15 分钟可及商业点 ${formatInt(c15)} 个（范围内共 ${formatInt(total)} 个）`)
    }
  } else if (module.module_id === 'rail_network_reachability') {
    const bands = obj(stats.bands)
    const req = num(obj(stats.centers).requested)
    const clusters = obj(stats.clusters)
    push(req != null ? `从 ${formatInt(req)} 个真实出入口代表出发（真实路网 walking）` : null)
    const b15 = obj(bands['15'])
    const km15 = num(b15.cumulative_area_km2)
    const c15 = num(b15.cumulative_count)
    push(km15 != null ? `15 分钟累计可覆盖 ${formatNumber(km15)} km²` : null)
    push(c15 != null ? `可及商业点 ${formatInt(c15)} 个` : null)
    push(req != null && clusters ? `（${formatInt(num(obj(stats.clusters).cluster_count) ?? 0)} 个出入口簇）` : null)
    const hard = num(stats.hard_to_reach_km2)
    const hardRate = num(stats.hard_to_reach_rate)
    push(hard != null && hardRate != null ? `难达区 ${formatNumber(hard)} km²（${formatNumber(hardRate)}%）` : null)
    // A1：难达区居住用地与人口·就业
    const rb = obj(stats.residential_blindspot)
    const bKm = num(rb.blind_km2)
    const bPct = num(rb.blind_pct)
    const bHome = num(rb.home_blind)
    const bWork = num(rb.work_blind)
    push(bKm != null && bPct != null ? `其中盲区（>15 分钟）居住用地 ${formatNumber(bKm)} km²（占研究区内居住 ${formatNumber(bPct)}%）` : null)
    push(bHome != null && bWork != null ? `盲区仍含约 ${formatInt(bHome)} 居住人口 / ${formatInt(bWork)} 就业人口` : null)
    // 商业 POI × 地铁可达：≤15 累计可达率
    const cr = obj(stats.commercial_reach)
    const crC = obj(cr.counts)
    const crTot = num(cr.total)
    const cx5 = num(crC['5'])
    const cx10 = num(crC['10'])
    const cx15 = num(crC['15'])
    const sum15 = cx5 != null && cx10 != null && cx15 != null ? cx5 + cx10 + cx15 : null
    push(
      crTot != null && sum15 != null
        ? `研究范围内 ${formatInt(crTot)} 个商业点中，${formatNumber(Math.round((sum15 / crTot) * 1000) / 10)}%（${formatInt(sum15)} 个）在 15 分钟地铁步行可达内`
        : null,
    )
    // 可达 × 多类资产：公共服务/文化设施/绿地 ≤15 可及（默认四类 + overlay_layers）
    const assetClauses: string[] = []
    for (const key of ['public', 'culture', 'green'] as const) {
      const r = obj(stats[`${key}_reach`])
      const tot = num(r.total)
      const cls = num(obj(r.counts)['15'])
      if (tot != null && cls != null) {
        const label = key === 'public' ? '公共服务' : key === 'culture' ? '文化设施' : '绿地'
        assetClauses.push(`${label} ${formatInt(cls)} 个${key === 'green' ? '地块' : ''}（共 ${formatInt(tot)}）`)
      }
    }
    if (assetClauses.length) push(`≤15 分钟可及${assetClauses.join('、')}`)
  }
  const text = clause.join('；').replace(/；（/g, '（').trim()
  return text ? `${text}。` : null
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
    const grad = densityLegendGroup(module)
    if (grad) return [grad]
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
    const grad = densityLegendGroup(module)
    const groups: LegendGroup[] = grad ? [grad] : []
    if (!grad) {
      // 回退（旧后端无 density_bands）：仍按高/低二值给图例
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
      groups.push({ title: '高低供给', rows, note: '正密度中位数以上为高供给、以下为低供给' })
    }
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
    const grad = densityLegendGroup(module)
    if (grad) return [grad]
    return [
      { title: '昼间 商业供给 × 活力', rows: quadrantRows(stats, 'supply_day_quadrants') },
      { title: '夜间 商业供给 × 活力', rows: quadrantRows(stats, 'supply_night_quadrants') },
    ]
  }
  if (module.module_id === 'public_service') {
    const grad = densityLegendGroup(module)
    if (grad) return [grad]
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
        title: '100m 格资源相对分布',
        rows: classRows,
        note: median != null
          ? `无设施格=设施数为 0；资源相对较多=设施数高于有设施格数量的中位数 ${median}；资源相对较少=有设施但不高于该中位数`
          : '无设施格与资源相对较多/较少按有设施格数量的中位数划分',
      },
      {
        title: '类型丰富度',
        rows: richnessRows,
        note: '类型丰富度=一个 100m 格覆盖四大功能组中的几类',
      },
    ]
  }
  if (module.module_id === 'population_profile') {
    const grad = densityLegendGroup(module)
    if (grad) return [grad]
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
  if (module.module_id === 'rail_network_reachability') {
    const bands = obj(stats.bands)
    const rows: LegendRow[] = [5, 10, 15].map((m) => {
      const b = obj(bands[String(m)])
      return {
        code: String(m),
        label: `≤${m} 分钟`,
        color: ISOCHRONE_COLORS[String(m)],
        cells: null,
        areaKm2: num(b.cumulative_area_km2),
        points: num(b.cumulative_count),
      }
    })
    const groups: LegendGroup[] = [
      {
        title: '真实路网步行可达带（累计）',
        rows,
        note: 'Mapbox walking；面积=累计覆盖 km²，点=累计可及商业点',
      },
    ]
    groups.push({
      title: '难达区',
      rows: [
        {
          code: 'hard',
          label: '15 分钟步行外',
          color: '#cbd5e1',
          cells: null,
          areaKm2: num(stats.hard_to_reach_km2),
          points: null,
        },
      ],
      note: '研究区内 15 分钟真实路网未覆盖的部分',
    })
    const totalByCat = obj(stats.total_by_category)
    const catRows: LegendRow[] = Object.keys(totalByCat)
      .filter((c) => (num(totalByCat[c]) ?? 0) > 0)
      .map((c) => ({
        code: c,
        label: c,
        color: COMMERCIAL_CATEGORY_COLORS[c] ?? '#8a8f98',
        cells: null,
        areaKm2: null,
        points: num(totalByCat[c]),
      }))
    if (catRows.length) {
      groups.push({ title: '≤15 分钟可及商业（分类）', rows: catRows })
    }
    // 可达 × 多类资产：公共服务/文化/绿地/任意指定图层 按 reach_class 分档图例
    const ASSET_META: Record<string, { label: string; color: Record<string, string>; unit: string }> = {
      public: { label: '公共服务', color: { '5': '#166534', '10': '#16a34a', '15': '#86efac', out: '#9ca3af' }, unit: '个' },
      culture: { label: '文化设施', color: { '5': '#4c1d95', '10': '#7c3aed', '15': '#c4b5fd', out: '#9ca3af' }, unit: '个' },
      green: { label: '绿地', color: { '5': '#14532d', '10': '#22c55e', '15': '#bbf7d0', out: '#9ca3af' }, unit: '块' },
    }
    for (const key of ['public', 'culture', 'green'] as const) {
      const r = obj(stats[`${key}_reach`])
      const meta = ASSET_META[key]
      const c = obj(r.counts)
      const tot = num(r.total)
      if (tot == null) continue
      const rowsA: LegendRow[] = ['5', '10', '15', 'out'].map((m) => ({
        code: m,
        label: m === 'out' ? '>15 分钟外' : `≤${m} 分钟`,
        color: meta.color[m],
        cells: null,
        areaKm2: null,
        points: num(c[m]),
      }))
      groups.push({
        title: `${meta.label} · 地铁步行可达`,
        rows: rowsA,
        note: `共 ${formatInt(tot)} ${meta.unit}；计数=点位/地块，非容量或可达水平`,
      })
    }
    // agent 指定任意图层叠加（overlay_*）
    const overlayKeys = Object.keys(stats).filter((k) => k.startsWith('overlay_') && k.endsWith('_reach'))
    for (const k of overlayKeys) {
      const r = obj(stats[k])
      const c = obj(r.counts)
      const tot = num(r.total)
      if (tot == null) continue
      const rowsO: LegendRow[] = ['5', '10', '15', 'out'].map((m) => ({
        code: m,
        label: m === 'out' ? '>15 分钟外' : `≤${m} 分钟`,
        color: ASSET_META.public.color[m],
        cells: null,
        areaKm2: null,
        points: num(c[m]),
      }))
      groups.push({
        title: `${str(r.label) || '指定图层'} · 地铁步行可达`,
        rows: rowsO,
        note: `共 ${formatInt(tot)} 个要素；agent 指定叠加图层`,
      })
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
      { label: '入 100m 稳定格', value: num(c.assigned_to_grid) },
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
    // 主图=昼间活力渐变（见 D2）；右卡另给一张节律类型网格数，把昼/夜口径分开可读、成对表达
    const types = obj(stats.day_night_types)
    const rhythmBars: ChartBar[] = RHYTHM_ORDER.map((code) => ({
      label: str(obj(types[code]).label) || RHYTHM_LABELS[code],
      value: num(obj(types[code]).grid_count),
    })).filter((b): b is ChartBar => b.value != null)
    const out: ChartGroup[] = []
    const density = densityBandChart(module)
    if (density) out.push(density)
    if (rhythmBars.length) {
      out.push({
        title: '昼夜节律 · 网格数',
        bars: rhythmBars,
        note: '昼高夜低≈商务潮汐候选、昼低夜高≈生活消费候选（相对判断，非因果）；无数据（ND）单独一列',
      })
    }
    return out
  }
  if (module.module_id === 'commercial_supply') {
    const out: ChartGroup[] = []
    const density = densityBandChart(module)
    if (density) {
      out.push(density)
    } else {
      const classes = obj(stats.supply_classes)
      const bars = (['high_supply', 'low_supply'] as const).map((key) => ({
        label: str(obj(classes[key]).label) || (key === 'high_supply' ? '高供给' : '低供给'),
        value: num(obj(classes[key]).grid_count),
      })).filter((b): b is ChartBar => b.value != null)
      out.push({ title: '商业供给 · 格数', bars, note: '正密度中位数以上为高供给、以下为低供给' })
    }
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
    // 昼/夜成对对照：商业×活力默认主图=昼间活力渐变，右卡同时给昼/夜两幅四象限，成对可读
    const quadGroups: ChartGroup[] = (
      ['supply_day_quadrants', 'supply_night_quadrants'] as const
    )
      .map((key, i) => {
        const table = obj(stats[key])
        const bars = QUADRANT_ORDER.map((code) => ({
          label: QUADRANT_LABELS[code],
          value: num(obj(table[code]).grid_count),
        })).filter((b): b is ChartBar => b.value != null)
        return {
          title: i === 0 ? '昼间 · 商业×活力' : '夜间 · 商业×活力',
          bars,
          note: '同一四象限口径下昼/夜对照；「高活力·低供给（错配候选）」为空间共现判断，非因果',
        }
      })
      .filter((g) => g.bars.length)
    const out: ChartGroup[] = [...quadGroups]
    const density = densityBandChart(module)
    if (density) out.unshift(density)
    return out
  }
  if (module.module_id === 'public_service') {
    const groups = obj(stats.groups)
    const out: ChartGroup[] = []
    const density = densityBandChart(module)
    if (density) out.push(density)
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
    if (classBars.length) out.push({ title: '100m 格 · 资源分布', bars: classBars })
    return out
  }
  if (module.module_id === 'population_profile') {
    const groups = obj(stats.groups)
    const out: ChartGroup[] = []
    const density = densityBandChart(module)
    if (density) out.push(density)
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
  if (module.module_id === 'rail_network_reachability') {
    const bands = obj(stats.bands)
    const areaBars: ChartBar[] = [5, 10, 15]
      .map((m) => ({
        label: `≤${m} 分钟`,
        value: num(obj(bands[String(m)]).cumulative_area_km2),
      }))
      .filter((b): b is ChartBar => b.value != null)
    const countBars: ChartBar[] = [5, 10, 15]
      .map((m) => ({
        label: `≤${m} 分钟`,
        value: num(obj(bands[String(m)]).cumulative_count),
      }))
      .filter((b): b is ChartBar => b.value != null)
    const out: ChartGroup[] = []
    if (areaBars.length) {
      out.push({
        title: '累计覆盖面积（km²）',
        bars: areaBars,
        unit: 'km²',
        note: '真实路网 walking 累计覆盖面积；面积守恒=各带+难达区=研究区',
      })
    }
    if (countBars.length) {
      out.push({
        title: '可及商业点（累计）',
        bars: countBars,
        note: '研究范围内商业点在分钟并集内的 within 计数（累计口径）',
      })
    }
    const totalByCat = obj(stats.total_by_category)
    const catBars: ChartBar[] = Object.keys(totalByCat)
      .filter((c) => (num(totalByCat[c]) ?? 0) > 0)
      .map((c) => ({ label: c, value: num(totalByCat[c]) as number }))
    if (catBars.length) {
      out.push({ title: '≤15 分钟可及商业（分类）', bars: catBars })
    }
    // A1：居住用地覆盖率 + 盲区人口/就业
    const rb = obj(stats.residential_blindspot)
    const covP = obj(rb.coverage_pct)
    const resPctItems: Array<{ label: string; value: number | null }> = [
      { label: '≤5 分钟', value: num(covP['5']) },
      { label: '≤10 分钟', value: num(covP['10']) },
      { label: '≤15 分钟', value: num(covP['15']) },
      { label: '难达>15', value: num(rb.blind_pct) },
    ]
    const resPctBars: ChartBar[] = resPctItems.reduce<ChartBar[]>((acc, it) => {
      if (it.value != null) acc.push({ label: it.label, value: it.value })
      return acc
    }, [])
    if (resPctBars.length) {
      const resTotal = num(rb.residential_total_km2)
      out.push({
        title: '居住用地覆盖率（%）',
        bars: resPctBars,
        unit: '%',
        note: `居住面积 ∩ 分钟带 / 研究区内居住面积（共 ${resTotal != null ? formatNumber(resTotal) : '—'} km²）；难达>15=盲区`,
      })
    }
    const blindBars: ChartBar[] = []
    if (num(rb.home_blind) != null) blindBars.push({ label: '居住人口', value: num(rb.home_blind) as number })
    if (num(rb.work_blind) != null) blindBars.push({ label: '就业人口', value: num(rb.work_blind) as number })
    if (blindBars.length) {
      out.push({ title: '盲区人口/就业', bars: blindBars, unit: '人', note: '15 分钟步行未覆盖区内的格点居住/就业人口（home_count/work_count）' })
    }
    // 商业 POI 地铁可达率（累计 %）
    const crRate = obj(stats.commercial_reach)
    const crRc = obj(crRate.counts)
    const crRt = num(crRate.total)
    const r5 = num(crRc['5'])
    const r10 = num(crRc['10'])
    const r15 = num(crRc['15'])
    const rOut = num(crRc['out'])
    if (crRt != null && crRt > 0) {
      const mk = (n: number | null): number | null => (n == null ? null : Math.round((n / crRt) * 1000) / 10)
      const sum2 = r5 != null && r10 != null ? r5 + r10 : null
      const sum3 = r5 != null && r10 != null && r15 != null ? r5 + r10 + r15 : null
      const rateItems: Array<{ label: string; value: number | null }> = [
        { label: '≤5 分钟', value: mk(r5) },
        { label: '≤10 分钟', value: mk(sum2) },
        { label: '≤15 分钟', value: mk(sum3) },
        { label: '区外>15', value: mk(rOut) },
      ]
      const rateBars: ChartBar[] = rateItems.reduce<ChartBar[]>((acc, it) => {
        if (it.value != null) acc.push({ label: it.label, value: it.value })
        return acc
      }, [])
      if (rateBars.length) {
        out.push({
          title: '商业点地铁可达率（%）',
          bars: rateBars,
          unit: '%',
          note: '累计占研究范围内分类商业点；区外=地铁 15 分钟步行不可达',
        })
      }
    }
    // 可达 × 多类资产：各分钟档 × 资产类型 柱状（公共/文化/绿地/指定层）
    const ASSET_KEYS: Array<{ key: string; label: string; unit: string }> = [
      { key: 'public', label: '公共服务', unit: '个' },
      { key: 'culture', label: '文化设施', unit: '个' },
      { key: 'green', label: '绿地', unit: '块' },
    ]
    const assetBars: ChartBar[] = []
    for (const a of ASSET_KEYS) {
      const r = obj(stats[`${a.key}_reach`])
      const c = obj(r.counts)
      const tot = num(r.total)
      const reach15 = num(c['15'])
      if (tot != null && reach15 != null) assetBars.push({ label: `${a.label} ≤15m`, value: reach15, unit: a.unit })
    }
    const overlayKeys2 = Object.keys(stats).filter((k) => k.startsWith('overlay_') && k.endsWith('_reach'))
    for (const k of overlayKeys2) {
      const c = obj(obj(stats[k]).counts)
      const reach15 = num(c['15'])
      const lab = str(obj(stats[k]).label) || '指定图层'
      if (reach15 != null) assetBars.push({ label: `${lab} ≤15m`, value: reach15, unit: '个' })
    }
    if (assetBars.length) {
      out.push({
        title: '各资产 ≤15 分钟可及数',
        bars: assetBars,
        note: '公共服务/文化设施=点位（四域/文化口径），绿地=地块（Layer 含绿地）；非容量或可达水平',
      })
    }
    return out
  }
  return []
}

/** 关键图兜底（D1-B）：每个 completed 板块至少一张图。主图表缺字段为空时，
 *  用图例同源的格数/点数再呈一次（数字与 buildLegendGroups 同源，不新增计算）。 */
function ensureKeyCharts(module: AnalysisModuleResult): ChartGroup[] {
  const charts = buildCharts(module)
  if (charts.length) return charts
  const rows = buildLegendGroups(module)
    .flatMap((group) => group.rows)
    .filter((row) => (row.cells ?? row.points) != null)
  if (!rows.length) return []
  const bars = rows.slice(0, 12).map((row) => ({
    label: row.label,
    value: (row.cells ?? row.points) as number,
  }))
  return [
    {
      title: '成果读数 · 格/点数',
      bars,
      note: '回退图：主图表缺字段时从图例同源数字读取格/点数',
    },
  ]
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
    // 结论一句：优先从 stats 提炼的可读行；未知模块回退 summary 首句（SectionCard 展示时兜底）
    conclusion: buildConclusion(module) ?? firstSentence(module.summary),
    metrics: buildMetrics(module),
    legendGroups: buildLegendGroups(module),
    funnel: buildFunnel(module),
    charts: ensureKeyCharts(module),
    evidence: evidenceGroups(module.evidence),
    outputLayer: module.output_layer ?? undefined,
    outputLayerIds: module.output_layer_ids?.length ? module.output_layer_ids : undefined,
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
