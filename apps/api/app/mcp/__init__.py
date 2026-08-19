"""MCP 客户端桥：让 Agent 能调用外部 MCP 服务器（gis-mcp）的空间分析工具。

- 后台线程 + 独立 asyncio 事件循环，通过 STDIO 传输连接 gis-mcp（FastMCP）；
- 启动时拉取工具清单与 JSON Schema，合并进 Executor 的可用工具；
- call_tool 同步包装（run_coroutine_threadsafe），与现有同步工具循环兼容；
- 任何失败不阻塞主流程：Agent 继续用本地确定性工具兜底。
"""

from app.mcp.bridge import GisMcpBridge, get_gis_mcp_bridge

__all__ = ["GisMcpBridge", "get_gis_mcp_bridge"]
