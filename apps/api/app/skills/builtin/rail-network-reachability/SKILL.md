# 全区地铁步行可达率（出入口簇 → 5/10/15 分钟）

## 何时使用

当用户要求对**全区/所有地铁出入口**做真实步行可达评价（可达率/难达/覆盖/所有地铁站/
全网可达），而不只是选取少数代表出入口时，调用 `execute_skill_script`，指定
`skill_id=rail-network-reachability`。

与 `commercial-rail-accessibility` 的区别：后者按 500m 商业密度受控取 ≤5 个真实出入口
（single-corridor 口径，72D 场景 2 仍走它）；本 Skill 把研究范围内全部出入口聚类成
出入口簇、每簇一个代表再请求等时圈，输出全区 5/10/15 分钟可达带 + 难达区 + 可达率。

## 固定方法

- 等时圈只来自 Mapbox 真实路网（`profile=walking`、`minutes=[5,10,15]`），复用
  `app.tools.isochrone._call_isochrone` 的请求实现（内置重试/退避），不新建路网算法。
- 出入口聚簇：研究范围内真实出入口两两直线距离 ≤300 m 视为同一出入口簇（union-find），
  每簇取**距簇质心最近的真实出入口**为代表，并标 `cluster_size`（簇内含口数）。
  只称「出入口簇/代表出入口」，不冒充地铁站名（OSM name 多为 C/D1 类编号）。
- 串行请求每个代表的 5/10/15 等时圈（档间 0.3s 间隔，失败中心记 `failed_centers`），
  跨簇逐分钟档取空间并集（fused5 ⊆ fused10 ⊆ fused15）。
- 图层：三档可达带（5=深、10=中、15=浅，环带）、15 分钟外「难达区」、
  出入口簇代表点（标注簇内含口数）。全部 EPSG:4326。
- 统计（米制 EPSG:32650）：各档 `intersection(boundary).area` → km² 与覆盖率%；
  难达区 = `boundary.difference(fused15)` 面积 km² 与 %；商业点在并集内 `within` 计数
  （≤5/≤10/≤15 累计 + 分带新增 + 分类构成）；守恒断言
  `≤5 带 + 5-10 带 + 10-15 带 + 难达区 = 研究区总面积`。
- **预计算缓存**：命中 `data/cache/rail_network_reachability_<输入hash>.json`（真实
  Mapbox 结果落盘、含输入文件 sha256 校验）即读缓存秒开；现场演示不依赖实时批请求。
  token 不落盘、不进缓存、不进日志。

## 红线

- 不使用 `coverage.stable_rate` 演示覆盖率兜底；全部真实路网请求失败 → `success=false`
  （`STOP_ISOCHRONE_FAILED`），部分失败 → 只用成功簇统计并在 evidence 写明失败中心数。
- 不把 500 米直线圆形称为步行可达；出入口编号只作标识、聚簇只称出入口簇/代表。
- 数字只来自真实原始文件（研究范围/出入口 shp/商业源+连接表）；统计用全量，不抽样。
- 覆盖率分母 = 研究区面积 km²（约 25 km²），页面与 evidence 都写明，不留孤立的百分比。

## 输出

三档可达带 + 难达区 + 出入口簇代表三类图层，及 stats（覆盖面积/率/难达/守恒/商业点
累计与分类）、evidence（输入 sha256、坐标、聚类口径、成功/失败中心、缓存命中与否）。

## 可达 × 多类资产（默认四类 + 可延伸）

默认叠加四类资产做可达盘点（商业 + 公共服务 + 文化设施 + 绿地），各产出
`reach_class`（≤5/≤10/≤15/外）点/面图层与 `stats.<key>_reach`（计数/占比/分类构成/守恒）：

| key | 图层 | 数据源（路径均可用 `*_path` 参数覆盖） |
|---|---|---|
| commercial | `rail_poi_reach_study` | 商业源 + 连接表（isdelete=0） |
| public | `rail_public_reach_study` | POI 主表（四域纳入清单） |
| culture | `rail_culture_reach_study` | 文化资源 shp（排除经营/培训） |
| green | `rail_green_reach` | 用地 shp（Layer 含「绿地」地块，面） |

**路径不是写死的**：默认值仅作仓库内兜底，agent/调用方始终可用
`commercial_path` / `public_poi_path` / `culture_path` / `green_land_path` 传入真实源；
更推荐用 **`overlay_layers`** 把任意已注册 POI/AOI 图层 id 传给本 Skill，Skill 读其
`data/layers/<id>.json`（EPSG:4326，点/面均可）做可达叠加，输出 `rail_overlay_<id>` 图层与
`stats.overlay_<id>_reach`。

**发现可叠加资产（高效，不扫盘）**：`_list_overlay_candidates(repo_root, workspace)` 返回
「默认四类 + 已物化点/面图层」的候选清单（名称/几何/来源/图层 id），agent 依此挑选后经
`overlay_layers` / `*_path` 注入。候选基于 data/layers 物化索引与目录快照，非全盘扫描。

红线补充：公共服务/文化/绿地计数是点位或地块数量，不是服务容量、品质或真实可达水平；
绿地按 Layer 含「绿地」的地块纳入，不推断产权/运营。
