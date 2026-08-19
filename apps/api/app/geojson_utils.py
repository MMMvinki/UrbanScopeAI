"""GeoJSON 通用解析工具：图层 id 规整、几何类型识别、坐标遍历、FeatureCollection 归一。

供 register_layer 工具与 DataRegistry 的工作区图层索引共用，避免逻辑重复。
"""

from __future__ import annotations

import re
from typing import Iterator

from app.models.layer import GeometryType


def clean_layer_id(raw: str) -> str:
    """把任意字符串规整成图层 id：小写、下划线、保留字母数字与中文、去其余字符。

    中文保留（如"测试设施"），否则中文文件名会被剥空回退成 "layer"，且多文件上传会撞 id。
    """
    cleaned = re.sub(r"[^0-9a-zA-Z_一-鿿]+", "_", raw.strip()).strip("_").lower()
    return cleaned or "layer"


def geometry_type_for(types: set[str]) -> GeometryType:
    if "Polygon" in types or "MultiPolygon" in types:
        return GeometryType.polygon
    if "LineString" in types or "MultiLineString" in types:
        return GeometryType.line
    return GeometryType.point


def iter_xy(coords: list) -> Iterator[tuple[float, float]]:
    """递归遍历 GeoJSON 坐标，逐点产出 (x, y)。"""
    for item in coords:
        if isinstance(item, (int, float)):
            yield float(coords[0]), float(coords[1])
            return
        yield from iter_xy(item)


def load_feature_collection(data: dict) -> tuple[list[dict], set[str], list[float] | None]:
    """把任意 GeoJSON 归一成 FeatureCollection，返回 (features, geometry_types, bbox)。

    容错裸 Feature / 裸 Geometry 输入；bbox 形如 [minx, miny, maxx, maxy]，无坐标时 None。
    """
    if data.get("type") == "FeatureCollection":
        features = data.get("features") or []
    elif data.get("type") == "Feature":
        features = [data]
    else:
        features = [{"type": "Feature", "geometry": data, "properties": {}}]

    geometry_types: set[str] = set()
    xs: list[float] = []
    ys: list[float] = []
    for feature in features:
        geom = feature.get("geometry") or {}
        gtype = geom.get("type")
        if gtype:
            geometry_types.add(gtype)
        coords = geom.get("coordinates")
        if isinstance(coords, list):
            for x, y in iter_xy(coords):
                xs.append(x)
                ys.append(y)
    bbox = [min(xs), min(ys), max(xs), max(ys)] if xs else None
    return features, geometry_types, bbox
