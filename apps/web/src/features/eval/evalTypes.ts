/**
 * eval 评测管理：后端 /eval/* 响应类型镜像。
 * 后端契约见 apps/api/app/eval/*（EvalCase / report 落盘 JSON）。
 */

/** 评估集列表项（GET /eval/sets） */
export interface EvalSetSummary {
  name: string
  description: string
  case_count: number
  tiers: Record<string, number>
  categories: Record<string, number>
  error?: string
}

/** 单条 case（GET /eval/sets/{name}.cases，镜像 EvalCase） */
export interface EvalCaseDetail {
  id: string
  title: string
  category: string
  tier: string
  difficulty: string
  tags: string[]
  tool?: string | null
  depends_external: string
  params: Record<string, unknown>
  setup?: { path: string; content: string }[]
  expected: {
    intent?: string | null
    tool_seq?: string[] | null
    params?: Record<string, unknown>
    clarify?: boolean | null
    answer_contains?: string[] | null
    expected_outcome: string
  }
  ground_truth: {
    layer_count_range?: number[] | null
    fields_present?: string[] | null
    bbox_inside?: number[] | null
    values?: Record<string, unknown>
    crs?: string | null
  }
  fault?: {
    kind: string
    technique: string
    env_override?: Record<string, string> | null
    via?: Record<string, unknown> | null
    expected_signal?: string[]
  } | null
  safety: { forbidden: string[] }
}

/** 评估集详情（GET /eval/sets/{name}） */
export interface EvalSetDetail {
  name: string
  description: string
  cases: EvalCaseDetail[]
}

/** 报告列表项（GET /eval/reports） */
export interface EvalReportSummary {
  id: string
  set: string
  tier: string
  pass_rate: number
  passed: number
  total: number
  failed: number
  by_category: Record<string, string>
  by_tool: Record<string, string>
  fail_reasons: Record<string, number>
  updated_at: string
}

/** 单条 case 打分结果（报告 results 项） */
export interface EvalCaseResult {
  id: string
  title: string
  category: string
  tier: string
  difficulty: string
  tool?: string | null
  depends_external: string
  tags?: string[]
  passed: boolean
  checks: { name: string; desc: string; ok: boolean; detail: string }[]
  message?: string | null
  error?: string | null
  generated_layers: string[]
  outcome: string
}

/** 报告详情（GET /eval/reports/{id}） */
export interface EvalReportDetail {
  id: string
  updated_at: string
  meta: { set: string; tier: string }
  summary: {
    total: number
    passed: number
    failed: number
    pass_rate: number
    by_category: Record<string, string>
    by_tool: Record<string, string>
    fail_reasons: Record<string, number>
  }
  results: EvalCaseResult[]
}

/** 运行状态（GET /eval/runs/{run_id}） */
export interface EvalRunStatus {
  run_id?: string
  state: 'running' | 'done' | 'error'
  current?: number
  total?: number
  passed?: number
  current_case?: string | null
  report_id?: string | null
  error?: string | null
}
