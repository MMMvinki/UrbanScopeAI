# -*- coding: utf-8 -*-
"""商业 × 轨交真实步行可达性 Skill。

从真实原始数据出发，跑一条「分类商业 POI + 地铁出入口 + 5/10/15 分钟真实路网等时圈」
的纵向链：受控选取最多 5 个真实地铁出入口作为 Mapbox 步行等时圈请求中心，再对选定
范围内的商业 POI 做累计空间统计（5 分钟内 / 10 分钟累计 / 15 分钟累计 / 15 分钟外）。

等时圈复用 ``app.tools.isochrone`` 的 Mapbox 请求实现（profile=walking），不新建路网
算法；真实请求失败时诚实停止（success=false），绝不走 coverage 的演示覆盖率兜底。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS
from shapely import union_all
from shapely.geometry import mapping, shape

from app.gis.commercial_poi import (
    COMMERCIAL_CATEGORY_COLORS,
    COMMERCIAL_CATEGORY_ORDER,
    load_classified_commercial_points,
)
from app.tools.isochrone import _call_isochrone, _mapbox_token

ANALYSIS_CRS = "EPSG:32650"
OUTPUT_CRS = "EPSG:4326"

DEFAULT_STUDY_AREA = "docs/workstreams/analysis-foundation/data/candidate_lohas_study_area.geojson"
DEFAULT_COMMERCIAL = "../04-乐活大道GIS数据/00-原始数据/专项资源/商业资源/商业资源Export_Output_46.shp"
DEFAULT_COMMERCIAL_LINK = "../04-乐活大道GIS数据/00-原始数据/专项资源/商业资源/商业资源_商业中小类_连接表_utf8.csv"
DEFAULT_SUBWAY = "../04-乐活大道GIS数据/00-原始数据/专项资源/地铁出入口/subway_entrance.shp"

ISOCHRONE_MINUTES = [5, 10, 15]
DEFAULT_MAX_CENTERS = 5
ISOCHRONE_PROFILE = "walking"

ENTRANCE_LAYER_ID = "subway_entrance_study"
POI_LAYER_ID = "commercial_points_study"
ISO_LAYER_ID = "commercial_rail_walking_isochrone"

# 等时圈分钟档 → 同色系（内→外 深→浅）；legend 按 contour 分 3 类。
ISOCHRONE_COLORS = {"5": "#0c4a6e", "10": "#0ea5e9", "15": "#bae6fd"}
ENTRANCE_COLOR = "#1f2937"


class AccessibilityError(ValueError):
    """输入或口径不满足本 Skill 契约。"""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[7]


def _resolve_path(repo_root: Path, raw: Any, default: str) -> Path:
    text = str(raw or default).strip()
    if not text:
        raise AccessibilityError("输入路径不能为空")
    path = Path(text)
    resolved = path.resolve() if path.is_absolute() else (repo_root / path).resolve()
    if not resolved.is_file():
        raise AccessibilityError(f"输入文件不存在：{resolved}")
    return resolved


def _crs(value: Any, label: str) -> CRS:
    text = str(value or "").strip()
    if not text:
        raise AccessibilityError(f"{label} CRS 未显式提供")
    try:
        return CRS.from_user_input(text)
    except Exception as exc:
        raise AccessibilityError(f"{label} CRS 无法解析：{text}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".shp":
        parts = [
            path.with_suffix(s)
            for s in (".shp", ".dbf", ".shx", ".prj", ".cpg")
            if path.with_suffix(s).is_file()
        ]
    else:
        parts = [path]
    components = [
        {"name": p.name, "size_bytes": p.stat().st_size, "sha256": _sha256(p)}
        for p in parts
    ]
    digest = hashlib.sha256()
    for item in components:
        digest.update(item["name"].encode("utf-8"))
        digest.update(item["sha256"].encode("ascii"))
    return {
        "path": str(path),
        "size_bytes": sum(i["size_bytes"] for i in components),
        "sha256": digest.hexdigest(),
        "components": components,
    }


def _clean_value(v: Any) -> str:
    """清理字段值：None / NaN / 空 → 空串，绝不输出 'nan'。"""
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(v).strip()
    if text.lower() in ("nan", "none", "null", ""):
        return ""
    return text


def clean_entrance_label(raw_ref: Any, raw_name: Any) -> str:
    """地铁出入口显示标签：优先 ref（出口编号），否则 name；都缺则统一「地铁出入口」。

    出口编号（C/D1 等）只作为出入口标识，不作为车站名；缺值不出现 'nan'。
    """
    code = _clean_value(raw_ref) or _clean_value(raw_name)
    if code:
        return f"出入口 {code}"
    return "地铁出入口"


def load_entrances(subway_path: Path, boundary_4326: Any) -> gpd.GeoDataFrame:
    """读取并裁剪地铁出入口（原始 EPSG:4326），输出研究范围内点 + 清理后的标签。"""
    entrances = gpd.read_file(subway_path)
    if entrances.empty:
        raise AccessibilityError("地铁出入口数据为空")
    if entrances.crs is None:
        raise AccessibilityError("地铁出入口文件没有 CRS 元数据")
    if CRS.from_user_input(entrances.crs) != CRS.from_user_input(OUTPUT_CRS):
        raise AccessibilityError(
            f"地铁出入口 CRS 应为 {OUTPUT_CRS}，实际为 {CRS.from_user_input(entrances.crs).to_string()}"
        )
    inside = entrances.loc[entrances.geometry.within(boundary_4326)].copy()
    if inside.empty:
        raise AccessibilityError("研究范围内没有地铁出入口")

    ref = inside["ref"].apply(_clean_value) if "ref" in inside.columns else pd.Series([""] * len(inside))
    name = inside["name"].apply(_clean_value) if "name" in inside.columns else pd.Series([""] * len(inside))
    inside["label"] = [clean_entrance_label(r, n) for r, n in zip(ref, name)]
    inside["ref_clean"] = ref.to_numpy()
    inside["name_clean"] = name.to_numpy()
    # 可追溯原始标识：优先 OSM @id（node/…），缺失回退 FID / 行号。
    if "@id" in inside.columns:
        id_series = inside["@id"].astype(str)
    elif "FID" in inside.columns:
        id_series = inside["FID"].astype(str)
    else:
        id_series = inside.index.astype(str)
    inside["entrance_id"] = id_series.to_numpy()
    cols = ["entrance_id", "label", "ref_clean", "name_clean", "geometry"]
    return gpd.GeoDataFrame(inside[cols], crs=OUTPUT_CRS)


def select_center_entrances(
    entrances_4326: gpd.GeoDataFrame,
    poi_4326: gpd.GeoDataFrame,
    max_centers: int,
) -> gpd.GeoDataFrame:
    """确定性选取最多 max_centers 个真实出入口：按 500 米直线范围商业 POI 数降序。

    每个中心都是真实出入口几何（不是任意几何中心）；超出硬上限时截断到 max_centers，
    绝不把全范围 176 个出入口全部送入 Mapbox。
    """
    if max_centers <= 0:
        raise AccessibilityError("max_centers 必须为正整数")
    entrances_m = entrances_4326.to_crs(ANALYSIS_CRS)
    poi_m = poi_4326.to_crs(ANALYSIS_CRS)
    sindex = poi_m.sindex
    counts: list[int] = []
    for geom in entrances_m.geometry:
        buf = geom.buffer(500.0)
        possible = list(sindex.query(buf, predicate="intersects"))
        if not possible:
            counts.append(0)
            continue
        inside = poi_m.iloc[possible].geometry.within(buf)
        counts.append(int(inside.sum()))
    result = entrances_4326.copy()
    result["poi_500m"] = counts
    result = result.sort_values("poi_500m", ascending=False, kind="stable").head(max_centers)
    return result.reset_index(drop=True)


def _cat_counts(frame: gpd.GeoDataFrame) -> dict[str, int]:
    counts = frame["category"].value_counts().to_dict()
    return {cat: int(counts.get(cat, 0)) for cat in COMMERCIAL_CATEGORY_ORDER}


def cumulative_stats(
    poi_4326: gpd.GeoDataFrame,
    fused_unions_4326: dict[int, Any],
) -> dict[str, Any]:
    """累计空间统计：5 分钟内 / 10 分钟累计内 / 15 分钟累计内 / 15 分钟外。

    fused_unions 是「完整」并集多边形（5 ⊆ 10 ⊆ 15），统计口径天然累计。
    在 EPSG:32650 米制下做 ``within``，与商业网格裁剪同口径。
    """
    poi_m = poi_4326.to_crs(ANALYSIS_CRS)
    minutes = sorted(m for m in ISOCHRONE_MINUTES if fused_unions_4326.get(m) is not None)
    if not minutes:
        raise AccessibilityError("没有可用等时圈多边形，无法统计")

    def _to_m(geom_4326: Any) -> Any:
        return gpd.GeoSeries([geom_4326], crs=OUTPUT_CRS).to_crs(ANALYSIS_CRS).iloc[0]

    bands: dict[str, Any] = {}
    prev_count = 0
    union_m_by_min: dict[int, Any] = {}
    for m in minutes:
        union_m_by_min[m] = _to_m(fused_unions_4326[m])
    for m in minutes:
        inside = poi_m.loc[poi_m.geometry.within(union_m_by_min[m])]
        count = int(len(inside))
        bands[str(m)] = {
            "cumulative_count": count,
            "added_since_previous": count - prev_count,
            "by_category": _cat_counts(inside),
        }
        prev_count = count
    outermost = union_m_by_min[minutes[-1]]
    outside = poi_m.loc[~poi_m.geometry.within(outermost)]
    return {
        "total_commercial_points": int(len(poi_m)),
        "bands": bands,
        "outside_15min": {
            "count": int(len(outside)),
            "by_category": _cat_counts(outside),
        },
    }


def _merge_rings(by_contour: dict[int, list[Any]], minutes: list[int]) -> tuple[list[tuple[int, Any]], dict[int, Any]]:
    """把逐中心等时圈多边形融合成「环带」：返回 ([(分钟, 环带几何)], {分钟: 完整并集})。

    环带用于图层（相邻档相减，不重叠）；完整并集用于累计统计。
    """
    present = sorted(m for m in minutes if by_contour.get(m))
    fused: dict[int, Any] = {m: union_all(by_contour[m]) for m in present}
    rings: list[tuple[int, Any]] = []
    for i, m in enumerate(present):
        geom = fused[m] if i == 0 else fused[m].difference(fused[present[i - 1]])
        if geom.is_empty:
            continue
        rings.append((m, geom))
    return rings, fused


def _write_layer_geojson(layer_dir: Path, layer_id: str, geojson: dict) -> int:
    geojson["name"] = layer_id
    geojson["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}
    (layer_dir / f"{layer_id}.json").write_text(
        json.dumps(geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return len(geojson.get("features") or [])


def _gdf_to_geojson(frame: gpd.GeoDataFrame, cols: list[str]) -> dict:
    return json.loads(frame[cols + ["geometry"]].to_json(drop_id=True, na="null"))


def run_analysis(params: dict[str, Any], workspace: Path) -> dict[str, Any]:
    repo_root = _repo_root()
    study_path = _resolve_path(repo_root, params.get("study_area_path"), DEFAULT_STUDY_AREA)
    commercial_path = _resolve_path(repo_root, params.get("commercial_path"), DEFAULT_COMMERCIAL)
    commercial_link_path = _resolve_path(repo_root, params.get("commercial_link_path"), DEFAULT_COMMERCIAL_LINK)
    subway_path = _resolve_path(repo_root, params.get("subway_entrance_path"), DEFAULT_SUBWAY)

    study_crs = _crs(params.get("study_area_crs", OUTPUT_CRS), "研究范围")
    try:
        max_centers = int(params.get("max_centers", DEFAULT_MAX_CENTERS))
    except (TypeError, ValueError):
        raise AccessibilityError("max_centers 必须为整数")
    if max_centers <= 0:
        raise AccessibilityError("max_centers 必须为正整数")
    if max_centers > 5:
        # 硬上限：超过 5 个直接停止，避免长时间无反馈的外部请求。
        raise AccessibilityError("max_centers 硬上限为 5，请缩小选择范围或明确选择 1–5 个出入口")

    study = gpd.read_file(study_path)
    if study.crs is None:
        raise AccessibilityError("研究范围文件没有 CRS 元数据")
    if CRS.from_user_input(study.crs) != study_crs:
        raise AccessibilityError(
            f"研究范围 CRS 不一致：文件为 {CRS.from_user_input(study.crs).to_string()}，参数声明为 {study_crs.to_string()}"
        )
    if study.empty or study.geometry.isna().any() or study.geometry.is_empty.any():
        raise AccessibilityError("研究范围没有可用几何")
    boundary_4326 = study.geometry.union_all()
    boundary_m = study.to_crs(ANALYSIS_CRS).geometry.union_all()

    # 1) 分类商业 POI（与 commercial-supply-profile 同口径）
    poi = load_classified_commercial_points(commercial_path, commercial_link_path, boundary_m)

    # 2) 地铁出入口（研究范围内，清理标签）
    entrances = load_entrances(subway_path, boundary_4326)

    # 3) 确定性选取最多 5 个真实出入口中心
    centers = select_center_entrances(entrances, poi, max_centers)

    # 4) Mapbox 真实步行等时圈（复用 isochrone 的请求实现）
    if not _mapbox_token():
        raise AccessibilityError("未配置 MAPBOX_TOKEN：请在 apps/api/.env 加 MAPBOX_TOKEN 后重启后端")
    by_contour: dict[int, list[Any]] = {}
    success_centers: list[dict[str, Any]] = []
    failed_centers: list[dict[str, Any]] = []
    for _, row in centers.iterrows():
        lon, lat = row.geometry.x, row.geometry.y
        label = str(row["label"])
        try:
            payload = _call_isochrone(ISOCHRONE_PROFILE, float(lon), float(lat), ISOCHRONE_MINUTES, generalize=0)
        except Exception as exc:  # noqa: BLE001
            failed_centers.append({"label": label, "lon": lon, "lat": lat, "error": str(exc)})
            continue
        feats = payload.get("features") if isinstance(payload, dict) else []
        got = 0
        for feat in feats or []:
            contour = feat.get("properties", {}).get("contour")
            geom = feat.get("geometry")
            if contour is None or geom is None or geom.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            by_contour.setdefault(int(contour), []).append(shape(geom))
            got += 1
        success_centers.append(
            {
                "label": label,
                "lon": lon,
                "lat": lat,
                "contours": sorted({f["properties"]["contour"] for f in (feats or []) if f.get("properties", {}).get("contour") is not None}),
            }
        )

    if not by_contour:
        # 所有中心都失败：诚实停止，不使用 coverage 演示兜底。
        detail = "；".join(f"{c['label']}: {c['error']}" for c in failed_centers) or "中心点附近无路网可达"
        return {
            "success": False,
            "summary": f"真实步行等时圈请求失败，未生成可达范围：{detail}",
            "stop_code": "STOP_ISOCHRONE_FAILED",
            "layers": [],
            "stats": {},
            "evidence": {
                "service": "Mapbox Isochrone",
                "profile": ISOCHRONE_PROFILE,
                "requested_centers": len(centers),
                "failed_centers": failed_centers,
            },
        }

    # 5) 环带（图层）+ 完整并集（累计统计）
    rings, fused_unions = _merge_rings(by_contour, ISOCHRONE_MINUTES)
    if not rings:
        return {
            "success": False,
            "summary": "等时圈融合后无有效多边形",
            "stop_code": "STOP_ISOCHRONE_EMPTY",
            "layers": [],
            "stats": {},
            "evidence": {"failed_centers": failed_centers},
        }
    accessibility_stats = cumulative_stats(poi, fused_unions)
    accessibility_stats["total_by_category"] = _cat_counts(poi)

    layer_dir = (workspace / "data" / "layers").resolve()
    layer_dir.mkdir(parents=True, exist_ok=True)

    # 等时圈图层（环带，EPSG:4326，contour 分类同色系）
    iso_features = [
        {"type": "Feature", "properties": {"contour": m}, "geometry": mapping(geom)}
        for m, geom in rings
    ]
    iso_fc = {"type": "FeatureCollection", "features": iso_features}
    iso_count = _write_layer_geojson(layer_dir, ISO_LAYER_ID, iso_fc)

    # 地铁出入口点图层
    entrance_cols = ["entrance_id", "label", "ref_clean", "name_clean"]
    entrance_gj = _gdf_to_geojson(entrances, entrance_cols)
    entrance_count = _write_layer_geojson(layer_dir, ENTRANCE_LAYER_ID, entrance_gj)

    # 分类商业 POI 点图层
    poi_gj = _gdf_to_geojson(poi, ["objectid", "name", "category"])
    poi_count = _write_layer_geojson(layer_dir, POI_LAYER_ID, poi_gj)

    stats = {
        "entrances": {
            "source_records": 1159,  # 固定原始口径（原始文件 1,159 个出入口）
            "in_study_area": int(len(entrances)),
        },
        "centers": {
            "rule": "按 500 米直线范围内商业 POI 数降序选取真实出入口，最多 5 个",
            "requested": int(len(centers)),
            "success": len(success_centers),
            "failed": len(failed_centers),
            "selected": success_centers + failed_centers,
        },
        "accessibility": accessibility_stats,
        "crs": {"entrance": OUTPUT_CRS, "isochrone": OUTPUT_CRS, "commercial": OUTPUT_CRS},
    }

    evidence = {
        "input_versions": {
            "study_area": _version(study_path),
            "commercial": _version(commercial_path),
            "commercial_link": _version(commercial_link_path),
            "subway_entrance": _version(subway_path),
        },
        "crs": {
            "study_area_file": CRS.from_user_input(study.crs).to_string(),
            "subway_file": CRS.from_user_input(gpd.read_file(subway_path).crs).to_string(),
            "analysis": ANALYSIS_CRS,
            "output": OUTPUT_CRS,
        },
        "service": {
            "name": "Mapbox Isochrone",
            "profile": ISOCHRONE_PROFILE,
            "minutes": ISOCHRONE_MINUTES,
            "coordinate_system": OUTPUT_CRS,
        },
        "filters": [
            "commercial.isdelete = 0",
            "commercial point within candidate study boundary",
            "subway entrance within candidate study boundary",
        ],
        "aggregation": (
            "等时圈 = Mapbox 真实路网步行 5/10/15 分钟多边形；同分钟档跨中心取并集，"
            "相邻档相减成环带；累计统计 = 商业点在完整并集多边形内的 within 计数"
        ),
        "limitations": [
            "等时圈来自 Mapbox 真实路网步行（walking），非直线圆形缓冲",
            "本次只计算选定的 N 个出入口/出入口组，不代表研究范围全部出入口的覆盖",
            "出入口编号（C/D1 等）只作标识，不作为地铁站名",
            "商业点数量不等于营业额、经营质量或消费等级",
            "等时圈未叠加地铁站名归属（OSM 字段质量未核验前不上图做站名标注）",
        ],
        "excluded_inputs": [
            "coverage.stable_rate 演示覆盖率兜底",
            "500 米直线圆形冒充步行时间范围",
            "历史 PPT / 派生汇总表数字",
        ],
    }

    if failed_centers:
        evidence["failed_centers"] = failed_centers

    iso_style_classes = [
        {"value": str(m), "color": ISOCHRONE_COLORS[str(m)]}
        for m in sorted({m for m, _ in rings})
    ]
    poi_style_classes = [
        {"value": cat, "color": COMMERCIAL_CATEGORY_COLORS[cat]}
        for cat in COMMERCIAL_CATEGORY_ORDER
    ]
    layers = [
        {
            "layer_id": ISO_LAYER_ID,
            "name": "真实步行等时圈（5/10/15 分钟）",
            "path": f"data/layers/{ISO_LAYER_ID}.json",
            "feature_count": iso_count,
            "geometry": "Polygon",
            "crs": OUTPUT_CRS,
            "fields": [{"name": "contour", "type": "number", "description": "等时圈分钟"}],
            "style": {
                "mode": "categorized",
                "field": "contour",
                "classes": iso_style_classes,
                "fillOpacity": 0.5,
                "strokeWidth": 0,
            },
            "description": "Mapbox walking 5/10/15 分钟真实路网等时圈（环带，深→浅同色系）",
        },
        {
            "layer_id": ENTRANCE_LAYER_ID,
            "name": "地铁出入口",
            "path": f"data/layers/{ENTRANCE_LAYER_ID}.json",
            "feature_count": entrance_count,
            "geometry": "Point",
            "crs": OUTPUT_CRS,
            "fields": [
                {"name": "entrance_id", "type": "string", "description": "可追溯原始标识"},
                {"name": "label", "type": "string", "description": "清理后的出入口标签（不出现 nan）"},
            ],
            "style": {
                "fillColor": ENTRANCE_COLOR,
                "radius": 5,
                "strokeColor": "#ffffff",
                "strokeWidth": 1,
            },
            "description": "研究范围内地铁出入口（原始 OSM 提取，出口编号不作为站名）",
        },
        {
            "layer_id": POI_LAYER_ID,
            "name": "分类商业 POI",
            "path": f"data/layers/{POI_LAYER_ID}.json",
            "feature_count": poi_count,
            "geometry": "Point",
            "crs": OUTPUT_CRS,
            "fields": [
                {"name": "objectid", "type": "string", "description": "原始 OBJECTID"},
                {"name": "name", "type": "string", "description": "商业点名称"},
                {"name": "category", "type": "string", "description": "商业大类"},
            ],
            "style": {
                "mode": "categorized",
                "field": "category",
                "classes": poi_style_classes,
                "radius": 5,
                "strokeColor": "#ffffff",
                "strokeWidth": 1,
            },
            "description": "isdelete=0 分类商业点（研究范围内，商业大类配色）",
        },
    ]

    n_centers = len(success_centers)
    band = accessibility_stats["bands"]
    conclusion_parts = [
        f"本次真实计算 {n_centers} 个地铁出入口（或出入口组）"
    ]
    if failed_centers:
        conclusion_parts.append(f"，另有 {len(failed_centers)} 个请求失败")
    conclusion_parts.append("的 5/10/15 分钟真实步行等时圈")
    summary = (
        f"商业 × 轨交真实步行可达性已完成：研究范围内 {len(entrances)} 个地铁出入口、"
        f"{poi_count} 个分类商业点；选取 {n_centers} 个真实出入口计算 Mapbox walking "
        f"5/10/15 分钟等时圈；5 分钟内商业 {band.get('5', {}).get('cumulative_count', 0)} 个、"
        f"10 分钟累计 {band.get('10', {}).get('cumulative_count', 0)} 个、"
        f"15 分钟累计 {band.get('15', {}).get('cumulative_count', 0)} 个。"
        + ("（真实路网请求部分失败，详见限制说明。）" if failed_centers else "")
    )

    return {
        "success": True,
        "summary": summary,
        "layer_id": ISO_LAYER_ID,
        "stats": stats,
        "evidence": evidence,
        "layers": layers,
    }


def _write_failure(output_path: Path, message: str, stop_code: str) -> None:
    output_path.write_text(
        json.dumps(
            {
                "success": False,
                "summary": message,
                "stop_code": stop_code,
                "layers": [],
                "stats": {},
                "evidence": {},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output_path = Path(args.output)
    try:
        wrapper = json.loads(Path(args.input).read_text(encoding="utf-8"))
        params = wrapper.get("params") or {}
        if not isinstance(params, dict):
            raise AccessibilityError("params 必须为对象")
        workspace_raw = str(wrapper.get("workspace") or "").strip()
        if not workspace_raw:
            raise AccessibilityError("input wrapper 缺少 workspace")
        result = run_analysis(params, Path(workspace_raw).resolve())
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except AccessibilityError as exc:
        _write_failure(output_path, str(exc), "STOP_INVALID_INPUT")
    except Exception as exc:
        _write_failure(output_path, f"分析执行失败：{type(exc).__name__}: {exc}", "STOP_EXECUTION_ERROR")


if __name__ == "__main__":
    main()
