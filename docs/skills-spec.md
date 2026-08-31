# 基础分析技能规格 v0.1（喂给 UrbanScopeAI 的 skill-creator）

> 用途：缪工把本文件的 4 个技能规格喂给 UrbanScopeAI 里的 Agent（skill-creator 链路）创建真技能。
> **这是「定义」不是「代码」**——本文件不写 SKILL.md / skill.json / scripts/main.py，只定义每个技能的方法论、输入、输出指标与判定规则。
> 所有真计算都落在已成型的「地基」上（见文末《地基依赖》），Agent 建出的技能只是调用它，不重造。

---

## 0. 共同约定（所有技能共用，建技能时先照此写）

### 0.1 数据源
原始人口/客流数据登记在 `app/data_sources.json`（已入库，路径相对仓库根，李工换数据目录只改这一处）：

| source_id | 内容 | 对应人口 | 关键列 |
|---|---|---|---|
| `huoyan_resident` | 居住人口量 + 画像（109,709 点） | 常住/居住 | `人数`（绝对量）、`性别_男/女`、`年龄阶段_1..7`、`职业/行业/人生阶段/资产` 占比 |
| `huoyan_worker` | 就业人口量 + 画像（110,471 点） | 就业/工作 | 同上（字段结构一致） |
| `huoyan_pedestrian` | 客流（3,272,237 行，小时级） | 昼间/夜间活力 | `人数`、`小时`（0~23）、`网格ID` |

- `人数` = 绝对量；画像占比列（如 `年龄阶段_1`）是 **0~1 小数**（0.72 即 72%）。
- 带 `_s` / 尾随下划线的列（`日期_sl`、`网格x坐标_`）是冗余副本，**计算一律用无后缀数字列**。

### 0.2 分析单元（面单元，已注册图层）
- 街道：`shenzhen_streets`（76，名字列 `JDNAME`）
- 社区：`shenzhen_communities`（666，名字列 `SQNAME`）
- 调用 `app.gis.aggregation.aggregate_points_to_zones`（点量值→区，sum/count/mean）与 `groupby_zone_ratios`（占比重加权）。
- 单元 polygons 已是 UTM EPSG:32650；点来自 `get_source`（EPSG:4326 WGS-84，**无偏移**），内部提示词必须在聚合前统一投影到 UTM 50N。

### 0.3 诚实失败（文档 §11 / §9.3-1，铁律）
- 面缺失 / 点空 / 无有效几何 / 缺列 → 返回 `success=False` + reason（`missing_source|missing_param|no_geometry|missing_columns|no_data_in_range`）+ 中文说明。
- **严禁**：面给不出就返回一个编造的覆盖率/密度；没有某列就猜一个数补上。
- 部分区无点（dropped>0）：在 `warnings` 里报「有 N 个点未落入任何面单元（占 M%），已从统计中剔除」，不中断、不造值。
- 结果必须携带 `source`（数据来源，如 `huoyan_resident`）、`params`（实际参数）、`rules`（用的判定规则）、`next_questions`。

### 0.4 输出契约（对齐 §4.4 统一结果格式）
每个技能出一组：`metrics[]`（结构化指标条）、`findings[]`（结构化发现）、`warnings[]`、`params{}`、`rules[]`、`source`、`next_questions[]`、`layers[]`（结果图层，`<id>_<后缀>`，自动注册上图并分层设色）。

---

## 1. 人口画像 population_profile

**description（触发话术参考）**：分析某区域的人口结构画像（年龄/性别/学历/收入/职业/行业分布），回答「这里的人口是什么样的人」「人口画像如何」。

**required_data**：`huoyan_resident`（或 `huoyan_worker`）+ 面单元图层（默认 `shenzhen_streets`）。

**输入参数（input_schema）**：

| 参数 | 必填 | 说明 | 默认 |
|---|---|---|---|
| `source_id` | 是 | `huoyan_resident`/`huoyan_worker` | — |
| `zone_layer_id` | 否 | 分析单元 | `shenzhen_streets` |
| `dims` | 否 | 画像维度（gender/age/education/income/car/life_stage/occupation/industry，可多选） | 全选 |
| `region_filter` | 否 | 只看某几个街道名（如 南园、华强北），不传=全市 | 全市 |
| `top_n` | 否 | 每维度取占比最高的 N 档写出 | 3 |

**方法**：`get_source(source_id, columns=["人数"]+dims对应列)` → 逐个维度 `groupby_zone_ratios(pop_field="人数", ratio_cols=该维度列)` 得各区加权占比；`region_filter` 时先按区名过滤。绝对量（该区人口数）用 `aggregate_points_to_zones(..., weight_field="人数", agg="sum")`。

**输出指标（metrics）**：每维度 per-区 占比 + 全区加权占比对照：
- 各区人口数、人口密度
- 性别比（男/女）
- 年龄结构占比（7 档，建议归类：亚幼 `_1`、`_2`、中 `_3`..`_4`、熟 `_5`..`_7`，归类映射先按数据源给 default，李工再核）
- 学历占比、收入占比、有车占比、职业/行业 TOP5

**判定规则（rules，默认阈值给缪工调）**：
- 老龄化区：老化档（`_5`~`_7` 之和）> **20%**；
- 年轻化区：`_1`~`_2` 之和 > **50%**；
- 单身/无车倾向：有车占比 < **30%**；
- 判「比较基准」= 全市（本次所有点的）加权占比，文本用「高/低于全市 X 个百分点」。

**输出**：
- `load`：各区人口结构 choropleth（按人口数分层设色）或每维度一个占比图；绝对量指标条。
- 发现示例：`南园 60 岁+ 占比 24%（全市 15%），老龄化特征明显`。
- 质量警告：dropped 点；某维度某区无数据（fillna 0 需提示）。
- 继续分析建议：人口老年化→「是否影响周边公共服务需求」；高学历区→「联动商业/就业结构」。

---

## 2. 居住与就业人口密度 population_density

**description**：计算各区居住/就业人口密度与职住平衡，回答「哪里人口最密」「哪里是就业中心」「哪里职住失衡」。

**required_data**：`huoyan_resident` + `huoyan_worker` + 面单元图层（默认 `shenzhen_streets`）。

**输入参数**：

| 参数 | 必填 | 说明 | 默认 |
|---|---|---|---|
| `mode` | 否 | `resident`/`worker`/`both` | `both` |
| `zone_layer_id` | 否 | 分析单元 | `shenzhen_streets` |
| `threshold_high` | 否 | 高密度阈值（倍数，相对全市均值） | 2.0 |
| `balance_range` | 否 | 职住平衡判定的比值范围 [lo,hi] | `[0.5, 2.0]` |

**方法**：
- 居住：`aggregate_points_to_zones(huoyan_resident, zone, weight_field="人数", agg="sum")` → value=居住人口；`人口密度 = value / area_km2`。
- 就业：同上用 `huoyan_worker`。
- 职住比 `ratio = 就业密度 / 居住密度`（密度用同单位人/km²，避免面积干扰）。

**输出指标**：每区 居住人口、居住密度（人/km²）、就业人口、就业密度、职住比。

**判定规则（默认）**：
- 高密度区：密度 ≥ 全区平均密度 × `threshold_high`（默认 2）；
- 居住型（睡城）：职住比 < `[0.5]` 下限；就业型（商务区）：> `[2.0]` 上限；混合型：落在 `[0.5, 2.0]`。
- 文本示例：`福田区 JDNAME=X 区就业密度 3.2 万人/km²，为全区均值 2.3 倍，属就业型高密度区`。

**输出**：居住密度 / 就业密度 choropleth 各一；职住比分级图（居住型/混合型/就业型三色）；指标条 + 发现。

**质量警告**：区面积 < 0.5 km² 时密度波动大，提示不参与排序（阈值区置信度低）。

---

## 3. 商业业态结构 poi_structure

**description**：统计各分析单元内的商业 POI 数量与业态结构，回答「哪里商业最密集」「商业以什么业态为主」「商业业态是否多样」。

**required_data**：POI 商业类图层（平台已注册 `poi_餐饮`、`poi_住宿`、`poi_零售/购物`、`poi_生活服务`、`poi_公司企业`、`poi_娱乐` 等）+ 面单元图层（默认 `shenzhen_streets`）。

**输入参数**：

| 参数 | 必填 | 说明 | 默认 |
|---|---|---|---|
| `poi_layer_ids` | 是 | 商业 POI 图层 id 列表 | `poi_餐饮` 等 |
| `zone_layer_id` | 否 | 分析单元 | `shenzhen_streets` |
| `top_n` | 否 | 每区列出的业态 TOP | 5 |
| `entropy_cut` | 否 | 业态多样性「高/低」的香农熵阈值 | `0.6` |

**方法**：每个 POI 图层 `engine.load_gdf`（点）→ `aggregate_points_to_zones(poi_gdf, zone, weight_field=None, agg="count")` 得各区该业态点数。多图层合并成 `poi_type` 长表：每区每业态点数 + 占比。业态多样性用香农熵 `H = -Σ p_i log p_i / log K`（K=业态数，归一化 0~1）。

**输出指标**：每区商业 POI 总数、商业密度（个/km²）、业态 TOP5 与占比、业态多样性 H。

**判定规则（默认）**：
- 商业中心区：商业 POI 总数 ≥ 全区均值 × **1.5**，且密度 ≥ 均值；
- 业态单一（H < `entropy_cut` 默认 0.6）：某单一业态占比 > **60%**；
- 业态多样（H ≥ 0.6）：无单一业态 > 40%。

**输出**：各业态数量分布图（bar）；每区商业密度 choropleth；业态结构长表；多样性指标条。

**质量警告**：POI 图层若本身坐标是 GCJ-02（平台 `poi_*` 的 经度/纬度 列是 GCJ-02，但 `geometry` 是 WGS-84）——**必须取 geometry 聚合，禁止读列坐标**，否则结果全区东偏 ~520m。

---

## 4. 功能混合度 poi_mix

**description**：计算各分析单元的功能混合度（居住/商业/办公/公共服务等功能的复合程度），回答「这里是单一功能区还是功能混合区」「职住商是否复合」。

**required_data**：`huoyan_resident`（居住）、`huoyan_worker`（办公/就业）、POI 商业类（零售≡商业）、公共服务 POI（学校/医院/公园≡公共服务）+ 面单元图层（默认 `shenzhen_streets`）。

**输入参数**：

| 参数 | 必填 | 说明 | 默认 |
|---|---|---|---|
| `zone_layer_id` | 否 | 分析单元 | `shenzhen_streets` |
| `mix_cut` | 否 | 高/低混合度阈值 | `0.6` |
| `high_cut` | 否 | 混合度「很高」阈值 | `0.75` |

**方法**：把人口/POI 归入功能大类，每类量纲归一（人口/工作用绝对量，POI 用数量），池化到区后算归一化熵：
- 功能大类：居住、就业（职）、商业、公共服务、其他；
- 每区每类占比 `p_i`，混合度 `M = 1 - Σ p_i²`（Simpson 指数，范围 0~1，越高越混合）。
- 需要先给每类出统一口径（缺工人数或零售 POI 单独缺失时，该区该类置 0 并进 `warnings`）。

**输出指标**：每区功能混合度 M（0~1）、各功能占比、混合度分级。

**判定规则（默认）**：
- 混合复合区：`M ≥ mix_cut`（默认 0.6）；
- 高度复合：`M ≥ high_cut`（默认 0.75）；
- 单一功能区：`M < 0.35`（如纯居住/纯工业）。

**输出**：混合度 choropleth（分级设色，单色渐变即可）；每区功能占比堆叠/饼图；指标条 + 发现。

**质量警告**：某功能类缺对应数据源时不动声色归 0 —— 必须 `warnings` 写明「缺少 X 类数据，混合度仅基于 A/B/C 三类」；严禁用编数量补类。

---

## 5. 技能间的依赖与顺序（建技能时说明）

- 这 4 个是「第 1 层基础描述 + 第 2 层空间分析」的基础结果，供李工「第 3 层组合诊断」用（§5.3 分析层级）。
- 输出要保持同一空间单元口径（默认街道；社区版切 `shenzhen_communities` + `SQNAME`），否则组合时对不上。

---

## 附：地基依赖（本文件不重复实现，由基础设施提供）

| 能力 | 位置 | 一句话 |
|---|---|---|
| 原始人口/客流按需读盘 | `app/data_sources.py` `get_source(id, columns, bbox, where)` | 大表只读列 + 空间/条件前缀，绝不整层载入；返回无偏移 WGS-84。 |
| 点入面聚合 | `app/gis/aggregation.aggregate_points_to_zones` | sum/count/mean 点量→面，丢的点进 `meta.dropped`。 |
| 结构占比重加权 | `app/gis/aggregation.groupby_zone_ratios` | Σ(pop·ratio)/Σ(pop) 逐面加权占比。 |
| 面单元 | `shenzhen_streets`(JDNAME) / `shenzhen_communities`(SQNAME) | UTM 32650，含 SHAPE_Area。 |
| 诚实失败 | 各工具 `success=False`+reason | 缺面/空点/缺列→原因 + 中文说明，绝不假值。 |
| 统一结果 | `ResultGroup.metrics/findings/warnings/params/rules/source/next_questions/stop_reason` | 前端成果栏渲染指标条/发现/质量提示/继续分析/停止徽标。 |
