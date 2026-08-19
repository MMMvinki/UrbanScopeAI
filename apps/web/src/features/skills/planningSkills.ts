export interface SkillDetail {
  dataRequirements: string[]
  workflow: string[]
  indicators: string[]
  tools: string[]
}

export interface PlanningSkill {
  id: string
  name: string
  description: string
  /** 发起分析时发送给 Agent 的问题 */
  prompt: string
  /** 概览用的所需数据摘要 */
  requiredData: string
  detail: SkillDetail
}

export const defaultSkills: PlanningSkill[] = [
  {
    id: 'community-health-check',
    name: '街区城市体检',
    description: '检查中学、公园、文化活动中心、乱停乱放等 4 项指标。',
    prompt: '帮我对这个片区做一次街区城市体检。',
    requiredData: '研究范围 · 居住用地 · 学校 · 公园 · 文化活动中心',
    detail: {
      dataRequirements: ['研究范围', '居住用地', '人口网格', '学校设施', '公园绿地', '文化活动中心', '乱停乱放现场调研数据'],
      workflow: ['数据准备', '指标计算 / 统计', '空间诊断', '规范审查 / 历史对比', '报告片段生成'],
      indicators: ['中学服务半径覆盖率', '公园绿化活动场地服务半径覆盖率', '未达标配建的文化活动中心数量', '存在乱停乱放车辆问题的道路数量'],
      tools: ['list_datasets', 'get_dataset_schema', 'coverage', 'statistics'],
    },
  },
  {
    id: 'public-service-assessment',
    name: '公共服务设施评估',
    description: '评估公共服务设施的空间覆盖与配置水平。',
    prompt: '帮我评估这个片区公共服务设施的覆盖情况。',
    requiredData: '设施点位 · 人口分布',
    detail: {
      dataRequirements: ['公共服务设施点位', '人口分布', '研究范围'],
      workflow: ['数据准备', '设施覆盖计算', '供需匹配判断', '结论生成'],
      indicators: ['设施覆盖率', '人均设施面积', '供需匹配度'],
      tools: ['list_datasets', 'coverage', 'spatial_join', 'statistics'],
    },
  },
  {
    id: '15min-life-circle',
    name: '15 分钟生活圈',
    description: '分析 15 分钟步行生活圈的设施可达性。',
    prompt: '帮我分析这个片区 15 分钟生活圈的设施可达性。',
    requiredData: '设施点位 · 路网 · 居住用地',
    detail: {
      dataRequirements: ['设施点位', '路网', '居住用地', '研究范围'],
      workflow: ['数据准备', '路网拓扑构建', '步行可达范围计算', '生活圈达标判断'],
      indicators: ['15 分钟可达覆盖率', '生活圈达标数量', '缺项设施清单'],
      tools: ['list_datasets', 'buffer', 'nearest_distance', 'statistics'],
    },
  },
]
