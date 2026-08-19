---
id: advanced-clustering
name: 空间聚类
description: 对点图层做空间聚类（DBSCAN / K-Means），在 UTM 米制坐标上真实聚类，输出按簇分类设色的 clustered 图层 + centers 质心图层与簇统计。当用户问「聚类/分组/找群/热点分区/DBSCAN/簇」时使用。
core_question: 点数据在空间上聚集成了几群？
keywords:
  - 聚类
  - 分组
  - DBSCAN
  - K-Means
  - 簇
  - 群
entry: scripts/main.py
runtime:
  language: python
  dependencies:
    - geopandas
    - shapely
    - scipy
    - numpy
    - scikit-learn
---

# 空间聚类

## 适用场景

用户要对点图层（POI、事件点）做空间聚类分析，问「这些点聚成几群」「哪些点属于同一个簇」「找热点片区」。

## 执行方式

- **可执行 skill**：Executor 命中后用 `execute_skill_script`（skill_id=`advanced-clustering`）代码直跑，真计算。
- 底层引擎：UTM 米制坐标 + sklearn DBSCAN / K-Means。
- 产物：`<点id>_clustered` 图层（每点带 cluster 标签，DBSCAN 噪声已剔除，按 cluster 分类设色）+ `<点id>_centers` 图层（各簇质心）。

## 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `layer_id` | 是 | 要聚类的点图层 id |
| `method` | 否 | `dbscan` 或 `kmeans`，默认 dbscan |
| `eps` | 否 | DBSCAN 邻域半径（米），默认 300 |
| `min_samples` | 否 | DBSCAN 核心点最少邻居数，默认 5 |
| `k` | 否 | K-Means 簇数；默认按点量自适应（3-10） |

## 注意事项

- 数值来自 sklearn 真实聚类，禁止 LLM 编造簇数/噪声占比；stats 以 output.json 为准。
- DBSCAN 的 -1 噪声点不会上图（clustered 已剔除），centers 只含真实簇质心。
- 点要素不足 2 个无法聚类，会在 output 里报错说明。
