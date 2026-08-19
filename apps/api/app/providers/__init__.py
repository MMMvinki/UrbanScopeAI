"""DataProvider 抽象与注册表：OSM / Datasource（预留高德）。"""

from app.providers.base import DataProvider, ProviderResult
from app.providers.registry import get_provider, providers, register

# 注册内置 provider
from app.providers.osm.osm_call import OSMProvider
from app.providers.datasource.datasource_call import DatasourceProvider

register(OSMProvider())
register(DatasourceProvider())

__all__ = [
    "DataProvider",
    "ProviderResult",
    "get_provider",
    "providers",
    "register",
    "OSMProvider",
    "DatasourceProvider",
]
