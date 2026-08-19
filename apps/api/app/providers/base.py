"""DataProvider 抽象：统一外部数据源接入契约。

先实现 OSM（Overpass/Nominatim）；未来高德（providers/gaode.py）实现同一抽象并
注册进 registry 即可，osm_call 工具无需改动。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.models.tool import ToolResult

if TYPE_CHECKING:
    from app.tools.context import ToolContext


class ProviderResult(BaseModel):
    """Provider 结构化输出（与 ToolResult 对齐，方便转接）。"""

    success: bool = True
    output: dict[str, object] = Field(default_factory=dict)
    message: str | None = None
    saved_path: str | None = None  # 落盘时带路径


class DataProvider(ABC):
    """数据提供者：按命令执行取数，返回统一 ToolResult。"""

    provider_id: str = ""
    commands: tuple[str, ...] = ()

    @abstractmethod
    def execute(self, command: str, params: dict, ctx: "ToolContext") -> ToolResult:
        """执行 provider 命令（params 已解析为 dict）。"""
