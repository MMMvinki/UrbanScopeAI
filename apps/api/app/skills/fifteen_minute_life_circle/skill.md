---
id: fifteen_minute_life_circle
name: 15 分钟生活圈分析
core_question: 居民能否方便到达基本公共服务设施？
keywords:
  - 15分钟
  - 十五分钟
  - 生活圈
  - 便利
  - 最后一公里
required_data:
  - 人口
  - 学校设施
  - 公园绿地
  - 文化活动中心
outputs:
  - 等时圈图层
  - 覆盖率统计
  - 服务盲区
plan:
  operation: coverage
  targets:
    - schools
    - parks
    - culture_centers
  target_distances:
    schools: 800
    parks: 500
    culture_centers: 1000
indicators:
  - name: 学校步行可达性
    data: 学校设施
    mvp_depth: 真实计算
    status: ready
    steps:
      - coverage(schools, 800m)
      - 按人口图层统计可达人口占比
  - name: 公园步行可达性
    data: 公园绿地
    mvp_depth: 真实计算
    status: ready
    steps:
      - coverage(parks, 500m)
      - 按人口图层统计可达人口占比
  - name: 文化设施可达性
    data: 文化活动中心
    mvp_depth: 真实计算
    status: ready
    steps:
      - coverage(culture_centers, 1000m)
      - 按人口图层统计可达人口占比
---

# 15 分钟生活圈分析

## 适用场景

用户询问"居民生活是否便利 / 15 分钟生活圈 / 最后一公里服务 / 设施可达性"。

## 执行流程

1. **就绪检查**：核对人口、学校、公园、文化设施图层是否存在。
2. **逐设施类目分析**：对每类设施按步行可达标准调用 `coverage` 工具：
   - 学校 800m（小学/中学步行 15 分钟）
   - 公园绿地 500m（社区公园步行可达）
   - 文化活动中心 1000m
3. **人口可达统计**：用人口网格图层与各设施服务范围叠加，估算可达人口占比。
4. **识别盲区**：未覆盖人口网格即服务盲区，生成盲区提示图层。
5. **汇总结论**：各类设施可达性的短板形成结论，写入结果组与 evidence。

## 注意事项

- 距离取值按国家标准（居住区生活圈步行 15 分钟 = 800–1000m）。
- 覆盖率为真计算，禁止 LLM 编造数字；工具失败时走规则兜底。
