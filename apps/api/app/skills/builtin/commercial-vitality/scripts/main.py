# -*- coding: utf-8 -*-
"""商业供给 × 昼夜活力确定性 Skill。"""

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


ANALYSIS_CRS = "EPSG:32650"
OUTPUT_CRS = "EPSG:4326"
GRID_VERSION = "grid_v1_100m_epsg32650"
STUDY_AREA_ID = "candidate_lohas_study_area"
DEFAULT_STUDY_AREA = "docs/workstreams/analysis-foundation/data/candidate_lohas_study_area.geojson"
DEFAULT_COMMERCIAL = "../04-乐活大道GIS数据/00-原始数据/专项资源/商业资源/商业资源Export_Output_46.shp"
DEFAULT_VITALITY = "../04-乐活大道GIS数据/00-原始数据/人口客流/研究范围人口数据全/客流分析/passenger_day_night_grid_avg_full_gcj02_to_wgs84.csv"
TIME_FIELDS = {
    "all": ("all_day_09_18_avg", "all_night_19_22_avg"),
    "wd": ("wd_day_09_18_avg", "wd_night_19_22_avg"),
    "we": ("we_day_09_18_avg", "we_night_19_22_avg"),
}
QUADRANT_ORDER = ("S_L_V_H", "S_H_V_L", "S_H_V_H", "S_L_V_L", "ND")
RHYTHM_ORDER = ("D_H_N_L", "D_L_N_H", "D_H_N_H", "D_L_N_L", "ND")


class AnalysisInputError(ValueError):
    """输入或口径不满足主场景契约。"""


def classify_supply_vitality(supply_high: bool, vitality_high: bool | None) -> str:
    if vitality_high is None:
        return "ND"
    if not supply_high and vitality_high:
        return "S_L_V_H"
    if supply_high and not vitality_high:
        return "S_H_V_L"
    if supply_high and vitality_high:
        return "S_H_V_H"
    return "S_L_V_L"


def classify_day_night(day_high: bool | None, night_high: bool | None) -> str:
    if day_high is None or night_high is None:
        return "ND"
    if day_high and not night_high:
        return "D_H_N_L"
    if not day_high and night_high:
        return "D_L_N_H"
    if day_high and night_high:
        return "D_H_N_H"
    return "D_L_N_L"


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


def _class_stats(frame: pd.DataFrame, field: str, order: tuple[str, ...], vitality_field: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for code in order:
        part = frame.loc[frame[field] == code]
        item: dict[str, Any] = {
            "grid_count": int(len(part)),
            "area_km2": round(len(part) * 0.01, 4),
            "commercial_points": int(part["commercial_count"].sum()),
        }
        if vitality_field and len(part) and part[vitality_field].notna().any():
            item["mean_vitality"] = round(float(part[vitality_field].mean()), 6)
        else:
            item["mean_vitality"] = None
        result[code] = item
    return result


def _priority_cells(frame: pd.DataFrame, field: str, vitality_field: str) -> list[dict[str, Any]]:
    part = frame.loc[frame[field] == "S_L_V_H"].sort_values(
        [vitality_field, "commercial_count", "cell_id"],
        ascending=[False, True, True],
        kind="stable",
    )
    return [
        {
            "cell_id": str(row.cell_id),
            "center_lon": round(float(row.center_lon), 7),
            "center_lat": round(float(row.center_lat), 7),
            "commercial_count": int(row.commercial_count),
            "commercial_density_km2": float(row.commercial_density_km2),
            "vitality": round(float(getattr(row, vitality_field)), 6),
        }
        for row in part.head(5).itertuples(index=False)
    ]


def run_analysis(params: dict[str, Any], workspace: Path) -> dict[str, Any]:
    repo_root = _repo_root()
    study_path = _resolve_path(repo_root, params.get("study_area_path"), DEFAULT_STUDY_AREA)
    commercial_path = _resolve_path(repo_root, params.get("commercial_path"), DEFAULT_COMMERCIAL)
    vitality_path = _resolve_path(repo_root, params.get("vitality_path"), DEFAULT_VITALITY)

    study_crs = _crs(params.get("study_area_crs", OUTPUT_CRS), "研究范围")
    commercial_crs = _crs(params.get("commercial_crs", "EPSG:4547"), "商业")
    vitality_crs = _crs(params.get("vitality_crs", OUTPUT_CRS), "活力")
    if vitality_crs != CRS.from_epsg(4326):
        raise AnalysisInputError("活力 lon/lat 当前只允许显式声明为 EPSG:4326")

    cell_size_m = int(params.get("cell_size_m", 100))
    if cell_size_m != 100:
        raise AnalysisInputError("本 Skill 固定使用 100m 网格，cell_size_m 必须为 100")
    time_scope = str(params.get("time_scope", "all")).strip().lower()
    if time_scope not in TIME_FIELDS:
        raise AnalysisInputError("time_scope 仅支持 all / wd / we")
    day_field, night_field = TIME_FIELDS[time_scope]

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

    vitality = pd.read_csv(vitality_path)
    required_vitality = {"grid_id", "lon", "lat", "coord_fix", day_field, night_field}
    missing = sorted(required_vitality.difference(vitality.columns))
    if missing:
        raise AnalysisInputError(f"活力数据缺少必需字段：{', '.join(missing)}")
    numeric_fields = ["lon", "lat", day_field, night_field]
    for field in numeric_fields:
        parsed = pd.to_numeric(vitality[field], errors="coerce")
        if parsed.isna().any() or not np.isfinite(parsed.to_numpy()).all():
            raise AnalysisInputError(f"活力字段 {field} 含空值或不可解析数值")
        vitality[field] = parsed
    coord_versions = sorted(set(vitality["coord_fix"].astype(str).str.strip()))
    if coord_versions != ["gcj02_to_wgs84"]:
        raise AnalysisInputError(
            "活力 coord_fix 必须唯一为 gcj02_to_wgs84，实际为：" + ", ".join(coord_versions)
        )
    if vitality["grid_id"].astype(str).duplicated().any():
        raise AnalysisInputError("活力 grid_id 存在重复")
    vitality_points = gpd.GeoDataFrame(
        vitality[["grid_id", day_field, night_field]].copy(),
        geometry=gpd.points_from_xy(vitality["lon"], vitality["lat"]),
        crs=vitality_crs,
    ).to_crs(ANALYSIS_CRS)
    _require_point_geometry(vitality_points, "活力数据")

    grid = _make_grid(boundary, cell_size_m)
    allowed_cells = set(grid["cell_id"])
    commercial_assigned, commercial_in_boundary = _assign_cells(
        commercial_filtered, boundary, allowed_cells, cell_size_m
    )
    vitality_assigned, vitality_in_boundary = _assign_cells(
        vitality_points, boundary, allowed_cells, cell_size_m
    )
    if len(vitality_assigned) < 10:
        raise AnalysisInputError("研究范围稳定网格内的活力点少于 10 个")

    commercial_counts = commercial_assigned.groupby("cell_id", sort=True).size().rename("commercial_count")
    vitality_grid = vitality_assigned.groupby("cell_id", sort=True).agg(
        day_vitality=(day_field, "mean"),
        night_vitality=(night_field, "mean"),
        vitality_source_points=("grid_id", "size"),
    )
    grid = grid.join(commercial_counts, on="cell_id").join(vitality_grid, on="cell_id")
    grid["commercial_count"] = grid["commercial_count"].fillna(0).astype(int)
    grid["vitality_source_points"] = grid["vitality_source_points"].fillna(0).astype(int)
    grid["commercial_density_km2"] = grid["commercial_count"] / 0.01

    supply_threshold, positive_supply_cells = _median_threshold(
        grid["commercial_density_km2"], "商业正密度", positive_only=True
    )
    day_threshold, day_valid_cells = _median_threshold(grid["day_vitality"], "昼间活力")
    night_threshold, night_valid_cells = _median_threshold(grid["night_vitality"], "夜间活力")
    grid["supply_level"] = np.where(
        grid["commercial_density_km2"] >= supply_threshold, "H", "L"
    )
    grid["day_level"] = np.where(
        grid["day_vitality"].isna(), "ND", np.where(grid["day_vitality"] >= day_threshold, "H", "L")
    )
    grid["night_level"] = np.where(
        grid["night_vitality"].isna(), "ND", np.where(grid["night_vitality"] >= night_threshold, "H", "L")
    )
    grid["supply_day_quadrant"] = [
        classify_supply_vitality(s == "H", None if d == "ND" else d == "H")
        for s, d in zip(grid["supply_level"], grid["day_level"])
    ]
    grid["supply_night_quadrant"] = [
        classify_supply_vitality(s == "H", None if n == "ND" else n == "H")
        for s, n in zip(grid["supply_level"], grid["night_level"])
    ]
    grid["day_night_type"] = [
        classify_day_night(None if d == "ND" else d == "H", None if n == "ND" else n == "H")
        for d, n in zip(grid["day_level"], grid["night_level"])
    ]

    grid_count = len(grid)
    for field in ("supply_day_quadrant", "supply_night_quadrant", "day_night_type"):
        if int(grid[field].value_counts(dropna=False).sum()) != grid_count:
            raise RuntimeError(f"{field} 分类数量不守恒")

    centers = gpd.GeoSeries(
        [Point((gx + 0.5) * cell_size_m, (gy + 0.5) * cell_size_m) for gx, gy in zip(grid.grid_x, grid.grid_y)],
        crs=ANALYSIS_CRS,
    ).to_crs(OUTPUT_CRS)
    grid["center_lon"] = centers.x.to_numpy()
    grid["center_lat"] = centers.y.to_numpy()
    grid["grid_version"] = GRID_VERSION
    grid["study_area_id"] = STUDY_AREA_ID
    grid["time_scope"] = time_scope

    day_stats = _class_stats(grid, "supply_day_quadrant", QUADRANT_ORDER, "day_vitality")
    night_stats = _class_stats(grid, "supply_night_quadrant", QUADRANT_ORDER, "night_vitality")
    rhythm_stats = _class_stats(grid, "day_night_type", RHYTHM_ORDER, None)
    stats = {
        "grid_version": GRID_VERSION,
        "cell_size_m": cell_size_m,
        "grid_count": grid_count,
        "standard_grid_area_km2": round(grid_count * 0.01, 4),
        "study_area_geometry_km2": round(boundary_area_km2, 6),
        "time_scope": time_scope,
        "time_fields": {"day": day_field, "night": night_field},
        "thresholds": {
            "commercial_density_km2": round(supply_threshold, 6),
            "day_vitality": round(day_threshold, 6),
            "night_vitality": round(night_threshold, 6),
            "method": "median; commercial uses positive-density cells; >= is high",
            "positive_supply_cells": positive_supply_cells,
            "day_valid_cells": day_valid_cells,
            "night_valid_cells": night_valid_cells,
        },
        "inputs": {
            "commercial_source_records": int(len(commercial)),
            "commercial_isdelete_0_records": int(len(commercial_filtered)),
            "commercial_points_in_boundary": int(commercial_in_boundary),
            "commercial_points_assigned_to_center_selected_grid": int(len(commercial_assigned)),
            "commercial_points_aggregated": int(grid["commercial_count"].sum()),
            "vitality_source_records": int(len(vitality)),
            "vitality_points_in_boundary": int(vitality_in_boundary),
            "vitality_points_assigned_to_center_selected_grid": int(len(vitality_assigned)),
            "vitality_aggregated_grid_count": int((grid["vitality_source_points"] > 0).sum()),
        },
        "supply_day_quadrants": day_stats,
        "supply_night_quadrants": night_stats,
        "day_night_types": rhythm_stats,
        "priority_cells": {
            "day_high_vitality_low_supply": _priority_cells(grid, "supply_day_quadrant", "day_vitality"),
            "night_high_vitality_low_supply": _priority_cells(grid, "supply_night_quadrant", "night_vitality"),
        },
    }

    versions = {
        "study_area": _version(study_path),
        "commercial": _version(commercial_path),
        "vitality": _version(vitality_path),
    }
    evidence = {
        "input_versions": versions,
        "crs": {
            "study_area_file": CRS.from_user_input(study.crs).to_string(),
            "commercial_file": CRS.from_user_input(commercial.crs).to_string(),
            "vitality_assumption": vitality_crs.to_string(),
            "analysis": ANALYSIS_CRS,
            "output": OUTPUT_CRS,
        },
        "filters": ["commercial.isdelete = 0", "point geometry within candidate study boundary", "grid center within candidate study boundary"],
        "aggregation": "commercial=count; day/night vitality=mean when multiple source points fall in one stable cell",
        "classification": "positive-supply median and separate day/night medians; threshold equality is high; missing vitality is ND",
        "excluded_inputs": ["*_activity_type fields", "historical supply-demand layers", "facility-density derivative layers", "report conclusions"],
        "limitations": [
            "gcj02_to_wgs84 is the current candidate conversion and has not been authoritatively confirmed by the data owner",
            "results describe spatial co-occurrence and relative high/low classes, not causation",
            "activity values are vitality indicators and are not asserted to be population or consumption",
        ],
    }

    layer_id = str(params.get("output_layer_id") or f"commercial_vitality_100m_{time_scope}").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", layer_id):
        raise AnalysisInputError("output_layer_id 必须为小写字母开头且仅含小写字母、数字、_、-")
    layer_dir = (workspace / "data" / "layers").resolve()
    layer_dir.mkdir(parents=True, exist_ok=True)
    layer_path = (layer_dir / f"{layer_id}.json").resolve()
    if layer_dir not in layer_path.parents:
        raise AnalysisInputError("结果图层路径越出 workspace")

    output_columns = [
        "cell_id", "grid_x", "grid_y", "grid_version", "study_area_id", "time_scope",
        "commercial_count", "commercial_density_km2", "day_vitality", "night_vitality",
        "vitality_source_points", "supply_level", "day_level", "night_level",
        "supply_day_quadrant", "supply_night_quadrant", "day_night_type",
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
        {"name": "day_vitality", "type": "number", "description": day_field},
        {"name": "night_vitality", "type": "number", "description": night_field},
        {"name": "supply_day_quadrant", "type": "string", "description": "商业供给 × 昼间活力四象限"},
        {"name": "supply_night_quadrant", "type": "string", "description": "商业供给 × 夜间活力四象限"},
        {"name": "day_night_type", "type": "string", "description": "昼间 × 夜间相对活力类型"},
    ]
    return {
        "success": True,
        "summary": (
            f"商业供给 × 昼夜活力已完成：{grid_count} 个稳定 100m 网格，"
            f"昼/夜有效分类各 {day_valid_cells}/{night_valid_cells} 格；"
            "结果仅表示空间共现，活力坐标转换版本仍是待权威确认的候选。"
        ),
        "layer_id": layer_id,
        "stats": stats,
        "evidence": evidence,
        "layers": [
            {
                "layer_id": layer_id,
                "name": f"商业供给 × 昼夜活力（{time_scope}）",
                "path": f"data/layers/{layer_id}.json",
                "feature_count": grid_count,
                "geometry": "Polygon",
                "crs": OUTPUT_CRS,
                "fields": fields,
                "style": {"mode": "categorized", "field": "supply_day_quadrant"},
                "description": "100m 全局锚定格网；格心入区；商业正密度中位数与昼夜活力中位数四象限",
            }
        ],
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
