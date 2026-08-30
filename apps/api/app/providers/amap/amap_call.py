"""高德 AMap Provider：POI / 行政区边界 / 地理编码统一取数入口（amap_call 工具实现）。

解决 OSM 在中国的短板：POI 全量带中文名与国内业态分类、行政区边界、中文地理编码。
- 4 个子命令：poi_search / district / geocode / regeo；
- 坐标一律 GCJ-02↔WGS-84 转换：高德返回/接收 GCJ-02，平台图层是 WGS-84，
  对外统一 WGS-84（查询范围自动转 GCJ-02 发请求，结果再转回 WGS-84）；
- 大结果（POI/边界）落盘到 workspace/.workdir/amap/，小结果内联返回；
- POI 多边形枚举按约 2km 网格自动分块全量爬取（不再依赖高德 count，实测对大范围 count 会被截断到 ~200），
  跨块按 POI id 去重；请求间小睡限速防配额超限。

需要 apps/api/.env 配置 AMAP_API_KEY（高德开放平台 Web 服务 key），改后需重启后端。
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
import uuid
from pathlib import Path
from typing import Any

import requests

from app.models.tool import ToolResult
from app.providers.base import DataProvider
from app.tools.context import ToolContext

logger = logging.getLogger("app.amap")

_AMAP_BASE = "https://restapi.amap.com"  # endpoint 自带 /v3 前缀，勿在此重复
_INLINE_GEOJSON_MAX_BYTES = 40 * 1024
_SLEEP_BETWEEN_REQUESTS = 0.25  # 秒：分页/分块请求间隔，控制 QPS
_MAX_REQUESTS = 60  # 单次工具调用的总请求预算（防配额烧穿）
_DEFAULT_MAX_POIS = 20000  # 单次调用最多落盘的 POI 数
_UA = {"User-Agent": "UrbanScopeAI/0.1 (urbanscope-app)"}

# 三种搜索的 offset 上限（高德文档）：text/around=25，polygon=50；page 上限 100
_OFFSET = {"text": 25, "around": 25, "polygon": 50}
_MAX_PAGE = 100
# polygon 网格分块：每格经纬跨度上限（约 2km），保证单格 POI 不触发高德单次返回截断。
# 分块按跨度而非高德 count——count 对大范围常被截断到 ~200，导致旧"count<=上限"判断
# 永远命中、整块只取 ~200 条（=获取不全）。改为按跨度强制分格，跨格按 id 去重。
_CELL_DEG = 0.02
_MAX_GRID_SIDE = 10  # 最大 X×Y 格数，防 bbox 超大导致请求爆量

AMAP_COMMANDS = ("poi_search", "district", "geocode", "regeo")

_COMMAND_HELP = {
    "poi_search": (
        "mode ∈ {text, around, polygon}。"
        "text: keywords + city(必填，限定城市防全国同名) + types(可选，行业分类码如 050000 餐饮服务)；"
        "around: location=[经度,纬度] + radius(米，默认 3000) + types/keywords；"
        "polygon: bbox=[西经,南纬,东经,北纬] + types/keywords（自动按约 2km 网格分块全量爬取，传整范围 bbox 即可，无需手动拆格）。"
        "坐标一律 WGS-84（与平台图层一致，自动转高德 GCJ-02）。"
        "types 用高德行业分类码（06 餐饮服务 08 购物服务 09 生活服务 05 商务住宅 01 汽车服务 02 汽车销售 03 汽车维修 20 房产小区 17 医疗保健 15 科教文化服务 16 运动健身 14 文化传媒 22 公司企业 23 旅游景点 11 金融保险 12 商务写字楼 10 道路附属 07 住宿服务 19 政府机构 18 交通设施等；"
        "多位码含子类，如 050101 中餐厅；types 不传则按 keywords 全文搜全部类型)。"
        "keyword 与 types 至少给一个。下载结果必然落盘，output 有 path，立即用 register_layer 发布成地图图层。"
    ),
    "district": (
        "params: keywords(行政区名，如 '深圳市'/'南山区') + subdistrict(0=本级 1=下一级，默认 0)。"
        "返回该行政区边界 GeoJSON（WGS-84 多边形），落盘到 .workdir/amap/，用 register_layer 发布。"
        "注意：高德边界只到区县一级，镇街边界要传区县名查其子级。"
    ),
    "geocode": (
        "params: address(地址，如 '深圳市南山区科技园') + city(可选，限定城市)。"
        "返回 WGS-84 经纬度 + 行政编码，用于把地址转成分析中心点（查中心点用 district/geocode 优于 OSM search）。"
    ),
    "regeo": (
        "params: location=[经度,纬度](WGS-84)。返回该点行政区划、街道、附近 POI。"
        "用于把坐标转回中文地址描述。"
    ),
}


def _amap_key() -> str:
    """复用 llm_client 的 _env 链（进程 env > apps/api/.env > 默认）。"""
    from app.agent import llm_client

    return llm_client._env("AMAP_API_KEY")


# ── GCJ-02（火星坐标）↔ WGS-84 ────────────────────────────────────────────
_A = 6378245.0
_EE = 0.00669342162296594323


def _out_of_china(lng: float, lat: float) -> bool:
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(x: float, y: float) -> float:
    ret = (
        -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
        + (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        + (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
        + (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    )
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = (
        300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
        + (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        + (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
        + (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    )
    return ret


def _gcj_offset(lng: float, lat: float) -> tuple[float, float]:
    """GCJ-02 相对 WGS-84 的偏移量（dlng, dlat）。"""
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return dlng, dlat


def wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    """WGS-84 → GCJ-02（查询范围/位置传高德前转换）。"""
    if _out_of_china(lng, lat):
        return lng, lat
    dlng, dlat = _gcj_offset(lng, lat)
    return lng + dlng, lat + dlat


def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    """GCJ-02 → WGS-84（高德结果转平台坐标系）。"""
    if _out_of_china(lng, lat):
        return lng, lat
    dlng, dlat = _gcj_offset(lng, lat)
    return lng * 2 - (lng + dlng), lat * 2 - (lat + dlat)


def _conv_coords(coords: Any, conv) -> Any:
    """递归把坐标序列按 conv 转换（[lng, lat] 叶节点）。"""
    if isinstance(coords, (list, tuple)):
        if len(coords) == 2 and isinstance(coords[0], (int, float)) and isinstance(coords[1], (int, float)):
            return list(conv(float(coords[0]), float(coords[1])))
        return [_conv_coords(item, conv) for item in coords]
    return coords


# ── 高德请求基础 ──────────────────────────────────────────────────────────
class _QuotaExceeded(Exception):
    pass


def _amap_get(ctx: ToolContext, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """调高德 REST API，返回 JSON dict；业务失败抛 ValueError，配额超限抛 _QuotaExceeded。"""
    budget = ctx.meta.setdefault("_amap_requests", 0)
    if budget >= _MAX_REQUESTS:
        raise _QuotaExceeded("本轮高德请求已达上限")
    params = dict(params)
    params.setdefault("key", _amap_key())
    resp = requests.get(f"{_AMAP_BASE}{endpoint}", params=params, headers=_UA, timeout=20)
    resp.raise_for_status()
    ctx.meta["_amap_requests"] = budget + 1
    time.sleep(_SLEEP_BETWEEN_REQUESTS)
    data = resp.json()
    if data.get("status") != "1":
        info = str(data.get("info") or "未知错误")
        infocode = str(data.get("infocode") or "")
        if infocode in {"10003", "10004", "10005", "20001"}:
            raise _QuotaExceeded(f"高德返回 {infocode}：{info}（key 无效/配额不足/QPS 超限，稍后再试）")
        raise ValueError(f"高德返回错误 {infocode}：{info}")
    return data


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
                min_x = min(min_x, float(coords[0]))
                min_y = min(min_y, float(coords[1]))
                max_x = max(max_x, float(coords[0]))
                max_y = max(max_y, float(coords[1]))
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


def _workspace_path(ctx: ToolContext) -> Path:
    if ctx.workspace_path:
        return Path(str(ctx.workspace_path)).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_output_path(ctx: ToolContext, raw_path: str | None, command: str) -> Path:
    workspace = _workspace_path(ctx)
    if raw_path:
        raw = Path(str(raw_path)).expanduser()
        path = raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()
    else:
        safe_command = re.sub(r"[^A-Za-z0-9_.\-一-鿿]+", "_", command).strip("_") or "amap"
        path = (workspace / ".workdir" / "amap" / f"{safe_command}_{uuid.uuid4().hex[:8]}.geojson").resolve()
    if workspace != path and workspace not in path.parents:
        raise ValueError(f"output_path 必须位于 workspace 内: {path}")
    if path.suffix.lower() not in {".geojson", ".json"}:
        path = path.with_suffix(".geojson")
    return path


def _finalize_geojson_result(
    ctx: ToolContext,
    *,
    command: str,
    params: dict[str, Any],
    geojson: dict[str, Any],
) -> ToolResult:
    """POI/边界结果统一收尾：大结果落盘 + 返回 path（register_layer 需要 file_path）。"""
    raw_output_path = params.get("output_path") or params.get("save_path")
    return_geojson = bool(params.get("return_geojson", False))
    encoded = json.dumps(geojson, ensure_ascii=False, separators=(",", ":"))
    should_save = bool(raw_output_path) or len(encoded.encode("utf-8")) > _INLINE_GEOJSON_MAX_BYTES

    if not should_save:
        count = _feature_summary(geojson)["feature_count"]
        return ToolResult(
            success=True,
            output={"geojson": geojson},
            message=f"已获取 {count} 个高德要素（内联返回）。",
        )

    output_path = _resolve_output_path(ctx, str(raw_output_path) if raw_output_path else None, command)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(encoded, encoding="utf-8")
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
        message=f"已下载 {summary['feature_count']} 个高德要素并保存 GeoJSON 到 {output_path}。",
    )


def _invalid_params_result(command: str, message: str) -> ToolResult:
    return ToolResult(
        success=False,
        output={"error": "amap_invalid_params", "command": command, "expected": _COMMAND_HELP.get(command, "")},
        message=f"高德参数无效: {message}",
    )


def _error_result(command: str, exc: Exception) -> ToolResult:
    if isinstance(exc, _QuotaExceeded):
        return ToolResult(
            success=False,
            output={"error": "amap_quota", "command": command, "retryable": True},
            message=str(exc),
        )
    is_timeout = isinstance(exc, requests.exceptions.Timeout)
    error = "amap_network_timeout" if is_timeout else "amap_network_error"
    return ToolResult(
        success=False,
        output={"error": error, "command": command, "retryable": True},
        message=f"高德请求失败: {exc}（建议缩小范围/换关键词，不要重复完全相同的请求）",
    )


# ── POI 搜索 ──────────────────────────────────────────────────────────────
_POI_PROP_FIELDS = (
    "id", "name", "type", "typecode", "address", "pname", "cityname",
    "adname", "adcode", "citycode", "business_area", "tel", "rating",
)


def _poi_to_feature(poi: dict[str, Any]) -> dict[str, Any] | None:
    """POI JSON → GeoJSON Feature；location 是 GCJ-02，转 WGS-84 做 geometry。"""
    location = poi.get("location")
    if not location or ";" in str(location):
        return None
    try:
        glng, glat = (float(part) for part in str(location).split(",")[:2])
    except (ValueError, TypeError):
        return None
    lng, lat = gcj02_to_wgs84(glng, glat)
    props: dict[str, Any] = {k: poi.get(k) for k in _POI_PROP_FIELDS if poi.get(k) is not None}
    props["gcj02_lng"], props["gcj02_lat"] = round(glng, 6), round(glat, 6)
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [round(lng, 6), round(lat, 6)]},
        "properties": props,
    }


def _paginate_search(
    ctx: ToolContext, endpoint: str, params: dict[str, Any], max_results: int
) -> list[dict[str, Any]]:
    """翻页爬完一次 POI 搜索（text/around/polygon），按 id 去重。返回 Feature 列表。"""
    offset = _OFFSET.get(endpoint.replace("/v3/place/", ""), 25)
    page = 1
    features: dict[str, dict[str, Any]] = {}
    while len(features) < max_results:
        p = dict(params)
        p["offset"] = offset
        p["page"] = page
        data = _amap_get(ctx, endpoint, p)
        pois = data.get("pois") or []
        if not pois:
            break
        for poi in pois:
            feat = _poi_to_feature(poi)
            if feat is not None:
                features.setdefault(feat["properties"].get("id") or str(len(features)), feat)
        total = int(data.get("count") or len(features))
        page += 1
        if page > _MAX_PAGE or len(features) >= total:
            break
    return list(features.values())


def _crawl_polygon(
    ctx: ToolContext,
    bbox: list[float],
    types: str | None,
    keywords: str | None,
    max_results: int,
) -> list[dict[str, Any]]:
    """polygon 搜索：按 bbox 跨度网格细分，逐格爬取全量去重。

    **不依赖高德 count 判断分块**：实测 place/polygon 对结果多/范围大的查询，count 会被
    截断到单请求实际返回上限（约 200），导致旧逻辑 `count <= _POLYGON_CAP` 永远命中 →
    整块只取 ~200 条（"获取不全"）。改为按经纬跨度强制分格（每格 ≤ _CELL_DEG，保证单格
    POI 不超上限、能翻页取全），跨格按 POI id 去重。
    """
    west, south, east, north = bbox
    features: dict[str, dict[str, Any]] = {}

    def query_one(w: float, s: float, e: float, n: float) -> int:
        polygon = f"{w:.6f},{s:.6f};{e:.6f},{n:.6f}"
        p: dict[str, Any] = {"polygon": polygon}
        if types:
            p["types"] = types
        if keywords:
            p["keywords"] = keywords
        page = 1
        got = 0
        while page <= _MAX_PAGE:
            data = _amap_get(ctx, "/v3/place/polygon", {**p, "offset": 50, "page": page})
            pois = data.get("pois") or []
            if not pois:
                break
            for poi in pois:
                feat = _poi_to_feature(poi)
                if feat is not None:
                    features.setdefault(feat["properties"].get("id") or f"{w}{s}{len(features)}", feat)
            got += len(pois)
            total = int(data.get("count") or got)
            page += 1
            if page > _MAX_PAGE or got >= total:
                break
        return got

    # 网格划分：按 bbox 跨度细分配到每格 ≤ _CELL_DEG，跨格按 id 去重。
    x_cells = max(1, min(_MAX_GRID_SIDE, math.ceil((east - west) / _CELL_DEG)))
    y_cells = max(1, min(_MAX_GRID_SIDE, math.ceil((north - south) / _CELL_DEG)))
    dw = (east - west) / x_cells
    dh = (north - south) / y_cells
    for i in range(x_cells):
        for j in range(y_cells):
            if len(features) >= max_results:
                break
            try:
                query_one(west + i * dw, south + j * dh, west + (i + 1) * dw, south + (j + 1) * dh)
            except _QuotaExceeded:
                break  # 请求预算耗尽：保留已爬部分，尽量不全
        if len(features) >= max_results:
            break
    return list(features.values())[:max_results]


def _run_poi_search(ctx: ToolContext, params: dict[str, Any]) -> ToolResult:
    mode = str(params.get("mode") or params.get("type") or "text").strip().lower()
    if mode not in _OFFSET:
        raise ValueError(f"mode 必须是 text/around/polygon 之一，收到：{mode or '（空）'}")
    types = params.get("types") or None
    keywords = params.get("keywords") or params.get("keyword") or None
    if not types and not keywords:
        raise ValueError("keyword 与 types 至少给一个（如 keywords='肯德基' 或 types='050000'）")
    max_results = int(params.get("max_results") or _DEFAULT_MAX_POIS)

    if mode == "text":
        city = params.get("city")
        if not city:
            raise ValueError("text 模式需要 city（限定城市，如 '深圳'，防全国同名 POI）")
        p: dict[str, Any] = {"keywords": keywords or "", "types": types or "", "city": city, "citylimit": "true"}
        features = _paginate_search(ctx, "/v3/place/text", p, max_results)

    elif mode == "around":
        location = params.get("location") or params.get("center")
        lng, lat = _parse_lng_lat(location, "location")
        glng, glat = wgs84_to_gcj02(lng, lat)
        radius = int(params.get("radius") or params.get("radius_m") or 3000)
        p: dict[str, Any] = {
            "location": f"{glng:.6f},{glat:.6f}", "radius": radius,
            "keywords": keywords or "", "types": types or "",
        }
        features = _paginate_search(ctx, "/v3/place/around", p, max_results)

    else:  # polygon
        bbox = _parse_bbox(params.get("bbox"))
        features = _crawl_polygon(ctx, bbox, types, keywords, max_results)

    geojson = {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "source": "amap",
            "mode": mode,
            "types": types,
            "keywords": keywords,
            "coordinate_system": "WGS-84 (converted from GCJ-02)",
        },
    }
    result = _finalize_geojson_result(ctx, command=f"poi_{mode}", params=params, geojson=geojson)
    if not result.success:
        return result
    result.output.setdefault("fetched", len(features))
    if len(features) >= max_results:
        result.message += f"（已达 {max_results} 条上限，可缩小范围/收窄类型）"
    return result


# ── 行政区划边界 ───────────────────────────────────────────────────────────
def _run_district(ctx: ToolContext, params: dict[str, Any]) -> ToolResult:
    keywords = params.get("keywords") or params.get("name")
    if not keywords:
        raise ValueError("district 需要 keywords（行政区名，如 '深圳市'/'南山区'）")
    subdistrict = int(params.get("subdistrict") or 0)
    data = _amap_get(ctx, "/v3/config/district", {
        "keywords": str(keywords), "subdistrict": subdistrict, "extensions": "all",
    })
    districts = data.get("districts") or []
    if not districts:
        raise ValueError(f"未找到行政区：{keywords}")
    ring_count = 0
    features: list[dict[str, Any]] = []
    for dist in districts:
        name = dist.get("name", keywords)
        adcode = dist.get("adcode", "")
        center = dist.get("center", "")
        polyline = dist.get("polyline") or ""
        # polyline: 多条环用 | 分隔（多面域），环内点用 ; 分隔，坐标用 , 分隔
        rings: list[list[Any]] = []
        for ring in str(polyline).split("|"):
            points = []
            for pair in ring.split(";"):
                pair = pair.strip()
                if not pair or "," not in pair:
                    continue
                try:
                    glng, glat = (float(x) for x in pair.split(",")[:2])
                except (ValueError, TypeError):
                    continue
                lng, lat = gcj02_to_wgs84(glng, glat)
                points.append([round(lng, 6), round(lat, 6)])
            if len(points) >= 4:
                rings.append(points)
        if not rings:
            continue
        ring_count += len(rings)
        geometry = {"type": "Polygon" if len(rings) == 1 else "MultiPolygon",
                    "coordinates": [rings] if len(rings) == 1 else [[r] for r in rings]}
        props: dict[str, Any] = {"name": name, "adcode": adcode, "level": dist.get("level", "")}
        if center:
            try:
                clng, clat = (float(x) for x in str(center).split(",")[:2])
                props["center_lng"], props["center_lat"] = gcj02_to_wgs84(clng, clat)
            except (ValueError, TypeError):
                pass
        features.append({"type": "Feature", "geometry": geometry, "properties": props})
    if not features:
        raise ValueError(f"行政区 {keywords} 无边界数据（高德边界只到区县一级）")
    geojson = {
        "type": "FeatureCollection",
        "features": features,
        "properties": {"source": "amap", "command": "district", "coordinate_system": "WGS-84"},
    }
    result = _finalize_geojson_result(ctx, command="district", params=params, geojson=geojson)
    if result.success:
        result.output["ring_count"] = ring_count
    return result


# ── 地理编码 / 逆地理编码 ─────────────────────────────────────────────────
def _run_geocode(ctx: ToolContext, params: dict[str, Any]) -> ToolResult:
    address = params.get("address")
    if not address:
        raise ValueError("geocode 需要 address（地址文本）")
    data = _amap_get(ctx, "/v3/geocode/geo", {
        "address": str(address), "city": str(params.get("city") or ""),
    })
    geocodes = data.get("geocodes") or []
    if not geocodes:
        raise ValueError(f"未解析出地址：{address}")
    results = []
    for gc in geocodes:
        location = gc.get("location") or ""
        try:
            glng, glat = (float(x) for x in str(location).split(",")[:2])
        except (ValueError, TypeError):
            continue
        lng, lat = gcj02_to_wgs84(glng, glat)
        results.append({
            "formatted_address": gc.get("formatted_address", ""),
            "location": [round(lng, 6), round(lat, 6)],
            "level": gc.get("level", ""),
            "adcode": gc.get("adcode", ""),
        })
    if not results:
        raise ValueError(f"地址 {address} 无坐标结果")
    return ToolResult(
        success=True,
        output={"results": results, "address": str(address)},
        message=f"已解析「{address}」→ {results[0]['formatted_address']} @ {results[0]['location']}",
    )


def _run_regeo(ctx: ToolContext, params: dict[str, Any]) -> ToolResult:
    location = params.get("location")
    lng, lat = _parse_lng_lat(location, "location")
    glng, glat = wgs84_to_gcj02(lng, lat)
    data = _amap_get(ctx, "/v3/geocode/regeo", {
        "location": f"{glng:.6f},{glat:.6f}", "extensions": "base",
    })
    regeocode = data.get("regeocode") or {}
    ac = regeocode.get("addressComponent") or {}
    def _pick(*keys: str) -> str:
        for k in keys:
            v = ac.get(k)
            if v in (None, "", [], {}):
                continue
            # 高德 addressComponent 的部分字段是 {name,type} 或 [{name,type}]，取 name 文本
            if isinstance(v, dict):
                name = v.get("name")
                if name:
                    return str(name)
                continue
            if isinstance(v, list) and v and isinstance(v[0], dict):
                name = v[0].get("name")
                if name:
                    return str(name)
                continue
            return str(v)
        return ""
    return ToolResult(
        success=True,
        output={
            "formatted_address": regeocode.get("formatted_address", ""),
            "address_component": {
                "province": _pick("province"), "city": _pick("city"),
                "district": _pick("district"), "township": _pick("township"),
                "adcode": _pick("adcode"), "neighborhood": _pick("neighborhood"),
            },
            "location": [round(lng, 6), round(lat, 6)],
        },
        message=f"该点位于 {regeocode.get('formatted_address', '（未知）')}",
    )


# ── 参数解析 ───────────────────────────────────────────────────────────────
def _parse_lng_lat(value: Any, name: str) -> tuple[float, float]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            pass
    if isinstance(value, str):
        parts = value.replace("[", "").replace("]", "").split(",")
        if len(parts) >= 2:
            try:
                return float(parts[0]), float(parts[1])
            except (TypeError, ValueError):
                pass
    raise ValueError(f"{name} 需要是 [经度, 纬度]（WGS-84）")


def _parse_bbox(value: Any) -> list[float]:
    """bbox 接受 [西,南,东,北] 或 [xmin,ymin,xmax,ymax]。"""
    if isinstance(value, (list, tuple)) and len(value) == 4:
        try:
            west, south, east, north = (float(x) for x in value)
        except (TypeError, ValueError):
            raise ValueError("bbox 需要 4 个数字") from None
    elif isinstance(value, str):
        parts = value.replace("[", "").replace("]", "").split(",")
        if len(parts) != 4:
            raise ValueError(f"bbox 需要 4 个数字，收到：{value}")
        try:
            west, south, east, north = (float(x) for x in parts)
        except (TypeError, ValueError):
            raise ValueError(f"bbox 需要 4 个数字，收到：{value}") from None
    else:
        raise ValueError("bbox 需要是 [西经, 南纬, 东经, 北纬]")
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("bbox 范围非法：应为 [西经, 南纬, 东经, 北纬]（WGS-84）")
    return [west, south, east, north]


class AMapProvider(DataProvider):
    provider_id = "amap"
    commands = AMAP_COMMANDS

    def execute(self, command: str, params: dict, ctx: ToolContext) -> ToolResult:
        if command not in AMAP_COMMANDS:
            return ToolResult(
                success=False,
                message=f"未知高德命令：{command}（可用：{', '.join(AMAP_COMMANDS)}）",
            )
        if not _amap_key():
            return ToolResult(
                success=False,
                output={"error": "amap_no_key"},
                message="未配置 AMAP_API_KEY：请在 apps/api/.env 加 AMAP_API_KEY=<高德开放平台 Web 服务 key> 后重启后端",
            )
        try:
            return self._dispatch(command, params, ctx)
        except _QuotaExceeded as exc:
            return _error_result(command, exc)
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as exc:
            return _error_result(command, exc)
        except ValueError as exc:
            return _invalid_params_result(command, str(exc))
        except Exception as exc:  # noqa: BLE001 解析/写入兜底
            logger.warning("[amap] %s failed: %s", command, exc)
            return _error_result(command, exc)

    def _dispatch(self, command: str, params: dict, ctx: ToolContext) -> ToolResult:
        if command == "poi_search":
            return _run_poi_search(ctx, params)
        if command == "district":
            return _run_district(ctx, params)
        if command == "geocode":
            return _run_geocode(ctx, params)
        if command == "regeo":
            return _run_regeo(ctx, params)
        return _invalid_params_result(command, f"未处理的命令：{command}")
