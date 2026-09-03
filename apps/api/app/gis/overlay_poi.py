# -*- coding: utf-8 -*-
"""可达性叠加读源（复用）：公共/文化/绿地/任意已注册图层 → 研究范围裁剪 → EPSG:4326。

供 ``rail-network-reachability`` skill 把「地铁可达 × 多类资产」做成可延伸能力：
默认四类（商业/公共服务/文化设施/绿地）+ agent 指定任意已注册 POI/AOI 图层。

口径纪律：
- 每类的纳入清单只来自真实原始数据文件（POI 主表四域 / 文化资源 SHP / 用地地块 SHP），
  不读任何人工加工汇总表；统计全量。
- 公共服务四域常量与 ``public-service-profile/scripts/main.py`` 完全同源（同步维护），
  保证跨板块「公共服务设施」口径一致。
- ``isdelete=0`` / ``if_inside=1`` / 排除经营培训类等过滤决定「设施点集合」，
  不依据名称、产权或运营主体推断。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import base

ANALYSIS_CRS = "EPSG:32650"
OUTPUT_CRS = "EPSG:4326"

DEFAULT_POI = "../04-乐活大道GIS数据/00-原始数据/POI全量/深圳市2024POI.csv"
# 文化设施：用「服务型科教文资源去掉培训_现存.shp」（487 条，已去掉培训机构；if_inside=1 共 385 条）
# 注：同目录「文化资源Export_Output_46.shp」为 193 条 EPSG:4326 且无 isdelete/if_inside，非主源。
DEFAULT_CULTURE = "../04-乐活大道GIS数据/00-原始数据/专项资源/文化资源/服务型科教文资源去掉培训_现存.shp"
DEFAULT_GREEN = (
    "../04-乐活大道GIS数据/04-恢复数据/商业文化部分（发兆杨）1(全量用地).mpk解包/"
    "commondata/数据处理/用地地块_范围内Export_Output.shp"
)

# ── 公共服务四域（与 public-service-profile 同步维护，勿单改）──────────────────
SOURCE_DOMAINS: tuple[str, ...] = (
    "公共设施", "科教文化服务", "医疗保健服务", "体育休闲服务",
)
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
EXCLUDE_REASONS: dict[str, str] = {
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
    "公共设施": "generic_toplevel",
    "科教文化场所": "generic_toplevel",
    "医疗保健服务场所": "generic_toplevel",
    "体育休闲服务场所": "generic_toplevel",
    "核酸检测": "generic_toplevel",
}
EXCLUDE_SMALL_IN_MID: dict[str, set[str]] = {
    "专科医院": {"整形美容"},
}


def _norm(value: Any) -> str:
    return str(value or "").split("|", 1)[0].strip()


def classify_public_record(mid_raw: Any, small_raw: Any) -> tuple[str | None, str | None]:
    """把一条 POI 归入公共服务功能组；返回 (group_code|None, reason_code|None)。"""
    mid = _norm(mid_raw)
    small = _norm(small_raw)
    if not mid:
        return None, "generic_toplevel"
    reason = EXCLUDE_REASONS.get(mid)
    if reason is not None:
        return None, reason
    if small in EXCLUDE_SMALL_IN_MID.get(mid, set()):
        return None, "cosmetic_surgery"
    for code in GROUP_ORDER:
        if mid in INCLUDE_MID[code]:
            return code, None
    return None, "generic_toplevel"


def load_public_points(poi_path: Path, study_boundary: base.BaseGeometry) -> gpd.GeoDataFrame:
    """读 POI 主表 → 四域纳入清单 → 落在研究范围 → EPSG:4326 点。

    返回列：``group_code``、``group_label``、``mid_type``、``small_type``、``geometry``。
    """
    needed = ["bigType", "midType", "smallType", "lon_wgs84", "lat_wgs84"]
    header = pd.read_csv(poi_path, nrows=1, dtype=str, encoding="utf-8-sig")
    for col in needed:
        if col not in header.columns:
            raise ValueError(f"POI 主表缺少字段 {col}：{poi_path}")

    rows: list[dict[str, Any]] = []
    for chunk in pd.read_csv(
        poi_path, usecols=needed, dtype=str, encoding="utf-8-sig", chunksize=200000
    ):
        mask = chunk["bigType"].isin(SOURCE_DOMAINS)
        dom = chunk.loc[mask]
        lon = pd.to_numeric(dom["lon_wgs84"], errors="coerce")
        lat = pd.to_numeric(dom["lat_wgs84"], errors="coerce")
        for mid, small, x, y in zip(dom["midType"], dom["smallType"], lon, lat):
            if x is None or y is None or pd.isna(x) or pd.isna(y):
                continue
            code, _reason = classify_public_record(mid, small)
            if code is None:
                continue
            rows.append({
                "group_code": code,
                "group_label": GROUP_LABELS[code],
                "mid_type": str(mid or ""),
                "small_type": str(small or ""),
                "lon": float(x),
                "lat": float(y),
            })
    if not rows:
        raise ValueError("POI 主表按四域纳入清单过滤后没有记录")

    gdf = gpd.GeoDataFrame(
        rows,
        geometry=gpd.points_from_xy([r["lon"] for r in rows], [r["lat"] for r in rows]),
        crs=OUTPUT_CRS,
    )
    inside = gdf.loc[gdf.geometry.to_crs(ANALYSIS_CRS).within(study_boundary)].copy()
    if inside.empty:
        raise ValueError("研究范围内没有纳入的公共服务点")
    return inside.reset_index(drop=True)


# 文化设施排除清单：培训机构 + 经营类（对齐公共服务，不含经营场所语义）
CULTURE_EXCLUDE_KEYWORDS: tuple[str, ...] = (
    "培训机构", "培训中心", "驾校", "会展中心", "传媒机构", "科研机构", "文艺团体",
    "影剧院", "电影院", "剧院", "歌舞厅", "KTV", "网吧", "棋牌", "游戏厅", "酒吧",
    "休闲场所", "娱乐场所", "度假疗养", "住宿",
)


def load_culture_points(shp_path: Path, study_boundary: base.BaseGeometry) -> gpd.GeoDataFrame:
    """读文化资源 SHP → isdelete=0 + if_inside=1 + 排除经营培训 → 研究范围裁剪 → EPSG:4326。

    返回列：``name``、``category``（细类，取 type 首位）、``geometry``。
    注意保留原始几何判定，不依据名称推断。
    """
    culture = gpd.read_file(shp_path)
    if "isdelete" not in culture.columns:
        raise ValueError(f"文化资源缺少字段 isdelete：{shp_path}")
    active = culture.loc[pd.to_numeric(culture["isdelete"], errors="coerce") == 0].copy()
    if active.empty:
        raise ValueError("文化资源按 isdelete=0 过滤后为空")

    # if_inside：仅当存在且可判定时用于预筛（不强制——有的源无 or 值为空），核心仍靠研究范围裁剪
    if "if_inside" in active.columns:
        inside_col = pd.to_numeric(active["if_inside"], errors="coerce")
        if inside_col.notna().any():
            active = active.loc[inside_col == 1].copy()
            if active.empty:
                raise ValueError("文化资源 if_inside=1 过滤后为空")

    # 排除经营/培训类：细类（type / 中类 / 大类 / Name）含关键词即排除
    def _excluded(row: Any) -> bool:
        probe = " ".join(str(v) for v in (
            row.get("type"), row.get("中类"), row.get("大类"), row.get("Name"),
        ))
        return any(kw in probe for kw in CULTURE_EXCLUDE_KEYWORDS)

    active = active.loc[~active.apply(_excluded, axis=1)].copy()

    active_m = active.to_crs(ANALYSIS_CRS)
    inside = active_m.loc[active_m.geometry.within(study_boundary)].copy()
    if inside.empty:
        raise ValueError("研究范围内没有符合口径的文化设施点")
    inside = inside.to_crs(OUTPUT_CRS)

    def _cat(row: Any) -> str:
        t = str(row.get("type") or "").split(";", 1)[0].strip()
        return t if t else str(row.get("大类") or "文化设施")

    result = gpd.GeoDataFrame(
        {
            "name": inside["Name"].fillna("").astype(str).to_numpy(),
            "category": inside.apply(_cat, axis=1).astype(str).to_numpy(),
        },
        geometry=inside.geometry.to_numpy(),
        crs=OUTPUT_CRS,
    )
    return result


def load_green_land(shp_path: Path, study_boundary: base.BaseGeometry) -> gpd.GeoDataFrame:
    """读用地地块 SHP → Layer 含「绿地」→ 研究范围裁剪 → EPSG:4326 面。

    返回列：``layer``（用地类）、``class_tier``（'1' 绿地）、``geometry``。
    """
    land = gpd.read_file(shp_path)
    if "Layer" not in land.columns:
        raise ValueError(f"用地数据缺少字段 Layer：{shp_path}")
    green = land.loc[land["Layer"].astype(str).str.contains("绿地", na=False)].copy()
    if green.empty:
        raise ValueError("用地 SHP 里没有 Layer 含「绿地」的地块")

    green_m = green.to_crs(ANALYSIS_CRS)
    # 与面边界相交的地块都纳入可达盘点（部分相交地块也计入），再裁剪到研究区
    inside = green_m.loc[green_m.geometry.intersects(study_boundary)].copy()
    if inside.empty:
        raise ValueError("研究范围内没有绿地地块")
    inside = inside.to_crs(OUTPUT_CRS)
    result = gpd.GeoDataFrame(
        {
            "layer": inside["Layer"].astype(str).to_numpy(),
            "class_tier": "1",
            "area_km2": (inside.to_crs(ANALYSIS_CRS).geometry.area / 1_000_000).to_numpy().round(6),
        },
        geometry=inside.geometry.to_numpy(),
        crs=OUTPUT_CRS,
    )
    return result


def load_land_parcels(shp_path: Path, study_boundary: base.BaseGeometry) -> gpd.GeoDataFrame:
    """读用地地块 SHP → 全部地块 → 研究范围裁剪 → EPSG:4326 面。

    与 ``load_green_land``（只 Layer 含「绿地」）不同——这里是**所有用地地块**，
    每块带 Layer 分类（含居住 R、绿地 G、公服 GIC、商业 C 等），用于「每块用地属于几分钟可达层级」。
    返回列：``layer``（用地类）、``geometry``、``area_km2``。
    """
    land = gpd.read_file(shp_path)
    if "Layer" not in land.columns:
        raise ValueError(f"用地数据缺少字段 Layer：{shp_path}")
    # 过滤无几何 / 空面
    land = land.loc[land.geometry.notna() & ~land.geometry.is_empty].copy()
    land_m = land.to_crs(ANALYSIS_CRS)
    inside = land_m.loc[land_m.geometry.intersects(study_boundary)].copy()
    if inside.empty:
        raise ValueError("研究范围内没有用地地块")
    inside = inside.to_crs(OUTPUT_CRS)
    result = gpd.GeoDataFrame(
        {
            "layer": inside["Layer"].astype(str).to_numpy(),
            "area_km2": (inside.to_crs(ANALYSIS_CRS).geometry.area / 1_000_000).to_numpy().round(6),
        },
        geometry=inside.geometry.to_numpy(),
        crs=OUTPUT_CRS,
    )
    return result


def load_overlay_layer(geojson: dict) -> gpd.GeoDataFrame:
    """从任意已物化 GeoJSON（EPSG:4326）读几何，保留原 properties 供分类字段候选。

    geometry 是 GeoJSON dict，需转 shapely 对象（gpd 不接受 dict 几何）。
    """
    import json
    from shapely.geometry import shape

    if isinstance(geojson, str):
        geojson = json.loads(geojson)
    features = (geojson or {}).get("features") or []
    if not features:
        raise ValueError("空 GeoJSON 图层，不能作为可达叠加输入")
    props = features[0].get("properties") or {}
    cols = [k for k in props.keys() if isinstance(props[k], (str, int, float))]
    rows = []
    for f in features:
        p = f.get("properties") or {}
        rows.append({k: p.get(k) for k in cols})
    geoms = []
    for f in features:
        g = f.get("geometry")
        geoms.append(shape(g) if g else None)
    gdf = gpd.GeoDataFrame(rows, geometry=geoms, crs=OUTPUT_CRS)
    return gdf
