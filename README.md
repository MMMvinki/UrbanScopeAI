# UrbanScope AI · 城市数据分析平台与智能体

> 面向城市规划前期研究的 AI 空间分析工作台（MVP）—— 把规划师用自然语言提出的问题，转化为**可执行、可追溯、可验证**的空间数据分析流程与成果。

## 为什么做

规划师在项目前期研究阶段，经常被「找数据、洗字段、坐标系转换、叠加分析、制图、整理结论」淹没；固定的一键分析又覆盖不了真实项目里数据缺失、字段不统一、临时追问、跨专题分析的问题。

UrbanScope AI 不定位为城市体检平台，也不定位为通用 GIS 软件，而是解决四个核心痛点：

- **数据不确定** → 分析前自动检查图层 / 字段 / 坐标系；缺数据时询问「上传 / 启用平台数据 / 换问法 / 跳过」
- **流程重复** → 覆盖率、叠加、距离、统计等常用分析沉淀为工具与技能（Skills），可复用、可追问
- **固定流程不够灵活** → Agent 自由编排，支持开放问题与连续追问
- **AI 结果不可信** → 每个结论都落到真实图层 / 工具 Trace / RunArchive，可验证、可回放

## 演示闭环

```text
规划问题 → Agent Plan（LLM） → 真流式 SSE 执行 → 地图工作区 → 图层 / 成果 → 继续追问
```

## 核心特性

**Agent 编排**
- Planner + Executor + Memory + Tool Registry 四件套；LiteLLM 多 Provider 接入（DeepSeek 等，`LLM_*` 或 `DEEPSEEK_*` 环境变量）
- 真流式：`/agent/execute` 走 SSE，thought / 代码块逐字增量到达，前端原样渲染；`/runs/{run_id}` 完整回放

**执行与安全**
- `execute_code` 子进程隔离执行（workspace 边界校验 + 代码校验 + 自动装依赖）
- 写操作默认权限审批：SSE 发出 `permission_request`，前端 ApprovalModal 审批，300s 超时自动拒绝
- 每次 run 归档 RunArchive（meta / events / tool_calls / llm_usage），轮次用尽强制收尾、不空转

**地图与图层**
- deck.gl 工作台：平台数据（只读底座）/ 上传 / 分析生成三类图层统一管理
- 样式侧车持久化：颜色、透明度、描边、分档设色（graduated）跨重启保留；平台数据「只读、呈现可改」
- 大图层 lazy 单取，不撑崩前端；DEM 高程瓦片金字塔渲染（z10–14）

**工具与技能**
- 7 类工具货架：本地 33 个 + GIS-MCP 桥接（88 个）空间分析能力
- 核密度等确定性分析工具：真 KDE → GeoJSON 网格 + 分层设色，**全部真计算、绝不编造数值**
- 技能体系（Anthropic Skill 格式）：流程引导 / 带脚本两类；可内置、可自建、可追问缺参

**项目厨房**
- 每个项目是一间独立厨房：数据文件 + 地图图层 + 运行历史 + Agent 记忆互不干扰，切项目即换整套上下文

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React + Vite + TypeScript + deck.gl + ECharts |
| 后端 | FastAPI + uvicorn + GeoPandas / Shapely（确定性几何计算） |
| Agent | LiteLLM 多 Provider（DeepSeek 等），SSE 流式 |
| 数据 | GeoJSON / Shapefile / GeoTIFF（DEM） |

## 快速开始

**0. 准备数据（重要）** —— 见下方「数据说明」。

**1. 后端（端口 8011）**

```powershell
cd apps/api
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8011
```

首次运行需创建虚拟环境并安装依赖，然后在 `apps/api/.env` 配置模型：

```ini
LLM_PROVIDER=<provider>
LLM_MODEL=<model>
LLM_API_KEY=<key>
LLM_BASE_URL=<url>
```

（或兼容的 `DEEPSEEK_*` 三件套）

**2. 前端（端口 5175）**

```powershell
cd apps/web
npm install
npm run dev     # http://localhost:5175
```

后端地址通过 `VITE_API_URL` 注入：

```powershell
$env:VITE_API_URL="http://127.0.0.1:8011"; npm run dev
```

也可以直接双击仓库根目录的 **`启动平台.bat`**，一键启动前后端并打印访问地址。

## 数据说明

平台图层数据与原始数据源体积超 GitHub 限制，**不入库**。运行演示前必须放置数据：

| 数据 | 位置 | 体积 | 缺失影响 |
|---|---|---|---|
| 运行时平台图层 | `apps/api/app/platform_data/` | 约 660MB | 后端启动失败（缺 `layers.json`） |
| 原始数据源（shp/tif） | `平台数据/` | 约 5GB | 仅影响重新生成图层 / DEM |

向数据提供方索取 `platform_data` 压缩包，解压到 `apps/api/app/platform_data/` 即可加载深圳 POI / 行政区划 / 详规一张图 / 路网 / DEM 图层；如需自行用原始 shp 重新生成，再索取 `平台数据` 原始包，运行 `apps/api/app/scripts/build_platform_layers.py` 与 `build_dem_tiles.py`。

## 目录结构

```text
apps/
  api/          # FastAPI 后端：agent / tools / skills / execution / providers / gis
  web/          # React + Vite 前端：地图工作台、图层面板、Agent 面板、成果栏
docs/           # PRD、产品设计文档
启动平台.bat     # 一键启动脚本（后端 8011 + 前端 5175）
```

## 验证

```powershell
cd apps/api && PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verify_llm.py
```

三条演示路径连跑（城市体检 / 人口薄弱 / 产业轨道），断言真流式与真实 LLM 路径。

## 许可

MIT License。部分模块在实现上参考/移植自开源项目，版权归原项目所有，详见 [NOTICE.md](NOTICE.md)。
