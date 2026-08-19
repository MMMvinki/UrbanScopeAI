"""Overpass API & Nominatim 客户端（OpenStreetMap 数据）。

裁剪自开源实现：限流、重试、out geom 归一化、osm_to_geojson 转换逻辑原样保留；
去掉原实现特有的异常语义（OSMError），统一抛 requests 异常 / ValueError，
由调用方转 ToolResult（移植来源见 NOTICE.md）。
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import requests

logger = logging.getLogger("app.osm")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
NOMINATIM_URL = "https://nominatim.openstreetmap.org"
_USER_AGENT = {"User-Agent": "UrbanScopeAI/0.1 (urbanscope-app)"}
_DEFAULT_OVERPASS_TIMEOUT = 120
_DEFAULT_NOMINATIM_TIMEOUT = 45
_DEFAULT_RETRIES = 2

# 限流：Overpass ~2 req/s，Nominatim ~1 req/s
_last_overpass_call: float = 0.0
_last_nominatim_call: float = 0.0


def overpass_query(
    query: str,
    *,
    timeout: int = _DEFAULT_OVERPASS_TIMEOUT,
    retries: int = 1,
) -> dict[str, Any]:
    """运行 Overpass QL 查询，返回 JSON 响应。"""
    global _last_overpass_call
    wait = 1.0 - (time.time() - _last_overpass_call)
    if wait > 0:
        time.sleep(wait)

    full_query = _normalize_overpass_query(query)
    logger.info("Overpass query: %s", full_query[:200])

    last_exc: Exception | None = None
    for attempt in range(max(0, int(retries)) + 1):
        try:
            resp = requests.post(
                OVERPASS_URL,
                data={"data": full_query},
                headers=_USER_AGENT,
                timeout=max(5, int(timeout or _DEFAULT_OVERPASS_TIMEOUT)),
            )
            _last_overpass_call = time.time()
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.HTTPError as exc:
            _last_overpass_call = time.time()
            last_exc = exc
            status = exc.response.status_code if exc.response is not None else None
            retryable = status in {429, 500, 502, 503, 504}
            if retryable and attempt < max(0, int(retries)):
                time.sleep(min(2.0 * (attempt + 1), 6.0))
                continue
            raise
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            _last_overpass_call = time.time()
            last_exc = exc
            if attempt < max(0, int(retries)):
                time.sleep(min(2.0 * (attempt + 1), 6.0))
                continue
            raise
    raise RuntimeError(f"Overpass request failed: {last_exc}")


def _normalize_overpass_query(query: str) -> str:
    text = str(query or "").strip()
    if not text:
        raise ValueError("Overpass query is empty")
    if re.match(r"^\[\s*out\s*:", text, flags=re.IGNORECASE):
        text = re.sub(r"^\[\s*out\s*:[^\]]+\]", "[out:json]", text, count=1, flags=re.IGNORECASE)
        if not re.search(r"\[\s*timeout\s*:", text, flags=re.IGNORECASE):
            text = "[timeout:60];\n" + text
        return _ensure_geojson_output_clause(text)
    return _ensure_geojson_output_clause(f"[out:json][timeout:60];\n{text}")


def _ensure_geojson_output_clause(query: str) -> str:
    """确保裸 Overpass body 返回 osm_to_geojson 可用的几何（out geom）。"""
    text = str(query or "").strip()
    if re.search(r"(?<!\[)\bout\s+count\s*;", text, flags=re.IGNORECASE):
        return text
    if re.search(r"(?<!\[)\bout\s+(?:body|tags|ids|skel|meta)\s*;", text, flags=re.IGNORECASE):
        return re.sub(
            r"(?<!\[)\bout\s+(?:body|tags|ids|skel|meta)\s*;",
            "out geom;",
            text,
            count=1,
            flags=re.IGNORECASE,
        )
    if not re.search(r"(?<!\[)\bout\b[^;]*;", text, flags=re.IGNORECASE):
        return text.rstrip(";") + ";\nout geom;"
    return text


def osm_to_geojson(data: dict) -> dict[str, Any]:
    """Overpass JSON 响应 → GeoJSON FeatureCollection。

    node→Point / 闭合 way→Polygon / 开放 way→LineString；跳过 relation。
    """
    features: list[dict] = []

    for element in data.get("elements", []):
        etype = element.get("type")
        tags = element.get("tags", {})

        if etype == "node":
            if not tags:
                continue  # way 顶点是结构输入，不作为 Point 要素
            lat = element.get("lat")
            lon = element.get("lon")
            if lat is None or lon is None:
                continue
            geometry = {"type": "Point", "coordinates": [lon, lat]}
        elif etype == "way":
            nodes = element.get("geometry", [])
            coords = [[n["lon"], n["lat"]] for n in nodes if "lat" in n and "lon" in n]
            if len(coords) < 2:
                continue
            if coords[0] == coords[-1] and len(coords) >= 4:
                geometry = {"type": "Polygon", "coordinates": [coords]}
            else:
                geometry = {"type": "LineString", "coordinates": coords}
        elif etype == "relation":
            continue  # 复杂 multipolygon，暂跳过
        else:
            continue

        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {**tags, "_osm_id": element.get("id"), "_osm_type": etype},
            }
        )

    return {"type": "FeatureCollection", "features": features}


def nominatim_search(
    query: str,
    limit: int = 5,
    *,
    timeout: int = _DEFAULT_NOMINATIM_TIMEOUT,
    retries: int = _DEFAULT_RETRIES,
) -> list[dict[str, Any]]:
    """按名称搜索地点（Nominatim 地理编码）。"""
    global _last_nominatim_call
    wait = 1.05 - (time.time() - _last_nominatim_call)
    if wait > 0:
        time.sleep(wait)

    last_exc: Exception | None = None
    max_attempts = max(0, int(retries)) + 1
    for attempt in range(max_attempts):
        try:
            resp = requests.get(
                f"{NOMINATIM_URL}/search",
                params={"q": query, "format": "json", "limit": limit, "addressdetails": 1},
                headers=_USER_AGENT,
                timeout=max(5, int(timeout or _DEFAULT_NOMINATIM_TIMEOUT)),
            )
            _last_nominatim_call = time.time()
            resp.raise_for_status()
            return resp.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            _last_nominatim_call = time.time()
            last_exc = exc
            if attempt < max_attempts - 1:
                logger.warning(
                    "Nominatim request failed (%s), retrying %d/%d",
                    type(exc).__name__,
                    attempt + 1,
                    max_attempts - 1,
                )
                time.sleep(min(2.0 * (attempt + 1), 6.0))
                continue
            raise
    raise RuntimeError(f"Nominatim request failed: {last_exc}")


def bbox_from_place(
    place: str,
    *,
    timeout: int = _DEFAULT_NOMINATIM_TIMEOUT,
    retries: int = _DEFAULT_RETRIES,
) -> list[float] | None:
    """地点名 → 包围盒 [south, north, west, east]；未找到返回 None。"""
    results = nominatim_search(place, limit=1, timeout=timeout, retries=retries)
    if not results:
        return None
    bb = results[0].get("boundingbox")
    if bb and len(bb) == 4:
        return [float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])]
    return None
