"""Provider 注册表：按 provider_id 取用 DataProvider 实现。

新增 provider（如高德）：实现 DataProvider → 在本文件 import 并注册即可，
工具层（osm_call 等）无需改动。
"""

from __future__ import annotations

from app.providers.base import DataProvider

# 延迟导入避免循环依赖
_registry: dict[str, DataProvider] = {}


def register(provider: DataProvider) -> None:
    _registry[provider.provider_id] = provider


def get_provider(provider_id: str) -> DataProvider:
    if provider_id not in _registry:
        from app.providers.osm.osm_call import OSMProvider

        register(OSMProvider())
    if provider_id not in _registry:
        raise KeyError(f"未知数据 provider：{provider_id}")
    return _registry[provider_id]


def providers() -> dict[str, DataProvider]:
    return dict(_registry)
