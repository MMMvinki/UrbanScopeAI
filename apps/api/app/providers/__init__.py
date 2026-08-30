"""DataProvider 抽象与注册表：OSM / Datasource（预留高德）。"""

from app.providers.base import DataProvider, ProviderResult
from app.providers.registry import get_provider, providers, register

# 注册内置 provider
from app.providers.osm.osm_call import OSMProvider
from app.providers.datasource.datasource_call import DatasourceProvider
from app.providers.amap.amap_call import AMapProvider

register(OSMProvider())
register(DatasourceProvider())
register(AMapProvider())

__all__ = [
    "DataProvider",
    "ProviderResult",
    "get_provider",
    "providers",
    "register",
    "OSMProvider",
    "DatasourceProvider",
    "AMapProvider",
]
