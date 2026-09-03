# -*- coding: utf-8 -*-
"""公共服务设施资源画像确定性 Skill（主输入：深圳市 POI 全量原始总表）。

数据契约（可追溯）：
- 主来源为深圳市 2024 POI 全量原始总表（原始文件，含权威的原始记录数与坐标字段），
  不再以 `01-加工数据/POI分类切片` 的派生 Shapefile 作为输入；
- 从总表开始依次执行：四域大类筛选（公共设施/科教文化服务/医疗保健服务/体育休闲服务）
  → 候选研究范围筛选 → 大中小类纳入/排除 → 100m 网格统计（稀疏点密度平滑多档渐变）；
- `01-加工数据` 中的分类 Shapefile 只用于可选交叉核对，不参与主计算；
- 不使用 PPT/PDF 中的任何数字、地图或结论作为输入、目标值或验收基准。

分析边界（不推断、不代答）：
- 同时保留“原始 POI 数量”与“规划分析纳入数量”，不把原始类别都当公共服务设施；
- 不推断设施公办或民办；POI 字段无法证明产权与运营主体；
- “资源相对较少”只是当前 POI 覆盖下的相对空间现象，不是设施短缺或规划缺口；
- 不做 15 分钟生活圈、步行可达、覆盖人口、服务容量/品质/利用率、供需平衡、
  补建优先级或因果结论；
- 缺文件、字段、CRS、有效样本或类别规则无法执行时明确失败，不生成演示兜底数据。
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
# D3：公共服务 500m → 100m（与其他板块同锚点 100m 稳定网格），数字随口径重算会变
GRID_VERSION = "grid_v1_100m_epsg32650"
STUDY_AREA_ID = "candidate_lohas_study_area"
CELL_SIZE_M = 100
# D3：稀疏设施点密度平滑带宽（米）。四域设施点稀疏，直接 100m 计数会大量 0/零星 1~2；
# 用固定带宽高斯核摊到邻格（截断半径 3×带宽），距设施 >3σ 的格保持 0 —— 保留「无设施」档，
# 不做伪 0 连续面；边缘 1 个带宽内的密度受研究区边界截断影响。
SMOOTH_BANDWIDTH_M = 300.0
# D3：主图层单色浅→深渐变（表达「匮乏分层」），最低档近底浅退让
PUBLIC_DENSITY_RAMP = [
    "#ecfdf5", "#d1fae5", "#a7f3d0", "#6ee7b7", "#34d399", "#059669",
]
DEFAULT_STUDY_AREA = "docs/workstreams/analysis-foundation/data/candidate_lohas_study_area.geojson"
DEFAULT_POI = "../04-乐活大道GIS数据/00-原始数据/POI全量/深圳市2024POI.csv"
DEFAULT_POI_ENCODING = "utf-8-sig"

# 四域大类：教育文化/医疗健康/体育活动/便民与应急对应的高德顶层类别（bigType）
SOURCE_DOMAINS: tuple[str, ...] = (
    "公共设施", "科教文化服务", "医疗保健服务", "体育休闲服务",
)
# 可选交叉核对：若给 crosscheck_dir，用 01-加工数据的分类 Shapefile 复核总数
SLICE_FILES = {
    "公共设施": "深圳市_公共设施.shp",
    "科教文化": "深圳市_科教文化服务.shp",
    "医疗保健": "深圳市_医疗保健服务.shp",
    "体育休闲": "深圳市_体育休闲服务.shp",
}

# ── 类别纳入清单（数据契约，可审计）────────────────────────────────────
# 每一类只允许出现在一个功能组。仅按类别字段归属；不按名称/产权/运营主体推断。

GROUP_ORDER: tuple[str, ...] = (
    "education_culture",
    "medical_health",
    "sports",
    "convenience_emergency",
)
GROUP_LABELS: dict[str, str] = {
    "education_culture": "教育文化",
    "medical_health": "医疗健康",
    "sports": "体育活动",
    "convenience_emergency": "便民与应急",
}

# 每组纳入的 AMap 二级类别（midType）
INCLUDE_MID: dict[str, set[str]] = {
    "education_culture": {
        "学校", "博物馆", "展览馆", "美术馆", "图书馆", "科技馆", "文化宫", "档案馆",
    },
    "medical_health": {
        "综合医院", "专科医院", "诊所", "疾病预防机构",
    },
    "sports": {
        "运动场馆", "高尔夫相关",
    },
    "convenience_emergency": {
        "公共厕所", "报刊亭", "公用电话", "紧急避难场所",
    },
}

# 泛化/顶层类别：字段无法细分到可判定的设施类别 → 不纳入，只单列说明。
EXCLUDE_REASONS: dict[str, str] = {
    # 明确按规则排除的经营/培训机构
    "培训机构": "training_institution",
    "驾校": "driving_school",
    "会展中心": "convention_center",
    "传媒机构": "media_agency",
    "科研机构": "research_institute",
    "文艺团体": "art_group",
    "医药保健销售店": "pharmacy_sales",
    "动物医疗场所": "pet_medical",
    "娱乐场所": "entertainment_venue",
    "影剧院": "theater",
    "休闲场所": "leisure_venue",
    "度假疗养场所": "resort",
    # 泛化/顶层类别（未细分，不能判定为哪类设施）
    "公共设施": "generic_toplevel",
    "科教文化场所": "generic_toplevel",
    "医疗保健服务场所": "generic_toplevel",
    "体育休闲服务场所": "generic_toplevel",
    "核酸检测": "generic_toplevel",
}

# 专科医院内按三级类别排除的“医疗美容”（整形美容）
EXCLUDE_SMALL_IN_MID: dict[str, set[str]] = {
    "专科医院": {"整形美容"},
}

EXCLUDE_LABELS: dict[str, str] = {
    "training_institution": "一般培训机构",
    "driving_school": "驾校（驾驶培训）",
    "convention_center": "会展中心（商务会展设施）",
    "media_agency": "传媒机构（媒体/出版/电视台）",
    "research_institute": "科研机构",
    "art_group": "文艺团体（演艺组织）",
    "pharmacy_sales": "药店与医疗用品销售",
    "cosmetic_surgery": "医疗美容（整形美容专科）",
    "pet_medical": "宠物医疗（动物医疗场所）",
    "entertainment_venue": "酒吧/KTV/网吧/棋牌/游戏厅等娱乐消费场所",
    "theater": "影剧院（文化演艺经营场所）",
    "leisure_venue": "游乐/垂钓/采摘/露营等休闲场所",
    "resort": "度假疗养场所",
    "generic_toplevel": "泛化/顶层类别（未细分到可判定设施）",
}

GROUP_COLORS: dict[str, str] = {
    "education_culture": "#2563eb",
    "medical_health": "#dc2626",
    "sports": "#16a34a",
    "convenience_emergency": "#d97706",
}

CLASS_LABELS: dict[str, str] = {
    "none": "无设施格",
    "low": "资源相对较少格",
    "high": "资源相对较多格",
}


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


def _require_field(frame: pd.DataFrame, field: str, label: str) -> None:
    if field not in frame.columns:
        raise AnalysisInputError(f"{label}数据缺少必需字段 {field}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_version(path: Path, *extra_suffixes: str) -> dict[str, Any]:
    if path.suffix.lower() == ".shp":
        component_paths = [
            path.with_suffix(suffix)
            for suffix in extra_suffixes or (".shp", ".dbf", ".shx", ".prj", ".cpg")
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


def _norm_category(value: Any) -> str:
    """归一 AMap 类别值：取 '|' 前的首段并去空白（数据里存在双类目噪声）。"""
    return str(value or "").split("|", 1)[0].strip()


def classify_record(mid_raw: Any, small_raw: Any) -> tuple[str | None, str | None]:
    """按类别字段把一条 POI 归入功能组；返回 (group_code|None, reason_code|None)。

    - 有 group_code 表示“纳入”该功能组；
    - group_code 为 None 时 reason_code 说明排除原因（详见 EXCLUDE_LABELS）。
    """
    mid = _norm_category(mid_raw)
    small = _norm_category(small_raw)
    if not mid:
        return None, "generic_toplevel"

    reason = EXCLUDE_REASONS.get(mid)
    if reason is not None:
        return None, reason

    exclude_small = EXCLUDE_SMALL_IN_MID.get(mid, set())
    if small in exclude_small:
        return None, "cosmetic_surgery"

    for code in GROUP_ORDER:
        if mid in INCLUDE_MID[code]:
            return code, None
    return None, "generic_toplevel"


def _read_domain_rows(poi_path: Path, encoding: str) -> tuple[list[dict[str, Any]], int, int]:
    """分块读取 POI 全量总表，仅保留四域大类的行；返回 (行字典列表, 全量记录数, 四域记录数)。"""
    needed = ["bigType", "midType", "smallType", "lon_wgs84", "lat_wgs84"]
    header = pd.read_csv(poi_path, nrows=1, encoding=encoding, dtype=str)
    for col in needed:
        _require_field(header, col, "POI 全量总表")

    rows: list[dict[str, Any]] = []
    total_all = 0
    total_domain = 0
    for chunk in pd.read_csv(
        poi_path,
        usecols=needed,
        dtype=str,
        encoding=encoding,
        chunksize=200000,
    ):
        total_all += int(len(chunk))
        mask = chunk["bigType"].isin(SOURCE_DOMAINS)
        dom = chunk.loc[mask]
        total_domain += int(len(dom))
        lon = pd.to_numeric(dom["lon_wgs84"], errors="coerce")
        lat = pd.to_numeric(dom["lat_wgs84"], errors="coerce")
        bad = (lon.isna()) | (lat.isna())
        if bad.any():
            raise AnalysisInputError("POI 全量总表中存在不可解析的经纬度（lon_wgs84/lat_wgs84）")
        for mid, small, x, y in zip(dom["midType"], dom["smallType"], lon, lat):
            rows.append({"midType": mid, "smallType": small, "lon": float(x), "lat": float(y)})
    return rows, total_all, total_domain


def _make_grid(boundary: Any, cell_size_m: int) -> gpd.GeoDataFrame:
    minx, miny, maxx, maxy = boundary.bounds
    gx_min = math.floor(minx / cell_size_m)
    gx_max = math.floor(maxx / cell_size_m)
    gy_min = math.floor(miny / cell_size_m)
    gy_max = math.floor(maxy / cell_size_m)
    grid_rows: list[dict[str, Any]] = []
    for gx in range(gx_min, gx_max + 1):
        x0 = gx * cell_size_m
        for gy in range(gy_min, gy_max + 1):
            y0 = gy * cell_size_m
            center = Point(x0 + cell_size_m / 2, y0 + cell_size_m / 2)
            if not boundary.covers(center):
                continue
            grid_rows.append(
                {
                    "cell_id": f"g_{gx}_{gy}",
                    "grid_x": gx,
                    "grid_y": gy,
                    "geometry": box(x0, y0, x0 + cell_size_m, y0 + cell_size_m),
                }
            )
    if not grid_rows:
        raise AnalysisInputError(f"研究范围内没有生成任何 {cell_size_m}m 格心网格")
    return gpd.GeoDataFrame(grid_rows, geometry="geometry", crs=ANALYSIS_CRS).sort_values(
        ["grid_x", "grid_y"], kind="stable"
    ).reset_index(drop=True)


def _median_int(values: pd.Series) -> int:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        raise AnalysisInputError("没有可计算中位数的有效样本")
    return int(numeric.median())


def _slice_crosscheck(study_path: Path, slice_dir: Path, boundary: Any, analysis_crs: str) -> dict[str, Any]:
    """可选核对：用 01-加工数据/POI分类切片 的 Shapefile 复核主表派生数量（不参与主计算）。

    若切片能独立算出的四域记录数与分类结果与主表一致，说明主表→切片的血缘可复现；
    否则如实记录不一致，不修改主结果。
    """
    inside_total = 0
    results: dict[str, Any] = {"slice_dir": str(slice_dir), "enabled": True}
    grp = {code: 0 for code in GROUP_ORDER}
    for label, fname in SLICE_FILES.items():
        path = slice_dir / fname
        if not path.is_file():
            results[label] = {"file": str(path), "error": "file missing"}
            continue
        frame = gpd.read_file(path, columns=["midType", "smallType"])
        g = frame.geometry.to_crs(analysis_crs)
        inside = frame.loc[g.within(boundary)]
        results[label] = {"source_records": int(len(frame)), "inside": int(len(inside))}
        inside_total += int(len(inside))
        for mid, small in zip(inside["midType"], inside["smallType"]):
            code, _reason = classify_record(mid, small)
            if code:
                grp[code] += 1
    results["inside_total"] = inside_total
    results["group_counts"] = grp
    return results


def run_analysis(params: dict[str, Any], workspace: Path) -> dict[str, Any]:
    repo_root = _repo_root()
    study_path = _resolve_path(repo_root, params.get("study_area_path"), DEFAULT_STUDY_AREA)
    poi_path = _resolve_path(repo_root, params.get("poi_path"), DEFAULT_POI)
    poi_encoding = str(params.get("poi_encoding") or DEFAULT_POI_ENCODING).strip() or DEFAULT_POI_ENCODING

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
    boundary = study.to_crs(ANALYSIS_CRS).geometry.union_all()
    boundary_area_km2 = float(boundary.area / 1_000_000)
    if boundary_area_km2 <= 0:
        raise AnalysisInputError("研究范围面积无效")

    # ── 从 POI 全量总表读取四域大类行 ───────────────────────────
    rows, total_all, total_domain = _read_domain_rows(poi_path, poi_encoding)
    if total_domain < 10:
        raise AnalysisInputError("POI 全量总表中四域大类记录少于 10 个")
    domain_frame = gpd.GeoDataFrame(
        rows,
        geometry=[Point(r["lon"], r["lat"]) for r in rows],
        crs=OUTPUT_CRS,
    ).to_crs(ANALYSIS_CRS)
    inside_mask = domain_frame.geometry.within(boundary)
    if not inside_mask.any():
        raise AnalysisInputError("四域大类 POI 在候选研究范围内没有记录")
    inside = domain_frame.loc[inside_mask].copy()

    group_counts = {code: 0 for code in GROUP_ORDER}
    excluded_counts: dict[str, int] = {}
    included_rows: list[tuple[str, float, float]] = []
    for mid, small, geom in zip(inside["midType"], inside["smallType"], inside.geometry):
        code, reason = classify_record(mid, small)
        if code is not None:
            group_counts[code] += 1
            included_rows.append((code, float(geom.x), float(geom.y)))
        else:
            excluded_counts[reason] = excluded_counts.get(reason, 0) + 1

    included_total = int(sum(group_counts.values()))
    excluded_total = int(sum(excluded_counts.values()))
    inside_total = int(len(inside))
    if inside_total != included_total + excluded_total:
        raise RuntimeError("纳入与排除数量不守恒")
    if included_total < 10:
        raise AnalysisInputError("按类别纳入清单筛选后的设施点少于 10 个")

    # ── 100m 稳定网格：格心入区 ────────────────────────────────
    grid = _make_grid(boundary, cell_size_m)
    allowed_cells = set(grid["cell_id"])
    cell_counters: dict[str, dict[str, int]] = {
        cid: {code: 0 for code in GROUP_ORDER} for cid in grid["cell_id"]
    }
    in_grid = 0
    for code, x, y in included_rows:
        cid = stable_cell_id(x, y, cell_size_m)
        if cid in allowed_cells:
            cell_counters[cid][code] += 1
            in_grid += 1
    if in_grid < 10:
        raise AnalysisInputError(f"入 {cell_size_m}m 稳定网格的设施点少于 10 个")

    facility_totals: list[int] = []
    class_counts = {"none": 0, "low": 0, "high": 0}
    richness_counts = {0: 0, 1: 0, 2: 0, 3: 0, 4: 0}
    grid["facility_count"] = 0
    for code in GROUP_ORDER:
        grid[f"{code}_count"] = 0
    for cid, counts in cell_counters.items():
        total = int(sum(counts.values()))
        cell_loc = grid.index[grid["cell_id"] == cid]
        if len(cell_loc) != 1:
            raise RuntimeError(f"网格重复：{cid}")
        idx = int(cell_loc[0])
        for code in GROUP_ORDER:
            grid.loc[idx, f"{code}_count"] = int(counts[code])
        grid.loc[idx, "facility_count"] = total
        if total > 0:
            facility_totals.append(total)

    group_count_cols = [f"{code}_count" for code in GROUP_ORDER]
    richness_array = (grid[group_count_cols].to_numpy() > 0).sum(axis=1)
    grid["type_richness"] = [int(v) for v in richness_array]
    for v in grid["type_richness"]:
        richness_counts[v] = richness_counts.get(v, 0) + 1
    for v in grid["facility_count"]:
        if int(v) == 0:
            class_counts["none"] += 1

    if not facility_totals:
        raise AnalysisInputError(f"没有任何 {cell_size_m}m 网格包含纳入设施点")
    relative_median = _median_int(pd.Series(facility_totals))
    grid["facility_class"] = np.where(
        grid["facility_count"] == 0,
        "none",
        np.where(grid["facility_count"] <= relative_median, "low", "high"),
    )
    class_counts["low"] = int((grid["facility_class"] == "low").sum())
    class_counts["high"] = int((grid["facility_class"] == "high").sum())
    if class_counts["none"] + class_counts["low"] + class_counts["high"] != len(grid):
        raise RuntimeError("facility_class 分类数量不守恒")

    # ── 密度平滑列（D3）────────────────────────────────────────────
    # 稀疏点直接按 100m 格计数 → 大量 0 / 零星 1~2 读不出结构；改用固定带宽高斯核把每个
    # 纳入设施点摊到邻格（截断 3×带宽），格值 ≈ 每 km² 的加权点密度。距任何设施 >3σ 的格
    # 保持 0 → 保留「无设施」档，不把空区当伪 0 连续面（详见方法论 D0 §3）。
    bandwidth_m = SMOOTH_BANDWIDTH_M
    sigma2 = 2.0 * bandwidth_m * bandwidth_m
    kernel0 = 1.0 / (math.pi * sigma2) * 1_000_000.0  # 0 距核值 ≈ 每 km² 计数
    cutoff2 = (3.0 * bandwidth_m) ** 2
    pts_by_group: dict[str, list[tuple[float, float]]] = {code: [] for code in GROUP_ORDER}
    for code, x, y in included_rows:
        pts_by_group[code].append((float(x), float(y)))
    cx = (grid["grid_x"].to_numpy(dtype=float) + 0.5) * cell_size_m
    cy = (grid["grid_y"].to_numpy(dtype=float) + 0.5) * cell_size_m
    grid["facility_density_km2"] = 0.0
    for code in GROUP_ORDER:
        density_col = f"{code}_density"
        if not pts_by_group[code]:
            grid[density_col] = 0.0
            continue
        px = np.asarray([p[0] for p in pts_by_group[code]], dtype=float)
        py = np.asarray([p[1] for p in pts_by_group[code]], dtype=float)
        total = np.zeros(len(grid), dtype=float)
        for i in range(len(grid)):
            d2 = (px - cx[i]) ** 2 + (py - cy[i]) ** 2
            within = d2 <= cutoff2
            if within.any():
                total[i] = float((np.exp(-d2[within] / sigma2) * kernel0).sum())
        grid[density_col] = np.round(total, 4)
    grid["facility_density_km2"] = np.round(
        sum((grid[f"{code}_density"].to_numpy(dtype=float) for code in GROUP_ORDER)), 4
    )

    grid_count = len(grid)
    cell_area_km2 = (cell_size_m / 1000.0) ** 2  # 100m → 0.01 km²
    centers = gpd.GeoSeries(
        [Point((gx + 0.5) * cell_size_m, (gy + 0.5) * cell_size_m)
         for gx, gy in zip(grid.grid_x, grid.grid_y)],
        crs=ANALYSIS_CRS,
    ).to_crs(OUTPUT_CRS)
    grid["center_lon"] = centers.x.to_numpy()
    grid["center_lat"] = centers.y.to_numpy()
    grid["grid_version"] = GRID_VERSION
    grid["study_area_id"] = STUDY_AREA_ID

    # ── 可选交叉核对（默认关闭）───────────────────────────────
    crosscheck: dict[str, Any] = {"enabled": False}
    slice_dir_raw = str(params.get("crosscheck_dir") or "").strip()
    if slice_dir_raw:
        slice_dir = Path(slice_dir_raw)
        slice_dir = slice_dir.resolve() if slice_dir.is_absolute() else (repo_root / slice_dir_raw).resolve()
        if not slice_dir.is_dir():
            raise AnalysisInputError(f"crosscheck_dir 目录不存在：{slice_dir}")
        crosscheck = _slice_crosscheck(study_path, slice_dir, boundary, ANALYSIS_CRS)
        crosscheck["matches_master"] = (
            crosscheck.get("inside_total") == inside_total
            and crosscheck.get("group_counts") == group_counts
        )

    # ── stats ───────────────────────────────────────────────────
    groups_stats = {
        code: {
            "code": code,
            "label": GROUP_LABELS[code],
            "count": int(group_counts[code]),
            "color": GROUP_COLORS[code],
        }
        for code in GROUP_ORDER
    }
    class_stats = {
        key: {
            "code": key,
            "label": CLASS_LABELS[key],
            "grid_count": int(class_counts[key]),
            "area_km2": round(int(class_counts[key]) * cell_area_km2, 4),
        }
        for key in ("none", "low", "high")
    }
    excluded_list = [
        {
            "reason_code": code,
            "label": EXCLUDE_LABELS.get(code, code),
            "count": int(excluded_counts[code]),
        }
        for code in sorted(excluded_counts, key=lambda c: -excluded_counts[c])
    ]
    richness_list = [
        {
            "types": int(t),
            "grid_count": int(richness_counts[t]),
            "area_km2": round(int(richness_counts[t]) * cell_area_km2, 4),
        }
        for t in range(0, 5)
        if richness_counts.get(t)
    ]
    stats = {
        "grid_version": GRID_VERSION,
        "cell_size_m": cell_size_m,
        "grid_count": grid_count,
        "standard_grid_area_km2": round(grid_count * cell_area_km2, 4),
        "study_area_geometry_km2": round(boundary_area_km2, 6),
        "counts": {
            "source_records": total_all,
            "domain_source_records": total_domain,
            "inside_study": inside_total,
            "included": included_total,
            "excluded": excluded_total,
            "assigned_to_grid": in_grid,
        },
        "groups": groups_stats,
        "grids": {
            "no_facility_cells": int(class_counts["none"]),
            "resource_low_cells": int(class_counts["low"]),
            "resource_high_cells": int(class_counts["high"]),
            "relative_median": int(relative_median),
            "type_richness_cells": {
                f"cells_{t}_types": int(richness_counts[t]) for t in range(0, 5) if richness_counts.get(t)
            },
        },
        "cell_classes": class_stats,
        "type_richness": richness_list,
        "excluded": excluded_list,
        "group_colors": GROUP_COLORS,
    }

    classification_desc = (
        "类别纳入清单：教育文化(学校/博物馆/展览馆/美术馆/图书馆/科技馆/文化宫/档案馆)；"
        "医疗健康(综合医院/专科医院(除整形美容)/诊所/疾病预防机构)；体育活动(运动场馆/高尔夫相关)；"
        "便民与应急(公共厕所/报刊亭/公用电话/紧急避难场所)"
    )
    evidence = {
        "input_versions": {
            "study_area": _file_version(study_path),
            "poi": _file_version(poi_path),
        },
        "crs": {
            "study_area_file": CRS.from_user_input(study.crs).to_string(),
            "poi": OUTPUT_CRS,
            "analysis": ANALYSIS_CRS,
            "output": OUTPUT_CRS,
        },
        "filters": [
            "主输入为 POI 全量原始总表（" + poi_path.name + "），编码 " + poi_encoding + "，原始记录 " + str(total_all),
            "四域大类筛选（bigType∈公共设施/科教文化服务/医疗保健服务/体育休闲服务）后 " + str(total_domain),
            "坐标使用 lon_wgs84/lat_wgs84（EPSG:4326）；候选研究范围内 " + str(inside_total),
            "仅按 midType/smallType 类别字段归组（不按名称/产权/运营主体推断）",
            f"格心落入候选研究范围的 {cell_size_m}m 单元进入结果网格",
        ],
        "aggregation": (
            f"纳入设施点按 {cell_size_m}m 稳定格心网格计数；类型丰富度=该格覆盖四大功能组中的几类；"
            "密度平滑=高斯核固定带宽 300m 的每格加权点密度估计（个/km²），无设施区保留 0"
        ),
        "classification": classification_desc,
        "relative_rule": (
            "无设施格=该格纳入设施数为 0；资源相对较多=设施数高于有设施格数量的中位数；"
            "资源相对较少=设施数>0 且不高于该中位数。分界仅表示当前 POI 下的相对空间现象。"
        ),
        "source_audit": {
            "original_file": str(poi_path),
            "original_sha256": evidence_poi_sha(poi_path),
            "encoding": poi_encoding,
            "used_fields": ["bigType", "midType", "smallType", "lon_wgs84", "lat_wgs84"],
            "classification_rule": classification_desc,
            "original_record_count": total_all,
            "domain_source_records": total_domain,
            "in_range_records": inside_total,
            "included": included_total,
            "excluded": excluded_total,
        },
        "crosscheck": crosscheck,
        "excluded_inputs": [
            "PPT/PDF 历史报告中的任何数字、地图或结论（不作为输入/目标值/验收基准）",
            "酒吧/KTV/网吧/棋牌/游戏厅等娱乐消费场所",
            "药店与医疗用品销售",
            "医疗美容（整形美容）与宠物医疗",
            "一般培训机构与驾校",
            "传媒/科研/会展等机构组织类别",
            "无法细分的泛化/顶层类别",
            "任何公办民办或产权运营主体推断",
            "历史供需图层或报告结论",
        ],
        "limitations": [
            "本板块为资源数量/类型/密度盘点，未计算覆盖、可达、容量与品质（15 分钟生活圈未做）",
            "100m 网格为 2026-09 重算口径（数字与旧 500m 版不可横比）；密度为高斯核平滑估计（带宽 300m），非逐格精确计数",
            "研究区边缘 1 个带宽内的密度受边界截断影响",
            "资源相对较少仅表示当前 POI 数据下的相对空间现象，不构成设施短缺、服务空白或规划缺口结论",
            "不推断设施公办或民办；POI 类别字段无法证明产权和运营主体",
            "POI 数量是点位计数，不是独立建筑物数，也不是服务容量、品质或利用率",
            "同一公共厕所可被数据拆为男/女/无障碍/母婴等多个点位，点位数可能高于实际设施座数",
            "泛化/顶层类别（含部分社区健康服务中心点位）因字段无法细分而未纳入，可能低估部分基层设施",
            "POI 经纬度为数据供应商提供的 WGS-84 转换字段；转换精度以数据供应商为准，本次未做二次坐标核验",
            "不做 15 分钟生活圈、真实步行可达性、覆盖人口、供需平衡、补建优先级或因果结论",
        ],
    }

    # ── 写出结果图层 ────────────────────────────────────────────
    layer_id = str(params.get("output_layer_id") or "public_service_profile_100m").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", layer_id):
        raise AnalysisInputError("output_layer_id 必须为小写字母开头且仅含小写字母、数字、_、-")
    layer_dir = (workspace / "data" / "layers").resolve()
    layer_dir.mkdir(parents=True, exist_ok=True)
    layer_path = (layer_dir / f"{layer_id}.json").resolve()
    if layer_dir not in layer_path.parents:
        raise AnalysisInputError("结果图层路径越出 workspace")

    output_columns = [
        "cell_id", "grid_x", "grid_y", "grid_version", "study_area_id",
        "facility_count", "education_culture_count", "medical_health_count",
        "sports_count", "convenience_emergency_count", "type_richness",
        "facility_class",
        "education_culture_density", "medical_health_density",
        "sports_density", "convenience_emergency_density",
        "facility_density_km2",
        "center_lon", "center_lat", "geometry",
    ]
    output_grid = grid[output_columns].to_crs(OUTPUT_CRS)
    geojson = json.loads(output_grid.to_json(drop_id=True, na="null"))
    geojson["name"] = layer_id
    geojson["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}}
    layer_path.write_text(json.dumps(geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    fields = [
        {"name": "cell_id", "type": "string", "description": "全局锚定 100m 网格 ID"},
        {"name": "facility_count", "type": "integer", "description": "该格纳入设施总点数"},
        {"name": "education_culture_count", "type": "integer", "description": "教育文化设施点数"},
        {"name": "medical_health_count", "type": "integer", "description": "医疗健康设施点数"},
        {"name": "sports_count", "type": "integer", "description": "体育活动设施点数"},
        {"name": "convenience_emergency_count", "type": "integer", "description": "便民与应急设施点数"},
        {"name": "type_richness", "type": "integer", "description": "类型丰富度=覆盖四大功能组中的几类"},
        {"name": "facility_class", "type": "string", "description": "相对分型：none=无设施格 low=资源相对较少格 high=资源相对较多格"},
        {"name": "education_culture_density", "type": "number", "description": "教育文化设施密度平滑估计（高斯核带宽 300m，个/km²）"},
        {"name": "medical_health_density", "type": "number", "description": "医疗健康设施密度平滑估计（高斯核带宽 300m，个/km²）"},
        {"name": "sports_density", "type": "number", "description": "体育活动设施密度平滑估计（高斯核带宽 300m，个/km²）"},
        {"name": "convenience_emergency_density", "type": "number", "description": "便民与应急设施密度平滑估计（高斯核带宽 300m，个/km²）"},
        {"name": "facility_density_km2", "type": "number", "description": "四功能组合计密度平滑估计（个/km²；距设施>900m 的格为 0）"},
    ]

    class_parts = ", ".join(
        f"{CLASS_LABELS[k]} {class_counts[k]} 格" for k in ("none", "low", "high")
    )
    richness_parts = "；".join(
        f"覆盖{t}类 {int(richness_counts[t])} 格" for t in range(1, 5) if richness_counts.get(t)
    )
    summary = (
        f"公共服务设施资源画像已完成：主表（{poi_path.name}，原始 {total_all} 条）中四域大类 {total_domain} 条，"
        f"候选范围内 {inside_total} 条，按类别纳入清单纳入 {included_total} 个、排除 {excluded_total} 个；"
        f"教育文化 {group_counts['education_culture']}、医疗健康 {group_counts['medical_health']}、"
        f"体育活动 {group_counts['sports']}、便民与应急 {group_counts['convenience_emergency']}；"
        f"100m 稳定网格 {grid_count} 格（含密度平滑多档渐变；{class_parts}；类型丰富度：{richness_parts}）。"
        "仅描述当前 POI 下的相对空间现象，不推断公办民办，也不构成设施短缺或规划缺口。"
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
                "name": "公共服务设施资源画像",
                "path": f"data/layers/{layer_id}.json",
                "feature_count": grid_count,
                "geometry": "Polygon",
                "kind": "grid",
                "crs": OUTPUT_CRS,
                "fields": fields,
                "style": {
                    "mode": "graduated",
                    "field": "facility_density_km2",
                    "method": "quantile",
                    "classes": 6,
                    "palette": PUBLIC_DENSITY_RAMP,
                    "fillColor": "#f8fafc",
                    "fillOpacity": 0.72,
                    "strokeWidth": 0,
                },
                "description": "100m 全局锚定格网；四功能组设施密度平滑估计多档渐变（单色浅→深，无设施格退让近底）；点数/类型丰富度/相对分型字段保留为辅助口径",
            }
        ],
    }


def evidence_poi_sha(poi_path: Path) -> str:
    return _sha256(poi_path)


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
