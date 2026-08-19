---
id: kernel-density
name: 核密度分析
description: 对点图层（POI/事件点）做核密度（Kernel Density Estimation，KDE）分析，生成密度分布网格多边形图层并按密度分层设色。当用户问「核密度/密度分布/热点分布/密度图层/密度图/栅格热力图」时使用。
core_question: 点的空间密度分布如何？
keywords:
  - 核密度
  - 密度分布
  - 热点
  - 密度图层
  - 密度图
  - KDE
entry: scripts/main.py
runtime:
  language: python
  dependencies:
    - geopandas
    - shapely
    - scipy
    - numpy
---

# 核密度分析

## 适用场景

用户要对点图层（如餐饮 POI、事件点）做空间密度分布分析，问「哪里密度高」「热点在哪」「密度分布如何」。

## 执行方式

- **可执行 skill**：Executor 命中后用 `execute_skill_script`（skill_id=`kernel-density`）代码直跑，真计算。
- 底层引擎：`app.gis.engine.kernel_density`（UTM 米制网格 + scipy gaussian_kde）。
- 产物：网格多边形 GeoJSON 图层（`<点id>_kde`），按 `density` 字段分层设色（graduated 5 级）上图；密度直方图进成果栏。

## 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `point_layer_id` | 是 | 点图层 id（平台 POI 或工作区点图层，如 `poi_餐饮`） |
| `area_layer_id` | 否 | 研究范围多边形图层 id，把点裁到范围内再算 |
| `cell_size_m` | 否 | 密度网格边长（米），默认 50（引擎超限时自动放宽 500/800/1000） |
| `bandwidth_m` | 否 | 核平滑带宽（米）；默认自动带宽并封顶 150m |
| `bw_method` | 否 | 自动带宽方法 scott/silverman，默认 scott |

## 注意事项

- 数值来自引擎真实计算，禁止 LLM 编造密度数字；stats 的 max/mean/median 以 output.json 为准。
- 平台图层只支持 GeoJSON——本 skill 自动生成网格多边形图层，禁止改存 .npy/.tif。
- 网格数超引擎 5 万格上限时自动放大格网退档（真计算不变，只降采样分辨率）。
