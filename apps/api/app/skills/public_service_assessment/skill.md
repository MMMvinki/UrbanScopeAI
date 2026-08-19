---
id: public_service_assessment
name: 公共服务设施评估
core_question: 设施数量和空间布局是否匹配人口需求？
keywords:
  - 公共服务
  - 设施评估
  - 评估
  - 设施配置
  - 供需
required_data:
  - 人口
  - 公共服务设施
  - 街区边界
outputs:
  - 供需匹配指数
  - 空间错配区
  - 设施短板清单
plan:
  operation: supply_demand
  targets:
    - schools
    - culture_centers
indicators:
  - name: 学校供需匹配
    data: 人口 + 学校设施
    mvp_depth: 真实计算
    status: ready
    steps:
      - supply_demand(schools, population_grid)
      - 计算供需匹配指数
      - 定位空间错配区
  - name: 文化设施供需匹配
    data: 人口 + 文化活动中心
    mvp_depth: 真实计算
    status: ready
    steps:
      - supply_demand(culture_centers, population_grid)
      - 计算供需匹配指数
      - 定位空间错配区
---

# 公共服务设施评估

## 适用场景

用户询问"设施配置是否充足 / 评估公共服务 / 设施够不够 / 供需匹配情况"。

## 执行流程

1. **就绪检查**：核对人口网格、设施图层、街区边界是否存在。
2. **供需匹配分析**：对每类设施调用 `supply_demand` 工具，以人口网格为需求侧、设施为供给侧。
3. **计算匹配指数**：供给强度（设施容量/服务范围）与需求强度（人口密度）的比值，输出匹配指数与空间错配区。
4. **产出短板清单**：匹配指数低于阈值的街区进入设施短板清单。
5. **汇总结论**：整体供给水平、主要短板方向写入结果组与 evidence。

## 注意事项

- 供需指数 = 供给强度 / 需求强度，报告时注明计算口径与阈值。
- 数值必须来自工具真计算，禁止 LLM 编造。
