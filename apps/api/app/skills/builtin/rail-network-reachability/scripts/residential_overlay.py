# -*- coding: utf-8 -*-
"""A1 轨道服务盲区叠加：居住用地 × 人口/就业网格 × 可达带/难达区。

纯函数设计（可单测，不依赖网络）：
- 输入为米制（EPSG:32650）的「累计可达几何 per 分钟 / 难达几何 / 研究区边界」，
  由 rail 主脚本把自身可达结果传入（与 rail stats 同源，面积口径一致）；
- 输出盲区指标 dict + 两幅盲区图层要素（难达区内居住地块、难达区内含人就业的 100m 格）；
- 数据契约：居住用地子集按「住宅本体」保守提取（Layer 含『居住』或独立代码 R1–R4，
  不含 R6 配套 / R22 幼托 / R24 社区其它 / R7 住宅区道路停车 / R8 住宅区绿地等非住宅小项）；
  人口/就业用人口板块 100m 格 home_count/work_count（格心在带内即计入，像样口径不逐格分摊）。
"""

from __future__ import annotations

import re
from typing import Any

import geopandas as gpd

DEFAULT_LAND_SHP = (
    "../04-乐活大道GIS数据/04-恢复数据/商业文化部分（发兆杨）1(全量用地).mpk解包/"
    "commondata/数据处理/用地地块_范围内Export_Output.shp"
)
DEFAULT_POP_GEOJSON = (
    "apps/api/app/workspace/data/layers/population_workforce_profile_100m.json"
)

# 住宅本体保守提取（口径 A，经用户确认）：
# - Layer 含「居住」（含 R1–R4 与商业/公建混合的居住用地，值内常写中文）；
# - 或含独立代码 R1–R4（如值被字段宽截断只剩代码段，如 "…R2…+二类"）。
# 注意不要误吞 R22/R24/R26/R42/R44（幼托/社区其它/住宅区绿地）与 R6/R7/R8。
_RES_RE = re.compile(r"(?<![A-Za-z0-9])R[1-4](?![0-9])")


def is_residential_layer(layer: Any) -> bool:
    s = str(layer or "")
    return ("居住" in s) or bool(_RES_RE.search(s))


def is_meters(geom: Any) -> bool:
    """按 bounds 判断是否已投影米制（UTM 50N：y≈2.49M / 深圳）；4326 经度 y<90。"""
    if geom is None or geom.is_empty:
        return False
    minx, miny, maxx, maxy = geom.bounds
    return (maxy > 90) or (maxx > 180)


def as_meters(geom: Any) -> Any:
    """把几何规范到 EPSG:32650 米制（已是米制则原样；4326 则转投影）。"""
    if is_meters(geom):
        return geom
    return gpd.GeoSeries([geom], crs="EPSG:4326").to_crs("EPSG:32650").iloc[0]


def as_wgs84(geom: Any) -> Any:
    """把米制几何转 EPSG:4326（用于落盘 geojson，前端按经纬度上图）。"""
    if not is_meters(geom):
        return geom
    return gpd.GeoSeries([geom], crs="EPSG:32650").to_crs("EPSG:4326").iloc[0]


def _km2(geom: Any) -> float:
    return float(geom.area / 1_000_000)


def load_residential_land(path, mask_fn=is_residential_layer) -> gpd.GeoDataFrame:
    """读用地 shp（EPSG:4547，CGCS2000 3°GK 114E）→ 32650，保留住宅本体子集。"""
    gdf = gpd.read_file(path, engine="pyogrio", encoding="utf-8")
    if "Layer" not in gdf.columns:
        raise ValueError("用地 shp 缺少 Layer 字段（用途编码在 Layer 列）")
    gdf = gdf[gdf["Layer"].map(mask_fn)].copy()
    return gdf.to_crs("EPSG:32650")


def load_population_grid(path) -> gpd.GeoDataFrame:
    """读人口板块 100m 格输出（EPSG:4326 geojson，home_count/work_count）→ 32650。"""
    gdf = gpd.read_file(path)
    for col in ("home_count", "work_count"):
        if col not in gdf.columns:
            raise ValueError(f"人口网格缺少字段 {col}")
    return gdf.to_crs("EPSG:32650")


def cum_geoms_from_bands(bands_geoms, boundary_m) -> dict[int, Any]:
    """bands 环带几何列表（属性 minute 升序的环带）→ 累计可达几何 ∩ 研究区（米制）。"""
    from shapely.ops import unary_union

    minute_layers: dict[int, list] = {}
    for minute, geom in bands_geoms:
        minute_layers.setdefault(int(minute), []).append(geom)
    out: dict[int, Any] = {}
    acc: list = []
    for m in sorted(minute_layers):
        acc.extend(minute_layers[m])
        out[m] = unary_union(acc).intersection(boundary_m)
    return out


def residential_blindspot(
    *,
    res_gdf_m: gpd.GeoDataFrame,
    pop_gdf_m: gpd.GeoDataFrame,
    boundary_m: Any,
    hard_m: Any,
    cum_by_minute: dict[int, Any],
) -> dict[str, Any]:
    """按米制几何算居住/人口就业的 ≤5/≤10/≤15 覆盖与 >15 盲区。纯函数，确定性。"""
    # 居住地块裁到研究区（shp 本身是“范围内”，仍 clip 保底）
    clipped = res_gdf_m[res_gdf_m.geometry.intersects(boundary_m)].copy()
    clipped["geom"] = clipped.geometry.intersection(boundary_m)
    if len(clipped) == 0:
        raise ValueError("研究范围内没有住宅本体地块")

    res_total_km2 = float(clipped["geom"].area.sum() / 1_000_000)

    def _area_in(geom: Any) -> float:
        return float(clipped["geom"].intersection(geom).area.sum() / 1_000_000)

    coverage_km2 = {str(m): round(_area_in(cum_by_minute[m]), 4) for m in sorted(cum_by_minute)}
    coverage_pct = {
        str(m): round(v / res_total_km2 * 100, 2) if res_total_km2 > 0 else 0.0
        for m, v in coverage_km2.items()
    }
    blind_km2 = round(_area_in(hard_m), 4)
    blind_pct = round(blind_km2 / res_total_km2 * 100, 2) if res_total_km2 > 0 else 0.0

    # 人口/就业：100m 格心 within 判定（累计 ≤m / 难达）
    centers = pop_gdf_m.geometry.representative_point()
    in_study = centers.within(boundary_m)
    c = pop_gdf_m.loc[in_study]
    home_total = int(c["home_count"].sum())
    work_total = int(c["work_count"].sum())

    def _counts(geom: Any) -> tuple[int, int]:
        m = centers.loc[c.index].within(geom)
        sub = c.loc[m]
        return int(sub["home_count"].sum()), int(sub["work_count"].sum())

    home_covered: dict[str, int] = {}
    work_covered: dict[str, int] = {}
    for m in sorted(cum_by_minute):
        h, w = _counts(cum_by_minute[m])
        home_covered[str(m)] = h
        work_covered[str(m)] = w
    home_blind, work_blind = _counts(hard_m)

    # 守恒自检（面积：≤15 覆盖 + 盲区 ≈ 总居住面积；人口：≤15 + 盲区 ≈ 区内总量）
    last_m = sorted(cum_by_minute)[-1]
    area_cons = coverage_km2[str(last_m)] + blind_km2
    home_cons = home_covered[str(last_m)] + home_blind
    work_cons = work_covered[str(last_m)] + work_blind

    def _pct(a: float, b: float) -> float:
        return round(abs(a - b) / b * 100, 3) if b > 0 else 0.0

    return {
        "residential_parcels": int(len(clipped)),
        "residential_total_km2": round(res_total_km2, 4),
        "coverage_km2": coverage_km2,
        "coverage_pct": coverage_pct,
        "blind_km2": blind_km2,
        "blind_pct": blind_pct,
        "home_total": home_total,
        "home_covered": home_covered,
        "home_blind": home_blind,
        "work_total": work_total,
        "work_covered": work_covered,
        "work_blind": work_blind,
        "conservation": {
            "area_15_plus_blind_km2": round(area_cons, 4),
            "area_total_km2": round(res_total_km2, 4),
            "area_delta_pct": _pct(area_cons, res_total_km2),
            "home_15_plus_blind": home_cons,
            "home_total": home_total,
            "home_delta_pct": _pct(float(home_cons), float(home_total)),
            "work_15_plus_blind": work_cons,
            "work_total": work_total,
            "work_delta_pct": _pct(float(work_cons), float(work_total)),
        },
    }


def blind_features(
    res_gdf_m: gpd.GeoDataFrame,
    pop_gdf_m: gpd.GeoDataFrame,
    boundary_m: Any,
    hard_m: Any,
) -> tuple[dict, dict]:
    """难达区内的居住地块与含人/就业格 → (residential_geojson, population_geojson)（米制，与 rail 图层同坐标系）。"""
    from shapely.geometry import mapping as _mapping

    clipped = res_gdf_m[res_gdf_m.geometry.intersects(boundary_m)].copy()
    clipped["geom"] = clipped.geometry.intersection(boundary_m)
    res_hit = clipped[clipped["geom"].intersects(hard_m)].copy()
    res_hit["geom"] = res_hit["geom"].intersection(hard_m)
    res_hit = res_hit[~res_hit["geom"].is_empty]

    centers = pop_gdf_m.geometry.representative_point()
    mask = centers.within(boundary_m) & centers.within(hard_m)
    hit = pop_gdf_m.loc[mask].copy()
    keep = (hit["home_count"].fillna(0) + hit["work_count"].fillna(0)) > 0
    hit = hit.loc[keep]

    def _fc(gdf, props_fn, geometry_col="geometry"):
        feats = []
        for _, row in gdf.iterrows():
            g = as_wgs84(row[geometry_col])  # 米制→WGS84 经纬度，供前端上图
            if g is None or g.is_empty:
                continue
            props = props_fn(row)
            feats.append({"type": "Feature", "properties": props, "geometry": _mapping(g)})
        return {"type": "FeatureCollection", "features": feats}

    res_gj = _fc(
        res_hit,
        lambda r: {
            "kind": "residential_blindspot",
            "label": "盲区居住地块（15 分钟步行外）",
            "layer": str(r["Layer"]),
            "area_km2": round(float(r["geom"].area / 1_000_000), 6),
        },
        "geom",
    )
    pop_gj = _fc(
        hit,
        lambda r: {
            "kind": "population_blindspot",
            "label": "盲区人口/就业格（15 分钟步行外）",
            "home_count": int(r.get("home_count") or 0),
            "work_count": int(r.get("work_count") or 0),
            "cell_id": str(r.get("cell_id") or ""),
        },
    )
    return res_gj, pop_gj
