# 实施计划：平台数据（基础底座）加载与使用

> 日期：2026-08-17 ｜ 状态：产品设计（已确认，待实施）
> 关联：[工具目录设计](./2026-08-17-tools-module-design.md) ｜ [项目功能设计](../project-feature-design.md)

---

## 1. 目标（一句话）

把工作区 `平台数据/` 文件夹里的**真实深圳数据**（POI、行政区划、详规一张图、路网、DEM）加载为**平台数据图层**：
可作为分析底座参与计算、可基于它创建新数据（提取子集）；**数据内容不可被随意更改，但颜色/名字等样式可改**。

## 2. 平台数据清单（现状核实）

| 数据 | 文件夹 | 格式 | 用途 |
|---|---|---|---|
| POI 2023 | `平台数据/POI2023/` | shp（全类 + 医疗/交通/住宿/体育/公共设施/公司/商务住宅…分文件夹）| 设施点分析 |
| 深圳行政区划 | `平台数据/深圳行政/` | shp（市/街道/社区三级边界）| 研究范围/分区统计 |
| 详规一张图 2023 | `平台数据/深圳详规一张图2023/planning.shp` | shp | 规划用地底图 |
| 单线路网 2024 | `平台数据/2024深圳单线路网/` | shp | 路网分析/15分钟生活圈 |
| 30 米 DEM | `平台数据/30米DEM/深圳市_DEM.tif` | tif（≈207MB，含 .ovr 金字塔）| 高程分析（坡度/坡向）|

> 所有 shp 为 WGS84（`EPSG:4326`，crs 字段已核对）；DEM 为 GeoTIFF。**geopandas 1.1.1 + rasterio 1.3.9 已装**，转换管线现成。

## 3. 与现有机制的关系（好消息）

现有代码已为"平台数据"打好基础，**不必新造概念**：

| 现有机制 | 位置 | 对平台数据的意义 |
|---|---|---|
| `LayerSource.platform` 枚举 | `models/layer.py` | 平台数据图层直接用 `source="platform"`，天然与 uploaded/generated 区分 |
| `LayerMetadata` 样式模型 | `models/layer.py` | 颜色/透明度/描边/分档设色全支持——满足"样式可改" |
| `DATA_REGISTRY.load_demo()` | `data_registry.py` | 目前从 `demo_data/layers.json` 读内置 8 个示例图层 |
| `PATCH /layers/{id}` + 样式侧车 | `main.py` + `style_store.py` | 名字/可见性/样式持久化——满足"名字可改" |
| `gis-mcp` rasterio 工具 | bridge | DEM 高程计算（坡度/坡向）已覆盖 |

**要补的核心**：把 `平台数据/` 的 shp/tif **转换成 GeoJSON/图层并注册进注册表**，即从"内置示例数据"升级为"真实深圳底座数据"。

## 4. 加载策略

### 4.1 转换管线（一次性脚本，产物可复用）

新建 `apps/api/app/scripts/build_platform_layers.py`：
1. 扫描 `平台数据/` 各子目录
2. 用 geopandas 读 shp → 转 WGS84（统一 EPSG:4326）→ 写 `apps/api/app/platform_data/geojson/<id>.json`
3. 用 rasterio 读 DEM → 按现有栅格路径注册（或先加载元数据，切片渲染后置）
4. 生成 `platform_layers.json`（LayerMetadata 列表：id/name/source=platform/geometry/feature_count/fields/description）
5. **产物提交到仓库**（或按需增量）：demo 时无需每次转换

转换与校验：
- 字段名中文化保留（如 POI 的 `名称/类型` 等原始字段）
- 记录每个图层的真实 `feature_count` 与 `bbox`
- DEM 单独一条目，标注 `kind=raster`

### 4.2 注册（两处）

| 位置 | 改动 |
|---|---|
| `data_registry.load_demo()` | 增加：读 `platform_data/layers.json`，与 demo 图层合并注册（source 均为 platform）|
| `DataRegistry` | 增加 `load_platform_geojson`，把平台图层的 GeoJSON 装入 `_geojson`（供渲染与 GIS 引擎）|

### 4.3 只读保护（数据不可改）

`LayerMetadata` 增加标记：`readonly: bool = False`；平台数据置 `True`。

| 写面 | 保护规则 |
|---|---|
| `PATCH /layers/{id}` | `readonly=True` 时**允许**：name/visible/样式字段（color/opacity/stroke/radius/fillColor/styleMode/visualVariables/labelField）|
| 同上 | **拒绝**：任何数据内容类字段（id/geometry/feature_count/crs/fields）——目前 PATCH 本就只暴露样式字段，天然满足 |
| 工具层 | 分析工具可读平台图层；不提供"覆盖平台图层内容"的写工具 |

> 说明：样式字段存的是"视觉呈现"，不动数据本身；这与常见的 `manual_ui` 思想一致（样式是 UI 状态）。

### 4.4 前端呈现

- 图层面板按 `source` 分组：**平台数据**（可展开）在上，上传/生成图层在下
- 平台数据图层行带"只读"角标（🔒），样式/改名仍可用，删除/覆盖隐藏或需确认
- DEM 图层：显示为栅格瓦片（复用现有 raster 渲染路径，切片后置）

## 5. 平台数据能干什么（使用场景）

| 场景 | 示例 | 涉及工具 |
|---|---|---|
| 分析底座 | 以深圳街道边界为研究范围算设施覆盖率 | coverage/spatial_join/statistics |
| 基于它创建数据 | 从全类 POI 里提取"医疗保健服务"子集 → 新图层 | 提取子集工具（新） |
| 底图 | 详规一张图作规划底图（配色修改后展示）| 样式工具 |
| 高程分析 | DEM 坡度/坡向 → 新栅格/面层 | gis-mcp rasterio |
| 15 分钟生活圈 | 路网 + POI 计算等时圈/服务盲区 | 现有分析工具 |

### 5.1 新增工具：提取子集（create_from_data）

与"基于数据创建数据"需求对应，建议新增 1 个确定性工具：

```
extract_features
参数: source_layer_id（平台或任意图层）
      filter（属性条件，如 {"type": "医院"}）
      output_name（新图层名）
返回: 新图层 id（source=generated），地图自动上架
```

实现：geopandas 读源 GeoJSON → 属性过滤 → 写结果图层 → `register_generated`。**确定性、全真计算**。

## 6. 实施步骤

| # | 任务 | 位置 | 说明 |
|---|---|---|---|
| P1 | `build_platform_layers.py`：shp→GeoJSON + layers.json 生成 | `apps/api/app/scripts/`（新）| geopandas 读取，统一 EPSG:4326，产物落 `app/platform_data/` |
| P2 | DEM 注册（元数据 + 切片渲染后置）| `app/platform_data/` | rasterio 读元数据；渲染管线后置 |
| P3 | 注册表接入：`load_demo()` 合并平台图层 + 几何 | `app/data_registry.py` | 复用现有加载路径 |
| P4 | `LayerMetadata.readonly` + PATCH 保护 | `models/layer.py` + `main.py` | 样式/改名放行，数据内容拒绝 |
| P5 | 前端：图层面板 source 分组 + 只读角标 | `apps/web`（LayerPanel）| 平台数据在上、锁标记、样式仍可改 |
| P6 | `extract_features` 工具 | `tools/registry.py` + `tools/__init__.py` | 属性过滤 → 新图层 |
| P7 | DEM 渲染（切片/瓦片）| 后置 sprint | 单独 sprint，先不做 |

## 7. 验证

1. 启动后 `/layers` 返回平台数据图层（POI/行政/详规/路网/DEM），source=platform
2. 地图能渲染真实深圳数据（缩放/平移流畅）
3. PATCH 改名/改色成功、跨重启保留；数据字段字段不可改
4. Agent 说"提取医疗类 POI 做成图层" → 生成新图层上地图
5. Agent 基于街道边界 + POI 算覆盖率，结果真计算
6. `scripts/verify_llm.py` 全绿回归

## 8. 明确边界

- **不做**：DEM 渲染管线（P7 后置）、坐标系实时转换 UI、平台数据管理 CRUD（增删平台数据源）、跨项目平台数据差异
- **演示口径不变**：确定性 GeoJSON、全部真计算、绝不编造数值
- 平台数据 = 只读底座 + 可改样式；产物图层（extract/分析结果）走 source=generated，不受只读限制
