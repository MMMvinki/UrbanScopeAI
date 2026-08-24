---
id: community_health_check
name: 街区维度城市体检
core_question: 这个街区总体有什么问题？
keywords:
  - 体检
  - 健康
  - 城市体检
  - 街区体检
required_data:
  - 研究范围
  - 人口
  - 学校设施
  - 公园绿地
  - 文化活动中心
  - 乱停乱放
outputs:
  - 指标卡片
  - 问题区图层
  - 分析结论
plan:
  operation: coverage
  # targets 留空：由 Planner/Executor 按当前项目真实图层动态选取（硬编码
  # schools/parks/culture_centers 在多数项目解析不到，会顶掉 LLM 选的真实 POI 图层）。
  targets: []
  target_distances: {}
indicators:
  - name: 中学服务半径覆盖率
    data: 学校设施
    mvp_depth: 真实计算
    status: ready
    steps:
      - coverage(schools, 800m)
      - 统计覆盖居住用地占比
      - 识别服务盲区
  - name: 公园绿化活动场地服务半径覆盖率
    data: 公园绿地
    mvp_depth: 真实计算
    status: ready
    steps:
      - coverage(parks, 500m)
      - 统计覆盖居住用地占比
      - 识别服务盲区
  - name: 文化活动中心配建审查
    data: 文化活动中心
    mvp_depth: 半真实审查
    status: planned
    steps:
      - 按规范审查配建数量
      - 对比人口规模
      - 输出未达标清单
  - name: 乱停乱放车辆问题
    data: 乱停乱放现场调研数据
    mvp_depth: 缺数据阻断
    status: blocked
    steps:
      - 检查调研数据
      - 空间分布统计
---

# 街区维度城市体检

## 适用场景

用户询问"这个街区 / 这片区域总体有什么问题 / 是否健康 / 体检结果如何"。

## 执行流程

1. **就绪检查**：核对 required_data 对应图层是否存在；`乱停乱放现场调研数据` 当前无对应图层 → 就绪检查标记为缺失并阻断该指标，其余指标照常执行。
2. **逐指标分析**：对每个 status=ready 的指标调用对应工具：
   - 中学服务半径覆盖率 → `coverage(schools, 800)`
   - 公园绿化活动场地覆盖率 → `coverage(parks, 500)`
   - 文化活动中心配建审查 → 规则审查 + 对比人口规模
   - 乱停乱放 → 数据缺失，标记 blocked
3. **汇总结论**：覆盖率的短板（未覆盖居住用地占比）即街区问题点，写入结果组与 evidence。
4. **产出图层**：每个指标的服务范围 buffer 图层作为生成图层注册，供地图联动展示。

## 注意事项

- 数值必须来自工具真实计算结果，禁止 LLM 编造覆盖率数字。
- 覆盖率口径：覆盖面积 / 研究范围内居住用地面积，报告时注明口径。
