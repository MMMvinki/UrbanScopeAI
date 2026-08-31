"""点入面按区聚合原语：把慧眼/POI 等点的量值聚合到街道/社区等面单元。

复用 engine.load_gdf 载入面单元（已是 UTM EPSG:32650、含 SHAPE_Area）；点来自
app.data_sources.get_source（EPSG:4326 WGS-84，无偏移）。聚合在 UTM 米制下做 sjoin
（predicate=within），输出仍为 UTM 面的 GeoDataFrame + 每区 value 列 + meta（丢了哪些点、
未落入比率、区数），供技能产出指标与图层。

诚实失败原则（文档 §11）：面缺失 / 点集空 / 区没有有效几何 → 返回 (None, meta) 带原因，
绝不抛错、绝不造假值。为保证「全深圳」，默认**不吞掉 0 值区**（keep_all_zones=True 时
用 left 连接保留无点区，value=0 —— 空区不是错误，只是没数据可加）。

与 engine = 引擎不同：这里做的是「点到面的空间汇总」，输出带 value 列的面，可直接上图/上指标。
agg 支持 sum/count/mean/min/max；weight_field=None 时按计数。
"""

from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

from app.gis import engine

logger = logging.getLogger(__name__)

# 本模块与 engine 一致：聚合在 UTM 50N 米制进行（量纲正确）
_PROJ = "EPSG:32650"

# 面单元名默认列：自动探测时优先看这些（街道/社区）
_PREFERRED_NAME_COLS = ("JDNAME", "SQNAME", "JDCODE", "SQCODE", "name", "NAME")


def _to_proj(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """保证点/面在 UTM 米制；crs 缺失时不瞎猜（原样返回，交给调用方）。"""
    if gdf.crs is None:
        return gdf
    if str(gdf.crs).upper() in ("EPSG:32650",):
        return gdf
    return gdf.to_crs(_PROJ)


def _pick_key(zone_gdf: gpd.GeoDataFrame, explicit: str | None) -> str | None:
    """选面单元的分组键列：显式指定 > 自动探测（名字列） > 首个非 geometry 列。"""
    if explicit:
        return explicit if explicit in zone_gdf.columns else None
    for col in _PREFERRED_NAME_COLS:
        if col in zone_gdf.columns:
            return col
    non_geo = [c for c in zone_gdf.columns if c != "geometry"]
    return non_geo[0] if non_geo else None


def aggregate_points_to_zones(
    point_gdf: gpd.GeoDataFrame,
    zone_layer_id: str,
    *,
    weight_field: str | None = None,
    agg: str = "sum",
    zone_key_field: str | None = None,
    keep_all_zones: bool = True,
) -> tuple[gpd.GeoDataFrame | None, dict]:
    """把点量值聚到面单元。

    返回 (zone_gdf | None, meta)。zone_gdf 含每区：名字列 + value + area_km2 + geometry；
    None 表示面缺失/点空/无有效几何（诚实失败）。agg 为 sum 时加权求和（人口等绝对量），
    count 时按点数，mean/min/max 时按点的值。
    """
    zones = engine.load_gdf(zone_layer_id)
    if zones is None:
        return None, {"ok": False, "reason": "missing_zone", "message": f"面单元「{zone_layer_id}」不存在或未注册"}
    if zones.empty or zones.geometry.isna().all():
        return None, {"ok": False, "reason": "no_zone_geometry", "message": f"面单元「{zone_layer_id}」无有效几何"}

    key = _pick_key(zones, zone_key_field)
    if key is None:
        return None, {"ok": False, "reason": "no_key", "message": "面单元缺少可聚合的关键列（名字/编码）"}

    points = _to_proj(point_gdf)
    if points is None or points.empty:
        return None, {"ok": False, "reason": "no_points", "message": "点集为空"}

    # 只保留面单元需要的列，避免 sjoin 结果叠出巨表
    zone_cols = [key, "geometry"]
    if "SHAPE_Area" in zones.columns:
        zone_cols.append("SHAPE_Area")
    zone_sel = zones[zone_cols].copy()

    if weight_field is not None and weight_field not in points.columns:
        return None, {"ok": False, "reason": "missing_weight", "message": f"点集缺少权重列「{weight_field}」"}

    try:
        joined = points.sjoin(zone_sel, how="left", predicate="within")
    except Exception:
        logger.exception("sjoin failed %s", zone_layer_id)
        return None, {"ok": False, "reason": "sjoin_failed", "message": "点面相交失败（几何不合法或投影不一致稍加核对）"}

    mapped = joined[key].notna()
    dropped = int((~mapped).sum())
    total = int(len(joined))

    # 聚合主体：mapped 的子集按 key 分组
    if weight_field is None:
        agg_df = joined.loc[mapped].groupby(key).size().rename("value").reset_index()
    else:
        agg_df = joined.loc[mapped].groupby(key)[weight_field].agg(agg).rename("value").reset_index()

    # 合并回面（left 连接保留无点区 → value 填 0；keep_all_zones=False 只保留有点区）
    out = zone_sel.merge(agg_df, on=key, how="left" if keep_all_zones else "inner")
    out["value"] = out["value"].fillna(0.0)

    # 各面面积（km²）：优先用 SHAPE_Area（已是 m²）除以 1e6；缺失则按投影几何重算
    if "SHAPE_Area" in out.columns:
        out["area_km2"] = out["SHAPE_Area"] / 1e6
    else:
        try:
            out["area_km2"] = out.geometry.area / 1e6
        except Exception:
            out["area_km2"] = float("nan")

    meta = {
        "ok": True,
        "zone_count": int(len(out)),
        "mapped": mapped.sum(),
        "dropped": dropped,
        "unassigned_ratio": round(dropped / total, 4) if total else 0.0,
        "total_points": total,
        "agg": agg,
        "weight_field": weight_field,
        "zone_key": key,
        "zone_layer_id": zone_layer_id,
    }
    return out, meta


def groupby_zone_ratios(
    point_gdf: gpd.GeoDataFrame,
    zone_layer_id: str,
    *,
    pop_field: str,
    ratio_cols: list[str],
    zone_key_field: str | None = None,
) -> tuple[gpd.GeoDataFrame | None, dict]:
    """把每个点的结构占比（如年龄/性别占比）按人口加权汇总到面单元。

    每个点是一份人口基数 pop_field + 若干占比字段 ratio_cols（0~1 小数）。
    区值 = Σ(pop × ratio) / Σ(pop)（在区内人口加权平均占比）。
    返回 (zone_gdf | None, meta)，zone_gdf 每区带名字列 + 各 ratio 列 + area_km2 + geometry。
    """
    zones = engine.load_gdf(zone_layer_id)
    if zones is None:
        return None, {"ok": False, "reason": "missing_zone", "message": f"面单元「{zone_layer_id}」不存在或未注册"}
    key = _pick_key(zones, zone_key_field)
    if key is None:
        return None, {"ok": False, "reason": "no_key", "message": "面单元缺少可聚合的关键列"}

    points = _to_proj(point_gdf)
    if points is None or points.empty:
        return None, {"ok": False, "reason": "no_points", "message": "点集为空"}
    missing = [c for c in [pop_field, *ratio_cols] if c not in points.columns]
    if missing:
        return None, {"ok": False, "reason": "missing_columns", "message": f"点集缺列：{'、'.join(missing)}"}

    joined = points.sjoin(zones[[key, "geometry"]], how="left", predicate="within")
    mapped = joined[key].notna()
    sub = joined.loc[mapped]

    # 每区加权占比 = Σ(pop*ratio) / Σ(pop)。用「先乘构造分子列 → groupby.sum → merge」，
    # 不用 index 对齐（reindex 在多区/点错位时易出 NaN）；merge 按 key 值精确配对。
    num_df = sub[[key, pop_field] + ratio_cols].copy()
    for rc in ratio_cols:
        num_df[rc] = num_df[pop_field] * num_df[rc]
    grp = num_df.groupby(key)[[pop_field] + ratio_cols].sum()
    for rc in ratio_cols:
        grp[rc] = grp[rc] / grp[pop_field]

    out = zones[[key, "geometry"]].merge(grp.reset_index(), on=key, how="left")
    for rc in ratio_cols:
        out[rc] = out[rc].fillna(0.0)

    if "SHAPE_Area" in out.columns:
        out["area_km2"] = out["SHAPE_Area"] / 1e6
    else:
        try:
            out["area_km2"] = out.geometry.area / 1e6
        except Exception:
            out["area_km2"] = float("nan")

    meta = {
        "ok": True,
        "zone_count": int(len(out)),
        "mapped": sub.shape[0],
        "dropped": int(len(joined) - sub.shape[0]),
        "total_points": int(len(joined)),
        "pop_field": pop_field,
        "ratio_cols": ratio_cols,
        "zone_key": key,
        "zone_layer_id": zone_layer_id,
    }
    return out, meta
