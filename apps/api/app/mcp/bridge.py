"""gis-mcp MCP 客户端桥（STDIO 传输）。

gis-mcp（mahdin75/gis-mcp）是 FastMCP 实现的空间分析 MCP 服务器，提供 93+ 个
基于 Shapely / PyProj / GeoPandas / Rasterio / PySAL 的 GIS 工具。本模块以
后台线程维护一个 asyncio 事件循环 + ClientSession，把它的工具动态合并进
Executor，让 Agent 能像调用本地工具一样调用 gis-mcp 工具。

- 连接：使用当前解释器（sys.executable）`-m gis_mcp` STDIO 启动；
- 生命周期：start() 立即返回（后台线程初始化），ready 后可用；
- 任何失败不阻塞主流程：Agent 继续用本地确定性工具兜底。
"""

import asyncio
import json
import logging
import sys
import threading
import time
from typing import Any

from app.agent import llm_client

logger = logging.getLogger("urbanscope.mcp")

# gis_mcp 是包无 __main__，需通过其 CLI 入口 main() 启动（FastMCP STDIO 默认传输）
DEFAULT_ARGS = ["-c", "from gis_mcp.main import main; main()"]


class GisMcpBridge:
    """与 gis-mcp 的 STDIO 客户端连接。线程安全（同步包装 async 调用）。"""

    def __init__(self, args: list[str] | None = None) -> None:
        # 用当前解释器启动，保证 gis_mcp 与其依赖（geopandas 等）来自同一 venv
        self._command = sys.executable
        self._args = args or list(DEFAULT_ARGS)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: Any = None
        self._tools: list[dict] = []
        self._ready = False
        self._error = ""
        self._thread: threading.Thread | None = None

    # ---------- 生命周期 ----------

    def start(self) -> None:
        """后台启动 MCP 客户端线程，立即返回。"""
        self._thread = threading.Thread(target=self._run_loop, name="gis-mcp-bridge", daemon=True)
        self._thread.start()

    def wait_ready(self, timeout: float = 8.0) -> bool:
        """阻塞等待连接就绪（供启动阶段预热）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._ready or self._error:
                return self._ready
            time.sleep(0.1)
        return self._ready

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect())
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
            logger.warning("gis-mcp 连接失败：%s", exc)

    async def _connect(self) -> None:
        from mcp import ClientSession, StdioServerParameters, stdio_client

        params = StdioServerParameters(command=self._command, args=self._args)
        # stdio_client 上下文负责维持子进程生命周期
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                self._session = session
                tools = await session.list_tools()
                self._tools = [
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema or {},
                    }
                    for tool in tools.tools
                ]
                self._ready = True
                logger.info("gis-mcp 就绪：%d 个工具", len(self._tools))
                # 保持事件循环存活（子进程由 stdio_client 上下文维持）
                await asyncio.Event().wait()

    # ---------- 同步接口 ----------

    def is_ready(self) -> bool:
        return self._ready and self._session is not None

    def tool_names(self) -> set[str]:
        return {tool["name"] for tool in self._tools}

    def tools(self) -> list[dict]:
        return self._tools

    def schema_list(self) -> list[dict]:
        """合并进 Executor 的 OpenAI function schema 列表。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"][:500],
                    "parameters": tool["input_schema"],
                },
            }
            for tool in self._tools
        ]

    def call_tool(self, name: str, arguments: dict | None) -> dict:
        """调用 gis-mcp 工具，返回与本地 ToolResult 兼容的 dict。"""
        if not self.is_ready():
            return {"success": False, "output": {}, "message": "gis-mcp 未就绪"}
        try:
            future = asyncio.run_coroutine_threadsafe(
                self._session.call_tool(name, arguments or {}), self._loop
            )
            result = future.result(timeout=60)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "output": {}, "message": f"gis-mcp 工具 {name} 失败：{exc}"}
        return _to_tool_result(result)


def _to_tool_result(result: Any) -> dict:
    """MCP call_tool 返回（content 列表）→ ToolResult 兼容 dict。

    gis-mcp 的文本输出通常是 JSON（geojson / 指标），尝试解析为结构化 output。
    """
    texts: list[str] = []
    for block in getattr(result, "content", []) or []:
        if getattr(block, "type", "") == "text":
            texts.append(str(block.text))
        else:
            texts.append(f"[{getattr(block, 'type', 'block')}]")
    payload = "\n".join(texts).strip()
    if not payload:
        return {"success": True, "output": {}, "message": "gis-mcp 返回空结果"}
    try:
        output = json.loads(payload)
        if isinstance(output, dict):
            return {"success": True, "output": output, "message": "gis-mcp 工具调用成功"}
    except json.JSONDecodeError:
        pass
    return {"success": True, "output": {"text": payload[:2000]}, "message": payload[:200]}


# 全局单例
_bridge: GisMcpBridge | None = None


def get_gis_mcp_bridge() -> GisMcpBridge | None:
    """懒加载单例；未配置 LLM 时返回 None（演示环境不启动 MCP）。"""
    global _bridge
    if _bridge is not None:
        return _bridge
    if not llm_client.is_available():
        return None
    _bridge = GisMcpBridge()
    _bridge.start()
    return _bridge
