# NOTICE

本项目（UrbanScope AI 城市数据分析平台与智能体）为独立实现：运行时**不依赖**任何开源项目的服务，仅在实现与集成层面参考/移植了若干开源项目代码，并在运行时依赖多个第三方开源库（均为各自许可证下的标准组件）。

按开源许可证要求，在此统一保留必要的版权信息；完整许可文本见各开源项目的官方仓库。

- **OpenGIS**（MIT License）—— 部分模块在实现上参考，并按本仓库架构做了改写。Copyright (c) OpenGIS Contributors

## 运行时依赖的开源库（部分）

| 开源库 | 用途 | License |
| --- | --- | --- |
| gis-mcp | 空间分析工具服务器（MCP 桥接） | MIT |
| deck.gl / MapLibre GL | 地图渲染 | MIT / BSD |
| ECharts | 成果图表 | Apache-2.0 |
| GeoPandas / Shapely / PyProj | 空间几何计算 | BSD |
| LiteLLM | 多 Provider LLM 客户端 | MIT |
| FastAPI / Pydantic / Uvicorn | Web 服务框架 | MIT |

> 表中各库的版权与许可以各官方仓库为准。
