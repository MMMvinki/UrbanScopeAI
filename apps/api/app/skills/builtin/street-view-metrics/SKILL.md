---
id: street-view-metrics
name: 街景绿视率与天空可视因子
description: 沿路网布点，采集百度街景全景图，用 Cityscapes 预训练语义分割模型（fastseg）逐像素分类，计算绿视率（植被占比）、天空可视因子（天空占比）等类别占比，输出点图层上图。当用户问「绿视率 / 天空可视因子 / 街景分类占比 / 绿化可视率 / 天空可见度」时使用。
core_question: 街道沿线的绿视率与天空可视因子分布如何？
keywords:
  - 绿视率
  - 天空可视因子
  - 街景
  - 街景分类占比
  - 绿化可视率
  - 天空可见度
  - 语义分割
entry: scripts/main.py
runtime:
  language: python
  dependencies:
    - torch
    - fastseg
    - geopandas
    - shapely
    - pyproj
    - numpy
    - requests
    - Pillow
---

# 街景绿视率与天空可视因子

> **⚠ 当前状态：预留（数据源待接入）**
>
> 本技能骨架、取图函数、语义分割链路、点图层产出全部完成并已验证（模型加载/推理/错误码处理端到端跑通）。
> **唯一未落地的是「真实街景图数据源」的授权 key** —— 百度全景静态图已升为需付费开通的高级服务（`240 APP 服务被禁用`），
> 腾讯街景对个人开发者免费但需先开发者认证。拿到任一家 key 即可真算什么。
>
> - 数据源已做成**可插拔**：只需改 `scripts/panorama.py` 的 `fetch_panorama()` 内部实现，其余（布点/分割/聚合/上图）全复用。
>   - 百度：`https://api.map.baidu.com/panorama/v2`，`coordtype=wgs84ll` 免坐标转换，AK 读 `BAIDU_AK`。
>   - 腾讯：`https://apis.map.qq.com/ws/streetview/v1/image`，建议「坐标→pano→取图」两步，key 类型选服务端；图上限 960×640 需放大到 ≥1024×512 喂 fastseg。
> - 独立实测脚本：[examples/fetch_and_segment_demo.py](../../../examples/fetch_and_segment_demo.py)（拿 key 直接跑，逐点打绿视率/天空率 + 存分割蒙太奇）。

## 适用场景

用户要在研究范围内分析「街景里的绿色占比 / 天空占比」，即惯用的街道景观度量：
**绿视率（Green View Index）** 与 **天空可视因子（Sky View Factor）**，以及建筑/道路等类别占比。
典型问法：「这片街区的绿视率怎么样」「天空可视因子分布」「哪些街道绿化好」。

## 执行方式

- **可执行 skill**：Executor 命中后用 `execute_skill_script`（skill_id=`street-view-metrics`）代码直跑，真计算。
- 底层：先沿路网按 `spacing_m` 布点 → 百度全景静态图 API（`/panorama/v2`，`coordtype=wgs84ll` 免坐标转换）
  每个点取 4 朝向（`view_mode=quad`）或 360° 全景（`view_mode=full`）→ fastseg（Cityscapes 19 类预训练）
  逐像素分类 → 按类别汇总算占比。
- 产物：点图层（<study>_streetview），每点含 `green_view_ratio` / `sky_view_factor` / `building_ratio` /
  `road_ratio` 等字段，按 `green_view_ratio` 分层设色上图；绿视率-天空率对比图进成果栏。

## 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `bbox` | 二选一 | 研究范围 [西经, 南纬, 东经, 北纬]（WGS-84） |
| `study_area_layer_id` | 二选一 | 研究范围多边形图层 id，用其范围限定采样 |
| `roads_layer_id` | 否 | 道路图层（LineString）id；不传则从 OSM 抓路网 |
| `spacing_m` | 否 | 沿路网采样间距（米），默认 50；大面积建议 100~200 |
| `view_mode` | 否 | `quad`=4 朝向(默认)；`full`=360°全景单图 |
| `fov` | 否 | 水平视角，默认 90 |
| `pitch` | 否 | 垂直视角，默认 -5（略俯视） |
| `max_points` | 否 | 本次最多采样点数，默认 60（防单次超时，超面积会提示分块） |
| `baidu_ak` | 否 | 百度服务端 AK；缺省读环境变量 `BAIDU_AK` / `apps/api/.env` |

## 注意事项

- **坐标**：平台图层/传入坐标一律 WGS-84；取图时 `coordtype=wgs84ll`，无需转 GCJ-02/BD-09。
- **覆盖不全**：百度全景覆盖不达处返回 402（该点无 panoid），自动跳过不硬编造 0。
- **相机近似**：Cityscapes 在车载前向相机上训练，百度全景按 `fov≈90°, pitch≈-5°` 裁近似视角，
  算出的占比是「接近」而非像素级真值，适合研究/相对比较。
- **一次只出一个图层**：结果单图层 `green_view_ratio` 分层设色；其余占比是属性字段，可改色重上。
- **超时护栏**：`fastseg` 单图 CPU 约 0.5~1.5s，全量受 `max_points` 截断；若超出建议缩小 `spacing_m` 或分块多次。
- **依赖**：`torch`+`fastseg` 体积较大，首次运行会自动检测缺失并 `pip install`（约数分钟，之后缓存）。
