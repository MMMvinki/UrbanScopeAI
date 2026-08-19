# NOTICE

本仓库是独立实现的「城市数据分析平台与智能体」（UrbanScope），运行时**不依赖**下述开源项目的任何组件。

以下模块在实现上参考/移植自开源项目 **OpenGIS**（MIT License），并按本仓库架构做了裁剪与改写。
按 MIT License 的要求，在此统一保留 OpenGIS 的版权与许可信息。

## OpenGIS — MIT License

> Author: OpenGIS Contributors
> OpenGIS 是 Agent 驱动的开源 GIS 桌面应用，基于 MIT License 发布。

| 本仓库文件 | 参考来源（OpenGIS 内路径） |
| --- | --- |
| `apps/api/app/execution/subprocess_runner.py` | `agent/execution/subprocess_runner.py` |
| `apps/api/app/execution/subprocess_executor.py` | `agent/execution/executor.py` |
| `apps/api/app/execution/code_validation.py` | `agent/execution/code_validation.py` |
| `apps/api/app/execution/auto_install.py` | `agent/execution/auto_install.py` |
| `apps/api/app/agent/llm_client.py` | `agent/llm.py`（chunk 归一化 / usage / 流式装配） |
| `apps/api/app/permissions/__init__.py` | `agent/governance/permission.py` |
| `apps/api/app/permissions/store.py` | `agent/governance/permission_store.py` |
| `apps/api/app/archive.py` | `runs/archive.py` |
| `apps/api/app/providers/osm/overpass.py` | `integrations/osm/overpass.py` |
| `apps/api/app/providers/osm/osm_call.py` | `integrations/osm/tools.py` |
| `apps/api/app/providers/datasource/datasource_call.py` | `integrations/datasource/tools.py` |
| `apps/api/app/tools/web.py` | `tools/builtin/web_tools.py` |
| `apps/api/app/tools/csv_geojson.py` | `tools/builtin/csv_to_geojson.py` |
| `apps/api/app/tools/context.py` | `tools/context.py` |
| `apps/api/app/tools/layer_style.py` | `tools/builtin/display.py`（色带 / 样式语义） |
