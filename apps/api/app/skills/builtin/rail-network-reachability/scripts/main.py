# -*- coding: utf-8 -*-
"""全区地铁步行可达率确定性 Skill（rail-network-reachability）。

把研究范围内全部真实地铁出入口按 300m 距离聚为「出入口簇」，每簇取距簇质心最近的
真实出入口为代表；对每个代表调用 Mapbox 真实路网 walking 5/10/15 分钟等时圈，
跨簇并成三档可达带与 15 分钟外难达区，统计覆盖面积 km² / 覆盖率% / 难达区 /
商业点在并集内数量，并做面积守恒自检。全部失败诚实停止；部分失败只用成功簇并写明。

真计算与红线：
- 只称「出入口簇/代表出入口」，不冒充地铁站名；地图不加站名标签。
- 覆盖率的几何只来自真实路网等时圈；500m 直线圆、演示 stable_rate 一律不用。
- 数字来自真实原始文件（研究范围/出入口 shp/商业专题源+连接表），统计全量。
- token 不落盘、不进日志/缓存/图层。预计算缓存（含输入 sha256）命中即秒开。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import geopandas as gpd
from pyproj import CRS

from app.tools.isochrone import _call_isochrone, _mapbox_token

# A1 轨道服务盲区叠加（同目录纯函数模块：脚本方式运行 / importlib 载入都可用）
_SKILL_SCRIPTS = Path(__file__).resolve().parent
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))
import residential_overlay as _ro  # noqa: E402

ANALYSIS_CRS = "EPSG:32650"  # UTM 50N，适合深圳
OUTPUT_CRS = "EPSG:4326"
STUDY_AREA_ID = "candidate_lohas_study_area"
CLUSTER_DISTANCE_M = 300
MINUTES = (5, 10, 15)

DEFAULT_STUDY_AREA = "docs/workstreams/analysis-foundation/data/candidate_lohas_study_area.geojson"
DEFAULT_ENTRANCE = "../04-乐活大道GIS数据/00-原始数据/专项资源/地铁出入口/subway_entrance.shp"
DEFAULT_COMMERCIAL = "../04-乐活大道GIS数据/00-原始数据/专项资源/商业资源/商业资源Export_Output_46.shp"
DEFAULT_COMMERCIAL_LINK = "../04-乐活大道GIS数据/00-原始数据/专项资源/商业资源/商业资源_商业中小类_连接表_utf8.csv"

# 等时圈档位色（内→外 深→浅；与 isochrone 工具同色系）
ISO_COLORS = {"5": "#0c4a6e", "10": "#0ea5e9", "15": "#bae6fd"}

BANDS_LAYER_ID = "rail_network_walking_bands"
HARD_LAYER_ID = "rail_network_hard_to_reach"
CLUSTERS_LAYER_ID = "rail_entrance_clusters"

# A1：难达区内的盲区图层（居住地块 / 人口·就业格）
BLIND_RES_LAYER_ID = "rail_blindspot_residential"
BLIND_POP_LAYER_ID = "rail_blindspot_population"
DEFAULT_LAND_SHP = _ro.DEFAULT_LAND_SHP
DEFAULT_POP_GEOJSON = _ro.DEFAULT_POP_GEOJSON

# 商业 POI × 地铁可达：每个范围内商业点按 ≤5/≤10/≤15 并集求 reach_class（'out'=15 分钟外）
POI_LAYER_ID = "rail_poi_reach_study"
# 点色：≤5 深 → ≤15 浅，区外近底灰（浅底图上仍可辨；见 REACH_COLORS）
REACH_COLORS = {"5": "#1e3a8a", "10": "#3b82f6", "15": "#93c5fd", "out": "#d1d5db"}


class AnalysisInputError(ValueError):
    """输入/口径不满足本 Skill 契约。"""


class IsochroneAllFailed(RuntimeError):
    """真实路网全部失败：诚实停止（success=false），不落演示覆盖率。"""


# ── 输入读取 / 血缘 ────────────────────────────────────────────

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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_version(path: Path) -> dict[str, Any]:
    return {"path": str(path), "name": path.name, "sha256": _sha256(path)}


def load_study_boundary(path: Path, crs_value: Any) -> tuple[Any, float]:
    """读研究范围（WGS84）→ 返回 (EPSG:32650 并集几何, 面积 km²)。"""
    study = gpd.read_file(path)
    _require_file_crs(study, _crs(crs_value, "研究范围"), "研究范围")
    if study.empty or study.geometry.isna().any() or study.geometry.is_empty.any():
        raise AnalysisInputError("研究范围没有可用几何")
    boundary = study.to_crs(ANALYSIS_CRS).geometry.union_all()
    area_km2 = float(boundary.area / 1_000_000)
    if area_km2 <= 0:
        raise AnalysisInputError("研究范围面积无效")
    return boundary, area_km2


def load_entrances_in_range(path: Path, boundary_m: Any) -> gpd.GeoDataFrame:
    """读出入口点（WGS84），返回 EPSG:4326 且格心落在研究范围内的点（无标签依赖）。"""
    gdf = gpd.read_file(path)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf = gdf[gdf.geometry.geom_type == "Point"].copy()
    if gdf.crs is None:
        raise AnalysisInputError("出入口文件没有 CRS 元数据")
    gdf_m = gdf.to_crs(ANALYSIS_CRS)
    inside = gdf_m.loc[gdf_m.geometry.within(boundary_m)].copy()
    if inside.empty:
        raise AnalysisInputError("研究范围内没有地铁出入口")
    return inside.to_crs(OUTPUT_CRS).reset_index(drop=True)


def load_commercial_in_range(commercial_path: Path, link_path: Path, boundary_m: Any) -> gpd.GeoDataFrame:
    """复用共享模块：isdelete=0 → EPSG:32650 within 研究范围 → EPSG:4326 点。"""
    from app.gis import commercial_poi

    return commercial_poi.load_classified_commercial_points(commercial_path, link_path, boundary_m)


# ── 出入口聚簇（纯函数，可测试） ───────────────────────────────

def cluster_entrances(coords: list[tuple[float, float]], distance_m: float) -> list[list[int]]:
    """两两欧氏距离 ≤ distance_m 视为同簇（union-find）；返回索引簇列表（确定性排序）。"""
    n = len(coords)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        xi, yi = coords[i]
        for j in range(i + 1, n):
            xj, yj = coords[j]
            if (xi - xj) ** 2 + (yi - yj) ** 2 <= distance_m * distance_m:
                parent[find(i)] = find(j)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    for members in groups.values():
        members.sort()
    return sorted(groups.values(), key=lambda members: (members[0], len(members)))


def representative_indices(clusters: list[list[int]], coords: list[tuple[float, float]]) -> list[int]:
    """每簇取距簇质心最近的真实出入口下标（确定性：平局取更小下标）。"""
    reps: list[int] = []
    for members in clusters:
        mx = sum(coords[i][0] for i in members) / len(members)
        my = sum(coords[i][1] for i in members) / len(members)
        best = min(members, key=lambda i: ((coords[i][0] - mx) ** 2 + (coords[i][1] - my) ** 2, i))
        reps.append(best)
    return reps


# ── 等时圈请求与融合 ───────────────────────────────────────────

def fetch_isochrones(reps_wgs84: list[tuple[float, float]], *, max_total_s: float = 240.0) -> tuple[dict[int, list[Any]], list[int]]:
    """对每个代表串行请求 5/10/15（0.3s 间隔）；返回 {minute: [EPSG:4326 geom]} 与失败下标。

    总耗时护栏：超过 max_total_s 立即整体停止（诚实失败），避免长时间把后端拖在重试里。
    """
    started = time.monotonic()
    by_minute: dict[int, list[Any]] = {m: [] for m in MINUTES}
    failed: list[int] = []
    for idx, (lon, lat) in enumerate(reps_wgs84):
        if time.monotonic() - started > max_total_s:
            raise IsochroneAllFailed(
                f"真实路网请求累计超过 {int(max_total_s)}s（已成功 {idx} 个代表、失败 {len(failed)} 个），"
                "已停止以免拖垮服务；请检查 MAPBOX_TOKEN/网络，或先跑一次预计算缓存"
            )
        try:
            payload = _call_isochrone("walking", lon, lat, list(MINUTES), 0)
        except Exception as exc:  # noqa: BLE001 —— 单个中心失败不致命，记入失败列表
            failed.append(idx)
            continue
        feats = payload.get("features") if isinstance(payload, dict) else []
        for feat in feats or []:
            props = feat.get("properties") or {}
            contour = props.get("contour")
            geom = feat.get("geometry")
            if contour is None or not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
                continue
            by_minute.setdefault(int(contour), []).append(geom)
        time.sleep(0.3)
    return by_minute, failed


def _to_metered(features_by_minute: dict[int, list[Any]]) -> dict[int, Any]:
    from shapely.geometry import shape

    fused: dict[int, Any] = {}
    for m in MINUTES:
        geoms = features_by_minute.get(m, [])
        if not geoms:
            continue
        gdf = gpd.GeoDataFrame(geometry=[shape(g) for g in geoms], crs=OUTPUT_CRS).to_crs(ANALYSIS_CRS)
        fused[m] = gdf.geometry.union_all()
    return fused


# ── 统计与守恒 ─────────────────────────────────────────────────

def reachability_stats(
    boundary_m: Any,
    fused: dict[int, Any],
    commercial_m: gpd.GeoDataFrame,
    *,
    area_km2: float,
) -> dict[str, Any]:
    """由米制融合几何算面积/覆盖率/难达区/商业点累计与守恒（纯函数，可注入合成几何测试）。"""
    mins = sorted(fused)  # 实际成功的分钟档（通常 [5,10,15]）
    # 等时圈会延伸出研究区边界；先 clip 到 boundary 再统计/输出，保证 ≤研究区面积守恒。
    # 跨分钟保序：5 ⊆ 10 ⊆ 15（缺失档从上一档继承并集 → 仍守恒）。
    levels: list[tuple[int, Any]] = []
    for m in MINUTES:
        if m in fused:
            geom = fused[m].intersection(boundary_m)
            levels.append((m, geom))
        elif levels:
            levels.append((m, levels[-1][1]))
    if not levels:
        raise RuntimeError("没有任何成功档位")

    full = levels[-1][1]  # ≤15 分钟覆盖 ∩ 研究区（≤研究区面积守恒基准）
    total_km2 = float(boundary_m.area / 1_000_000)
    if abs(total_km2 - area_km2) / area_km2 > 1e-6:
        total_km2 = area_km2

    def _km2(geom: Any) -> float:
        return round(float(geom.area / 1_000_000), 4)

    def _rate(km2: float) -> float:
        return round(km2 / total_km2 * 100, 2) if total_km2 > 0 else 0.0

    # 环带（相邻档相减，不重叠）+ 难达区
    ring_km2: dict[str, float] = {}
    ring_polys: dict[str, Any] = {}
    prev: Any | None = None
    for m, geom in levels:
        ring = geom.difference(prev) if prev is not None else geom
        ring_km2[str(m)] = _km2(ring)
        ring_polys[str(m)] = ring
        prev = geom
    hard = boundary_m.difference(levels[-1][1])
    hard_km2 = _km2(hard)
    outside_km2 = total_km2 - _km2(levels[-1][1])
    # 防御：差集因精度略偏时，用总守恒回填 hard
    hard_km2 = max(hard_km2, outside_km2 - 1e-4) if outside_km2 > hard_km2 else hard_km2
    if hard_km2 < 0:
        hard_km2 = 0.0

    # 商业点累计（并集内 within）
    def _counts_within(geom: Any) -> dict[str, int]:
        if geom is None or geom.is_empty:
            return {}
        mask = commercial_m.geometry.within(geom)
        return {"total": int(mask.sum())}

    commercial_cum: dict[int, dict[str, int]] = {}
    for m, geom in levels:
        commercial_cum[m] = _counts_within(geom)

    # 每档「新增」商业点 + 难达区外 = 用环带 within
    band_counts: dict[str, dict[str, int]] = {}
    for m, geom in levels:
        band_counts[str(m)] = _counts_within(ring_polys[str(m)])
    outside_points = _counts_within(boundary_m.difference(levels[-1][1]))

    # 商业点 ≤15 覆盖的分类构成（累计，去重由 within 保证）
    total_by_category: dict[str, int] = {}
    if commercial_m is not None and len(commercial_m):
        if "category" in commercial_m.columns:
            cum15 = commercial_m.geometry.within(levels[-1][1])
            for cat in dict.fromkeys(str(x) for x in commercial_m.loc[cum15, "category"]):
                total_by_category[cat] = int((commercial_m.loc[cum15, "category"] == cat).sum())

    conservation = {
        "<=5": round(ring_km2.get("5", 0.0), 4),
        "5_10": round(ring_km2.get("10", 0.0), 4),
        "10_15": round(ring_km2.get("15", 0.0), 4),
        "hard": round(hard_km2, 4),
    }
    conservation["sum"] = round(sum(conservation.values()), 4)
    conservation["study"] = round(total_km2, 4)

    def _band_stat(m: int) -> dict[str, Any]:
        cum_km2 = round(sum(ring_km2[str(x)] for x in MINUTES if x <= m), 4)
        return {
            "cumulative_area_km2": cum_km2,
            "coverage_rate": _rate(cum_km2),
            "cumulative_count": int(commercial_cum.get(m, {}).get("total", 0)),
        }

    return {
        "minutes": list(MINUTES),
        "bands": {str(m): _band_stat(m) for m in MINUTES},
        "ring_area_km2": ring_km2,
        "band_commercial_counts": band_counts,
        "hard_to_reach_km2": round(hard_km2, 4),
        "hard_to_reach_rate": _rate(hard_km2),
        "reachable_15_km2": round(_km2(levels[-1][1]), 4),
        "reachable_15_rate": _rate(_km2(levels[-1][1])),
        "total_commercial_points": int(len(commercial_m)),
        "total_by_category": total_by_category,
        "conservation": conservation,
    }


# ── 输出图层（EPSG:4326 GeoJSON） ──────────────────────────────

def geojson_of(geom: Any, props: dict) -> dict:
    from shapely.geometry import mapping

    if geom is None or geom.is_empty:
        return {"type": "FeatureCollection", "features": []}
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": props, "geometry": mapping(geom)}]}


# ── 缓存 ───────────────────────────────────────────────────────

def _input_fingerprint(versions: list[Path]) -> str:
    h = hashlib.sha256()
    for path in versions:
        h.update(path.name.encode("utf-8"))
        h.update(_sha256(path).encode("ascii"))
    return h.hexdigest()[:16]


def _cache_path(workspace: Path, fingerprint: str) -> Path:
    cache_dir = (workspace / "data" / "cache").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"rail_network_reachability_{fingerprint}.json"


# ── 主入口 ─────────────────────────────────────────────────────

def run_analysis(params: dict[str, Any], workspace: Path) -> dict[str, Any]:
    repo_root = _repo_root()
    study_path = _resolve_path(repo_root, params.get("study_area_path"), DEFAULT_STUDY_AREA)
    entrance_path = _resolve_path(repo_root, params.get("subway_entrance_path"), DEFAULT_ENTRANCE)
    commercial_path = _resolve_path(repo_root, params.get("commercial_path"), DEFAULT_COMMERCIAL)
    link_path = _resolve_path(repo_root, params.get("commercial_link_path"), DEFAULT_COMMERCIAL_LINK)
    study_crs = _crs(params.get("study_area_crs", OUTPUT_CRS), "研究范围")
    try:
        cluster_dist = int(params.get("cluster_distance_m", CLUSTER_DISTANCE_M))
    except (TypeError, ValueError):
        cluster_dist = CLUSTER_DISTANCE_M
    if cluster_dist <= 0 or cluster_dist > 1000:
        raise AnalysisInputError("cluster_distance_m 必须为 1–1000（米）")
    use_cache = str(params.get("use_cache", "true")).strip().lower() not in ("0", "false", "no", "off")

    boundary_m, area_km2 = load_study_boundary(study_path, study_crs)
    entrances = load_entrances_in_range(entrance_path, boundary_m)
    entrance_count = int(len(entrances))

    versions = [study_path, entrance_path, commercial_path, link_path]
    fingerprint = _input_fingerprint(versions)
    cache_file = _cache_path(workspace, fingerprint)

    # 预计算缓存命中：秒开，不再实时批请求
    if use_cache and cache_file.is_file():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if cached.get("fingerprint") == fingerprint and cached.get("stats"):
                return _output_from_cache(cached, entrance_count, cache_file, workspace, repo_root, params)
        except Exception:  # noqa: BLE001 —— 缓存损坏则回退实时计算
            pass

    # 出入口聚簇 → 代表
    ent_m = entrances.to_crs(ANALYSIS_CRS)
    coords = [(float(p.x), float(p.y)) for p in ent_m.geometry]
    clusters = cluster_entrances(coords, float(cluster_dist))
    rep_idx = representative_indices(clusters, coords)
    reps_wgs84 = [(float(entrances.geometry.iloc[i].x), float(entrances.geometry.iloc[i].y)) for i in rep_idx]

    # 硬化（避免拖挂）：无缓存且无 token → 快速诚实停止，不逐口重试
    if not str(_mapbox_token() or "").strip():
        raise IsochroneAllFailed(
            "未配置 MAPBOX_TOKEN（且无预计算缓存命中）：无法请求真实路网等时圈。"
            "请在 apps/api/.env 配置 MAPBOX_TOKEN 或先跑一次预计算缓存后再演示"
        )

    by_minute, failed = fetch_isochrones(reps_wgs84)
    success_count = len(rep_idx) - len(failed)
    if success_count == 0:
        raise IsochroneAllFailed(
            "全部出入口簇代表的真实路网等时圈请求失败，不做演示覆盖率，请检查 MAPBOX_TOKEN 与网络后重试"
        )
    fused = _to_metered(by_minute)
    if not fused:
        raise IsochroneAllFailed("真实路网未返回任何有效等时圈多边形")

    # 商业点（米制）
    commercial_4326 = load_commercial_in_range(commercial_path, link_path, boundary_m)
    commercial_m = commercial_4326.to_crs(ANALYSIS_CRS)

    stats = reachability_stats(boundary_m, fused, commercial_m, area_km2=area_km2)
    stats["entrances"] = {"in_study_area": entrance_count}
    stats["clusters"] = {"cluster_count": len(clusters), "representatives": len(rep_idx)}
    stats["centers"] = {
        "requested": len(rep_idx),
        "succeeded": success_count,
        "failed": len(failed),
        "rule": f"{cluster_dist}m 距离出入口簇 → 每簇取距质心最近真实出入口为代表（真实路网 walking 5/10/15）",
    }
    stats["study_area_km2"] = round(area_km2, 6)

    # 图层几何（EPSG:4326）：clip 到研究区的环带 + 难达区（与研究区边界内面积守恒）
    inside_levels = [(m, fused[m].intersection(boundary_m)) for m in MINUTES if m in fused]
    prev: Any | None = None
    ring_features: list[dict] = []
    for m, geom in inside_levels:
        ring = geom.difference(prev) if prev is not None else geom
        if ring.is_empty:
            prev = geom
            continue
        from shapely.geometry import mapping as _mapping

        ring_features.append(
            {
                "type": "Feature",
                "properties": {"minute": int(m), "label": f"{m} 分钟", "kind": "reachable"},
                "geometry": _mapping(_ro.as_wgs84(ring)),  # 米制计算 → WGS84 经纬度落盘
            }
        )
        prev = geom
    bands_gj = {"type": "FeatureCollection", "features": ring_features}
    hard_geom = boundary_m.difference(inside_levels[-1][1]) if inside_levels else boundary_m
    hard_gj = geojson_of(_ro.as_wgs84(hard_geom), {"kind": "hard_to_reach", "label": "难达区（15 分钟外）"})
    cluster_feats = [
        {
            "type": "Feature",
            "properties": {
                "cluster_id": ci,
                "cluster_size": int(len(clusters[ci])),
                "entrance_count_total": entrance_count,
                "kind": "entrance_cluster",
            },
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
        }
        for ci, (lon, lat) in enumerate(reps_wgs84)
    ]
    clusters_gj = {"type": "FeatureCollection", "features": cluster_feats}

    geojson_by_id = {BANDS_LAYER_ID: bands_gj, HARD_LAYER_ID: hard_gj, CLUSTERS_LAYER_ID: clusters_gj}

    # A1：居住用地 × 人口/就业 × 可达带/难达区 叠加（并入 stats + 两幅盲区图层）
    _attach_blindspot(stats, geojson_by_id, repo_root, params)
    # 商业 POI × 地铁可达：reach_class 第 4 图层 rail_poi_reach_study + stats（只加不拆）
    _attach_commercial_reach(stats, geojson_by_id, repo_root, params)

    evidence = _build_evidence(
        versions,
        entrance_count,
        stats,
        success_count,
        len(rep_idx),
        cluster_dist,
        cached=False,
        cache_file=cache_file,
    )

    # 落盘图层文件（data/layers，供输出 layers 引用）+ 写缓存（真实结果秒开复用）
    materialized = _materialize_layers(workspace, geojson_by_id)
    try:
        cache_file.write_text(
            json.dumps(
                {
                    "fingerprint": fingerprint,
                    "stats": stats,
                    "evidence": evidence,
                    "geojson": geojson_by_id,
                    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 —— 缓存写失败不致命
        pass

    return _output(stats, evidence, materialized, entrance_count, cluster_dist)


def _build_evidence(
    versions: list[Path],
    entrance_count: int,
    stats: dict[str, Any],
    success_count: int,
    requested: int,
    cluster_dist: int,
    *,
    cached: bool,
    cache_file: Path,
) -> dict[str, Any]:
    limits = [
        "等时圈只来自 Mapbox 真实路网 walking 5/10/15；覆盖率分母=研究区面积 km²，不是出入口数量",
        "出入口簇仅按 300m 距离聚簇的「出入口簇/代表出入口」，不构成地铁站名归属",
        "覆盖率受路网与步行假设影响；难达区指 15 分钟步行未覆盖区域，不代表无任何交通可达",
        "商业点计数是点位数量，不是服务容量或真实人流",
    ]
    if cached:
        limits.append("本次命中预计算缓存（输入 sha256 校验一致），未重新请求 Mapbox")
    return {
        "input_versions": {p.name: _file_version(p) for p in versions},
        "crs": {
            "study_area_file": OUTPUT_CRS,
            "entrances": OUTPUT_CRS,
            "commercial": OUTPUT_CRS,
            "analysis": ANALYSIS_CRS,
            "output": OUTPUT_CRS,
        },
        "method": (
            f"范围内 {entrance_count} 个真实出入口 → {cluster_dist}m 距离出入口簇 "
            f"({stats['clusters']['cluster_count']} 簇) → 每簇真实代表 × walking 5/10/15 真实路网 → "
            f"跨簇并集 → 覆盖面积/率 + 难达区 + 商业点 within 累计；守恒自检=各带+难达=研究区面积"
        ),
        "centers": {
            "requested": requested,
            "succeeded": success_count,
            "failed": int(stats["centers"]["failed"]),
            "rule": stats["centers"]["rule"],
        },
        "cache": {"used": cached, "path": str(cache_file)},
        "limitations": limits,
    }


def _materialize_layers(workspace: Path, geojson_by_id: dict[str, dict]) -> list[dict]:
    layer_dir = (workspace / "data" / "layers").resolve()
    layer_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    styles = {
        BANDS_LAYER_ID: {"mode": "categorized", "field": "minute", "classes": [{"value": str(m), "color": c} for m, c in ISO_COLORS.items()], "strokeWidth": 0, "fillOpacity": 0.62},
        HARD_LAYER_ID: {"fillColor": "#cbd5e1", "fillOpacity": 0.35, "strokeWidth": 0},
        CLUSTERS_LAYER_ID: {"radius": 6, "fillColor": "#334155", "strokeColor": "#ffffff", "strokeWidth": 1},
        BLIND_RES_LAYER_ID: {"mode": "categorized", "field": "kind", "classes": [{"value": "residential_blindspot", "color": "#e11d48"}], "fillColor": "#e11d48", "fillOpacity": 0.5, "strokeColor": "#7f1d1d", "strokeWidth": 1},
        BLIND_POP_LAYER_ID: {"mode": "categorized", "field": "kind", "classes": [{"value": "population_blindspot", "color": "#f97316"}], "fillColor": "#f97316", "fillOpacity": 0.55, "strokeColor": "#9a3412", "strokeWidth": 1},
        POI_LAYER_ID: {"mode": "categorized", "field": "reach_class", "classes": [{"value": v, "color": c} for v, c in REACH_COLORS.items()], "radius": 3, "fillColor": "#1e3a8a", "strokeColor": "#ffffff", "strokeWidth": 0.5, "fillOpacity": 0.85},
    }
    for layer_id, gj in geojson_by_id.items():
        path = layer_dir / f"{layer_id}.json"
        path.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
        count = len(gj.get("features") or [])
        geom = _dominant_geom(gj)
        layer = {
            "layer_id": layer_id,
            "name": _layer_name(layer_id),
            "path": f"data/layers/{layer_id}.json",
            "feature_count": count,
            "geometry": geom,
            "crs": OUTPUT_CRS,
            "style": styles[layer_id],
        }
        layer["description"] = _layer_name(layer_id)
        out.append(layer)
    return out


def _dominant_geom(geojson: dict) -> str:
    """按首要素几何类型归一为平台 GeometryType 可接受的枚举值（面统一记 Polygon）。"""
    for feature in geojson.get("features", [])[:1]:
        gtype = (feature.get("geometry") or {}).get("type")
        if gtype == "Point":
            return "Point"
        if gtype in ("Polygon", "MultiPolygon", "GeometryCollection"):
            return "Polygon"
    return "Polygon"


def _layer_name(layer_id: str) -> str:
    return {
        BANDS_LAYER_ID: "研究区步行可达带（5/10/15 分钟，真实路网）",
        HARD_LAYER_ID: "难达区（15 分钟步行外）",
        CLUSTERS_LAYER_ID: "出入口簇（真实出入口代表）",
        BLIND_RES_LAYER_ID: "盲区居住地块（15 分钟步行外）",
        BLIND_POP_LAYER_ID: "盲区人口/就业格（15 分钟步行外）",
        POI_LAYER_ID: "商业 POI · 地铁步行可达（5/10/15/外）",
    }.get(layer_id, layer_id)


def _attach_blindspot(stats: dict, geojson_by_id: dict, repo_root: Path, params: dict) -> bool:
    """A1：把「居住用地 × 人口/就业 × 可达带/难达区」叠加结果并入 stats 与图层集。

    可达几何复用本 run 的环带/难达 geojson（rail 图层按其米制坐标落盘，此处同口径解读），
    再裁到研究区边界；用地/人口为本地文件。可选数据缺失时静默跳过（保持 rail 主结果可用）；
    显式传了参数却缺文件则失败，避免默默降级。
    """
    try:
        from shapely.geometry import shape

        explicit_land = params.get("land_use_path") is not None
        explicit_pop = params.get("population_grid_path") is not None
        land_default = (repo_root / _ro.DEFAULT_LAND_SHP).resolve()
        pop_default = (repo_root / _ro.DEFAULT_POP_GEOJSON).resolve()
        if explicit_land:
            land_path = _resolve_path(repo_root, params["land_use_path"], _ro.DEFAULT_LAND_SHP)
        elif land_default.is_file():
            land_path = land_default
        else:
            land_path = None
        if explicit_pop:
            pop_path = _resolve_path(repo_root, params["population_grid_path"], _ro.DEFAULT_POP_GEOJSON)
        elif pop_default.is_file():
            pop_path = pop_default
        else:
            pop_path = None
        if land_path is None or pop_path is None:
            return False

        study_path = _resolve_path(repo_root, params.get("study_area_path"), DEFAULT_STUDY_AREA)
        study_crs = _crs(params.get("study_area_crs", OUTPUT_CRS), "研究范围")
        boundary_m, _area = load_study_boundary(study_path, study_crs)

        bands = (geojson_by_id.get(BANDS_LAYER_ID) or {}).get("features") or []
        band_geoms = [
            (int(f["properties"]["minute"]), _ro.as_meters(shape(f["geometry"])))
            for f in bands
            if f.get("geometry") and f.get("properties", {}).get("minute") is not None
        ]
        if not band_geoms:
            return False
        hard_feats = (geojson_by_id.get(HARD_LAYER_ID) or {}).get("features") or []
        hard_m = _ro.as_meters(shape(hard_feats[0]["geometry"])) if hard_feats else None
        if hard_m is None:
            return False

        res_gdf_m = _ro.load_residential_land(land_path)
        pop_gdf_m = _ro.load_population_grid(pop_path)
        cum_by_minute = _ro.cum_geoms_from_bands(band_geoms, boundary_m)
        extra = _ro.residential_blindspot(
            res_gdf_m=res_gdf_m,
            pop_gdf_m=pop_gdf_m,
            boundary_m=boundary_m,
            hard_m=hard_m,
            cum_by_minute=cum_by_minute,
        )
        res_gj, pop_gj = _ro.blind_features(res_gdf_m, pop_gdf_m, boundary_m, hard_m)
        stats["residential_blindspot"] = extra
        geojson_by_id[BLIND_RES_LAYER_ID] = res_gj
        geojson_by_id[BLIND_POP_LAYER_ID] = pop_gj
        return True
    except AnalysisInputError:
        raise
    except Exception:  # noqa: BLE001 —— 可选叠加失败不阻断可达主结果
        return False


def _attach_commercial_reach(stats: dict, geojson_by_id: dict, repo_root: Path, params: dict) -> bool:
    """商业 POI × 地铁可达：范围内每个商业点按 ≤5/≤10/≤15 并集求 reach_class（'out'=区外）。

    只加不拆：不动可达带/守恒/既有 stats 键；新增第 4 图层 rail_poi_reach_study（EPSG:4326 点）
    与 stats.commercial_reach。fresh 与缓存命中两路径都调（本地读商业文件，不入 Mapbox 指纹）。
    """
    try:
        from shapely.geometry import mapping as _mapping
        from shapely.geometry import shape

        comm_default = (repo_root / DEFAULT_COMMERCIAL).resolve()
        link_default = (repo_root / DEFAULT_COMMERCIAL_LINK).resolve()
        if params.get("commercial_path") is not None:
            comm_path = _resolve_path(repo_root, params["commercial_path"], DEFAULT_COMMERCIAL)
        elif comm_default.is_file():
            comm_path = comm_default
        else:
            comm_path = None
        if params.get("commercial_link_path") is not None:
            link_path = _resolve_path(repo_root, params["commercial_link_path"], DEFAULT_COMMERCIAL_LINK)
        elif link_default.is_file():
            link_path = link_default
        else:
            link_path = None
        if comm_path is None or link_path is None:
            return False

        study_path = _resolve_path(repo_root, params.get("study_area_path"), DEFAULT_STUDY_AREA)
        study_crs = _crs(params.get("study_area_crs", OUTPUT_CRS), "研究范围")
        boundary_m, _area = load_study_boundary(study_path, study_crs)
        bands = (geojson_by_id.get(BANDS_LAYER_ID) or {}).get("features") or []
        band_geoms = [
            (int(f["properties"]["minute"]), _ro.as_meters(shape(f["geometry"])))
            for f in bands
            if f.get("geometry") and f.get("properties", {}).get("minute") is not None
        ]
        if not band_geoms:
            return False
        cum = _ro.cum_geoms_from_bands(band_geoms, boundary_m)
        mins = sorted(cum)
        if not mins:
            return False

        comm4326 = load_commercial_in_range(comm_path, link_path, boundary_m)
        if len(comm4326) == 0:
            return False
        comm_m = comm4326.to_crs(ANALYSIS_CRS)

        # reach_class：取最小分钟并集（within），否则 out
        idx = list(comm_m.index)
        cls_map: dict[int, str] = {}
        remaining = set(idx)
        within_by_m: dict[int, list[int]] = {}
        for m in mins:
            within_ser = comm_m.geometry.within(cum[m])
            hit = [ix for ix in remaining if bool(within_ser.loc[ix])]
            for ix in hit:
                cls_map[ix] = str(m)
                remaining.discard(ix)
            within_by_m[m] = hit
        for ix in remaining:
            cls_map[ix] = "out"
        labels = [str(m) for m in mins] + ["out"]
        counts = {lab: int(sum(1 for c in cls_map.values() if c == lab)) for lab in labels}
        total = int(len(comm_m))
        pct = {lab: round(counts[lab] / total * 100, 2) if total else 0.0 for lab in labels}
        by_class_category: dict[str, dict[str, int]] = {}
        for lab in labels:
            sub = comm_m.loc[[ix for ix in idx if cls_map.get(ix) == lab]]
            if len(sub) == 0:
                continue
            by_class_category[lab] = {str(k): int(v) for k, v in sub["category"].value_counts().items()}

        stats["commercial_reach"] = {
            "total": total,
            "counts": counts,
            "pct": pct,
            "by_class_category": by_class_category,
            "conservation": {
                "reach15_cumulative": int(sum(counts[l] for l in (str(m) for m in mins))),
                "out": counts["out"],
                "sum": total,
                "total": total,
                "delta_pct": 0.0,
            },
        }

        feats = []
        for ix in idx:
            pt = comm4326.loc[ix]
            feats.append(
                {
                    "type": "Feature",
                    "properties": {
                        "objectid": str(pt["objectid"]),
                        "name": str(pt["name"]),
                        "category": str(pt["category"]),
                        "reach_class": cls_map[ix],
                    },
                    "geometry": _mapping(pt.geometry),
                }
            )
        geojson_by_id[POI_LAYER_ID] = {"type": "FeatureCollection", "features": feats}
        return True
    except AnalysisInputError:
        raise
    except Exception:  # noqa: BLE001 —— 商业可达叠加失败不阻断可达主结果
        return False


def _output(stats: dict[str, Any], evidence: dict[str, Any], materialized: list[dict], entrance_count: int, cluster_dist: int) -> dict[str, Any]:
    conserve = stats["conservation"]
    summary = (
        f"全区地铁可达已完成：研究范围 {stats['study_area_km2']} km² 内 {entrance_count} 个出入口 → "
        f"{cluster_dist}m 距离聚为 {stats['clusters']['cluster_count']} 个出入口簇、{stats['clusters']['representatives']} 个真实代表；"
        f"真实步行 5/10/15 分钟累计覆盖 {stats['reachable_15_km2']} km²（占研究区 {stats['reachable_15_rate']}%），"
        f"难达区 {stats['hard_to_reach_km2']} km²（{stats['hard_to_reach_rate']}%）；"
        f"5/10/15 分钟可及商业点 {stats['bands']['5']['cumulative_count']}/"
        f"{stats['bands']['10']['cumulative_count']}/{stats['bands']['15']['cumulative_count']} 个。"
        f"面积守恒自检：≤5+5-10+10-15+难达={conserve['sum']} km²（研究区 {conserve['study']} km²）。"
        "仅描述真实路网步行可达，不代表无任何交通可达；出入口簇不冒充站名。"
    )
    return {
        "success": True,
        "summary": summary,
        "layer_id": BANDS_LAYER_ID,
        "stats": stats,
        "evidence": evidence,
        "layers": materialized,
    }


def _output_from_cache(cached: dict, entrance_count: int, cache_file: Path, workspace: Path, repo_root: Path, params: dict) -> dict[str, Any]:
    stats = cached["stats"]
    geojson = cached.get("geojson") or {}
    # A1：缓存命中也补叠加（本地文件，快；不入 Mapbox 缓存指纹）
    _attach_blindspot(stats, geojson, repo_root, params)
    # 商业 POI × 地铁可达：缓存命中同样补（本地读商业文件）
    _attach_commercial_reach(stats, geojson, repo_root, params)
    # 迁移旧缓存：polygon 图层老数据为米制坐标，落盘前统一转 WGS84（前端按经纬度上图）
    from shapely.geometry import mapping as _mapping
    from shapely.geometry import shape as _shape

    for _lid in (BANDS_LAYER_ID, HARD_LAYER_ID):
        _fc = geojson.get(_lid)
        if not _fc:
            continue
        _feats = []
        for _f in (_fc.get("features") or []):
            _g = _shape(_f.get("geometry"))
            if _g is not None and _ro.is_meters(_g):
                _f["geometry"] = _mapping(_ro.as_wgs84(_g))
            _feats.append(_f)
        _fc["features"] = _feats
    materialized = _materialize_layers(workspace, geojson)
    evidence = _build_evidence(
        [Path(v["path"]) for v in cached["evidence"]["input_versions"].values()] if cached.get("evidence") else [],
        entrance_count,
        stats,
        int(stats["centers"]["succeeded"]),
        int(stats["centers"]["requested"]),
        300,
        cached=True,
        cache_file=cache_file,
    )
    return _output(stats, evidence, materialized, entrance_count, 300)


def _write_failure(output_path: Path, message: str, stop_code: str) -> None:
    output_path.write_text(
        json.dumps(
            {"success": False, "summary": message, "stop_code": stop_code, "layers": [], "stats": {}, "evidence": {}},
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
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    except IsochroneAllFailed as exc:
        _write_failure(output_path, str(exc), "STOP_ISOCHRONE_FAILED")
    except AnalysisInputError as exc:
        _write_failure(output_path, str(exc), "STOP_INVALID_INPUT")
    except Exception as exc:  # noqa: BLE001
        _write_failure(output_path, f"分析执行失败：{type(exc).__name__}: {exc}", "STOP_EXECUTION_ERROR")


if __name__ == "__main__":
    main()
