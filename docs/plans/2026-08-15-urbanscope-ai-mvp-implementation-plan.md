# UrbanScope AI MVP Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a competition-ready MVP that demonstrates an AI Agent turning urban planning questions into data readiness checks, GIS tool execution, map layers, and traceable result widgets.

**Architecture:** The MVP uses a React/Vite web app with a fixed planning workspace layout, a FastAPI backend for Agent orchestration and Tool Runtime, and local GIS tools backed by GeoPandas/Shapely/DuckDB Spatial. Planner, Executor, Memory, and Tool Registry are implemented as explicit modules so Planning Skills and Open Analysis can share the same execution path.

**Tech Stack:** React, Vite, TypeScript, MapLibre GL JS, deck.gl, Zustand, FastAPI, Pydantic, GeoPandas, Shapely, PyProj, DuckDB Spatial.

---

## Phase 0: Scope Reset

### Task 1: Remove Prototype Assumptions That Conflict With PRD

**Files:**
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/App.css`
- Reference: `docs/UrbanScope-AI-PRD.md`

**Steps:**

1. Remove Agent Cards from the current prototype.
2. Remove the bottom Context Panel.
3. Restructure the layout into:

```text
left nav
+ left panel
+ map workspace
+ right results sidebar
```

4. Verify the left nav still contains 项目 / Agent / 数据 / 分析 / 成果.
5. Run:

```powershell
cd apps/web
npm run build
```

Expected: build passes.

## Phase 1: Frontend Information Architecture

### Task 2: Implement App Shell

**Files:**
- Create: `apps/web/src/components/AppShell.tsx`
- Create: `apps/web/src/components/LeftNav.tsx`
- Create: `apps/web/src/components/TopBar.tsx`
- Create: `apps/web/src/components/ResultsSidebar.tsx`
- Modify: `apps/web/src/App.tsx`

**Steps:**

1. Create `AppShell` with CSS grid:

```text
72px left nav
360px left panel
1fr map
360px right sidebar
```

2. Create `LeftNav` with five fixed entries.
3. Create `TopBar` with project title and primary execution controls.
4. Create `ResultsSidebar` empty state.
5. Wire active nav state in `App.tsx`.
6. Run build.

Expected: app renders stable shell without bottom panel.

### Task 3: Implement Agent Chat Panel

**Files:**
- Create: `apps/web/src/features/agent/AgentPanel.tsx`
- Create: `apps/web/src/features/agent/ChatMessage.tsx`
- Create: `apps/web/src/features/agent/TraceDisclosure.tsx`
- Create: `apps/web/src/features/agent/agentTypes.ts`

**Steps:**

1. Implement user messages as right-aligned bubbles.
2. Implement Agent messages as left-aligned bubbles.
3. Add collapsible sections:

```text
数据检查
分析计划
Tool Trace
结果依据
```

4. Add static sample conversation:

```text
用户：帮我对这个片区做一次街区城市体检。
Agent：我会先检查 4 个指标的数据是否齐全。
```

5. Run build.

Expected: Agent page matches Codex-style chat behavior, without Agent Cards.

### Task 4: Implement Data Layer Panel

**Files:**
- Create: `apps/web/src/features/layers/LayerPanel.tsx`
- Create: `apps/web/src/features/layers/LayerRow.tsx`
- Create: `apps/web/src/features/layers/LayerMenu.tsx`
- Create: `apps/web/src/features/layers/layerTypes.ts`

**Steps:**

1. Implement Chinese layer list inspired by CARTO Builder.
2. Include layer actions:

```text
缩放至图层
仅显示此图层
图层样式
复制
重命名
创建分组
预览数据
删除
```

3. Treat platform, uploaded, and generated layers as one list.
4. Mark platform data with subtle metadata, not a separate top-level category.
5. Run build.

Expected: 数据 nav shows layer management.

### Task 5: Implement Analysis Launcher

**Files:**
- Create: `apps/web/src/features/analysis/AnalysisPanel.tsx`
- Create: `apps/web/src/features/analysis/SkillList.tsx`
- Create: `apps/web/src/features/analysis/PatternList.tsx`

**Steps:**

1. Show Planning Skills:

```text
街区城市体检
公共服务设施评估
15 分钟生活圈
```

2. Show Analysis Patterns:

```text
空间叠加判断
覆盖率分析
最近距离分析
密度分析
供需匹配分析
```

3. Clicking a Skill creates a draft Agent message instead of running a separate page.
4. Run build.

Expected: 分析 is a Skill/Pattern launcher, not a GIS toolbox page.

## Phase 2: Backend Agent Runtime

### Task 6: Define Core Models

**Files:**
- Create: `apps/api/app/models/layer.py`
- Create: `apps/api/app/models/agent.py`
- Create: `apps/api/app/models/tool.py`
- Create: `apps/api/app/models/result.py`

**Steps:**

1. Define `LayerMetadata`.
2. Define `AgentMessage`, `PlanStep`, `TraceEvent`.
3. Define `ToolSpec`, `ToolCall`, `ToolResult`.
4. Define `ResultWidget`, `ResultGroup`.
5. Run:

```powershell
cd apps/api
python -c "from app.models.agent import AgentMessage; print(AgentMessage)"
```

Expected: imports succeed.

### Task 7: Implement Data Registry

**Files:**
- Create: `apps/api/app/data_registry.py`
- Create: `apps/api/app/demo_data/layers.json`
- Modify: `apps/api/app/main.py`

**Steps:**

1. Create demo layer metadata for:

```text
项目边界
居住用地
人口网格
学校设施
公园绿地
文化活动中心
产业用地
轨道站点
```

2. Add `GET /layers`.
3. Add `GET /layers/{layer_id}/schema`.
4. Verify with local requests.

Expected: frontend can load layer metadata.

### Task 8: Implement Planner

**Files:**
- Create: `apps/api/app/agent/planner.py`
- Create: `apps/api/app/agent/patterns.py`
- Modify: `apps/api/app/main.py`

**Steps:**

1. Implement rule-based MVP intent detection:

```text
城市体检 -> community_health_check
人口 + 薄弱区 -> overlay_population_with_issue_area
产业 + 轨道 -> industry_transit_accessibility
```

2. Planner returns required data and plan steps.
3. Add `POST /agent/plan`.
4. Verify with sample prompts.

Expected: planner outputs stable JSON plans.

### Task 9: Implement Readiness Checker

**Files:**
- Create: `apps/api/app/agent/readiness.py`

**Steps:**

1. Check layer existence.
2. Check geometry type.
3. Check required fields.
4. Return:

```text
ready
partial
blocked
```

5. Include suggested actions:

```text
上传数据
启用平台数据
使用示例数据
跳过该项
```

Expected: missing 乱停乱放 data returns partial readiness.

### Task 10: Implement Executor and Trace

**Files:**
- Create: `apps/api/app/agent/executor.py`
- Create: `apps/api/app/agent/memory.py`
- Modify: `apps/api/app/main.py`

**Steps:**

1. Execute plan steps sequentially.
2. Return trace events for each step.
3. Store generated result references in memory.
4. Add `POST /agent/execute`.

Expected: endpoint returns trace and result group.

## Phase 3: Map and Results

### Task 11: Replace Static Map With MapLibre

**Files:**
- Create: `apps/web/src/features/map/MapWorkspace.tsx`
- Create: `apps/web/src/features/map/mapLayers.ts`
- Modify: `apps/web/src/App.tsx`

**Steps:**

1. Add MapLibre CSS import.
2. Initialize map with a neutral basemap or blank style.
3. Render demo GeoJSON layers.
4. Add layer visibility wiring.
5. Run build.

Expected: map loads and layers can be toggled.

### Task 12: Implement Results Sidebar

**Files:**
- Modify: `apps/web/src/components/ResultsSidebar.tsx`
- Create: `apps/web/src/features/results/ResultGroup.tsx`
- Create: `apps/web/src/features/results/ResultWidget.tsx`

**Steps:**

1. Render result groups.
2. Render metric widgets and chart placeholders.
3. Add buttons:

```text
查看图层
打开表格
定位地图
```

4. Run build.

Expected: right sidebar replaces bottom Widgets panel.

## Phase 4: MVP Demo Skills

### Task 13: Implement Community Health Check Skill Definition

**Files:**
- Create: `apps/api/app/skills/community_health_check.py`
- Modify: `apps/api/app/skills/registry.py`

**Steps:**

1. Define parent Skill.
2. Define four Indicator Skills.
3. Mark MVP implementation depth:

```text
中学：真实计算
公园：真实计算
文化活动中心：半真实审查
乱停乱放：缺数据阻断
```

Expected: `GET /skills` returns full Skill metadata.

### Task 14: Implement First Two Coverage Tools

**Files:**
- Create: `apps/api/app/tools/coverage.py`
- Modify: `apps/api/app/tools/registry.py`

**Steps:**

1. Implement shared coverage tool contract.
2. For MVP, allow demo deterministic results before real GeoPandas data is ready.
3. Return output layer IDs, coverage rate, covered area, uncovered area.

Expected: city health check can produce school and park results.

### Task 15: Implement Open Analysis Demo Patterns

**Files:**
- Modify: `apps/api/app/agent/patterns.py`
- Modify: `apps/api/app/agent/planner.py`

**Steps:**

1. Add population-overlap pattern.
2. Add industry-transit-distance pattern.
3. Ensure both require readiness checks.
4. Return blocked state if data is missing.

Expected: Agent can answer high-confidence open analysis prompts.

## Phase 5: Demo Hardening

### Task 16: Connect Frontend to Backend

**Files:**
- Create: `apps/web/src/lib/api.ts`
- Modify: Agent, Layer, Analysis, Results components

**Steps:**

1. Fetch `/layers`, `/skills`, `/agent/plan`, `/agent/execute`.
2. Render readiness results in collapsible Agent sections.
3. Render trace as streaming-style events.
4. Render result widgets in right sidebar.
5. Run build.

Expected: demo flow runs end-to-end.

### Task 17: Write Demo Script

**Files:**
- Create: `docs/demo-script.md`

**Steps:**

1. Write 3-minute main story.
2. Include exact user prompts.
3. Include expected Agent response.
4. Include fallback if an endpoint fails.

Expected: presenter can run the demo reliably.

### Task 18: Final Verification

**Files:**
- No code changes unless bugs are found.

**Steps:**

1. Run frontend build.
2. Run backend import checks.
3. Start frontend and backend.
4. Manually execute:

```text
城市体检主线
人口与薄弱区追问
产业与轨道追问
```

Expected: all three demo paths complete or produce correct data-missing prompts.
