<p align="center">
  <h1 align="center">🏙️ UrbanScope AI</h1>
  <p align="center"><b>城市数据分析平台与智能体</b> · AI 空间分析工作台</p>
  <p align="center">
    <img src="https://img.shields.io/badge/React-19-61DAFB?style=flat-square&logo=react&logoColor=white" alt="React 19" />
    <img src="https://img.shields.io/badge/TypeScript-6-3178C6?style=flat-square&logo=typescript&logoColor=white" alt="TypeScript" />
    <img src="https://img.shields.io/badge/Vite-8-646CFF?style=flat-square&logo=vite&logoColor=white" alt="Vite" />
    <img src="https://img.shields.io/badge/deck.gl-9-0AB8AB?style=flat-square" alt="deck.gl 9" />
    <img src="https://img.shields.io/badge/FastAPI-0.115-009688?style=flat-square&logo=fastapi&logoColor=white" alt="FastAPI" />
    <img src="https://img.shields.io/badge/Python-3.12-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python 3.12" />
    <img src="https://img.shields.io/badge/GeoPandas-Shapely-4B8BBE?style=flat-square" alt="GeoPandas / Shapely" />
    <img src="https://img.shields.io/badge/License-MIT-green?style=flat-square" alt="MIT License" />
  </p>
  <p align="center">
    把规划师用自然语言提出的问题，转化为<b>可执行、可追溯、可验证</b>的空间数据分析流程与成果。
  </p>
</p>

---

## 为什么做

城市规划师在项目前期研究阶段，经常被「找数据、洗字段、坐标系转换、叠加分析、制图、整理结论」淹没；固定的一键分析又覆盖不了真实项目里数据缺失、字段不统一、临时追问、跨专题分析的问题。

UrbanScope AI **不是**城市体检平台，也**不是**通用 GIS 软件，而是解决四个核心痛点：

| 痛点 | 平台的解法 |
|---|---|
| **数据不确定**：图层来源/字段/坐标系/质量不统一 | 分析前自动检查数据就绪性；缺数据时询问「上传 / 启用平台数据 / 换问法 / 跳过」 |
| **流程重复**：覆盖率、叠加、距离、统计反复出现 | 常用分析沉淀为工具与技能（Skills），可复用、可组合、可追问 |
| **固定流程不灵活**：一键分析无法覆盖开放问题 | Agent 自由编排工具链，支持开放问题与连续追问 |
| **AI 结果不可信**：只输出文字无法验证 | 每个结论都落到真实图层 / 工具 Trace / RunArchive，可验证、可回放 |

## 界面一览

```text
┌───────────────────────────────────────────────────────────────────┐
│  项目切换器      「福田街道现状研究」                       搜索  ⚙  │
├──────┬─────────────────────────────────────────────┬──────────────┤
│      │                                             │  Agent 对话   │
│ 📂   │                                             │  ┌────────┐   │
│ 图层 │             中央地图工作台 (deck.gl)          │  │ 计划卡  │   │
│ 工具 │                                             │  │ 执行流  │   │
│ 技能 │                                             │  │ 审批弹窗 │   │
│ 成果 │                                             │  └────────┘   │
│ 数据 │                                             │  输入框…      │
└──────┴─────────────────────────────────────────────┴──────────────┘
  左侧导航       图层 / 工具 / 技能 / 成果 / 项目面板
```

## 你可以直接问

| 你说 | 平台做什么 |
|---|---|
| 「对福田街道做城市体检」 | 检查数据 → 生成计划 → 调统计/GIS 工具 → 地图分层设色 + 成果图表 |
| 「哪些区域 15 分钟生活圈覆盖不足？」 | 路网 + POI → 服务盲区分析 → 盲区图层上地图 |
| 「把餐饮 POI 做成核密度图层」 | 真 KDE 核密度估计 → GeoJSON 网格 + 分层设色图层 |
| 「提取医疗类 POI 做成新图层」 | 属性过滤 → 生成新图层并上地图 |
| 「哪里人口薄弱但设施也少？」 | 人口网格 × 设施覆盖 → 薄弱区识别 → 图表结论 |

> 演示口径：**确定性 GeoJSON、全部真计算（GeoPandas/Shapely）、绝不编造数值**。

## 核心能力

**🧠 Agent 编排**
- Planner + Executor + Memory + Tool Registry 四件套；LiteLLM 多 Provider 接入（DeepSeek 等，`LLM_*` / `DEEPSEEK_*` 配置）
- 真流式：`/agent/execute` 走 SSE，thought / 代码块逐字增量到达，前端原样渲染；`/runs/{run_id}` 完整回放
- 轮次用尽强制收尾输出结论，保证不空转

**🔒 执行与安全**
- `execute_code` 子进程隔离执行：workspace 边界校验 + 代码校验 + 自动装依赖
- 写操作默认权限审批：SSE 发出 `permission_request`，前端 ApprovalModal 审批，300s 超时自动拒绝
- 每次 run 归档 RunArchive（meta / events / tool_calls / llm_usage）

**🗺️ 地图与图层**
- deck.gl + MapLibre 工作台：平台数据（只读底座）/ 上传 / 分析生成三类图层统一管理
- 样式侧车持久化：颜色、透明度、描边、分档设色（graduated）跨重启保留；平台数据「只读、呈现可改」
- 大图层 lazy 按需单取，不撑崩前端；DEM 高程瓦片金字塔渲染（z10–14）

**🧰 工具与技能**
- 7 类工具货架：地图 / 分析 / 数据 / 文件 / 报告 / 技能 / GIS —— 本地 33 个 + GIS-MCP 桥接 88 个
- 核密度、覆盖分析、要素查询、样式等确定性分析工具，全部基于真实几何计算
- 技能体系（Anthropic Skill 格式）：流程引导 / 带脚本两类；内置（城市体检 / 15 分钟生活圈 / 公共服务评估 / 核密度 / 聚类）+ 可自建

**🏗️ 项目厨房**
- 每个项目是一间独立厨房：数据文件 + 地图图层 + 运行历史 + Agent 记忆互不干扰，切项目即换整套上下文

## 架构

```text
┌──────────────────────────────────────────────────────────────┐
│ Web 前端  React 19 · Vite · TypeScript · deck.gl · ECharts    │
│   左侧导航 │ 图层/工具/技能面板 │ Agent 对话 │ 中央地图 │ 成果栏 │
└───────────────┬─────────────────────────────┬────────────────┘
                │ SSE 真流式                   │ REST / GeoJSON
┌───────────────▼─────────────────────────────▼────────────────┐
│ FastAPI 后端                                                   │
│  Agent   Planner → Executor → Memory → Tool Registry           │
│  Tools   地图/分析/数据/文件/报告/技能/GIS（MCP 桥接）          │
│  Exec    子进程隔离 · 权限审批 · RunArchive                     │
│  GIS     GeoPandas · Shapely · PyProj · DuckDB · SciPy(KDE)    │
└───────────────┬──────────────────────────────────────────────┘
                │ 只读底座（样式可改）
┌───────────────▼──────────────────────────────────────────────┐
│ 数据层                                                         │
│  平台数据  深圳 POI · 行政区划 · 详规一张图 · 路网 · DEM(GeoJSON)│
│  用户数据  上传 / 分析生成图层 · workspace 运行档案              │
└──────────────────────────────────────────────────────────────┘
```

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | React 19 · TypeScript · Vite 8 · deck.gl 9 + MapLibre · ECharts 6 · TanStack Table · Zustand |
| 后端 | FastAPI · uvicorn · Pydantic · Starlette |
| 空间计算 | GeoPandas · Shapely · PyProj · DuckDB · SciPy（核密度） |
| Agent | LiteLLM（多 Provider + 流式）· MCP + gis-mcp |
| 数据 | GeoJSON · Shapefile · GeoTIFF（DEM） |

## 快速开始

**0. 准备数据（重要）** —— 见下方「数据说明」。

**1. 后端（端口 8011）**

```powershell
cd apps/api
# 首次：Python 3.12 创建虚拟环境并安装依赖
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
# 配置 apps/api/.env：
#   LLM_PROVIDER / LLM_MODEL / LLM_API_KEY / LLM_BASE_URL（或 DEEPSEEK_* 三件套）
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8011
```

**2. 前端（端口 5175）**

```powershell
cd apps/web
npm install
npm run dev        # http://localhost:5175
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
