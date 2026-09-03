# -*- coding: utf-8 -*-
"""人口·就业画像确定性 Skill（主输入：常住数量 + 常住画像 四份原始 TXT）。

数据契约（可追溯，事实来源 66/67 已核验）：
- 坐标契约：原始网格中心坐标一律按 GCJ-02 读入，执行标准 GCJ02→WGS84（本模块自包含
  与 amap_call.py 同常数的标准实现，与 `_gcj02_to_wgs84.csv` 逐点残差 < 1.3 m），
  得 WGS84≈CGCS2000 真实地面，再按候选研究范围（EPSG:4326）做格心 `within` 裁剪；
  需要米制再转 EPSG:32650。禁止把原始坐标当 WGS84 或按 BD09 纠偏（会偏 0.5–1.2 km）。
- 日期口径：居住/工作/画像均为单一 202604（2026-04 单月快照）；不与客流/OD（2025 各日）
  横比成同一时间。
- 人口总量口径：一律标注为「202604 单月快照数据口径下的估算值 + 候选范围筛选后」，
  在取得数据供应商「坐标为 GCJ-02 且授权展示」确认前，不输出权威总量。
- 画像属性是「网格汇总条件分布」，不是个人调查；不据此判断个人身份、不做收入/消费精确推断。

分析边界（不推断、不代答）：
- 不做 OD 流向、通勤方式/时间/距离、跨日期客流趋势（留给后续 commuting-flow-profile Skill）；
- 不把画像当 100% 完备个人画像；收入/消费/人生阶段/工作/行业等维度按条件分布并标注缺失比例；
- 缺文件、字段、坐标、CRS 或有效样本时明确失败，不生成演示兜底数据。
"""

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


ANALYSIS_CRS = "EPSG:32650"  # UTM 50N，适合深圳的投影
OUTPUT_CRS = "EPSG:4326"
GRID_VERSION = "grid_v1_100m_epsg32650"
STUDY_AREA_ID = "candidate_lohas_study_area"
CELL_SIZE_M = 100
DATE_CALIBER = "202604"  # 单一月份快照（2026-04）
DEFAULT_STUDY_AREA = "docs/workstreams/analysis-foundation/data/candidate_lohas_study_area.geojson"
DATA_BASE = "../04-乐活大道GIS数据/00-原始数据/人口客流/研究范围人口数据全"
DEFAULT_HOME_COUNT = f"{DATA_BASE}/常住分析/常住数量/居住数量.txt"
DEFAULT_WORK_COUNT = f"{DATA_BASE}/常住分析/常住数量/工作数量.txt"
DEFAULT_HOME_PROFILE = f"{DATA_BASE}/常住分析/常住画像/居住画像.txt"
DEFAULT_WORK_PROFILE = f"{DATA_BASE}/常住分析/常住画像/工作画像.txt"

# 数量/画像文件的公共基础列（其余均为「维度:取值」比例列）
BASE_COLS: tuple[str, ...] = ("日期", "区域名称", "网格ID", "网格x坐标", "网格y坐标", "人口类型")
COUNT_COLS: tuple[str, ...] = ("网格ID", "网格x坐标", "网格y坐标", "人口类型", "人数", "日期")

# 群体口径（基于居住人群；列名来自画像文件，稳定不变）
YOUTH_COLS: tuple[str, ...] = ("年龄阶段:18-24", "年龄阶段:25-34")
HIGH_EDU_COLS: tuple[str, ...] = ("教育水平:本科及以上",)
MID_HIGH_INCOME_COLS: tuple[str, ...] = (
    "收入水平:4000~7999", "收入水平:8000~19999", "收入水平:20000及以上",
)

GROUP_DEFS: dict[str, dict[str, Any]] = {
    "youth": {
        "label": "青年（18-34 岁）",
        "cols": YOUTH_COLS,
        "note": "年龄阶段 18-24 + 25-34 之和，基于居住人群口径",
    },
    "high_edu": {
        "label": "高学历（本科及以上）",
        "cols": HIGH_EDU_COLS,
        "note": "教育水平 本科及以上，基于居住人群口径",
    },
    "mid_high_income": {
        "label": "中高收入（4000 元及以上）",
        "cols": MID_HIGH_INCOME_COLS,
        "note": "收入水平 4000~7999 + 8000~19999 + 20000及以上 之和，基于居住人群口径",
    },
}

# ── GCJ-02 ↔ WGS-84（与 amap_call.py 同常数；GCJ→WGS 用「逆偏移」）───────────
_A = 6378245.0
_EE = 0.00669342162296594323


def _out_of_china(lng: float, lat: float) -> bool:
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(x: float, y: float) -> float:
    return (
        -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
        + (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        + (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
        + (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    )


def _transform_lng(x: float, y: float) -> float:
    return (
        300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
        + (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
        + (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
        + (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    )


def _gcj_offset(lng: float, lat: float) -> tuple[float, float]:
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return dlng, dlat


def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    """GCJ-02 → WGS-84（原始网格中心坐标纠偏到真实地面）。"""
    if _out_of_china(lng, lat):
        return lng, lat
    dlng, dlat = _gcj_offset(lng, lat)
    return lng * 2 - (lng + dlng), lat * 2 - (lat + dlat)


class AnalysisInputError(ValueError):
    """输入或口径不满足本 Skill 契约。"""


def stable_cell_id(easting: float, northing: float, cell_size_m: int = CELL_SIZE_M) -> str:
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


def _require_fields(frame: pd.DataFrame, fields: tuple[str, ...], label: str) -> None:
    missing = [f for f in fields if f not in frame.columns]
    if missing:
        raise AnalysisInputError(f"{label}数据缺少必需字段：{', '.join(missing)}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_version(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "name": path.name,
        "size_bytes": path.stat().st_size,
        "mtime_ns": path.stat().st_mtime_ns,
        "sha256": _sha256(path),
    }


def _round6(value: float) -> float:
    return round(float(value), 6)


def _read_count(path: Path, expected_type: str, expected_date: str) -> pd.DataFrame:
    """读取并校验一份常住数量 TXT；返回原始 DataFrame（人数已转数值）。"""
    df = pd.read_csv(path, sep="\t", encoding="utf-8", dtype=str)
    _require_fields(df, COUNT_COLS, path.name)
    # 日期口径：单一月份快照
    dates = sorted(set(df["日期"].astype(str).str.strip()))
    if dates != [expected_date]:
        raise AnalysisInputError(
            f"{path.name} 日期口径不是单一 {expected_date}，实际为 {dates}"
        )
    types = sorted(set(df["人口类型"].astype(str).str.strip()))
    if types != [expected_type]:
        raise AnalysisInputError(
            f"{path.name} 人口类型应唯一为 {expected_type}，实际为 {types}"
        )
    # 人数 / 坐标均为数值、无缺失
    pop = pd.to_numeric(df["人数"], errors="coerce")
    if pop.isna().any():
        raise AnalysisInputError(f"{path.name} 人数存在不可解析值")
    lons = pd.to_numeric(df["网格x坐标"], errors="coerce")
    lats = pd.to_numeric(df["网格y坐标"], errors="coerce")
    if lons.isna().any() or lats.isna().any():
        raise AnalysisInputError(f"{path.name} 网格坐标存在不可解析值")
    df["人数"] = pop
    return df


def _to_wgs84_gdf(df: pd.DataFrame) -> gpd.GeoDataFrame:
    """按坐标契约把原始 GCJ-02 网格中心坐标转为 WGS84 点（EPSG:4326）。"""
    lons: list[float] = []
    lats: list[float] = []
    for x, y in zip(df["网格x坐标"], df["网格y坐标"]):
        wlng, wlat = gcj02_to_wgs84(float(x), float(y))
        lons.append(wlng)
        lats.append(wlat)
    return gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(lons, lats), crs=OUTPUT_CRS)


def _read_profile(path: Path) -> pd.DataFrame:
    """读取并校验一份常住画像 TXT；返回原始 DataFrame（比例列保持字符串，稍后转数值）。"""
    df = pd.read_csv(path, sep="\t", encoding="utf-8", dtype=str)
    _require_fields(df, BASE_COLS, path.name)
    dates = sorted(set(df["日期"].astype(str).str.strip()))
    if dates != [DATE_CALIBER]:
        raise AnalysisInputError(f"{path.name} 日期口径不是单一 {DATE_CALIBER}，实际为 {dates}")
    profile_cols = [c for c in df.columns if c not in BASE_COLS]
    if not profile_cols:
        raise AnalysisInputError(f"{path.name} 没有任何画像比例列")
    return df


def _profile_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in BASE_COLS]


def _dimension_groups(profile_cols: list[str]) -> list[tuple[str, list[str]]]:
    """按列名 `维度:取值` 前缀分组，保持文件出现顺序。"""
    dims: dict[str, list[str]] = {}
    order: list[str] = []
    for col in profile_cols:
        dim = col.split(":", 1)[0]
        if dim not in dims:
            dims[dim] = []
            order.append(dim)
        dims[dim].append(col)
    return [(d, dims[d]) for d in order]


def _numeric_profile(profile_df: pd.DataFrame, profile_cols: list[str]) -> pd.DataFrame:
    pnum = profile_df[["网格ID"] + profile_cols].copy()
    for c in profile_cols:
        pnum[c] = pd.to_numeric(pnum[c], errors="coerce").fillna(0.0)
    return pnum


def _compute_structure(
    in_range: gpd.GeoDataFrame, profile_df: pd.DataFrame
) -> dict[str, Any]:
    """人口加权条件分布：每维度类别占比 + 覆盖率 + 缺失比例。

    缺失来源：①数量网格没有画像行（合并后比例为 NaN）；②该维度比例列加总 < 1。
    """
    profile_cols = _profile_columns(profile_df)
    pnum = _numeric_profile(profile_df, profile_cols)
    merged = in_range[["网格ID", "人数"]].merge(
        pnum, on="网格ID", how="left", validate="one_to_one"
    )
    # 合并前记录「数量有而画像无」的网格与人口
    missing_mask = merged[profile_cols[0]].isna()
    missing_grids = int(missing_mask.sum())
    missing_population = float(merged.loc[missing_mask, "人数"].sum())
    merged[profile_cols] = merged[profile_cols].fillna(0.0)

    total_pop = float(in_range["人数"].sum())
    dimensions: list[dict[str, Any]] = []
    for dim, cols in _dimension_groups(profile_cols):
        dim_sum = merged[cols].sum(axis=1)
        weighted_sum = float((dim_sum * merged["人数"]).sum())
        coverage = weighted_sum / total_pop if total_pop > 0 else 0.0
        categories: list[dict[str, Any]] = []
        for col in cols:
            cat_weighted = float((merged[col] * merged["人数"]).sum())
            prop = cat_weighted / weighted_sum if weighted_sum > 0 else 0.0
            categories.append(
                {"label": col.split(":", 1)[1], "proportion": _round6(prop)}
            )
        dimensions.append(
            {
                "code": dim,
                "coverage": _round6(coverage),
                "missing_ratio": _round6(1.0 - coverage),
                "categories": categories,
            }
        )
    return {
        "source_rows": int(len(profile_df)),
        "joined_grids": int((~missing_mask).sum()),
        "missing_grids": missing_grids,
        "missing_population": round(missing_population),
        "dimensions": dimensions,
    }


def _group_estimates(
    in_range: gpd.GeoDataFrame, profile_df: pd.DataFrame
) -> dict[str, dict[str, Any]]:
    """基于居住/工作人群口径的群体人口加权占比与估算人数（不用于精确个体推断）。"""
    profile_cols = _profile_columns(profile_df)
    pnum = _numeric_profile(profile_df, profile_cols)
    merged = in_range[["网格ID", "人数"]].merge(
        pnum, on="网格ID", how="left", validate="one_to_one"
    )
    merged[profile_cols] = merged[profile_cols].fillna(0.0)
    total_pop = float(in_range["人数"].sum())
    out: dict[str, dict[str, Any]] = {}
    for code, spec in GROUP_DEFS.items():
        cols = spec["cols"]
        weighted = float((merged[list(cols)].sum(axis=1) * merged["人数"]).sum())
        prop = weighted / total_pop if total_pop > 0 else 0.0
        out[code] = {
            "label": spec["label"],
            "note": spec["note"],
            "proportion": _round6(prop),
            "estimate": round(weighted),
        }
    return out


def run_analysis(params: dict[str, Any], workspace: Path) -> dict[str, Any]:
    repo_root = _repo_root()
    study_path = _resolve_path(repo_root, params.get("study_area_path"), DEFAULT_STUDY_AREA)
    home_count_path = _resolve_path(repo_root, params.get("home_count_path"), DEFAULT_HOME_COUNT)
    work_count_path = _resolve_path(repo_root, params.get("work_count_path"), DEFAULT_WORK_COUNT)
    home_profile_path = _resolve_path(repo_root, params.get("home_profile_path"), DEFAULT_HOME_PROFILE)
    work_profile_path = _resolve_path(repo_root, params.get("work_profile_path"), DEFAULT_WORK_PROFILE)

    study_crs = _crs(params.get("study_area_crs", OUTPUT_CRS), "研究范围")
    cell_size_m = int(params.get("cell_size_m", CELL_SIZE_M))
    if cell_size_m != CELL_SIZE_M:
        raise AnalysisInputError(f"本 Skill 固定使用 {CELL_SIZE_M}m 网格，cell_size_m 必须为 {CELL_SIZE_M}")

    # ── 研究范围 ────────────────────────────────────────────────
    study = gpd.read_file(study_path)
    _require_file_crs(study, study_crs, "研究范围")
    if study.empty or study.geometry.isna().any() or study.geometry.is_empty.any():
        raise AnalysisInputError("研究范围没有可用几何")
    if not study.geometry.is_valid.all():
        raise AnalysisInputError("研究范围存在无效几何")
    if not set(study.geometry.geom_type.unique()).issubset({"Polygon", "MultiPolygon"}):
        raise AnalysisInputError("研究范围必须为 Polygon 或 MultiPolygon")
    boundary_4326 = study.to_crs(OUTPUT_CRS).geometry.union_all()
    boundary_32650 = study.to_crs(ANALYSIS_CRS).geometry.union_all()
    boundary_area_km2 = float(boundary_32650.area / 1_000_000)
    if boundary_area_km2 <= 0:
        raise AnalysisInputError("研究范围面积无效")

    # ── 数量：坐标契约转换 + 候选范围 within 裁剪 + 100m 网格 ──────
    home_raw = _read_count(home_count_path, "home", DATE_CALIBER)
    work_raw = _read_count(work_count_path, "work", DATE_CALIBER)
    home_gdf = _to_wgs84_gdf(home_raw)
    work_gdf = _to_wgs84_gdf(work_raw)

    def _assign(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        inside = gdf[gdf.geometry.within(boundary_4326)].copy()
        inside = inside.to_crs(ANALYSIS_CRS)
        east = inside.geometry.x.to_numpy()
        north = inside.geometry.y.to_numpy()
        inside["cell_id"] = [
            stable_cell_id(float(e), float(n), cell_size_m) for e, n in zip(east, north)
        ]
        return inside

    home_in = _assign(home_gdf)
    work_in = _assign(work_gdf)
    if len(home_in) < 10 or len(work_in) < 10:
        raise AnalysisInputError("候选研究范围内居住/就业网格数少于 10")

    home_cells = home_in.groupby("cell_id")["人数"].sum()
    work_cells = work_in.groupby("cell_id")["人数"].sum()
    all_cells = sorted(set(home_cells.index) | set(work_cells.index))
    if not all_cells:
        raise AnalysisInputError("没有任何 100m 网格承载人口")

    # ── 画像结构 + 群体 ─────────────────────────────────────────
    home_profile = _read_profile(home_profile_path)
    work_profile = _read_profile(work_profile_path)
    home_structure = _compute_structure(home_in, home_profile)
    work_structure = _compute_structure(work_in, work_profile)
    home_groups = _group_estimates(home_in, home_profile)
    work_groups = _group_estimates(work_in, work_profile)

    # ── 组装 100m 网格 GeoDataFrame ─────────────────────────────
    grid_rows: list[dict[str, Any]] = []
    for cid in all_cells:
        gx, gy = int(cid.split("_")[1]), int(cid.split("_")[2])
        x0 = gx * cell_size_m
        y0 = gy * cell_size_m
        grid_rows.append(
            {
                "cell_id": cid,
                "grid_x": gx,
                "grid_y": gy,
                "home_count": int(home_cells.get(cid, 0)),
                "work_count": int(work_cells.get(cid, 0)),
                "geometry": box(x0, y0, x0 + cell_size_m, y0 + cell_size_m),
            }
        )
    grid = gpd.GeoDataFrame(grid_rows, geometry="geometry", crs=ANALYSIS_CRS).sort_values(
        ["grid_x", "grid_y"], kind="stable"
    ).reset_index(drop=True)

    # 职住比 = 就业 / 居住；居住为 0 记无值
    grid["jobs_per_resident"] = np.where(
        grid["home_count"] > 0, grid["work_count"] / grid["home_count"], np.nan
    )

    # 群体 per-cell 人数（居住口径）—— 逐格 = home_count × 群体比例之和
    def _cell_group_field(cols: tuple[str, ...], profile_df: pd.DataFrame) -> pd.Series:
        profile_cols = _profile_columns(profile_df)
        pnum = _numeric_profile(profile_df, profile_cols)
        merged = home_in[["网格ID", "cell_id", "人数"]].merge(
            pnum[["网格ID"] + profile_cols], on="网格ID", how="left", validate="one_to_one"
        )
        merged[profile_cols] = merged[profile_cols].fillna(0.0)
        merged["_g"] = merged[list(cols)].sum(axis=1) * merged["人数"]
        return merged.groupby("cell_id")["_g"].sum()

    youth_field = _cell_group_field(YOUTH_COLS, home_profile)
    high_edu_field = _cell_group_field(HIGH_EDU_COLS, home_profile)
    mid_high_income_field = _cell_group_field(MID_HIGH_INCOME_COLS, home_profile)
    grid["youth_resident_count"] = [float(youth_field.get(cid, 0.0)) for cid in grid["cell_id"]]
    grid["high_edu_resident_count"] = [float(high_edu_field.get(cid, 0.0)) for cid in grid["cell_id"]]
    grid["mid_high_income_resident_count"] = [
        float(mid_high_income_field.get(cid, 0.0)) for cid in grid["cell_id"]
    ]

    # 相对集聚：正密度格的中位数阈值
    def _relative_median(series: pd.Series) -> float:
        pos = series[series > 0]
        return float(pos.median()) if len(pos) else 0.0

    youth_median = _relative_median(grid["youth_resident_count"])
    high_edu_median = _relative_median(grid["high_edu_resident_count"])
    mid_high_income_median = _relative_median(grid["mid_high_income_resident_count"])

    # ── stats ───────────────────────────────────────────────────
    home_in_pop = int(home_in["人数"].sum())
    work_in_pop = int(work_in["人数"].sum())
    jobs_resident_ratio = _round6(work_in_pop / home_in_pop) if home_in_pop else 0.0

    group_stats = {
        code: {
            "label": spec["label"],
            "note": spec["note"],
            "proportion": home_groups[code]["proportion"],
            "estimate": home_groups[code]["estimate"],
            "median_threshold": round(
                {"youth": youth_median, "high_edu": high_edu_median, "mid_high_income": mid_high_income_median}[code], 1
            ),
            "high_cells": int(
                {
                    "youth": (grid["youth_resident_count"] > youth_median).sum(),
                    "high_edu": (grid["high_edu_resident_count"] > high_edu_median).sum(),
                    "mid_high_income": (grid["mid_high_income_resident_count"] > mid_high_income_median).sum(),
                }[code]
            ),
        }
        for code, spec in GROUP_DEFS.items()
    }

    stats = {
        "grid_version": GRID_VERSION,
        "cell_size_m": cell_size_m,
        "grid_count": int(len(grid)),
        "date_caliber": DATE_CALIBER,
        "counts": {
            "home_source_grids": int(len(home_raw)),
            "home_source_population": int(home_raw["人数"].sum()),
            "home_in_range_grids": int(len(home_in)),
            "home_in_range_population": home_in_pop,
            "work_source_grids": int(len(work_raw)),
            "work_source_population": int(work_raw["人数"].sum()),
            "work_in_range_grids": int(len(work_in)),
            "work_in_range_population": work_in_pop,
            "home_cells": int((grid["home_count"] > 0).sum()),
            "work_cells": int((grid["work_count"] > 0).sum()),
        },
        "jobs_resident_ratio": jobs_resident_ratio,
        "jobs_resident_per_cell_median": _round6(
            float(grid["jobs_per_resident"].dropna().median())
        ),
        "structure": {
            "home": {"label": "居住人群", **home_structure},
            "work": {"label": "就业人群", **work_structure},
        },
        "groups": group_stats,
        "group_definitions": {
            code: {"label": spec["label"], "note": spec["note"], "cols": list(spec["cols"])}
            for code, spec in GROUP_DEFS.items()
        },
    }

    # ── evidence ────────────────────────────────────────────────
    coordinate_contract = (
        "原始网格中心坐标按 GCJ-02 读入，执行标准 GCJ02→WGS84（与 _gcj02_to_wgs84.csv "
        "逐点残差 < 1.3 m，本模块自包含同常数实现）→ 得 WGS84≈CGCS2000 真实地面 → "
        "候选研究范围（EPSG:4326）格心 within 裁剪 → 需要米制再转 EPSG:32650。"
        "禁止把原始坐标直接当 WGS84 或按 BD09 纠偏（会系统性偏 0.5–1.2 km）。"
    )
    caliber = (
        "人口总数均为「202604 单月快照数据口径下的估算值 + 候选范围筛选后」；"
        "在取得数据供应商「坐标为 GCJ-02 且授权展示」确认前，不输出权威总量。"
        "画像属性为「网格汇总条件分布」，不是个人调查；不据此判断个人身份或做精确个体推断。"
    )
    evidence = {
        "input_versions": {
            "study_area": _file_version(study_path),
            "home_count": _file_version(home_count_path),
            "work_count": _file_version(work_count_path),
            "home_profile": _file_version(home_profile_path),
            "work_profile": _file_version(work_profile_path),
        },
        "crs": {
            "study_area_file": CRS.from_user_input(study.crs).to_string(),
            "coordinate_contract": coordinate_contract,
            "analysis": ANALYSIS_CRS,
            "output": OUTPUT_CRS,
        },
        "date_caliber": DATE_CALIBER,
        "population_caliber": caliber,
        "filters": [
            f"居住数量（{home_count_path.name}）原始 {len(home_raw)} 格 / {int(home_raw['人数'].sum())} 人，"
            f"GCJ02→WGS84 后候选范围内 {len(home_in)} 格 / {home_in_pop} 人",
            f"工作数量（{work_count_path.name}）原始 {len(work_raw)} 格 / {int(work_raw['人数'].sum())} 人，"
            f"GCJ02→WGS84 后候选范围内 {len(work_in)} 格 / {work_in_pop} 人",
            "原始网格中心坐标经 GCJ02→WGS84 后按候选研究范围（EPSG:4326）格心 within 裁剪",
            "凡承载任一在范围内原始网格中心的 100m 单元进入结果网格",
        ],
        "aggregation": (
            "居住/就业人口按 100m 全局锚定格网格心（floor(E/100), floor(N/100)）聚合；"
            "职住比 = 就业人口 ÷ 居住人口（居住为 0 记无值）；"
            "结构为人口加权条件分布（Σ P·p / Σ P·Σp），维度覆盖率 = Σ P·Σp / Σ P，缺失比例 = 1 − 覆盖率。"
        ),
        "classification": (
            "群体（青年/高学历/中高收入）基于居住人群口径：每格群体人数 = 居住人数 × 该群体比例之和；"
            "相对集聚区域 = 该格群体人数高于正密度格的中位数。"
        ),
        "source_audit": {
            "home_count": {
                "original_file": str(home_count_path),
                "original_sha256": _sha256(home_count_path),
                "used_fields": list(COUNT_COLS),
                "population_type": "home",
                "original_grid_count": int(len(home_raw)),
                "in_range_grids": int(len(home_in)),
                "in_range_population": home_in_pop,
            },
            "work_count": {
                "original_file": str(work_count_path),
                "original_sha256": _sha256(work_count_path),
                "used_fields": list(COUNT_COLS),
                "population_type": "work",
                "original_grid_count": int(len(work_raw)),
                "in_range_grids": int(len(work_in)),
                "in_range_population": work_in_pop,
            },
            "home_profile": {
                "original_file": str(home_profile_path),
                "original_sha256": _sha256(home_profile_path),
                "used_fields": ["网格ID"] + _profile_columns(home_profile),
                "original_rows": int(len(home_profile)),
                "joined_grids": home_structure["joined_grids"],
                "missing_grids": home_structure["missing_grids"],
                "missing_population": home_structure["missing_population"],
            },
            "work_profile": {
                "original_file": str(work_profile_path),
                "original_sha256": _sha256(work_profile_path),
                "used_fields": ["网格ID"] + _profile_columns(work_profile),
                "original_rows": int(len(work_profile)),
                "joined_grids": work_structure["joined_grids"],
                "missing_grids": work_structure["missing_grids"],
                "missing_population": work_structure["missing_population"],
            },
        },
        "excluded_inputs": [
            "PPT/PDF 历史报告中的任何人口数字、地图或结论（不作为输入/目标值/验收基准）",
            "bd09_to_wgs84 派生坐标（会把原始 GCJ 当 BD09 过校正约 0.6–0.7 km）",
            "原始坐标直接当 WGS84 上图（会偏约 0.5–0.8 km）",
            "客流/OD 数据（2025 各日，与常住 202604 日期口径不同，不横比）",
            "职住分析的通勤方式/距离/时间与 OD 流向（后续 commuting-flow-profile 范畴）",
            "任何把画像当个人身份、或用收入/消费标签做精确个体判断的做法",
        ],
        "limitations": [
            "人口总数为 202604 单月快照数据口径下的估算值，且为候选研究范围筛选后；未经供应商授权与坐标系书面确认，不作为权威总量对外发布",
            "画像属性是网格汇总条件分布，不是个人调查；禁止当作个人真实身份或做精确个体推断",
            "收入/消费/人生阶段/工作/行业等维度存在约 0.03%–0.2% 的网格级加总缺口，按条件分布展示并标注缺失比例，非 100% 完备画像",
            "职住比只描述网格级就业与居住人数的相对比例，不代表真实的通勤平衡或职住匹配结论",
            "群体（青年/高学历/中高收入）占比为人口加权估算，基于居住人群口径，不用于个体识别",
            "常住（202604）与客流/OD（2025 各日）日期口径不同，不横比成同一时间",
            "不做 OD 流向、通勤方式/时间/距离、跨日期客流趋势；不做真实服务容量/供需或因果结论",
        ],
    }

    # ── 写出结果图层 ────────────────────────────────────────────
    layer_id = str(params.get("output_layer_id") or "population_workforce_profile_100m").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", layer_id):
        raise AnalysisInputError("output_layer_id 必须为小写字母开头且仅含小写字母、数字、_、-")
    layer_dir = (workspace / "data" / "layers").resolve()
    layer_dir.mkdir(parents=True, exist_ok=True)
    layer_path = (layer_dir / f"{layer_id}.json").resolve()
    if layer_dir not in layer_path.parents:
        raise AnalysisInputError("结果图层路径越出 workspace")

    centers = gpd.GeoSeries(
        [Point((gx + 0.5) * cell_size_m, (gy + 0.5) * cell_size_m)
         for gx, gy in zip(grid.grid_x, grid.grid_y)],
        crs=ANALYSIS_CRS,
    ).to_crs(OUTPUT_CRS)
    grid["center_lon"] = centers.x.to_numpy()
    grid["center_lat"] = centers.y.to_numpy()
    grid["grid_version"] = GRID_VERSION
    grid["study_area_id"] = STUDY_AREA_ID
    for col in ("jobs_per_resident", "youth_resident_count", "high_edu_resident_count", "mid_high_income_resident_count"):
        grid[col] = grid[col].round(6)

    output_columns = [
        "cell_id", "grid_x", "grid_y", "grid_version", "study_area_id",
        "home_count", "work_count", "jobs_per_resident",
        "youth_resident_count", "high_edu_resident_count", "mid_high_income_resident_count",
        "center_lon", "center_lat", "geometry",
    ]
    output_grid = grid[output_columns].to_crs(OUTPUT_CRS)
    geojson = json.loads(output_grid.to_json(drop_id=True, na="null"))
    geojson["name"] = layer_id
    geojson["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}
    layer_path.write_text(json.dumps(geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    fields = [
        {"name": "cell_id", "type": "string", "description": "全局锚定 100m 网格 ID"},
        {"name": "home_count", "type": "integer", "description": "居住人口（202604 单月快照估算，候选范围筛选后）"},
        {"name": "work_count", "type": "integer", "description": "就业人口（202604 单月快照估算，候选范围筛选后）"},
        {"name": "jobs_per_resident", "type": "number", "description": "职住比 = 就业人口 ÷ 居住人口；居住为 0 记无值"},
        {"name": "youth_resident_count", "type": "number", "description": "青年（18-34 岁）居住人口估算"},
        {"name": "high_edu_resident_count", "type": "number", "description": "高学历（本科及以上）居住人口估算"},
        {"name": "mid_high_income_resident_count", "type": "number", "description": "中高收入（4000 元及以上）居住人口估算"},
    ]

    summary = (
        f"人口·就业画像已完成（{DATE_CALIBER} 单月快照数据口径估算，候选范围筛选后）："
        f"居住 {len(home_in)} 格 / {home_in_pop} 人，就业 {len(work_in)} 格 / {work_in_pop} 人，"
        f"职住比（就业/居住）{jobs_resident_ratio}；"
        f"100m 网格 {len(grid)} 格。"
        f"结构（人口加权条件分布）：居住 本科及以上 {stats['structure']['home']['dimensions'][2]['categories'][2]['proportion']}，"
        f"就业 本科及以上 {stats['structure']['work']['dimensions'][2]['categories'][2]['proportion']}；"
        f"青年（18-34）占比 {home_groups['youth']['proportion']}，"
        f"高学历占比 {home_groups['high_edu']['proportion']}，"
        f"中高收入占比 {home_groups['mid_high_income']['proportion']}。"
        "画像为网格汇总条件分布（非个人调查），人口总量未经供应商授权确认，不作为权威总量发布。"
    )
    return {
        "success": True,
        "summary": summary,
        "layer_id": layer_id,
        "stats": stats,
        "evidence": evidence,
        "layers": [
            {
                "layer_id": layer_id,
                "name": "人口·就业画像",
                "path": f"data/layers/{layer_id}.json",
                "feature_count": int(len(grid)),
                "geometry": "Polygon",
                "kind": "grid",
                "crs": OUTPUT_CRS,
                "fields": fields,
                "style": {"mode": "graduated", "field": "home_count", "method": "quantile", "classes": 5},
                "description": "100m 全局锚定格网；居住人口、就业人口、职住比与青年/高学历/中高收入群体（居住口径）的网格估算",
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
