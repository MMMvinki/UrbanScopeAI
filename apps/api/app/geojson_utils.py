"""GeoJSON 通用解析工具：图层 id 规整、几何类型识别、坐标遍历、FeatureCollection 归一。

供 register_layer 工具与 DataRegistry 的工作区图层索引共用，避免逻辑重复。
"""

from __future__ import annotations

import re
from typing import Iterator

from app.models.layer import FieldSpec, GeometryType


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


def build_fields_from_features(features: list[dict]) -> list[FieldSpec]:
    """从要素 properties 键并集推断字段列表（供手绘/无 schema 图层入库）。

    按 Python 类型映射 GeoJSON 字段 type：int/float→number、bool→boolean、
    str→string、缺省 None→string。字段顺序按首次出现序；跳过 Terra Draw 内部键
    （mode/midPoint/selectionPoint/selected/id），避免中间态污染图层 schema。
    """
    reserved = {"mode", "midpoint", "midPoint", "selectionpoint", "selectionPoint", "selected", "id"}
    fields: list[FieldSpec] = []
    seen: set[str] = set()
    for feature in features:
        props = feature.get("properties") or {}
        for key in props:
            if key in reserved or key in seen:
                continue
            seen.add(key)
            value = props[key]
            if isinstance(value, bool):
                ftype = "boolean"
            elif isinstance(value, (int, float)):
                ftype = "number"
            else:
                ftype = "string"
            fields.append(FieldSpec(name=key, type=ftype))
    return fields
