# -*- coding: utf-8 -*-
"""商业供给格局确定性 Skill。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS
from shapely.geometry import Point, box

from app.gis.commercial_poi import (
    COMMERCIAL_CATEGORY_COLORS,
    COMMERCIAL_CATEGORY_ORDER,
    load_classified_commercial_points,
)


ANALYSIS_CRS = "EPSG:32650"
OUTPUT_CRS = "EPSG:4326"
GRID_VERSION = "grid_v1_100m_epsg32650"
STUDY_AREA_ID = "candidate_lohas_study_area"
DEFAULT_STUDY_AREA = "docs/workstreams/analysis-foundation/data/candidate_lohas_study_area.geojson"
DEFAULT_COMMERCIAL = "../04-乐活大道GIS数据/00-原始数据/专项资源/商业资源/商业资源Export_Output_46.shp"
DEFAULT_COMMERCIAL_LINK = "../04-乐活大道GIS数据/00-原始数据/专项资源/商业资源/商业资源_商业中小类_连接表_utf8.csv"
POI_LAYER_ID = "commercial_points_study"


class AnalysisInputError(ValueError):
    """输入或口径不满足本 Skill 契约。"""


def stable_cell_id(easting: float, northing: float, cell_size_m: int = 100) -> str:
    return f"g_{math.floor(easting / cell_size_m)}_{math.floor(northing / cell_size_m)}"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[7]


def _resolve_path(repo_root: Path, raw: Any, default: str) -> Path:
    text = str(raw or default).strip()
    if not text:
        raise AnalysisInputError("输入路径不能为空")
    path = Path(text)
    resolved = path.resolve() if path.is_absolute() else (repo_root / path).resolve()
    if not resolved.is_file():
        raise AnalysisInputError(f"输入文件不存在：{resolved}")
    return resolved


def _crs(value: Any, label: str) -> CRS:
    text = str(value or "").strip()
    if not text:
        raise AnalysisInputError(f"{label} CRS 未显式提供")
    try:
        return CRS.from_user_input(text)
    except Exception as exc:
        raise AnalysisInputError(f"{label} CRS 无法解析：{text}") from exc


def _require_file_crs(gdf: gpd.GeoDataFrame, expected: CRS, label: str) -> None:
    if gdf.crs is None:
        raise AnalysisInputError(f"{label}文件没有 CRS 元数据")
    actual = CRS.from_user_input(gdf.crs)
    if actual != expected:
        raise AnalysisInputError(
            f"{label} CRS 不一致：文件为 {actual.to_string()}，参数声明为 {expected.to_string()}"
        )


def _require_point_geometry(gdf: gpd.GeoDataFrame, label: str) -> None:
    if gdf.empty:
        raise AnalysisInputError(f"{label}没有记录")
    if gdf.geometry.isna().any() or gdf.geometry.is_empty.any():
        raise AnalysisInputError(f"{label}存在空几何")
    if not gdf.geometry.is_valid.all():
        raise AnalysisInputError(f"{label}存在无效几何")
    types = set(gdf.geometry.geom_type.unique())
    if not types.issubset({"Point"}):
        raise AnalysisInputError(f"{label}必须全部为 Point，实际为 {sorted(types)}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version(path: Path) -> dict[str, Any]:
    if path.suffix.lower() == ".shp":
        component_paths = [
            path.with_suffix(suffix)
            for suffix in (".shp", ".dbf", ".shx", ".prj", ".cpg")
            if path.with_suffix(suffix).is_file()
        ]
    else:
        component_paths = [path]
    components = [
        {
            "name": item.name,
            "size_bytes": item.stat().st_size,
            "mtime_ns": item.stat().st_mtime_ns,
            "sha256": _sha256(item),
        }
        for item in component_paths
    ]
    dataset_digest = hashlib.sha256()
    for item in components:
        dataset_digest.update(item["name"].encode("utf-8"))
        dataset_digest.update(item["sha256"].encode("ascii"))
    return {
        "path": str(path),
        "size_bytes": sum(item["size_bytes"] for item in components),
        "mtime_ns": max(item["mtime_ns"] for item in components),
        "sha256": dataset_digest.hexdigest(),
        "components": components,
    }


def _make_grid(boundary: Any, cell_size_m: int) -> gpd.GeoDataFrame:
    minx, miny, maxx, maxy = boundary.bounds
    gx_min = math.floor(minx / cell_size_m)
    gx_max = math.floor(maxx / cell_size_m)
    gy_min = math.floor(miny / cell_size_m)
    gy_max = math.floor(maxy / cell_size_m)
    rows: list[dict[str, Any]] = []
    for gx in range(gx_min, gx_max + 1):
        x0 = gx * cell_size_m
        for gy in range(gy_min, gy_max + 1):
            y0 = gy * cell_size_m
            center = Point(x0 + cell_size_m / 2, y0 + cell_size_m / 2)
            if not boundary.covers(center):
                continue
            rows.append(
                {
                    "cell_id": f"g_{gx}_{gy}",
                    "grid_x": gx,
                    "grid_y": gy,
                    "geometry": box(x0, y0, x0 + cell_size_m, y0 + cell_size_m),
                }
            )
    if not rows:
        raise AnalysisInputError("研究范围内没有生成任何 100m 格心网格")
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=ANALYSIS_CRS).sort_values(
        ["grid_x", "grid_y"], kind="stable"
    ).reset_index(drop=True)


def _assign_cells(points: gpd.GeoDataFrame, boundary: Any, allowed: set[str], cell_size_m: int) -> tuple[gpd.GeoDataFrame, int]:
    inside = points.loc[points.geometry.within(boundary)].copy()
    boundary_count = len(inside)
    inside["cell_id"] = [
        stable_cell_id(x, y, cell_size_m)
        for x, y in zip(inside.geometry.x.to_numpy(), inside.geometry.y.to_numpy())
    ]
    return inside.loc[inside["cell_id"].isin(allowed)].copy(), boundary_count


def _median_threshold(values: pd.Series, label: str, *, positive_only: bool = False) -> tuple[float, int]:
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric.loc[numeric.notna()]
    if positive_only:
        numeric = numeric.loc[numeric > 0]
    if len(numeric) < 10:
        raise AnalysisInputError(f"{label}有效网格少于 10 个，不能计算稳定中位数阈值")
    return float(numeric.median()), int(len(numeric))


def _supply_stats(frame: pd.DataFrame, field: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for code, key, label in (("H", "high_supply", "高供给"), ("L", "low_supply", "低供给")):
        part = frame.loc[frame[field] == code]
        result[key] = {
            "label": label,
            "grid_count": int(len(part)),
            "area_km2": round(len(part) * 0.01, 4),
            "commercial_points": int(part["commercial_count"].sum()),
            "mean_density_km2": round(float(part["commercial_density_km2"].mean()), 6) if len(part) else 0.0,
        }
    return result


def _category_breakdown(frame: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    """按稳定商业大类顺序统计点数，返回 [{category, count}]（含 0 计数的已知类）。"""
    counts = frame["category"].value_counts().to_dict()
    return [
        {"category": cat, "count": int(counts.get(cat, 0))}
        for cat in COMMERCIAL_CATEGORY_ORDER
    ]


def _write_point_geojson(layer_dir: Path, layer_id: str, frame: gpd.GeoDataFrame) -> int:
    """把分类商业点 GeoDataFrame 写成 GeoJSON 图层文件，返回要素数。"""
    geojson = json.loads(
        frame[["objectid", "name", "category", "geometry"]].to_json(
            drop_id=True, na="null"
        )
    )
    geojson["name"] = layer_id
    geojson["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}
    (layer_dir / f"{layer_id}.json").write_text(
        json.dumps(geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return len(geojson.get("features") or [])


def run_analysis(params: dict[str, Any], workspace: Path) -> dict[str, Any]:
    repo_root = _repo_root()
    study_path = _resolve_path(repo_root, params.get("study_area_path"), DEFAULT_STUDY_AREA)
    commercial_path = _resolve_path(repo_root, params.get("commercial_path"), DEFAULT_COMMERCIAL)
    commercial_link_path = _resolve_path(repo_root, params.get("commercial_link_path"), DEFAULT_COMMERCIAL_LINK)

    study_crs = _crs(params.get("study_area_crs", OUTPUT_CRS), "研究范围")
    commercial_crs = _crs(params.get("commercial_crs", "EPSG:4547"), "商业")

    cell_size_m = int(params.get("cell_size_m", 100))
    if cell_size_m != 100:
        raise AnalysisInputError("本 Skill 固定使用 100m 网格，cell_size_m 必须为 100")

    study = gpd.read_file(study_path)
    _require_file_crs(study, study_crs, "研究范围")
    if study.empty or study.geometry.isna().any() or study.geometry.is_empty.any():
        raise AnalysisInputError("研究范围没有可用几何")
    if not study.geometry.is_valid.all():
        raise AnalysisInputError("研究范围存在无效几何")
    if not set(study.geometry.geom_type.unique()).issubset({"Polygon", "MultiPolygon"}):
        raise AnalysisInputError("研究范围必须为 Polygon 或 MultiPolygon")
    study_m = study.to_crs(ANALYSIS_CRS)
    boundary = study_m.geometry.union_all()
    boundary_area_km2 = float(boundary.area / 1_000_000)
    if boundary_area_km2 <= 0:
        raise AnalysisInputError("研究范围面积无效")

    active_field = "isdelete"
    commercial = gpd.read_file(commercial_path, columns=[active_field])
    _require_file_crs(commercial, commercial_crs, "商业")
    if active_field not in commercial.columns:
        raise AnalysisInputError(f"商业数据缺少必需字段 {active_field}")
    _require_point_geometry(commercial, "商业数据")
    commercial_active = pd.to_numeric(commercial[active_field], errors="coerce")
    if commercial_active.isna().any():
        raise AnalysisInputError("商业字段 isdelete 含不可解析值")
    commercial_filtered = commercial.loc[commercial_active == 0].copy().to_crs(ANALYSIS_CRS)
    if commercial_filtered.empty:
        raise AnalysisInputError("商业数据按 isdelete=0 过滤后为空")

    grid = _make_grid(boundary, cell_size_m)
    allowed_cells = set(grid["cell_id"])
    commercial_assigned, commercial_in_boundary = _assign_cells(
        commercial_filtered, boundary, allowed_cells, cell_size_m
    )
    if len(commercial_assigned) < 10:
        raise AnalysisInputError("研究范围稳定网格内的商业点少于 10 个")

    commercial_counts = commercial_assigned.groupby("cell_id", sort=True).size().rename("commercial_count")
    grid = grid.join(commercial_counts, on="cell_id")
    grid["commercial_count"] = grid["commercial_count"].fillna(0).astype(int)
    grid["commercial_density_km2"] = grid["commercial_count"] / 0.01

    supply_threshold, positive_supply_cells = _median_threshold(
        grid["commercial_density_km2"], "商业正密度", positive_only=True
    )
    grid["supply_level"] = np.where(
        grid["commercial_density_km2"] >= supply_threshold, "H", "L"
    )

    grid_count = len(grid)
    if int(grid["supply_level"].value_counts(dropna=False).sum()) != grid_count:
        raise RuntimeError("supply_level 分类数量不守恒")

    centers = gpd.GeoSeries(
        [Point((gx + 0.5) * cell_size_m, (gy + 0.5) * cell_size_m) for gx, gy in zip(grid.grid_x, grid.grid_y)],
        crs=ANALYSIS_CRS,
    ).to_crs(OUTPUT_CRS)
    grid["center_lon"] = centers.x.to_numpy()
    grid["center_lat"] = centers.y.to_numpy()
    grid["grid_version"] = GRID_VERSION
    grid["study_area_id"] = STUDY_AREA_ID

    supply_classes = _supply_stats(grid, "supply_level")
    total_commercial_points = int(grid["commercial_count"].sum())
    stats = {
        "grid_version": GRID_VERSION,
        "cell_size_m": cell_size_m,
        "grid_count": grid_count,
        "standard_grid_area_km2": round(grid_count * 0.01, 4),
        "study_area_geometry_km2": round(boundary_area_km2, 6),
        "total_commercial_points": total_commercial_points,
        "thresholds": {
            "commercial_density_km2": round(supply_threshold, 6),
            "method": "median of positive-density cells; >= is high supply",
            "positive_supply_cells": positive_supply_cells,
        },
        "inputs": {
            "commercial_source_records": int(len(commercial)),
            "commercial_isdelete_0_records": int(len(commercial_filtered)),
            "commercial_points_in_boundary": int(commercial_in_boundary),
            "commercial_points_assigned_to_center_selected_grid": int(len(commercial_assigned)),
            "commercial_points_aggregated": total_commercial_points,
        },
        "supply_classes": supply_classes,
    }

    versions = {
        "study_area": _version(study_path),
        "commercial": _version(commercial_path),
        "commercial_link": _version(commercial_link_path),
    }
    evidence = {
        "input_versions": versions,
        "crs": {
            "study_area_file": CRS.from_user_input(study.crs).to_string(),
            "commercial_file": CRS.from_user_input(commercial.crs).to_string(),
            "analysis": ANALYSIS_CRS,
            "output": OUTPUT_CRS,
        },
        "filters": ["commercial.isdelete = 0", "point geometry within candidate study boundary", "grid center within candidate study boundary"],
        "aggregation": "commercial = count per stable cell; density = count / 0.01 km2",
        "classification": "positive-density median; threshold equality is high supply",
        "excluded_inputs": ["*_activity_type fields", "historical supply-demand layers", "facility-density derivative layers", "report conclusions"],
        "limitations": [
            "商业点数量不等于营业额或客流量，也不代表消费规模",
            "不能根据商业点数量判断经营好坏或门店经营绩效",
            "不能推断消费等级、客单价或完整业态结构",
            "商业点密度是相对供给强度，不能直接称为商业繁荣度",
            "结果仅描述商业供给的相对高低，不证明任何因果",
        ],
    }

    layer_id = str(params.get("output_layer_id") or "commercial_supply_profile_100m").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", layer_id):
        raise AnalysisInputError("output_layer_id 必须为小写字母开头且仅含小写字母、数字、_、-")
    layer_dir = (workspace / "data" / "layers").resolve()
    layer_dir.mkdir(parents=True, exist_ok=True)
    layer_path = (layer_dir / f"{layer_id}.json").resolve()
    if layer_dir not in layer_path.parents:
        raise AnalysisInputError("结果图层路径越出 workspace")

    output_columns = [
        "cell_id", "grid_x", "grid_y", "grid_version", "study_area_id",
        "commercial_count", "commercial_density_km2", "supply_level",
        "center_lon", "center_lat", "geometry",
    ]
    output_grid = grid[output_columns].to_crs(OUTPUT_CRS)
    geojson = json.loads(output_grid.to_json(drop_id=True, na="null"))
    geojson["name"] = layer_id
    geojson["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}
    layer_path.write_text(json.dumps(geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    fields = [
        {"name": "cell_id", "type": "string", "description": "全局锚定稳定网格 ID"},
        {"name": "commercial_count", "type": "integer", "description": "isdelete=0 商业点数量"},
        {"name": "commercial_density_km2", "type": "number", "description": "商业点密度（个/km²）"},
        {"name": "supply_level", "type": "string", "description": "供给高低分类（H=高供给，L=低供给）"},
    ]

    # 分类商业 POI 点图层（与网格同口径：isdelete=0 → EPSG:32650 → within 研究范围）。
    poi = load_classified_commercial_points(commercial_path, commercial_link_path, boundary)
    poi_count = _write_point_geojson(layer_dir, POI_LAYER_ID, poi)
    category_breakdown = _category_breakdown(poi)
    stats["category_breakdown"] = category_breakdown
    stats["commercial_points_in_boundary_category"] = {
        "total": poi_count,
        "by_category": {item["category"]: item["count"] for item in category_breakdown},
    }

    poi_fields = [
        {"name": "objectid", "type": "string", "description": "原始 OBJECTID（可追溯标识）"},
        {"name": "name", "type": "string", "description": "商业点名称（源字段，可能为空）"},
        {"name": "category", "type": "string", "description": "商业大类（购物/餐饮/生活/体育休闲等）"},
    ]
    poi_style_classes = [
        {"value": cat, "color": COMMERCIAL_CATEGORY_COLORS[cat]}
        for cat in COMMERCIAL_CATEGORY_ORDER
    ]
    layers = [
        {
            "layer_id": layer_id,
            "name": "商业供给格局",
            "path": f"data/layers/{layer_id}.json",
            "feature_count": grid_count,
            "geometry": "Polygon",
            "crs": OUTPUT_CRS,
            "fields": fields,
            "style": {"mode": "categorized", "field": "supply_level"},
            "description": "100m 全局锚定格网；格心入区；isdelete=0 商业点计数与正密度中位数高低供给分型",
        },
        {
            "layer_id": POI_LAYER_ID,
            "name": "分类商业 POI",
            "path": f"data/layers/{POI_LAYER_ID}.json",
            "feature_count": poi_count,
            "geometry": "Point",
            "crs": OUTPUT_CRS,
            "fields": poi_fields,
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
    return {
        "success": True,
        "summary": (
            f"商业供给格局已完成：{grid_count} 个稳定 100m 网格，"
            f"高供给 {supply_classes['high_supply']['grid_count']} 格、低供给 {supply_classes['low_supply']['grid_count']} 格；"
            f"研究范围内 {poi_count} 个分类商业点（购物/餐饮/生活/体育休闲等）。"
            "商业点数量不等于营业额，结果仅描述相对供给强度。"
        ),
        "layer_id": layer_id,
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
            raise AnalysisInputError("params 必须为对象")
        workspace_raw = str(wrapper.get("workspace") or "").strip()
        if not workspace_raw:
            raise AnalysisInputError("input wrapper 缺少 workspace")
        result = run_analysis(params, Path(workspace_raw).resolve())
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
    except AnalysisInputError as exc:
        _write_failure(output_path, str(exc), "STOP_INVALID_INPUT")
    except Exception as exc:
        _write_failure(output_path, f"分析执行失败：{type(exc).__name__}: {exc}", "STOP_EXECUTION_ERROR")


if __name__ == "__main__":
    main()
