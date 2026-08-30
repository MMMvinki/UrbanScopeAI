"""工具注册表：唯一事实源。ToolSpec.json_schema 为 LLM 工具调用契约，
执行实现位于 tools/__init__.py 的 TOOL_IMPLEMENTATIONS。"""

from app.models.tool import ToolSpec

TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="list_datasets",
        category="inspection",
        description="列出当前项目可用的空间数据图层。",
        status="ready",
        json_schema={"type": "object", "properties": {}, "required": []},
    ),
    ToolSpec(
        name="get_dataset_schema",
        category="inspection",
        description="读取图层字段、几何类型和要素数。",
        status="ready",
        parameters={"layer_id": "图层 id，如 schools"},
        json_schema={
            "type": "object",
            "properties": {"layer_id": {"type": "string", "description": "图层 id，如 schools"}},
            "required": ["layer_id"],
        },
    ),
    ToolSpec(
        name="buffer",
        category="analysis",
        description="按距离生成设施服务半径缓冲区图层。",
        status="ready",
        parameters={"layer_id": "源图层 id", "distance": "缓冲距离（米），默认 800"},
        json_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "源图层 id，如 schools"},
                "distance": {"type": "integer", "description": "缓冲距离（米），默认 800"},
            },
            "required": ["layer_id"],
        },
    ),
    ToolSpec(
        name="coverage",
        category="analysis",
        description="计算设施服务半径覆盖率，返回结果图层与覆盖/未覆盖面积。",
        status="ready",
        parameters={"layer_id": "目标设施图层 id", "distance": "服务半径（米），默认 800"},
        json_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "设施图层 id，如 schools/parks"},
                "distance": {"type": "integer", "description": "服务半径（米），默认 800"},
            },
            "required": ["layer_id"],
        },
    ),
    ToolSpec(
        name="isochrone",
        category="analysis",
        description=(
            "生成等时圈（按真实路网从中心点 N 分钟可达范围，Mapbox Isochrone API）。"
            "一次调用生成【一个图层】：把要分析的全部中心点一次性传进 centers，"
            "如用 query_features 从地铁站/POI 图层取区域内所有站点坐标组成 [[经度, 纬度, '站点名'], ...]，"
            "所有站点×各分钟档的等时圈合并到同一图层；严禁只传一个站或拆成多次调用。"
            "默认 merge=true 融合：同一分钟档的多边形取并集——一个时间档一个融合形状"
            "（不再是一站一个圈），多档时相邻档相减成环带（5分钟=中心、15分钟=最外）；"
            "merge=false 才保留一站一圈。"
            "坐标必须是 WGS84 经纬度（勿臆造，从已有图层查真实坐标）；minutes 传 1~4 个整数分钟；"
            "profile 可选 walking（步行，默认）/driving（驾车）/cycling（骑行）/driving-traffic（实时驾车）。"
            "默认按 contour 分钟统一配色（同时间档同色：单档整层一色、多档分钟越大颜色越深）；"
            "style_by=station 可改为按站点分色。"
            "生成后图层自动上地图，可用于服务范围/15 分钟生活圈等分析。"
        ),
        status="ready",
        parameters={
            "centers": "中心点数组 [[经度, 纬度, 站点名], ...]，一次传全部中心点（一次调用=一个图层）",
            "center": "可选，单中心点 [经度, 纬度]（多中心时用 centers）",
            "minutes": "分钟数组 1~4 个（如 [5,10,15]）",
            "profile": "出行方式：walking/driving/cycling/driving-traffic，默认 walking",
            "name": "可选，图层基础名",
            "palette": "可选，配色（单档可传 #hex 色；多档默认蓝梯度；station 默认 category10）",
            "style_by": "可选，contour 按分钟统一配色（默认）/ station 按站点分色",
            "merge": "可选，默认 true 融合（同档多边形并集、多档成环带）；false 保留一站一圈",
        },
        json_schema={
            "type": "object",
            "properties": {
                "centers": {
                    "type": "array",
                    "items": {
                        "type": "array",
                        "items": {},
                        "minItems": 2,
                        "description": "[经度, 纬度, 可选站点名]",
                    },
                    "description": "中心点数组，一次传全部（如区域内所有地铁站）：[[经度, 纬度, '站点名'], ...]",
                },
                "center": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 2,
                    "maxItems": 2,
                    "description": "单中心点 [经度, 纬度]，WGS84 坐标系（多中心请用 centers）",
                },
                "minutes": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1, "maximum": 120},
                    "minItems": 1,
                    "maxItems": 4,
                    "description": "等时圈分钟档（1~4 个，如 [5, 10, 15]）",
                },
                "profile": {
                    "type": "string",
                    "enum": ["walking", "driving", "cycling", "driving-traffic"],
                    "description": "出行方式，默认 walking",
                },
                "name": {"type": "string", "description": "图层基础名（如「华侨城地铁站步行15分钟」）"},
                "palette": {"type": "string", "description": "分类配色名，默认 blues 单中心 / category10 多中心"},
                "style_by": {
                    "type": "string",
                    "enum": ["contour", "station"],
                    "description": "contour=按分钟分色，station=按站点分色（默认自动）",
                },
                "merge": {
                    "type": "boolean",
                    "description": "默认 true：融合（同档多边形并集、多档相减成环带）；false 保留一站一圈",
                },
            },
            "required": ["centers", "minutes"],
        },
    ),
    ToolSpec(
        name="spatial_join",
        category="analysis",
        description="空间叠加两个图层，统计空间关系占比。",
        status="ready",
        parameters={"target_layer_id": "目标图层 id", "overlay_layer_id": "叠加图层 id"},
        json_schema={
            "type": "object",
            "properties": {
                "target_layer_id": {"type": "string", "description": "目标图层 id"},
                "overlay_layer_id": {"type": "string", "description": "叠加图层 id"},
            },
            "required": ["target_layer_id", "overlay_layer_id"],
        },
    ),
    ToolSpec(
        name="nearest_distance",
        category="analysis",
        description="计算目标图层要素到最近参考设施的平均距离。",
        status="ready",
        parameters={"target_layer_id": "目标图层 id", "reference_layer_id": "参考设施图层 id"},
        json_schema={
            "type": "object",
            "properties": {
                "target_layer_id": {"type": "string", "description": "目标图层 id"},
                "reference_layer_id": {"type": "string", "description": "参考设施图层 id"},
            },
            "required": ["target_layer_id", "reference_layer_id"],
        },
    ),
    ToolSpec(
        name="density",
        category="analysis",
        description="计算图层的空间密度指数（一个数值，非核密度分布）。",
        status="ready",
        parameters={"layer_id": "图层 id"},
        json_schema={
            "type": "object",
            "properties": {"layer_id": {"type": "string", "description": "图层 id"}},
            "required": ["layer_id"],
        },
    ),
    ToolSpec(
        name="supply_demand",
        category="analysis",
        description="计算设施与人口的供需匹配满足度。",
        status="ready",
        parameters={"facility_layer_id": "设施图层 id", "population_layer_id": "人口图层 id"},
        json_schema={
            "type": "object",
            "properties": {
                "facility_layer_id": {"type": "string", "description": "设施图层 id"},
                "population_layer_id": {"type": "string", "description": "人口图层 id"},
            },
            "required": ["facility_layer_id", "population_layer_id"],
        },
    ),
    ToolSpec(
        name="bivariate_moran",
        category="analysis",
        description=(
            "双变量空间自相关（双变量 Moran's I + LISA）：衡量两个变量在空间上的协同聚集/排斥。"
            "模式 A：传 layer_id + x_field + y_field（同一多边形图层两个数值字段，用邻接权重；"
            "点图层用 KNN）。模式 B：传 x_layer_id + y_layer_id（两个点图层，自动渔网聚合后计算，"
            "cell_size_m 控制格网）。输出带 lisa_cluster（HH/LH/LL/HL/NS）的生成图层并自动配色，"
            "返回全局 Moran's I 与伪 p 值。"
        ),
        status="ready",
        parameters={
            "layer_id": "多边形/点图层 id（模式 A）",
            "x_field": "X 数值字段名（模式 A）",
            "y_field": "Y 数值字段名（模式 A）",
            "x_layer_id": "X 点图层 id（模式 B，两图层自动渔网）",
            "y_layer_id": "Y 点图层 id（模式 B）",
            "cell_size_m": "渔网格宽米（模式 B，默认 300，下限 100）",
            "permutations": "置换次数（默认 999）",
            "name": "图层基础名（可选）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "多边形/点图层 id（模式 A）"},
                "x_field": {"type": "string", "description": "X 数值字段名（模式 A）"},
                "y_field": {"type": "string", "description": "Y 数值字段名（模式 A）"},
                "x_layer_id": {"type": "string", "description": "X 点图层 id（模式 B）"},
                "y_layer_id": {"type": "string", "description": "Y 点图层 id（模式 B）"},
                "cell_size_m": {"type": "integer", "description": "渔网格宽米（模式 B，默认 300）"},
                "permutations": {"type": "integer", "description": "置换次数（默认 999）"},
                "name": {"type": "string", "description": "图层基础名"},
            },
        },
    ),
    ToolSpec(
        name="statistics",
        category="analysis",
        description="汇总多个图层的指标。",
        status="ready",
        parameters={"layer_ids": "图层 id 列表", "agg": "coverage_rate|avg_distance|density|satisfaction"},
        json_schema={
            "type": "object",
            "properties": {
                "layer_ids": {"type": "array", "items": {"type": "string"}},
                "agg": {
                    "type": "string",
                    "enum": ["coverage_rate", "avg_distance", "density", "satisfaction"],
                },
            },
            "required": ["layer_ids"],
        },
    ),
    # ── Phase 0：数据获取（联网取数 / 导入） ─────────────────────────
    ToolSpec(
        name="osm_call",
        category="data",
        description=(
            "通过 Overpass/Nominatim 下载 OpenStreetMap 数据。"
            "command ∈ {overpass_query, download_bbox, download_features, search}，"
            "params 为 JSON 字符串，如 '{\"place\":\"上海\",\"key\":\"building\"}'。\n"
            "POI 下载配方：① 先 search 拿中心点坐标——同名地铁/公交站（class=railway/highway）常排在最前，"
            "取点前检查第一条结果，选 class/type 为 exhibition_centre/amenity/building/tourism 的地点作中心；"
            "② 用 download_bbox 一次下载全部业态：south/west/north/east（中心点±约 0.01 度）+ key='amenity' + "
            "value 用 | 分隔多业态（如 'restaurant|cafe|fast_food'），不传 geometry_type 自动查 node+way；"
            "③ 下载必然落盘，output 有 path 字段，立即用 register_layer 发布成地图图层（file_path 填 path）；"
            "④ 分析用 execute_code 读已发布图层 data/layers/<id>.json 按字段统计，每个统计目标只出一张图。"
            "禁止手写 overpass_query Overpass QL 或直接 requests 调 Overpass API（易被 400/406 拒且浪费轮次）。"
        ),
        display_description=(
            "从 OpenStreetMap 下载真实地图数据（路网/建筑/POI 等），支持地点搜索与按范围批量下载；"
            "下载后可发布为地图图层。"
        ),
        status="ready",
        parameters={
            "command": "overpass_query|download_bbox|download_features|search",
            "params": "JSON 字符串，如 '{\"place\":\"上海\",\"key\":\"building\",\"value\":\"*\"}'",
        },
        json_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "OSM 命令：overpass_query/download_bbox/download_features/search"},
                "params": {
                    "type": "string",
                    "description": "JSON 字符串参数，如 '{\"place\":\"上海\",\"key\":\"building\"}'。download_bbox 需要 south/west/north/east + key；download_features 需要 place + key；search 需要 query。",
                },
            },
            "required": ["command"],
        },
    ),
    ToolSpec(
        name="amap_call",
        category="data",
        description=(
            "通过高德地图 Web 服务 API 获取中国数据（覆盖 OSM 在中国 POI 稀疏/无行政区边界的短板）。"
            "command ∈ {poi_search, district, geocode, regeo}，params 为 JSON 字符串。\n"
            "① poi_search 下载 POI（最常用）：mode ∈ {text, around, polygon}。"
            "text=关键字+城市（keywords='肯德基' + city='深圳'）；"
            "around=中心点周边（location=[经度,纬度] + radius 米，默认 3000，WGS-84）；"
            "polygon=按范围框下载（bbox=[西,南,东,北]，types 给高德行业分类码如 050000 餐饮服务，"
            "超过单次上限自动分块全量爬取）。keyword 与 types 至少给一个；"
            "下载结果必然落盘到 .workdir/amap/，output 有 path 字段，立即用 register_layer 发布成地图图层。\n"
            "② district 下载行政区边界：keywords='深圳市'/'南山区' + subdistrict=0（本级）或 1（下一级），"
            "返回 WGS-84 多边形 GeoJSON，落盘后 register_layer 发布（高德边界只到区县一级）。\n"
            "③ geocode 地址转坐标：address='深圳市南山区科技园' + city（可选），返回 WGS-84 经纬度，"
            "用作分析中心点（如等时圈/周边搜索的中心，比 OSM search 更准）。\n"
            "④ regeo 坐标转地址：location=[经度,纬度]，返回行政区划与地址描述。\n"
            "**坐标系约定**：所有坐标统一 WGS-84（与平台图层一致），自动转高德 GCJ-02；结果属性保留 gcj02_lng/gcj02_lat 原值。"
        ),
        display_description=(
            "从高德地图获取中国 POI、行政区边界、地理编码数据（中国数据比 OSM 完整）。"
        ),
        status="ready",
        parameters={
            "command": "poi_search|district|geocode|regeo",
            "params": "JSON 字符串，如 '{\"mode\":\"polygon\",\"bbox\":[113.9,22.5,114.2,22.6],\"types\":\"050000\"}'",
        },
        json_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "高德命令：poi_search/district/geocode/regeo"},
                "params": {
                    "type": "string",
                    "description": "JSON 字符串参数。poi_search 需要 mode + (keywords|types) + 范围（text 要 city，around 要 location/radius，polygon 要 bbox）；district 要 keywords；geocode 要 address；regeo 要 location。",
                },
            },
            "required": ["command"],
        },
    ),
    ToolSpec(
        name="datasource_call",
        category="data",
        description="从预置 catalog 获取行政区划等公开 GeoJSON 数据。command ∈ {list, get, fetch}，params 为 JSON 字符串。",
        status="ready",
        parameters={
            "command": "list|get|fetch",
            "params": "JSON 字符串，如 '{\"name\":\"上海市\"}'",
        },
        json_schema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "list（列出全部）/ get（查单个源）/ fetch（下载 GeoJSON）"},
                "params": {"type": "string", "description": "JSON 字符串参数，如 '{\"name\":\"上海市\"}'"},
            },
            "required": ["command"],
        },
    ),
    ToolSpec(
        name="webfetch",
        category="system",
        description="抓取一个 HTTP/HTTPS URL 并返回 markdown/text/html。需要具体 URL 时用它；需要发现页面用 websearch。",
        status="ready",
        parameters={
            "url": "http(s) URL",
            "format": "markdown|text|html，默认 markdown",
            "timeout": "超时秒数，默认 30，最大 120",
        },
        json_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要抓取的 HTTP/HTTPS URL"},
                "format": {"type": "string", "enum": ["markdown", "text", "html"]},
                "timeout": {"type": "integer"},
            },
            "required": ["url"],
        },
    ),
    ToolSpec(
        name="websearch",
        category="system",
        description="搜索网络获取模型知识之外的当前信息，返回结果标题/URL/摘要。需要详情时用 webfetch 抓取选中 URL。",
        status="ready",
        parameters={
            "query": "搜索关键词",
            "num_results": "返回条数，默认 8，最大 20",
            "timeout": "超时秒数，默认 25，最大 60",
        },
        json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "num_results": {"type": "integer"},
                "timeout": {"type": "integer"},
            },
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="extract_features",
        category="data",
        description=(
            "从任意图层按属性条件提取子集，生成新图层（source=generated）并上地图。"
            "可用于从平台数据创建数据：如从深圳POI中提取'行业小含医院'的要素做成新图层。"
            "filter 为属性条件字典，如 {'name': '医院'} 精确匹配，"
            "{'type_': {'op': 'contains', 'value': '医院'}} 包含匹配，"
            "数值比较用 {'field': {'op': 'gt', 'value': 1000}}；多条件为 AND。"
        ),
        display_description="按属性条件从图层中提取符合要求的要素，生成新图层。",
        status="ready",
        parameters={
            "source_layer_id": "源图层 id（平台/上传/生成图层均可）",
            "filter": "属性过滤条件字典，如 {'name': '医院'} 或 {'type_': {'op': 'contains', 'value': '医院'}}",
            "output_name": "新图层显示名（可选）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "source_layer_id": {"type": "string", "description": "源图层 id"},
                "filter": {
                    "type": "object",
                    "description": "属性过滤条件：{'field': value} 精确，{'field': {'op': 'contains|startswith|gt|gte|lt|lte', 'value': x}} 高级",
                },
                "output_name": {"type": "string", "description": "新图层显示名"},
            },
            "required": ["source_layer_id", "filter"],
        },
    ),
    ToolSpec(
        name="register_layer",
        category="data",
        description=(
            "把工作区内已下载/已生成的 GeoJSON 文件发布为地图图层，地图立即显示。"
            "OSM 下载（osm_call 的 download_bbox/download_features）落盘的文件不会自动上地图，"
            "需要本工具发布。file_path 通常是 .workdir/osm/download_bbox_xxx.geojson 这类路径。"
        ),
        display_description="把工作区内已下载/生成的 GeoJSON 文件发布为地图图层。",
        status="ready",
        parameters={
            "file_path": "工作区内 GeoJSON 文件路径（相对或绝对）",
            "layer_id": "可选：目标图层 id（默认由文件名推导）",
            "name": "可选：图层显示名（默认取 layer_id）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "要发布的 GeoJSON 文件路径，如 .workdir/osm/download_bbox_xxx.geojson"},
                "layer_id": {"type": "string", "description": "可选：目标图层 id"},
                "name": {"type": "string", "description": "可选：图层显示名称"},
            },
            "required": ["file_path"],
        },
    ),
    ToolSpec(
        name="register_uploaded_file",
        category="data",
        description=(
            "把 Agent 对话框上传并暂存在工作区的文件（data/uploads/ 下，如 data/uploads/xxx.csv）"
            "解析并注册为地图图层，地图立即显示。用户上传后文件默认不自动上地图，"
            "需要真正用它做地图分析时先调用本工具；支持 CSV/Excel/GeoJSON/SHP(zip)/GPKG，"
            "自动识别经纬度列。"
        ),
        display_description="把暂存的上传文件（CSV/GeoJSON/SHP 等）注册为地图图层。",
        status="ready",
        parameters={
            "file_path": "暂存的上传文件路径（data/uploads/ 下，相对或绝对）",
            "name": "可选：图层显示名（默认取文件名）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "暂存上传文件路径，如 data/uploads/oct_poi.csv"},
                "name": {"type": "string", "description": "可选：图层显示名称"},
            },
            "required": ["file_path"],
        },
    ),
    ToolSpec(
        name="csv_to_geojson",
        category="data",
        description="把带经纬度列的 CSV/Excel 文件转换为 GeoJSON 并保存到工作区。自动识别坐标列名（lat/latitude/y/纬度 与 lng/lon/longitude/x/经度）。",
        status="ready",
        parameters={
            "input_path": "工作区内 CSV/Excel 文件路径",
            "output_path": "输出 GeoJSON 路径（默认同名 .geojson）",
            "lat_column": "纬度列名（自动识别时省略）",
            "lng_column": "经度列名（自动识别时省略）",
            "delimiter": "CSV 分隔符（自动探测时省略）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "input_path": {"type": "string", "description": "工作区内 CSV/Excel 文件路径"},
                "output_path": {"type": "string"},
                "lat_column": {"type": "string"},
                "lng_column": {"type": "string"},
                "delimiter": {"type": "string"},
            },
            "required": ["input_path"],
        },
    ),
    # ── Sprint 2：地图控制 / 图层样式 ───────────────────────────────
    ToolSpec(
        name="add_layer",
        category="map",
        description=(
            "把图层添加到地图显示。layer_id 为已存在图层时置为可见；"
            "给 file_path 时把工作区内 GeoJSON 发布为地图图层（委托 register_layer）。"
        ),
        status="ready",
        parameters={
            "layer_id": "已存在图层 id（可选）",
            "file_path": "工作区内 GeoJSON 文件路径（可选，未给 layer_id 时用于发布）",
            "name": "发布时的显示名（可选）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "已存在图层 id"},
                "file_path": {"type": "string", "description": "工作区内 GeoJSON 文件路径（发布用）"},
                "name": {"type": "string"},
            },
        },
    ),
    ToolSpec(
        name="zoom_to_layer",
        category="map",
        description="地图缩放到指定图层，使其完整可见。",
        status="ready",
        parameters={"layer_id": "图层 id"},
        json_schema={
            "type": "object",
            "properties": {"layer_id": {"type": "string", "description": "图层 id"}},
            "required": ["layer_id"],
        },
    ),
    ToolSpec(
        name="set_layer_visibility",
        category="map",
        description="显示或隐藏地图上的图层。",
        status="ready",
        parameters={"layer_id": "图层 id", "visible": "是否可见（true 显示 / false 隐藏）"},
        json_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "图层 id"},
                "visible": {"type": "boolean", "description": "true 显示 / false 隐藏"},
            },
            "required": ["layer_id", "visible"],
        },
    ),
    ToolSpec(
        name="show_only_layer",
        category="map",
        description="仅显示指定图层，隐藏其余所有图层。",
        status="ready",
        parameters={"layer_id": "图层 id"},
        json_schema={
            "type": "object",
            "properties": {"layer_id": {"type": "string", "description": "图层 id"}},
            "required": ["layer_id"],
        },
    ),
    ToolSpec(
        name="update_layer_style",
        category="map",
        description=(
            "修改图层的单值样式：颜色、透明度、描边/边框、点大小、可见性。"
            "例：去边框 → line_width: 0；暖色 → fill_color: '#fd8d3c'。"
        ),
        status="ready",
        parameters={
            "layer_id": "图层 id",
            "color": "整体颜色（hex）",
            "opacity": "整体透明度 0..1",
            "fill_color": "填充色（面/点）",
            "line_color": "描边颜色",
            "line_width": "描边线宽（0 表示无边框）",
            "line_opacity": "描边透明度 0..1",
            "border_width": "边框宽度（别名，同 line_width）",
            "point_size": "点半径",
            "point_opacity": "点透明度 0..1",
            "visible": "是否可见",
        },
        json_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "图层 id"},
                "color": {"type": "string", "description": "整体颜色 hex，如 #fd8d3c"},
                "opacity": {"type": "number", "description": "整体透明度 0..1"},
                "fill_color": {"type": "string"},
                "line_color": {"type": "string"},
                "line_width": {"type": "number", "description": "描边线宽，0 表示无边框"},
                "line_opacity": {"type": "number"},
                "border_width": {"type": "number"},
                "point_size": {"type": "number", "description": "点半径"},
                "point_opacity": {"type": "number"},
                "visible": {"type": "boolean"},
            },
            "required": ["layer_id"],
        },
    ),
    ToolSpec(
        name="set_graduated_style",
        category="map",
        description=(
            "按数值字段分层设色（choropleth / graduated）：把要素按字段值分档，"
            "每档一个颜色。method ∈ quantile | equal-interval | geometric-interval | stddev | "
            "jenks（自然间断点，Fisher-Jenks）| manual。"
            "palette 可为色带名（viridis/plasma/reds/blues/greens/oranges/purples/rdylgn/rdylbu/spectral/tableau10）"
            "或逗号分隔的 hex 色串。breaks 为手动分档断点时 method 视为 manual。"
        ),
        status="ready",
        parameters={
            "layer_id": "图层 id",
            "field": "按哪个数值字段分档",
            "method": "分档方法：quantile|equal-interval|geometric-interval|stddev|jenks|manual（默认 quantile）",
            "classes": "分档数 2-12（默认 5）",
            "palette": "色带名或 hex 色串（默认 viridis）",
            "breaks": "手动断点数组（可选）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "图层 id"},
                "field": {"type": "string", "description": "数值字段名，如 pop/density"},
                "method": {
                    "type": "string",
                    "enum": ["quantile", "equal-interval", "geometric-interval", "stddev", "jenks", "manual"],
                    "description": "分档方法：quantile 分位数 / equal-interval 等距 / geometric-interval 几何间隔 / stddev 标准差 / jenks 自然间断点 / manual 手动",
                },
                "classes": {"type": "integer", "description": "分档数 2-12"},
                "palette": {"type": "string", "description": "色带名或逗号分隔 hex 色串"},
                "breaks": {"type": "array", "items": {"type": "number"}, "description": "手动断点"},
            },
            "required": ["layer_id", "field"],
        },
    ),
    ToolSpec(
        name="set_layer_visual_variables",
        category="map",
        description=(
            "设置图层的视觉变量：点大小按字段渐变（size_field）、透明度按字段渐变（opacity_field）。"
            "常用于「按某属性分一下透明度」的渐变效果。"
        ),
        status="ready",
        parameters={
            "layer_id": "图层 id",
            "size_field": "点大小按哪个字段渐变（可选）",
            "size_method": "size 分档方法（默认 quantile）",
            "size_classes": "size 分档数（默认 5）",
            "size_range": "点大小范围 [min, max]（默认 [4, 20]）",
            "opacity_field": "透明度按哪个字段渐变（可选）",
            "opacity_method": "opacity 分档方法（默认 quantile）",
            "opacity_classes": "opacity 分档数（默认 5）",
            "opacity_range": "透明度范围 [min, max]（默认 [0.3, 1.0]）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "图层 id"},
                "size_field": {"type": "string"},
                "size_method": {"type": "string"},
                "size_classes": {"type": "integer"},
                "size_range": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
                "opacity_field": {"type": "string"},
                "opacity_method": {"type": "string"},
                "opacity_classes": {"type": "integer"},
                "opacity_range": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2},
            },
            "required": ["layer_id"],
        },
    ),
    ToolSpec(
        name="query_features",
        category="data",
        description=(
            "查询图层要素：按属性条件从真实数据里筛出要素，返回命中数与样本属性。"
            "可用于探查'这个图层里有什么'或数清'叫人民医院的 POI 有几家'。"
            "filter 语法与提取工具一致：{'name': '医院'} 精确、{'type_': {'op': 'contains', 'value': '医院'}} 包含、"
            "{'pop': {'op': 'gt', 'value': 1000}} 数值比较。"
        ),
        display_description="按条件查询图层中的要素，返回命中数量与样本属性。",
        status="ready",
        parameters={
            "layer_id": "图层 id",
            "filter": "属性过滤条件字典（可选，缺省返回全部）",
            "limit": "返回样本条数，默认 20，最多 50",
        },
        json_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "图层 id"},
                "filter": {"type": "object", "description": "属性过滤条件字典（可选）"},
                "limit": {"type": "integer", "description": "样本条数，默认 20"},
            },
            "required": ["layer_id"],
        },
    ),
    ToolSpec(
        name="remove_layer",
        category="map",
        description="把图层从地图上移除（注册表与样式清理；平台只读图层拒绝，只能隐藏）。",
        status="ready",
        parameters={"layer_id": "图层 id"},
        json_schema={
            "type": "object",
            "properties": {"layer_id": {"type": "string", "description": "图层 id"}},
            "required": ["layer_id"],
        },
    ),
    ToolSpec(
        name="set_layer_order",
        category="map",
        description="调整图层的显示顺序：to_index 为可见序列下标，0 表示最底层。",
        status="ready",
        parameters={"layer_id": "图层 id", "to_index": "目标顺序下标（0=最底）"},
        json_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "图层 id"},
                "to_index": {"type": "integer", "description": "目标顺序下标，0=最底"},
            },
            "required": ["layer_id", "to_index"],
        },
    ),
    # ── Sprint 2 地图控制扩展（display.py 对齐面）：全部真实现 ──
    ToolSpec(
        name="set_basemap",
        category="map",
        description=(
            "切换地图底图：卫星影像 / 浅灰街道 / 深色街道 / 街道详图。"
            "basemap_id ∈ satellite | light | dark | streets（默认 satellite）。"
        ),
        status="ready",
        parameters={
            "basemap_id": "底图 id：satellite（卫星）/ light（浅灰）/ dark（深色）/ streets（街道详图）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "basemap_id": {
                    "type": "string",
                    "enum": ["satellite", "light", "dark", "streets"],
                    "description": "底图 id",
                }
            },
            "required": ["basemap_id"],
        },
    ),
    ToolSpec(
        name="set_categorized_style",
        category="map",
        description=(
            "分类配色：按类型字段的唯一取值给要素分色（如按 school/hospital 分色）。"
            "读字段真实取值，频次最高前 11 类各一色，其余并入「其它」。palette 为色带名"
            "（默认 category10）或逗号分隔 hex 色串。"
        ),
        status="ready",
        parameters={
            "layer_id": "图层 id",
            "field": "按哪个分类字段分色（字符串字段，如 type/name）",
            "palette": "色带名或 hex 色串（可选，默认 category10）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "图层 id"},
                "field": {"type": "string", "description": "分类字段名"},
                "palette": {"type": "string", "description": "色带名或逗号分隔 hex 色串"},
            },
            "required": ["layer_id", "field"],
        },
    ),
    ToolSpec(
        name="set_layer_filter",
        category="map",
        description=(
            "图层过滤：只显示满足条件的要素。filter 语法与查询一致："
            "{'type': '医院'} 精确、{'name': {'op': 'contains', 'value': '人民医院'}} 包含、"
            "{'pop': {'op': 'gt', 'value': 1000}} 数值比较。filter={} 清除过滤。"
        ),
        status="ready",
        parameters={
            "layer_id": "图层 id",
            "filter": "属性过滤条件字典（空对象清除过滤）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "图层 id"},
                "filter": {"type": "object", "description": "属性过滤条件字典"},
            },
            "required": ["layer_id", "filter"],
        },
    ),
    ToolSpec(
        name="set_layer_label",
        category="map",
        description=(
            "图层标注：在要素边上显示指定字段的取值（如 POI 名称）。"
            "field=None 或空字符串清除标注。"
        ),
        status="ready",
        parameters={
            "layer_id": "图层 id",
            "field": "标注字段名（None 清除标注）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "图层 id"},
                "field": {"type": "string", "description": "标注字段名"},
            },
            "required": ["layer_id"],
        },
    ),
    ToolSpec(
        name="highlight_features",
        category="map",
        description=(
            "高亮要素：满足条件的要素用醒目色标出（默认金色）。"
            "filter 语法与 set_layer_filter 一致；filter={} 清除高亮。"
            "常见于把分析结果在图上标出来。"
        ),
        status="ready",
        parameters={
            "layer_id": "图层 id",
            "filter": "属性过滤条件字典（空对象清除高亮）",
            "color": "高亮颜色 hex（可选，默认金色 #ffd166）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "layer_id": {"type": "string", "description": "图层 id"},
                "filter": {"type": "object", "description": "属性过滤条件字典"},
                "color": {"type": "string", "description": "高亮颜色 hex"},
            },
            "required": ["layer_id", "filter"],
        },
    ),
    # ── 文件与系统：14 个文件操作工具（工作区沙箱内真实现） ────────────
    ToolSpec(
        name="list_files",
        category="file",
        description="列出工作区目录内容（文件/目录/大小），path 缺省列出根目录。",
        status="ready",
        parameters={"path": "要列出的目录（相对工作区，缺省为根）"},
        json_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "目录路径（相对工作区）"}},
        },
    ),
    ToolSpec(
        name="read_file",
        category="file",
        description="读取工作区内文本文件内容（超 200KB 自动截断）。",
        status="ready",
        parameters={"path": "文件路径（相对工作区）"},
        json_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "文件路径（相对工作区）"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="write_file",
        category="file",
        description="写文件（覆盖）：把内容写入工作区指定路径，目录不存在自动创建。",
        status="ready",
        parameters={"path": "文件路径", "content": "文件内容"},
        json_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径（相对工作区）"},
                "content": {"type": "string", "description": "文件内容"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolSpec(
        name="append_to_file",
        category="file",
        description="往文件末尾追加内容（文件不存在则创建）。",
        status="ready",
        parameters={"path": "文件路径", "content": "要追加的内容"},
        json_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件路径（相对工作区）"},
                "content": {"type": "string", "description": "要追加的内容"},
            },
            "required": ["path", "content"],
        },
    ),
    ToolSpec(
        name="edit_file",
        category="file",
        description="按文本替换编辑文件（old_text → new_text；replace_all=true 替换全部匹配）。",
        status="ready",
        parameters={"path": "文件路径", "old_text": "要替换的文本", "new_text": "替换后的文本", "replace_all": "是否全部替换"},
        json_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old_text": {"type": "string"},
                "new_text": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["path", "old_text"],
        },
    ),
    ToolSpec(
        name="create_folder",
        category="file",
        description="创建工作区目录（含父目录，已存在则幂等）。",
        status="ready",
        parameters={"path": "目录路径（相对工作区）"},
        json_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "目录路径"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="delete_file",
        category="file",
        description="删除工作区文件或目录（递归）。不允许删除工作区根或 .workdir 内部。",
        status="ready",
        parameters={"path": "要删除的文件/目录路径"},
        json_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "文件/目录路径"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="move_file",
        category="file",
        description="移动 / 重命名工作区文件（src → dst，目录自动创建）。",
        status="ready",
        parameters={"src": "源路径", "dst": "目标路径"},
        json_schema={
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "源路径"},
                "dst": {"type": "string", "description": "目标路径"},
            },
            "required": ["src", "dst"],
        },
    ),
    ToolSpec(
        name="copy_file",
        category="file",
        description="复制工作区文件或目录到新位置。",
        status="ready",
        parameters={"src": "源路径", "dst": "目标路径"},
        json_schema={
            "type": "object",
            "properties": {
                "src": {"type": "string", "description": "源路径"},
                "dst": {"type": "string", "description": "目标路径"},
            },
            "required": ["src", "dst"],
        },
    ),
    ToolSpec(
        name="search_files",
        category="file",
        description="搜索工作区文件：按文件名（默认）或内容关键字匹配，最多返回 50 条。",
        status="ready",
        parameters={"query": "关键字", "path": "搜索范围目录（缺省为根）", "content": "是否按内容搜索"},
        json_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "path": {"type": "string"},
                "content": {"type": "boolean"},
            },
            "required": ["query"],
        },
    ),
    ToolSpec(
        name="get_file_info",
        category="file",
        description="查看工作区文件/目录的元信息（类型/大小/修改时间/子项数）。",
        status="ready",
        parameters={"path": "文件/目录路径"},
        json_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "路径"}},
            "required": ["path"],
        },
    ),
    ToolSpec(
        name="run_terminal",
        category="file",
        description=(
            "在工作区根目录执行一条终端命令（超时 20s，输出截断）。"
            "需要真跑命令时用它；复杂逻辑建议用 execute_code。"
        ),
        status="ready",
        parameters={"command": "要执行的终端命令"},
        json_schema={
            "type": "object",
            "properties": {"command": {"type": "string", "description": "终端命令"}},
            "required": ["command"],
        },
    ),
    ToolSpec(
        name="run_script",
        category="file",
        description="在工作区里执行一段 Python 脚本（一次性子进程，输出截断）。",
        status="ready",
        parameters={"code": "Python 源码"},
        json_schema={
            "type": "object",
            "properties": {"code": {"type": "string", "description": "Python 源码"}},
            "required": ["code"],
        },
    ),
    ToolSpec(
        name="download_url",
        category="file",
        description="把 URL 的内容下载保存到工作区文件。",
        status="ready",
        parameters={"url": "下载地址", "filename": "保存路径（相对工作区）"},
        json_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "下载地址"},
                "filename": {"type": "string", "description": "保存路径"},
            },
            "required": ["url", "filename"],
        },
    ),
    # ── 报告生成：分节报告 / 导出 PDF / 页面快照 ───────────────────────
    ToolSpec(
        name="write_report",
        category="report",
        description=(
            "分节生成报告：给标题 + 章节列表（heading/content），组装成 Markdown 报告"
            "保存到工作区 .workdir/reports/，返回路径与预览。内容由 Agent 组织，绝不编造。"
        ),
        status="ready",
        parameters={
            "title": "报告标题",
            "filename": "保存文件名（可选，缺省用标题）",
            "sections": "章节列表 [{'heading': '章节名', 'content': '章节内容'}, ...]",
        },
        json_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "filename": {"type": "string"},
                "sections": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "heading": {"type": "string"},
                            "content": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["title", "sections"],
        },
    ),
    ToolSpec(
        name="export_pdf",
        category="report",
        description=(
            "导出 PDF：把工作区已有的文本/Markdown 报告（path）或直接给的内容（content）"
            "渲染成 PDF（中文字体自动使用系统中文字体），保存到 .workdir/reports/。"
        ),
        status="ready",
        parameters={
            "path": "已有报告文件路径（相对工作区或 .workdir/reports/）",
            "content": "或直接给文本内容",
            "filename": "PDF 文件名（可选）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
                "filename": {"type": "string"},
            },
        },
    ),
    ToolSpec(
        name="page_snapshot",
        category="report",
        description=(
            "页面快照：抓取网页 HTML 保存到工作区 .workdir/reports/，并返回纯文本预览。"
            "用于把外部网页资料留档供后续引用。"
        ),
        status="ready",
        parameters={
            "url": "要抓取的网页地址",
            "filename": "保存文件名（可选）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "filename": {"type": "string"},
            },
            "required": ["url"],
        },
    ),
    # ── Phase 1：受控代码执行 ────────────────────────────────────────
    ToolSpec(
        name="execute_code",
        category="code",
        description=(
            "在受控子进程中执行 Python 代码（状态跨调用持久，如 Jupyter kernel）。"
            "可导入任意包（缺失依赖自动 pip 安装）、读写工作区文件、调用 coverage/buffer 等内置工具，"
            "以及两个出图函数：emit_chart(title, chart_type='bar'|'pie'|'line'|'scatter', x_labels=[...], "
            "series=[{'name':..., 'data':[...]}], y_label=..., height=...) 生成**交互式** ECharts 图表（前端"
            "可悬浮/缩放/动画，一张卡片一个图，常规柱/饼/折线/散点优先用 emit_chart；"
            "emit_chart 仅当对比类别/分区/取值点 ≥2 时使用（少于 2 个可比项用指标卡即可）；"
            "可比类别合并成一张多系列图表，不要拆成多个近似单图；勿输出近似冗余图）；"
            "save_plot(caption=..., filename=...) 保存 matplotlib 图为静态图片（多子图会自动拆成多个 widget，"
            "复杂/自定义图形才用它）。每个图表调用一次，不要在一个图里塞多个子图。"
            "执行需用户审批；请只发送纯 Python 源码，不要带 ``` 围栏或思考过程注释。"
            "**数据路径约定**：平台图层（只读底座）用 gpd.read_file('../platform_data/geojson/图层id.json')"
            "直接读全量几何（如 poi_餐饮 → '../platform_data/geojson/poi_餐饮.json'）；工作区 data/layers/"
            "只有本会话生成/上传的图层。具体路径可用 get_dataset_schema 的 data_path 字段获取。"
            "平台数据只读：禁止对它做任何写操作（open 'w'、覆盖、改名、删除），分析产出写入工作区。"
        ),
        display_description=(
            "在受控环境中执行 Python 代码：可读取平台图层与工作区文件、调用内置分析工具、"
            "生成交互图表与静态图片。"
        ),
        status="ready",
        parameters={"code": "要执行的完整 Python 源码（字符串）"},
        json_schema={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "要执行的完整 Python 源码。缺依赖自动安装；print 输出会流式回传。",
                }
            },
            "required": ["code"],
        },
    ),
    ToolSpec(
        name="spatial_clip",
        category="processing",
        description=(
            "把目标图层裁剪到指定范围图层（clip_layer_id，取其全部要素的几何并集作为裁剪边界），"
            "生成裁剪后的结果图层（source=generated）并上地图。"
            "常用于把 POI/路网裁到研究片区或行政边界范围。output_name 可选。"
        ),
        display_description="把目标图层裁剪到研究范围（如行政边界/片区范围），生成裁剪后的新图层。",
        status="ready",
        parameters={
            "target_layer_id": "被裁剪的图层 id",
            "clip_layer_id": "裁剪边界图层 id（如行政边界/街区范围），取该图层要素的并集作为裁剪范围",
            "output_name": "可选：结果图层显示名（默认「源图层名裁剪」）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "target_layer_id": {"type": "string", "description": "被裁剪的图层 id"},
                "clip_layer_id": {"type": "string", "description": "裁剪边界图层 id，取该图层要素的几何并集作为裁剪范围"},
                "output_name": {"type": "string", "description": "可选：结果图层显示名"},
            },
            "required": ["target_layer_id", "clip_layer_id"],
        },
    ),
]

# Skill 工具 10 件套（M1）：可复用分析能力的增删改查运行 + 脚本货架。
# 定义在 skill_specs.py，避免这一大段大字面量继续膨胀。
from app.tools.skill_specs import SKILL_TOOL_SPECS  # noqa: E402

TOOLS += SKILL_TOOL_SPECS
