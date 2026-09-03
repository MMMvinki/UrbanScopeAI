---
id: building-function-color
name: 范围内建筑功能分类配色
core_question: 这个范围内的建筑都是干什么用的？按功能分类并用颜色区分
keywords:
  - 建筑功能
  - 建筑用途
  - 建筑分类
  - 建筑上色
  - 建筑描边
  - 按功能区分
  - 用颜色区分建筑
  - 范围内建筑
  - 区域内建筑
  - building function
  - 建筑业态
required_data:
  - 研究范围
  - 建筑
outputs:
  - 建筑功能分类图层
  - 分类配色呈现
  - 功能占比饼图(可选)
plan:
  operation: general
  # targets 留空：由 Planner/Executor 按当前项目真实图层动态选取范围层，不硬编码图层 id。
  targets: []
  target_distances: {}
indicators:
  - name: 范围内建筑获取
    data: 研究范围 + 建筑
    mvp_depth: 真实计算
    status: ready
    steps:
      - 读范围层取 bbox
      - osm_call(download_bbox, key=building) 拉建筑
      - spatial_clip 裁到范围
  - name: 建筑功能分类标注
    data: OSM 建筑标签
    mvp_depth: 真实计算
    status: ready
    steps:
      - 由 amenity/shop/office/leisure 等标签映射功能
      - 无标签 → 其他
  - name: 分类配色呈现
    data: func_type 字段
    mvp_depth: 真实计算
    status: ready
    steps:
      - set_categorized_style(mapping=精确语义色)
      - 只留一个数据源为真建筑的规范图层
---

# 范围内建筑功能分类配色

## 适用场景

用户给一个研究范围，要看"这块地方每栋建筑是干什么用的"，并按功能上色。典型说法：
- "帮我获取规划范围内的建筑，按功能分类并上色"
- "把XX范围内的建筑按用途画出来，不同功能不同颜色"
- "这个片区的建筑都是干什么的，用颜色区分一下"
- "建筑描边、换颜色"（在本 skill 语境下=给建筑加轮廓 + 按功能换填充色）

## 输入参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `area_layer_id` | 是 | 研究范围多边形图层 id（如 `规划范围_568b13`）；用户只给地名时先 geocode 或借用现有范围层 |
| `area_name` | 否 | 产物图层显示名，默认「规划范围内建筑功能分类」 |
| `color_map` | 否 | 用户指定某类颜色时给 `{功能: hex色}`；不给则用下方「默认语义配色」 |

## 执行流程（确定性、全真计算）

1. **探范围**：`query_features` + `get_dataset_schema` 读 `area_layer_id`，用 `execute_code` 取图层真实 bbox（minx/miny/maxx/maxy）。
2. **拉建筑（中间层，事后删）**：`osm_call(command=download_bbox)` 传 bbox + `key=building` 下载范围内建筑，得 OSM 建筑图层（如「OSM建筑原始结果」）。**记下它的 layer_id**；这是中间计算层，不是产物。若 OSM 覆盖不足（返回空/极少），改用 `amap_call`（建筑类 POI）兜底，并说明数据源。
3. **裁剪（中间层，事后删）**：`spatial_clip(target=建筑, clip_layer=范围层)` 只留范围内建筑，得「OSM建筑原始裁剪」。**记下它的 layer_id**；同样是中间层，不是产物。
4. **分类功能**：`execute_code` 读 OSM 标签，**用共享分类器** `from app.gis.labels import classify_building_function`（它已内置下方「分类规则」的完整字典，含 hotel/fire_station 等，且把所有无功能含义的 `building=yes/roof/construction/tower/no` 落「其他」）。对整块 clip 逐行 `props["func_type"] = classify_building_function(props)`。**严禁自己手写分类字典**——历史上手写字典会漏 hotel/fire_station 等标签，导致「building 写了功能却被归成其他」（用户复现过）。取值严格是 9 个标签（居住/商业/办公/教育/医疗/公共服务/工业/交通/其他），禁止发明新标签。
5. **简化与命名**：**直接跑下面这段（把分类 + 保留原始标签 + 剔导航键 + 裁非面 一步做完，别再做二次「只留几列」伪筛选）**，`<clip_id>`/`<final_id>` 用前两步骤返回的真实图层 id：

   ```python
   import geopandas as gpd
   from app.gis.labels import classify_building_function, NAV_KEYS

   gdf = gpd.read_file("data/layers/<clip_id>.json")          # 读裁剪层（含全部 OSM 原始标签）
   gdf["func_type"] = gdf.apply(lambda r: classify_building_function(r.to_dict()), axis=1)
   keep = [c for c in gdf.columns if c not in NAV_KEYS]        # 只剔 OSM 导航元数据键，其余全保留
   gdf = gdf[keep]
   gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
   gdf.to_file("data/layers/<final_id>.json", driver="GeoJSON", encoding="utf-8")
   print("rows:", len(gdf), "| func_type 分布:", gdf["func_type"].value_counts().to_dict())
   ```

   **别把属性表删成只剩两三列**（用户反馈过"属性表很不全"：保留 name/building/amenity/shop/office/landuse/height/addr:*/operator 等原始标签，才能对照解释"为什么这么分"）。`NAV_KEYS` 只含 `@id/@relations/source/created_by/version/timestamp/changeset/user/uid` 这些导航键。
6. **按语义配色**：`set_categorized_style(field="func_type", mapping=「下面 9 类各自的语义 hex」)`。
   - **只传 `mapping`（`{标签: hex色}` 字典），**禁止只传 `palette`**。`mapping` 是字典、与标签一一绑定，颜色天然确定（某标签永远同色、调序无效）；只传 `palette` 会按「取值频次第 N 高」铺色，且你每次给的 palette 顺序不同 → 同一标签每次都拿到不同颜色——这就是「每次使用颜色都不一样」的根因。
   - **mapping 里的 key 必须与 func_type 实际标签完全一致**，用下方「默认语义色」。表里没有的标签会被 `mapping` 兜底成灰 `#9CA3AF`（确定、不乱跳）。
   - 用户给了 `color_map` 就按其覆盖；覆盖不到的类仍用语义色。
7. **收尾**：本 skill 只应留下**一个产物图层**（命名清晰，如「规划范围内建筑功能分类」）+ 用户原本就有的范围层/底图；**其余本 skill 新建的中间层全部清掉**。
   - **清理中间层（必须）**：对步骤 2/3 记下的「OSM建筑原始结果」「OSM建筑原始裁剪」中间层**逐个 `remove_layer`**。它们是列表噪音（用户明确反馈过），**严禁留存**。只处理本 skill 新建的中间层，**绝不**删用户的范围层、底图、其它分析产物。
   - 确保目标图层可见（默认已可见，必要时 `set_layer_visibility(visible=true)`）；**禁止用 `show_only_layer`**——它会关掉用户已打开的其他图层（范围层/底图），这是「生成了新图层、之前图层被关」的根因；用 `zoom_to_layer` 定位；可选用 `emit_chart` 出功能占比饼图。
   - **描边**：分类就是看填充色，**平台对 categorized 图层默认无描边**（deck.gl 与 MapLibre 兜底都把 `strokeWidth=0` 当默认）。若仍见基础色描边（历史图层 / 非分类路径残留），用 `update_layer_style(strokeWidth=0)` 补一刀；改描边/透明度会**保留**分类配色（已修：不再清空 visualVariables），所以先去边框再分类、或分类后再改描边都安全；不要为此走 `execute_code` 烘焙。

## 分类规则（OSM 标签 → 功能类，func_type 只允许这 9 个取值）

**权威实现 = `app/gis/labels.py` 的 `classify_building_function` / `BUILDING` / `AMENITY`**。agent 必须 `from app.gis.labels import classify_building_function`，**严禁手写/抄写下面字典**——手写必漏 hotel/fire_station 等（用户复现过「building 有功能却归成其他」），「照着这张表写」不可靠，import 才确定。下面表只用于理解语义与取色，不作为手写依据。

**优先级（从高到低，命中即停）**：① `amenity`/`shop`/`office` 语义标签 → ② `building` 标签 → ③ landuse=industrial → ④ 其他。

**① 语义标签映射**（最优先）：

| 功能类 | 命中标签 | 默认色（mapping 用它） |
|---|---|---|
| 商业 | `shop∈*`，或 `amenity∈{retail,restaurant,cafe,bar,bank,fast_food,marketplace,supermarket}` | 红 `#FF4444` |
| 办公 | `office∈*`（无 shop） | 蓝 `#4169E1` |
| 教育 | `amenity∈{school,kindergarten,college,university,library,music_school}` | 绿 `#32CD32` |
| 医疗 | `amenity∈{hospital,clinic,doctors,dentist,pharmacy}` | 青 `#00CED1` |
| 公共服务 | `amenity∈{government,police,fire_station,post_office,townhall,courthouse,embassy}` | 品红 `#FF69B4` |
| 交通 | `amenity∈{bus_station,train_station,parking,charging_station,taxi,ferry_terminal}` | 蓝紫 `#9370DB` |

**② `building` 标签映射**（无 amenity/shop/office 时用 —— **这是"building 写了具体功能却被归成其他"的补漏根因**，务必覆盖到）：

| 功能类 | `building∈`（小写） | 默认色 |
|---|---|---|
| 商业 | commercial, retail, supermarket, hotel, shops | 红 |
| 办公 | office | 蓝 |
| 教育 | school, college, university, kindergarten, kindergarden, school_house, classrooms | 绿 |
| 医疗 | hospital, clinic | 青 |
| 公共服务 | civic, government, public, fire_station, police, church, mosque, temple, chapel, community_centre | 品红 |
| 交通 | train_station, transportation, station, pavilion | 蓝紫 |
| 居住 | house, residential, apartments, dormitory, detached, semidetached, terrace, bungalow, cabin, hut | 金 `#FFD700` |
| 工业 | industrial, warehouse, manufacture, factory, barn | 棕 `#8B4513` |

**③ landuse=industrial** → 工业；**④ 其他**：以上皆无（含 `building=yes/roof/tower/grandstand/elevator/porch/no` 等无功能含义）→ 灰 `#808080`。

**step 6 的 mapping 就照上表逐条写**（9 键→9 色），别再传 palette。某功能类在这批数据里取值为空也没关系——mapping 里照样给出该键，不出现即不上色；mapping 未列出的取值落灰 `#9CA3AF` 兜底（确定、不乱跳）。分类结果以 `classify_building_function` 返回值为准，若与上表有出入以 `app/gis/labels.py` 为准。

## 注意事项（硬性边界，不要做）

- **严禁**用 `execute_code` 把颜色烘焙进 GeoJSON 再 `register_layer` 一个新图层——那会造出「无用的重复图层」（如历史上的「…(配色)结果图层」）。颜色是样式不是数据，分类一变就得重新烘焙，且纯属列表噪音。
- **严禁**在分类逻辑不到位时反复调 palette 顺序试；**只传 `mapping`**。每次传不同顺序的 palette = 每个类别每次拿到不同颜色——「每次使用颜色都不一样」的根因就在这。
- **严禁**边改列边 `register` 多个近似图层（历史上出现过 4 个几乎一样的建筑图层）；只留一个规范图层，中间副本用 `remove_layer` 清掉。
- **严禁**把 OSM 原始下载层 / 裁剪输出层留着当用户图层——「OSM建筑原始结果」「OSM建筑原始裁剪」只是中间计算层，**必须 `remove_layer` 删掉**，用户只该看到最终「规划范围内建筑功能分类」。
- **分类必须同时吃 `amenity`/`shop`/`office` 和 `building` 两类标签**：先用语义标签（①），命中不了再取 `building` 的具体功能值（②，见上方映射表）；**禁止**只看 amenity 就跳过 building、也**禁止**把 `building=yes/roof/construction` 这类无功能含义的值硬归成某类——那种才落「其他」。历史上「building 写了功能却归成其他」就是漏了第 ② 步。
- **严禁**用 `show_only_layer` 隐藏用户其他图层；只确保自己的产物图层可见（默认可见），用 `zoom_to_layer` 定位，绝不「仅显示我、其它全关」。
- 数据口径 = 真读盘的真建筑。feature_count、分类计数一律来自产物 geojson，**禁止 LLM 编造数量**。
- 只做「范围内建筑功能分类 + 配色」，不叠加热力图 / 密度 / 服务半径等其他口径。
- 缺范围层、OSM+amap 都拉不到、范围内无建筑数据 → 明确说明，不硬凑、不编造「其他」占比。
