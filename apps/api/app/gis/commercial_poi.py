# -*- coding: utf-8 -*-
"""商业分类 POI 读取（复用）：SHP + 连接表 → isdelete=0 → 研究范围裁剪 → EPSG:4326 点。

供 ``commercial-supply-profile`` 与 ``commercial-rail-accessibility`` 两个 skill 复用，
保证两处「商业大类」分类口径一致：同一张连接表、同一个 ``isdelete=0`` 过滤、同一次
EPSG:32650 米制范围裁剪（与 100m 供给网格的裁剪完全同口径，守恒自检可对得上）。

分类口径只来自「商业原始专题源 + 已核验分类连接表」，不读取任何人工加工汇总表
（``商业资源_商业中小类_统计汇总_utf8.csv`` 只作对照、不作事实输入）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
from shapely.geometry import base

ANALYSIS_CRS = "EPSG:32650"
OUTPUT_CRS = "EPSG:4326"

DEFAULT_COMMERCIAL = "../04-乐活大道GIS数据/00-原始数据/专项资源/商业资源/商业资源Export_Output_46.shp"
DEFAULT_LINK = "../04-乐活大道GIS数据/00-原始数据/专项资源/商业资源/商业资源_商业中小类_连接表_utf8.csv"

# 商业大类 → 稳定配色（与 72A 3.2③ 统一商业点分类色板一致；跨板块复用）。
# 当前连接表实际只有 4 个大类（购物/餐饮/生活/体育休闲）；「文化娱乐」「其它」作为
# 兜底色预留，保证未来数据扩展或类别异常时不至于全黑。
COMMERCIAL_CATEGORY_ORDER = [
    "餐饮服务",
    "购物服务",
    "生活服务",
    "体育休闲服务",
    "文化娱乐服务",
    "其它",
]
COMMERCIAL_CATEGORY_COLORS: dict[str, str] = {
    "餐饮服务": "#f4a261",
    "购物服务": "#e76f51",
    "生活服务": "#2a9d8f",
    "体育休闲服务": "#457b9d",
    "文化娱乐服务": "#9b5de5",
    "其它": "#8a8f98",
}


def resolve_category(raw: Any) -> str:
    """把连接表「商业大类」原值归一到稳定类别集合；空/异常归入「其它」。"""
    text = str(raw or "").strip()
    if text in COMMERCIAL_CATEGORY_COLORS:
        return text
    return "其它"


def load_category_map(link_path: Path) -> dict[str, str]:
    """读连接表 → ``{OBJECTID(str): 商业大类(归一)}``；OBJECTID 去重取首条。

    连接表存在 33 行 ``OBJECTID=0``（无对应 SHP 几何的孤立行），去重后按 1:1 join，
    SHP 全量记录都能命中类别（见 test 断言：unmatched == 0）。
    """
    link = pd.read_csv(link_path, dtype=str, encoding="utf-8-sig")
    if "OBJECTID" not in link.columns or "商业大类" not in link.columns:
        raise ValueError(f"连接表缺少必需字段 OBJECTID / 商业大类：{link_path}")
    link = link.drop_duplicates(subset=["OBJECTID"], keep="first")
    mapping: dict[str, str] = {}
    for oid, cat in zip(link["OBJECTID"].astype(str), link["商业大类"]):
        mapping[oid] = resolve_category(cat)
    return mapping


def load_classified_commercial_points(
    commercial_path: Path,
    link_path: Path,
    study_boundary: base.BaseGeometry,
) -> gpd.GeoDataFrame:
    """读取并裁剪分类商业 POI，返回 EPSG:4326 点 GeoDataFrame。

    返回列：``objectid``、``name``、``category``（归一商业大类）、``geometry``。
    过滤与裁剪口径：``isdelete=0`` → 转 EPSG:32650 → 与研究范围边界 ``within``
    （与 100m 网格的 ``commercial_points_in_boundary`` 完全一致）。
    """
    commercial = gpd.read_file(
        commercial_path, columns=["OBJECTID", "Name", "isdelete"]
    )
    if "isdelete" not in commercial.columns:
        raise ValueError(f"商业数据缺少字段 isdelete：{commercial_path}")
    cat_map = load_category_map(link_path)

    commercial["objectid"] = commercial["OBJECTID"].astype(str)
    commercial["category"] = commercial["objectid"].map(cat_map)
    if commercial["category"].isna().any():
        raise ValueError("商业点存在无法匹配连接表类别的记录")

    active = commercial.loc[pd.to_numeric(commercial["isdelete"], errors="coerce") == 0].copy()
    if active.empty:
        raise ValueError("商业数据按 isdelete=0 过滤后为空")

    active_m = active.to_crs(ANALYSIS_CRS)
    inside = active_m.loc[active_m.geometry.within(study_boundary)].copy()
    if inside.empty:
        raise ValueError("研究范围内没有商业点")
    inside_4326 = inside.to_crs(OUTPUT_CRS)
    result = gpd.GeoDataFrame(
        {
            "objectid": inside_4326["objectid"].to_numpy(),
            "name": inside_4326["Name"].fillna("").astype(str).to_numpy(),
            "category": inside_4326["category"].to_numpy(),
        },
        geometry=inside_4326.geometry.to_numpy(),
        crs=OUTPUT_CRS,
    )
    return result
