"""Mapbox Isochrone 等时圈工具：从中心点按真实路网算 N 分钟可达范围。

等时圈 = 按实际路网步行/驾车/骑行 N 分钟内可到达的区域（多边形），与 coverage
（直线欧氏缓冲）互补。调用 Mapbox Isochrone API（polygons=true 返回填充多边形，
原生 WGS-84 坐标，不重投影）。

一次生成【一个图层】：centers 可传多个中心点（如一片区域里的全部地铁站），所有
中心点 × 各分钟档的等时圈并入同一个图层，并**默认融合**（同档多边形取并集：一个
时间档一个形状，多档时相邻档相减成环带；merge=false 可关闭，保留一站一圈）。
单中心按 contour 分钟分类配色，多中心自动按 station 站点分类配色（可用 style_by
覆盖）。返回 output_layer_id，executor 据此自动发 GENERATED_LAYER 事件让图层上地图。

generalize 默认 0 = 不简化，多边形最精细（与 Mapbox 原始输出一致）；传大值
（如 100/300）会显著减少顶点数、颗粒变粗，一般只在数据量过大时才需要。
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.data_registry import DATA_REGISTRY
from app.geojson_utils import clean_layer_id
from app.models.layer import FieldSpec, GeometryType, LayerMetadata, LayerSource
from app.models.tool import ToolResult
from app.tools.context import get_current_context
from app.workspace import LAYER_DATA_DIR

_ISOCHRONE_URL = "https://api.mapbox.com/isochrone/v1/mapbox/{profile}/{lon:.6f},{lat:.6f}"
_PROFILES = ("walking", "driving", "cycling", "driving-traffic")
_UA = {"User-Agent": "UrbanScopeAI/0.1 (urbanscope-app)"}


def _mapbox_token() -> str:
    """复用 llm_client 的 _env 链（进程 env > apps/api/.env > 默认）。"""
    from app.agent import llm_client

    return llm_client._env("MAPBOX_TOKEN")


def _fail(message: str, retryable: bool = False) -> ToolResult:
    return ToolResult(
        success=False,
        output={"error": message, "retryable": retryable},
        message=message,
    )


def _call_isochrone(
    profile: str, lon: float, lat: float, minutes: list[int], generalize: int
) -> dict:
    """调用 Mapbox Isochrone API；失败抛异常（超时/HTTP 错误），由调用方兜底。"""
    import requests

    token = _mapbox_token()
    params = {
        "contours_minutes": ",".join(str(m) for m in minutes),
        # polygons=true 必须显式传：不传默认返回边界线（LineString），不是填充多边形
        "polygons": "true",
        "generalize": generalize,
        "access_token": token,
    }
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.get(
                _ISOCHRONE_URL.format(profile=profile, lon=lon, lat=lat),
                params=params,
                headers=_UA,
                timeout=20,
            )
            if resp.status_code in (429, 500, 502, 503, 504):
                raise RuntimeError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < 2:
                time.sleep(1 + attempt)  # 简单退避
    assert last_err is not None
    raise last_err


def _parse_centers(params: dict) -> list[dict] | None:
    """解析 centers / center，返回 [{lon, lat, name}]；缺失或非法返回 None。"""
    raw = params.get("centers")
    if raw is None:
        if params.get("center") is None:
            return None
        raw = [params["center"]]
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    centers: list[dict] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            return None
        try:
            lon, lat = float(item[0]), float(item[1])
        except (TypeError, ValueError):
            return None
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            return None
        name = str(item[2]).strip() if len(item) > 2 and str(item[2]).strip() else ""
        centers.append({"lon": lon, "lat": lat, "name": name})
    return centers


def run_isochrone(params: dict) -> ToolResult:
    """为给定中心点（可多个）生成路网等时圈，一次调用 = 一个图层。"""
    centers = _parse_centers(params)
    if not centers:
        return _fail(
            "缺少中心点：请传 centers = [[经度, 纬度, 可选名称], ...]（WGS84），"
            "如 [[114.06, 22.54, '华侨城站'], ...]；单个中心点也可传 center = [经度, 纬度]"
        )

    raw_minutes = params.get("minutes") or params.get("minute") or [15]
    if isinstance(raw_minutes, int):
        raw_minutes = [raw_minutes]
    minutes = sorted({int(m) for m in raw_minutes if str(m).strip().lstrip("-").isdigit() and 1 <= int(m) <= 120})
    if not minutes or len(minutes) > 4:
        return _fail("minutes 需要 1~4 个 1~120 的整数分钟（如 [5, 10, 15]）")

    profile = str(params.get("profile") or "walking")
    if profile not in _PROFILES:
        return _fail(f"profile 必须是 {'/'.join(_PROFILES)} 之一")
    try:
        generalize = int(params.get("generalize") or 0)
    except (TypeError, ValueError):
        generalize = 0

    if not _mapbox_token():
        return _fail("未配置 MAPBOX_TOKEN：请在 apps/api/.env 加 MAPBOX_TOKEN=<token> 后重启后端")

    # 1) 逐个中心点调 Mapbox API，全部并入同一个 FeatureCollection
    raw_name = str(params.get("name") or "").strip()
    base = raw_name.rstrip("等时圈").strip() or f"{profile}{'/'.join(str(m) for m in minutes)}分钟"
    multi = len(centers) > 1
    fc: dict = {"type": "FeatureCollection", "features": []}
    errors: list[str] = []
    for i, c in enumerate(centers):
        try:
            payload = _call_isochrone(profile, c["lon"], c["lat"], minutes, generalize)
        except Exception as exc:  # noqa: BLE001
            label = c["name"] or (base if not multi else f"中心{i + 1}")
            errors.append(f"{label}：{exc}")
            continue
        station = c["name"] or (base if not multi else f"中心{i + 1}")
        feats = payload.get("features") if isinstance(payload, dict) else []
        for feat in feats or []:
            props = feat.get("properties") or {}
            contour = props.get("contour")
            if contour is None:
                continue
            g = feat.get("geometry")
            if not g or g.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            fc["features"].append(
                {
                    "type": "Feature",
                    "properties": {"contour": int(contour), "station": station},
                    "geometry": g,
                }
            )
    if not fc["features"]:
        detail = "；".join(errors) if errors else "中心点附近无路网可达"
        return _fail(f"等时圈无有效多边形（{detail}），换个中心点试试", retryable=bool(errors))

    # 2) 融合（merge 默认开）：同一分钟档的多边形取并集 → 一个时间档一个融合形状，
    #    不再是一站一个圈。多档时相邻档相减成环带（5分钟=中心、15分钟=最外），
    #    避免整层叠色，与 Mapbox 官方等时圈的「档位带」展示一致。
    # 注意不能用 `params.get("merge") or "true"`：merge=False 是 falsy，会被 or 吞成默认 true。
    merge = (
        True
        if params.get("merge") is None
        else str(params.get("merge")).strip().lower() not in ("0", "false", "no", "off")
    )
    if merge:
        try:
            from shapely import union_all
            from shapely.geometry import mapping, shape

            by_contour: dict[int, list] = {}
            for feat in fc["features"]:
                by_contour.setdefault(feat["properties"]["contour"], []).append(feat)
            minutes_sorted = sorted(by_contour)
            fused: dict[int, Any] = {}
            for m in minutes_sorted:
                fused[m] = union_all([shape(f["geometry"]) for f in by_contour[m]])
            # 嵌套方向：fused[5] ⊆ fused[10] ⊆ fused[15]（同一站可达集随分钟增大而扩大，
            # 并集保序）。最内档（最小分钟）= 完整融合形状；其余档 = 与前一更小档相减成环带。
            merged_features: list[dict] = []
            for i, m in enumerate(minutes_sorted):
                if i == 0:
                    geom = fused[m]
                else:
                    geom = fused[m].difference(fused[minutes_sorted[i - 1]])
                if geom.is_empty:
                    continue
                props: dict[str, Any] = {"contour": m}
                if not multi:
                    props["station"] = centers[0]["name"]
                else:
                    props["station_count"] = len(by_contour[m])
                merged_features.append(
                    {"type": "Feature", "properties": props, "geometry": mapping(geom)}
                )
            fc["features"] = merged_features
        except Exception:  # noqa: BLE001
            pass  # 融合失败回退原样（仍一站一圈），不影响图层注册

    # 3) 注册生成图层（id 以 _iso 结尾，配合 _generated_info 自动拼「等时圈」）
    layer_id = clean_layer_id(f"{base}_iso")
    fields = [FieldSpec(name="contour", type="number", description="等时圈分钟")]
    if not multi:
        fields.append(FieldSpec(name="station", type="string", description="中心点/站点名称"))
    elif merge:
        fields.append(FieldSpec(name="station_count", type="number", description="参与融合的站点数"))
    else:
        fields.append(FieldSpec(name="station", type="string", description="中心点/站点名称"))
    layer = LayerMetadata(
        id=layer_id,
        name=base,
        source=LayerSource.generated,
        geometry=GeometryType.polygon,
        color="#0F172A",
        visible=True,
        feature_count=len(fc["features"]),
        crs="EPSG:4490",
        fields=fields,
        description=(
            f"{base}：{len(centers)} 个中心点的 {profile} "
            f"{'/'.join(str(m) for m in minutes)} 分钟等时圈"
            f"（{'成功 %d/%d' % (len(centers) - len(errors), len(centers)) if errors else ''}）"
        ),
        generated_by="isochrone 工具",
    )
    DATA_REGISTRY.register_generated(layer, geojson=fc)
    try:
        LAYER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        (LAYER_DATA_DIR / f"{layer_id}.json").write_text(
            json.dumps(fc, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass  # 物化失败不致命，内存注册已让地图可见

    # 3) 配色：默认按 contour 分钟统一配色（同时间档同色——单档整层一色、多档分钟越
    #    大颜色越深）；style_by=station 可改为每站一色。配色失败不影响图层注册。
    try:
        from app.tools.layer_style import (
            apply_renderer_style,
            apply_style_update,
            build_categorized_style,
        )

        emit = get_current_context().emit
        style_by = str(params.get("style_by") or "contour").strip()
        if style_by == "station":
            style = build_categorized_style(
                layer_id, "station", palette=str(params.get("palette") or "category10").strip()
            )
            apply_renderer_style(layer_id, style, emit=emit)
        else:
            unique_minutes = sorted({f["properties"]["contour"] for f in fc["features"]})
            if len(unique_minutes) == 1:
                # 单一时档：整层一个统一颜色（干净；palette 传 #hex 可自定义）
                single_color = str(params.get("palette") or "").strip()
                if len(single_color) != 7 or not single_color.startswith("#"):
                    single_color = "#3182bd"
                apply_style_update(layer_id, fillColor=single_color, emit=emit)
            else:
                # 多档：分钟升序 → 颜色由浅到深（用可见蓝梯度，跳过默认 blues 前段近白）。
                # 不用 build_categorized_style（它按要素频次排序，同频时分钟顺序不稳定，
                # 会让 5 分钟意外拿到最深色）；这里显式按分钟值排序保证渐变方向。
                from app.tools.layer_style import resolve_ramp

                ramp = str(
                    params.get("palette") or "#9ecae1,#6baed6,#3182bd,#08519c"
                ).strip()
                colors = resolve_ramp(ramp, len(unique_minutes))
                classes = [
                    {"value": str(m), "color": c}
                    for m, c in zip(unique_minutes, colors)
                ]
                style = {
                    "styleMode": "categorized",
                    "visualVariables": {
                        "renderer": "categorized",
                        "categorized": {"field": "contour", "classes": classes},
                    },
                }
                apply_renderer_style(layer_id, style, emit=emit)
    except Exception:  # noqa: BLE001
        pass

    label = "/".join(str(m) for m in minutes)
    center_out = [[c["lon"], c["lat"], c["name"]] for c in centers]
    return ToolResult(
        success=True,
        output={
            "layer_id": layer_id,
            "output_layer_id": layer_id,  # executor 生成图层检测依赖
            "name": base,
            "feature_count": len(fc["features"]),
            "profile": profile,
            "minutes": minutes,
            "centers": center_out,
            "center_count": len(centers),
            "failed_centers": len(errors),
        },
        message=(
            f"已生成 {profile} {label} 分钟等时圈图层（{len(centers)} 个中心点"
            f"{'，%d 个失败' % len(errors) if errors else ''}），一次调用一个图层"
        ),
    )
