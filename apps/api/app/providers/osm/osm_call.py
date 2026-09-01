"""OSM Provider：Overpass/Nominatim 统一取数入口（osm_call 工具实现）。

裁剪自开源实现，适配我们的 ToolResult / ToolContext（移植来源见 NOTICE.md）：
- 4 个子命令：overpass_query / download_bbox / download_features / search；
- >40KB 落盘到 workspace/.workdir/osm/（L2 边界内），小结果内联返回；
- 结构化错误（ToolResult.success=False + message），不再抛给 LLM 裸异常。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

import requests

from app.models.tool import ToolResult
from app.providers.base import DataProvider
from app.providers.osm.overpass import (
    bbox_from_place,
    nominatim_search,
    osm_to_geojson,
    overpass_query,
)
from app.tools.context import ToolContext

logger = logging.getLogger("app.osm")

_INLINE_GEOJSON_MAX_BYTES = 40 * 1024

OSM_COMMANDS = (
    "overpass_query",
    "download_bbox",
    "download_features",
    "search",
)

_COMMAND_HELP = {
    "overpass_query": (
        "params: query (Overpass QL), timeout(默认120), retries(默认1), "
        "output_path/save_path(可选), return_geojson(bool)"
    ),
    "download_bbox": (
        "params: south/west/north/east 或 bbox, key(OSM tag key), value('*'任意或 | 分隔多值), "
        "geometry_type(可选；不传时自动同时查 node+way，POI 用 node、建筑用 way，无需指定), "
        "output_path/save_path(可选；不传也会自动落盘到 .workdir/osm/ 并返回 path)"
    ),
    "download_features": (
        "params: place(地点名), key, value('*'任意或 | 分隔多值), "
        "geometry_type(可选；不传时自动同时查 node+way), output_path/save_path"
    ),
    "search": "params: query(搜索文本), limit(默认5)",
}


def _workspace_path(ctx: ToolContext) -> Path:
    if ctx.workspace_path:
        return Path(str(ctx.workspace_path)).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_output_path(ctx: ToolContext, raw_path: str | None, command: str) -> Path:
    from app.data_download import resolve_download_output

    return resolve_download_output(raw_path, command, _workspace_path(ctx), "osm")


def _feature_summary(geojson: dict[str, Any]) -> dict[str, Any]:
    features = geojson.get("features") if isinstance(geojson, dict) else None
    if not isinstance(features, list):
        return {"feature_count": 0, "geometry_types": [], "bbox": None}

    geometry_types: set[str] = set()
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    def visit_coords(coords: Any) -> None:
        nonlocal min_x, min_y, max_x, max_y
        if not coords:
            return
        if isinstance(coords, list) and coords and isinstance(coords[0], (int, float)):
            if len(coords) >= 2:
                x = float(coords[0])
                y = float(coords[1])
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
            return
        if isinstance(coords, list):
            for item in coords:
                visit_coords(item)

    for feat in features:
        geom = feat.get("geometry") if isinstance(feat, dict) else None
        if not isinstance(geom, dict):
            continue
        gtype = geom.get("type")
        if isinstance(gtype, str):
            geometry_types.add(gtype)
        visit_coords(geom.get("coordinates"))

    bbox = None if min_x == float("inf") else [min_x, min_y, max_x, max_y]
    return {"feature_count": len(features), "geometry_types": sorted(geometry_types), "bbox": bbox}


def _finalize_geojson_result(
    ctx: ToolContext,
    *,
    command: str,
    params: dict[str, Any],
    geojson: dict[str, Any],
) -> ToolResult:
    raw_output_path = params.get("output_path") or params.get("save_path")
    return_geojson = bool(params.get("return_geojson", False))
    encoded = json.dumps(geojson, ensure_ascii=False, separators=(",", ":"))
    # 下载类命令（download_bbox/download_features）总是落盘：register_layer 需要 file_path，
    # 内联 geojson 会让 LLM 额外 write 一步，容易失败。overpass_query 保持大小阈值。
    force_save = command in {"download_bbox", "download_features"}
    should_save = force_save or bool(raw_output_path) or len(encoded.encode("utf-8")) > _INLINE_GEOJSON_MAX_BYTES

    if not should_save:
        # 注意：不能用 `count and f"..."`——count=0 时求值结果是 int 0，
        # 会触发 ToolResult.message 的 pydantic 字符串校验失败
        count = _feature_summary(geojson)["feature_count"]
        return ToolResult(
            success=True,
            output={"geojson": geojson, **{k: v for k, v in _feature_summary(geojson).items() if k != "geometry_types"}},
            message=f"已下载 {count} 个 OSM 要素（内联返回）。" if count else "OSM 查询返回 0 个要素。",
        )

    output_path = _resolve_output_path(ctx, str(raw_output_path) if raw_output_path else None, command)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(encoded, encoding="utf-8")
    from app.data_download import notify_download_saved

    notify_download_saved()
    summary = _feature_summary(geojson)
    output: dict[str, Any] = {
        "path": str(output_path),
        "output_path": str(output_path),
        "feature_count": summary["feature_count"],
        "geometry_types": summary["geometry_types"],
        "bbox": summary["bbox"],
        "bytes": len(encoded.encode("utf-8")),
    }
    if return_geojson:
        output["geojson"] = geojson
    return ToolResult(
        success=True,
        output=output,
        message=f"已下载 {summary['feature_count']} 个 OSM 要素并保存 GeoJSON 到 {output_path}。",
    )


def _invalid_params_result(command: str, message: str) -> ToolResult:
    return ToolResult(
        success=False,
        output={"error": "osm_invalid_params", "command": command, "expected": _COMMAND_HELP.get(command, "")},
        message=f"OSM 参数无效: {message}",
    )


def _network_error_result(command: str, exc: Exception) -> ToolResult:
    is_timeout = isinstance(exc, requests.exceptions.Timeout)
    error = "osm_network_timeout" if is_timeout else "osm_network_error"
    return ToolResult(
        success=False,
        output={"error": error, "command": command, "retryable": not isinstance(exc, ValueError)},
        message=f"OSM 请求失败: {exc}（建议：缩小 bbox 或用更简单的 Overpass 查询，不要重复完全相同的请求）",
    )


def _validate_bbox(south: float, west: float, north: float, east: float) -> None:
    if not (-90 <= south <= 90 and -90 <= north <= 90):
        raise ValueError("south/north 必须在 [-90, 90] 内")
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        raise ValueError("west/east 必须在 [-180, 180] 内")
    if south >= north:
        raise ValueError("south 必须小于 north")
    if west >= east:
        raise ValueError("west 必须小于 east")


def _bbox_from_params(params: dict[str, Any]) -> tuple[float, float, float, float] | None:
    """从常见参数形态解析 bbox → (south, west, north, east)。"""
    if all(key in params for key in ("south", "west", "north", "east")):
        return (
            float(params["south"]),
            float(params["west"]),
            float(params["north"]),
            float(params["east"]),
        )
    bbox = params.get("bbox") or params.get("boundingbox") or params.get("bounds")
    if isinstance(bbox, str):
        try:
            bbox = json.loads(bbox)
        except Exception:
            parts = [item.strip() for item in bbox.split(",") if item.strip()]
            bbox = [float(item) for item in parts] if len(parts) == 4 else bbox
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        values = [float(item) for item in bbox]
        # Nominatim: [south, north, west, east]；地图库常用 [west, south, east, north]
        if abs(values[0]) <= 90 and abs(values[1]) <= 90 and abs(values[2]) > 90:
            south, north, west, east = values
        else:
            west, south, east, north = values
        return south, west, north, east
    return None


# 场馆/建筑/景点类 class/type：作为分析中心点首选
_VENUE_RANK_KEYS = (
    "exhibition_centre", "conference_centre", "convention_centre", "exhibition", "attraction",
    "amenity", "building", "tourism", "place", "leisure", "shop", "museum", "gallery", "stadium",
)
# 交通类 class/type：同名地铁站/公交站常被 Nominatim 排到展馆前面，应降权
_TRANSPORT_RANK_KEYS = ("railway", "highway", "aeroway", "route", "public_transport", "station", "stop")


def _rank_search_results(results: list[dict]) -> list[dict]:
    """重排 search 结果：场馆/建筑/景点类排前，交通站点排后，其余保持原序。

    原因：Nominatim 对「XX会展中心」这类查询常把同名地铁站（railway/station）排在
    真正的展馆之前，LLM 取第一条会把中心点落在车站上（深圳实测偏移约 440 米）。
    把场馆/建筑/景点提到最前，LLM 的默认选择才是真正的地点。
    """
    if not results:
        return results

    def rank(item: dict) -> int:
        cls = str(item.get("class") or "").lower()
        typ = str(item.get("type") or "").lower()
        if cls in _VENUE_RANK_KEYS or typ in _VENUE_RANK_KEYS:
            return 0
        if cls in _TRANSPORT_RANK_KEYS or typ in _TRANSPORT_RANK_KEYS:
            return 2
        return 1

    # 稳定排序：同权结果保持 Nominatim 原始顺序
    return sorted(results, key=lambda item: rank(item))


def _tag_from_params(params: dict[str, Any]) -> tuple[str, str]:
    """解析 OSM tag key/value（严格参数或常见 LLM 别名）。"""
    key = params.get("key")
    value = params.get("value", "*")
    tags = (
        params.get("tags")
        or params.get("features")
        or params.get("feature")
        or params.get("feature_type")
    )
    if not key and isinstance(tags, list) and tags:
        first = tags[0]
        if isinstance(first, str):
            key = first
        elif isinstance(first, dict):
            key = first.get("key") or first.get("k")
            value = first.get("value", first.get("v", value))
    if not key and isinstance(tags, dict):
        if len(tags) == 1:
            key, value = next(iter(tags.items()))
        else:
            key = tags.get("key") or tags.get("k")
            value = tags.get("value", tags.get("v", value))
    if not key and params.get("query"):
        key = params.get("query")
    if not key:
        raise ValueError("缺少 OSM tag key；请提供 key/value 或 tags")
    return str(key), "*" if value is None else str(value)


def _tag_clause(key: str, value: str) -> str:
    """OSM tag 过滤子句：`["amenity"="restaurant"]` / `["amenity"~"^(a|b)$"]`。"""
    if not re.match(r"^[A-Za-z0-9_:\-]+$", key or ""):
        raise ValueError(f"非法的 OSM tag key: {key!r}")
    if value == "*":
        return f'["{key}"]'
    if "|" in str(value):
        # 多业态一次下载：value 含 | 时按 Overpass 正则匹配（["amenity"~"^(restaurant|cafe|fast_food)$"]）。
        # 加 ^...$ 锚定避免误匹配（如 cafe 匹配到 internet_cafe）。
        escaped_value = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'["{key}"~"^({escaped_value})$"]'
    escaped_value = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'["{key}"="{escaped_value}"]'


def _build_tag_filter(key: str, value: str, geometry_type: str = "way") -> str:
    geometry_type = (geometry_type or "way").strip().lower()
    if geometry_type not in {"node", "way", "relation"}:
        raise ValueError("geometry_type 必须是 node/way/relation 之一")
    return f'{geometry_type}{_tag_clause(key, value)}'


class OSMProvider(DataProvider):
    provider_id = "osm"
    commands = OSM_COMMANDS

    def execute(self, command: str, params: dict, ctx: ToolContext) -> ToolResult:
        if command not in OSM_COMMANDS:
            return ToolResult(
                success=False,
                message=f"未知 OSM 命令：{command}（可用：{', '.join(OSM_COMMANDS)}）",
            )
        try:
            return self._dispatch(command, params, ctx)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as exc:
            return _network_error_result(command, exc)
        except ValueError as exc:
            return _invalid_params_result(command, str(exc))

    def _dispatch(self, command: str, params: dict, ctx: ToolContext) -> ToolResult:
        if command == "search":
            query = params.get("query")
            if not query:
                return _invalid_params_result(command, "缺少必填参数 query")
            results = _rank_search_results(
                nominatim_search(
                    query,
                    limit=int(params.get("limit", 5)),
                    timeout=int(params.get("timeout", 45)),
                    retries=int(params.get("retries", 2)),
                )
            )
            return ToolResult(
                success=True,
                output={"results": results},
                message=f"找到 {len(results)} 个地点。",
            )

        if command == "overpass_query":
            query = params.get("query")
            if not query:
                return _invalid_params_result(command, "缺少必填参数 query")
            data = overpass_query(query, timeout=int(params.get("timeout", 120)), retries=int(params.get("retries", 1)))
            return _finalize_geojson_result(ctx, command=command, params=params, geojson=osm_to_geojson(data))

        if command == "download_bbox":
            bbox = _bbox_from_params(params)
            if bbox is None:
                if params.get("place"):
                    key, value = _tag_from_params(params)
                    bb = bbox_from_place(
                        str(params["place"]),
                        timeout=int(params.get("geocode_timeout", params.get("timeout", 45))),
                        retries=int(params.get("geocode_retries", params.get("retries", 2))),
                    )
                    if bb is None:
                        return _invalid_params_result(command, f"未找到地点：{params['place']}")
                    south, north, west, east = bb
                    return self._download_bbox(ctx, params, south, west, north, east, key, value)
                return _invalid_params_result(command, "缺少 bbox 坐标（south/west/north/east 或 bbox）")
            south, west, north, east = bbox
            key, value = _tag_from_params(params)
            return self._download_bbox(ctx, params, south, west, north, east, key, value)

        if command == "download_features":
            key, value = _tag_from_params(params)
            bbox = _bbox_from_params(params)
            if bbox is not None and not params.get("place"):
                south, west, north, east = bbox
                return self._download_bbox(ctx, params, south, west, north, east, key, value)
            place = params.get("place")
            if not place:
                return _invalid_params_result(command, "缺少必填参数 place；已有 bbox 时请用 download_bbox")
            bb = bbox_from_place(
                str(place),
                timeout=int(params.get("geocode_timeout", params.get("timeout", 45))),
                retries=int(params.get("geocode_retries", params.get("retries", 2))),
            )
            if bb is None:
                return _invalid_params_result(command, f"未找到地点：{place}")
            south, north, west, east = bb
            return self._download_bbox(ctx, params, south, west, north, east, key, value)

        return _invalid_params_result(command, f"未处理的命令：{command}")

    def _download_bbox(
        self,
        ctx: ToolContext,
        params: dict[str, Any],
        south: float,
        west: float,
        north: float,
        east: float,
        key: str,
        value: str,
    ) -> ToolResult:
        _validate_bbox(south, west, north, east)
        bbox = f"({south},{west},{north},{east})"
        geometry_type = params.get("geometry_type")
        if geometry_type:
            element = _build_tag_filter(key, value, geometry_type)
            query = f"{element}{bbox};\nout geom;"
        else:
            # 未指定 geometry_type 时联合查询 node+way：POI（餐饮等）通常是 node，
            # 建筑/用地通常是 way。单独默认 way 会让餐饮等 POI 查回 0 个要素。
            tag = _tag_clause(key, value)
            query = f"(node{tag}{bbox};way{tag}{bbox};);\nout geom;"
        data = overpass_query(query, timeout=int(params.get("timeout", 120)), retries=int(params.get("retries", 1)))
        return _finalize_geojson_result(ctx, command="download_bbox", params=params, geojson=osm_to_geojson(data))
