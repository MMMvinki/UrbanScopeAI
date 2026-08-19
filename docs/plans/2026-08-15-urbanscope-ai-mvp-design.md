# UrbanScope AI MVP Design

日期：2026-08-15

## 1. 产品收敛

UrbanScope AI 第一阶段不做完整城市体检平台，也不做通用 GIS SaaS，而是做一个面向规划前期研究的 AI 空间分析工作台。

核心 Demo 目标：

> 让评委清楚看到：用户提出规划问题后，Agent 会检查数据、制定计划、调用确定性的空间分析工具、生成地图图层与结果卡片，并支持基于结果继续追问。

MVP 定义：

- 1 个 Agent Workspace
- 1 张可交互地图
- 3 个 Planning Skills
- 10 个核心 Data/GIS Tools
- 5 类基础数据
- 图层管理、属性表、分析过程、结果 Widgets

第一版要突出“Agent 正在做空间分析”，避免落入“地图展示 + AI 写报告”的观感。

## 2. 推荐技术架构

### 前端

推荐：

- React + Vite 或 Next.js
- MapLibre GL JS
- Zustand 或 Redux Toolkit 管理前端工作台状态
- TanStack Table 用于属性表
- ECharts 用于轻量图表

Demo 阶段优先 React + Vite。原因是启动快、工程简单、适合高完成度单页工作台。除非需要服务端渲染、登录、多页面路由，否则 Next.js 的收益不明显。

### 后端

推荐：

- FastAPI
- Pydantic
- GeoPandas
- Shapely
- PyProj
- NetworkX
- DuckDB
- PostGIS

Demo 阶段建议分两层：

- FastAPI 作为统一 API、Agent 执行入口和任务状态服务。
- 空间分析先用 GeoPandas / Shapely / DuckDB 跑通，PostGIS 作为可选增强。

如果比赛时间有限，PostGIS 不应成为阻塞项。能用文件型 GeoJSON / GeoPackage + DuckDB 跑通 Demo，比过早建设完整空间数据库更稳。

### 地图与数据格式

推荐统一使用 GeoJSON 作为前后端交换格式：

- 前端地图直接消费 GeoJSON。
- 后端工具输出 GeoJSON 图层、表格统计、Widget JSON。
- 用户上传 Shapefile 后由后端转换为 GeoJSON / GeoPackage。

Demo 数据建议内置：

- 研究范围
- 人口网格或居住点
- POI / 公共设施
- 道路
- 公交 / 地铁
- 街区边界

## 3. Agent 架构

MVP 推荐采用轻量 Planner + Executor，而不是一开始上复杂多 Agent。

```text
User Intent
  -> Intent Router
  -> Skill Selector
  -> Planner
  -> Data Readiness Checker
  -> Tool Executor
  -> Result Composer
  -> Workspace State
```

### 核心组件

Intent Router：

- 判断用户是在启动 Planning Skill、调用单个 Tool、查询数据，还是继续追问。

Skill Selector：

- 在“街区城市体检 / 15 分钟生活圈 / 公共服务设施评估”中选择最合适的 Skill。

Planner：

- 根据 Skill Schema 生成可展示的执行计划。
- 不需要让 LLM 发明所有步骤，优先从 Skill 模板生成。

Data Readiness Checker：

- 检查当前项目是否具备 required_data。
- 给出“可执行 / 部分可执行 / 不可执行”的状态。

Tool Executor：

- 顺序执行工具，记录每一步的输入、输出、耗时、状态。

Result Composer：

- 把工具结果组织成地图图层、属性表、KPI、图表和结论。

Workspace State：

- 保存项目数据、图层、分析历史、Agent 消息、工具调用日志、结果卡片。

### 是否需要 Reflection

MVP 不建议做开放式 Reflection。可以做规则化校验：

- 输出图层是否为空
- CRS 是否一致
- 统计数量是否为 0
- 覆盖率是否在 0-100%
- 结果是否缺少关键字段

这种“结果验证”比抽象 Reflection 更可控，也更符合规划软件的可信要求。

## 4. Skill Schema

建议第一版 Skill 使用 YAML 或 JSON 定义，后端加载为 Pydantic Model。

```yaml
id: community_health_check
name: 街区维度城市体检
description: 评估街区公共服务、绿地、学校覆盖等现状问题
intent_examples:
  - 帮我对这个片区做一次街区城市体检
  - 看看这个街区总体有什么问题

required_data:
  - key: boundary
    label: 研究范围
    geometry: polygon
  - key: population
    label: 人口数据
    geometry: point_or_polygon
  - key: schools
    label: 学校设施
    geometry: point
  - key: parks
    label: 公园绿地
    geometry: point_or_polygon

optional_data:
  - key: parking_disorder
    label: 乱停乱放数据

parameters:
  school_service_radius_m: 800
  park_service_radius_m: 500
  walk_speed_m_per_min: 80

workflow:
  - id: check_data
    label: 检查分析数据
    tool: check_data_readiness
  - id: clip_layers
    label: 裁剪到研究范围
    tool: spatial_clip
  - id: school_coverage
    label: 计算学校服务覆盖
    tool: coverage
  - id: park_coverage
    label: 计算公园绿地覆盖
    tool: coverage
  - id: diagnose
    label: 识别空间问题
    tool: diagnose_indicators

outputs:
  layers:
    - service_coverage
    - underserved_area
  widgets:
    - coverage_kpi
    - issue_summary
  narrative:
    - conclusion
    - evidence
```

三个 MVP Skills 的边界：

| Skill | 核心问题 | Demo 展示重点 |
| --- | --- | --- |
| 街区维度城市体检 | 这个街区总体有什么问题？ | 数据缺失判断、指标计算、综合诊断 |
| 15 分钟生活圈 | 居民能否方便到达设施？ | 等时圈、人口覆盖、服务盲区 |
| 公共服务设施评估 | 设施供给是否匹配人口需求？ | 供需匹配、空间错配、薄弱区识别 |

## 5. Tool Schema

后端工具统一为确定性函数，LLM 只负责选择和组织，不直接计算 GIS。

```python
class ToolSpec(BaseModel):
    name: str
    description: str
    category: Literal["inspection", "processing", "analysis", "editing"]
    input_schema: dict
    output_schema: dict
    side_effects: bool = False
    execute: Callable
```

每次工具调用都要记录：

- tool_name
- input_summary
- output_summary
- output_layer_ids
- status
- started_at
- finished_at
- evidence_refs

### MVP 10 个 Tools

优先真实实现：

1. list_datasets
2. get_dataset_schema
3. preview_table
4. check_crs
5. spatial_clip
6. buffer
7. spatial_join
8. coverage
9. nearest_distance
10. statistics

可 Mock 或半 Mock：

- network_isochrone：Demo 可先用道路网络简化计算，或使用预生成等时圈。
- correlation：可先对表格字段做基础相关性。
- hotspot：不进入第一版。

## 6. 前端信息架构

整体布局采用：

```text
Top Bar
Left Nav
Agent Workspace
Map Workspace
Context Panel
```

左侧导航只保留：

- 项目
- Agent
- 数据
- 分析
- 成果

### Agent Workspace

包含：

- 用户输入
- Agent Plan
- Tool Calling Timeline
- Agent Cards
- 继续执行 / 跳过缺失数据 / 上传数据等动作

关键原则：不要只显示“正在思考”，而要显示：

```text
✓ get_dataset_schema(schools)
✓ check_crs(boundary)
✓ spatial_clip(schools, boundary)
○ coverage(schools, population)
```

### Map Workspace

包含：

- MapLibre 地图
- 图层开关
- 结果图层高亮
- 点击 Feature 查看属性
- Agent 正在生成的图层状态

### Context Panel

底部或右侧可渐进展开：

- 图层列表
- 属性表
- 数据预览
- 结果 Widgets
- 分析历史

不要一开始把所有面板全部展开。默认展示 Agent + Map，其他内容按任务打开。

## 7. Demo 真实现与 Mock 边界

### 必须真实实现

- 项目 Workspace 的基本状态
- GeoJSON 读取与图层展示
- 图层显示 / 隐藏
- 属性表
- Agent Plan 展示
- Tool Calling 状态流
- 至少 6 个确定性 Tools
- 至少 1 个完整 Skill 闭环
- 结果图层生成
- KPI / Widget 生成
- 基于前一步结果的继续追问

### 可以半 Mock

- Shapefile 上传：可以先提供内置示例，后端保留接口。
- network_isochrone：使用预计算服务区或简化网络。
- 城市体检第 4 个缺失指标：故意展示“缺失数据 -> 是否跳过”的 Agent 能力。
- 报告导出：只展示结果卡片，不做完整报告系统。

### 不建议第一版做

- 团队协作
- 权限系统
- 完整报告生成
- 多 Agent
- Skill Marketplace
- 复杂样式编辑器
- 云端任务调度
- 完整自动联网数据采集

## 8. 推荐 Demo 剧本

1. 创建项目：XX 街区现状研究。
2. 加载研究范围与用地数据。
3. 系统内置人口、POI、道路、公园、学校。
4. 用户输入：帮我对这个片区做一次街区城市体检。
5. Agent 检查数据：
   - 研究范围：已具备
   - 人口：已具备
   - 学校：已具备
   - 公园：已具备
   - 乱停乱放：缺失
6. Agent 提示：当前可完成 3 项指标，是否先执行？
7. 用户确认。
8. Agent 执行工具链：
   - spatial_clip
   - buffer
   - coverage
   - statistics
   - diagnose_indicators
9. 地图生成服务覆盖区和问题区。
10. 生成 Widgets：
    - 小学服务覆盖率
    - 公园覆盖率
    - 公共服务薄弱区
11. 用户追问：公共服务薄弱区是不是人口也更集中？
12. Agent 调用人口图层与薄弱区 overlay，输出人口统计与结论。

这个剧本的 Wow Moment 在第 11-12 步：它证明系统不是固定指标计算，而是可以基于已有空间结果继续动态分析。

## 9. 实施顺序

第一阶段：

- 建立前端工作台骨架
- 接入 MapLibre
- 加载内置 GeoJSON 示例数据
- 实现图层管理与属性表

第二阶段：

- 建立 FastAPI Tool Runtime
- 实现 list_datasets / schema / clip / buffer / coverage / statistics
- 前端展示 Tool Calling Timeline

第三阶段：

- 编码第一个 Skill：街区维度城市体检
- 实现数据缺失判断
- 输出结果图层与 Widgets

第四阶段：

- 增加继续追问
- 补齐公共服务评估与 15 分钟生活圈的 Demo 版本
- 打磨 3 分钟演示剧本

## 10. 当前建议

最优先不是选择 LangGraph 还是 PydanticAI，而是先把“可验证的空间分析闭环”跑通：

```text
用户问题 -> Skill Plan -> 数据检查 -> Tool 调用 -> 地图变化 -> Widget -> 可追问
```

Agent 架构可以先保持轻量；Tool 与 Skill 的边界要尽早规范。只要这个闭环成立，后续无论接入更复杂的 Agent Framework、PostGIS、异步任务队列还是报告生成，都有清晰的扩展位置。
