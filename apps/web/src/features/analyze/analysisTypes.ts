/**
 * 综合分析板块：后端 `structured_analysis`（StructuredAnalysisResult）的前端类型镜像。
 * 与 apps/api/app/models/result.py 的 AnalysisModuleResult / AnalysisReadinessItem /
 * StructuredAnalysisResult 一一对应。前端只读这些字段，不解析长工具文本。
 */

/** 数据就绪项：某个板块经真实 Skill 校验后的结论 */
export interface AnalysisReadinessItem {
  module_id: string
  status: 'ready' | 'blocked'
  required_data: string[]
  reason: string
}

/** 单个分析板块的稳定结果 */
export interface AnalysisModuleResult {
  module_id: string
  status: 'completed' | 'blocked'
  skill_id: string | null
  run_id: string | null
  achieved_level: string
  summary: string
  stats: Record<string, unknown>
  evidence: Record<string, unknown>
  limitations: string[]
  output_layer: string | null
  /** 本 run 实际产出的全部图层 id（主图层 + 辅助层；后端 AnalysisModuleResult.output_layer_ids） */
  output_layer_ids: string[]
  stop_code: string | null
  blocked_reason: string | null
}

/** 综合分析入口的稳定结果契约（顶层） */
export interface StructuredAnalysisResult {
  route_id: string
  route_rule: string
  route_reason: string
  requested_modules: string[]
  ready_modules: string[]
  blocked_modules: string[]
  data_readiness: AnalysisReadinessItem[]
  modules: AnalysisModuleResult[]
  overall_summary: string
}
