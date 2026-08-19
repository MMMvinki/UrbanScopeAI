"""一次性转换脚本：把工作区「平台数据/」的真实深圳 shp/tif 转换为平台数据图层。

产物（提交到仓库，demo 时无需每次转换）：
  - app/platform_data/layers.json        平台图层元数据（LayerMetadata 列表）
  - app/platform_data/geojson/<id>.json  每个矢量图层的真实 GeoJSON（EPSG:4326）

设计约束（与演示口径一致）：
  - 全部真计算：直接读 shp → 重投影 EPSG:4326 → 写 GeoJSON，要素数/字段/bbox 均为真实值
  - 平台图层 readonly=True（数据内容只读、样式可改）、lazy=True（前端按需拉几何）
  - 全类 POI（深圳市_POI_WGS84）因 DBF 恰好 2.0GB（DBF 2GB 硬限制）无法整读，
    跳过主文件，改用 16 个分类 POI 图层（内容等价覆盖）
  - DEM 只注册元数据（kind=raster），栅格渲染管线后置（单独 sprint）

用法：
    .venv/Scripts/python.exe -X utf8 app/scripts/build_platform_layers.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd

# ── 路径 ──────────────────────────────────────────────────────────────
# 脚本位于 app/scripts/，仓库根 = 脚本目录的 ../../../..
REPO_ROOT = Path(__file__).resolve().parents[4]
PLATFORM_SRC = REPO_ROOT / "平台数据"
OUT_DIR = Path(__file__).resolve().parent.parent / "platform_data"
GEOJSON_DIR = OUT_DIR / "geojson"

# ── 图层清单：id/名称/shp 相对路径/颜色/简介 ─────────────────────────
# POI（深圳市_POI_WGS84 主文件跳过，原因见模块 docstring）
_POI_COLORS = [
    "#e63946", "#f4a261", "#2a9d8f", "#9b5de5", "#00bbf9",
    "#f15bb5", "#90be6d", "#43aa8b", "#f9844a", "#577590",
    "#ee896b", "#f9c74f", "#6d597a", "#b56576", "#3a86ff", "#84a59d",
]


def _poi_entry(name: str, color: str, description: str, *, folder: str, shp: str) -> dict:
    """POI 分类 → 清单条目（文件夹与 shp 命名不统一，显式传入避免拼接错误）。"""
    return {
        "id": "poi_" + name,
        "name": f"深圳POI·{name}",
        "path": f"POI2023/{folder}/{shp}.shp",
        "color": color,
        "description": description,
    }


MANIFEST: list[dict] = [
    # 行政区划（重投影 EPSG:4326）
    {
        "id": "shenzhen_districts",
        "name": "深圳市行政区划",
        "path": "深圳行政/深圳市行政边界/深圳行政区.shp",
        "color": "#64748b",
        "description": "深圳市行政区划边界（真实数据）",
    },
    {
        "id": "shenzhen_streets",
        "name": "深圳街道边界",
        "path": "深圳行政/深圳街道行政边界/深圳街道.shp",
        "color": "#0f766e",
        "description": "深圳市街道级行政边界（真实数据）",
    },
    {
        "id": "shenzhen_communities",
        "name": "深圳社区边界",
        "path": "深圳行政/深圳社区行政边界/深圳社区.shp",
        "color": "#4338ca",
        "description": "深圳市社区级行政边界（真实数据）",
    },
    # 详规一张图（45k 要素，前端惰性加载）
    {
        "id": "shenzhen_planning",
        "name": "深圳详规一张图2023",
        "path": "深圳详规一张图2023/planning.shp",
        "color": "#7c3aed",
        "description": "深圳市国土空间详细规划一张图（2023，真实数据）",
    },
    # 单线路网（29k 要素，前端惰性加载）
    {
        "id": "shenzhen_roads",
        "name": "深圳单线路网2024",
        "path": "2024深圳单线路网/2024深圳单线路网模型WGS84.shp",
        "color": "#dc2626",
        "description": "深圳市单线路网模型（2024，真实数据）",
    },
    # POI 分类（16 个，文件夹与 shp 命名不统一 → 显式传参）
    _poi_entry("医疗保健", _POI_COLORS[0], "医疗保健服务设施点位（真实 POI 数据）",
               folder="深圳市医疗保健服务poi_2023", shp="深圳市_医疗保健服务_WGS84"),
    _poi_entry("科教文化", _POI_COLORS[1], "科教文化服务设施点位（含学校，真实 POI 数据）",
               folder="深圳市科教文化服务poi_2023", shp="深圳市_科教文化服务_WGS84"),
    _poi_entry("交通设施", _POI_COLORS[2], "交通设施服务点位（真实 POI 数据）",
               folder="深圳市交通设施服务poi_2023", shp="深圳市_交通设施服务_WGS84"),
    _poi_entry("住宿", _POI_COLORS[3], "住宿服务点位（真实 POI 数据）",
               folder="深圳市住宿服务poi_2023", shp="深圳市_住宿服务_WGS84"),
    _poi_entry("体育休闲", _POI_COLORS[4], "体育休闲服务点位（真实 POI 数据）",
               folder="深圳市体育休闲poi_2023", shp="深圳市_体育休闲服务_WGS84"),
    _poi_entry("公共设施", _POI_COLORS[5], "公共设施点位（真实 POI 数据）",
               folder="深圳市公共设施poi_2023", shp="深圳市_公共设施_WGS84"),
    _poi_entry("公司企业", _POI_COLORS[6], "公司企业点位（真实 POI 数据）",
               folder="深圳市公司企业poi_2023", shp="深圳市_公司企业_WGS84"),
    _poi_entry("商务住宅", _POI_COLORS[7], "商务住宅点位（真实 POI 数据）",
               folder="深圳市商务住宅poi_2023", shp="深圳市_商务住宅_WGS84"),
    _poi_entry("政府机构及社会团体", _POI_COLORS[8], "政府机构及社会团体点位（真实 POI 数据）",
               folder="深圳市政府机构及社会团体poi_2023", shp="深圳市_政府机构及社会团体_WGS84"),
    _poi_entry("汽车服务相关", _POI_COLORS[9], "汽车服务相关点位（真实 POI 数据）",
               folder="深圳市汽车服务相关poi_2023", shp="深圳市_汽车服务相关_WGS84"),
    _poi_entry("生活服务", _POI_COLORS[10], "生活服务点位（真实 POI 数据）",
               folder="深圳市生活服务poi_2023", shp="深圳市_生活服务_WGS84"),
    _poi_entry("购物", _POI_COLORS[11], "购物服务点位（真实 POI 数据）",
               folder="深圳市购物服务poi_2023", shp="深圳市_购物服务_WGS84"),
    _poi_entry("道路附属设施", _POI_COLORS[12], "道路附属设施点位（真实 POI 数据）",
               folder="深圳市道路附属设施poi_2023", shp="深圳市_道路附属设施_WGS84"),
    _poi_entry("金融保险", _POI_COLORS[13], "金融保险服务点位（真实 POI 数据）",
               folder="深圳市金融保险服务poi_2023", shp="深圳市_金融保险服务_WGS84"),
    _poi_entry("风景名胜", _POI_COLORS[14], "风景名胜点位（真实 POI 数据）",
               folder="深圳市风景名胜poi_2023", shp="深圳市_风景名胜_WGS84"),
    _poi_entry("餐饮", _POI_COLORS[15], "餐饮服务点位（真实 POI 数据）",
               folder="深圳市餐饮服务poi_2023", shp="深圳市_餐饮服务_WGS84"),
    # DEM（只注册元数据，kind=raster）
    {
        "id": "shenzhen_dem",
        "name": "深圳30米DEM",
        "path": "30米DEM/深圳市_DEM.tif",
        "color": "#b45309",
        "description": "深圳市 30 米分辨率 DEM 高程数据（GeoTIFF，栅格渲染后置）",
        "kind": "raster",
    },
]

# 字段类型 → FieldSpec.type（与 demo_data/layers.json 对齐）
_FIELD_TYPE_MAP = {
    "int64": "integer",
    "float64": "number",
    "int32": "integer",
    "float32": "number",
    "object": "string",
    "str": "string",
    "bool": "string",
    "datetime64[ms]": "string",
    "datetime64[ns]": "string",
}


def _field_specs(gdf: gpd.GeoDataFrame) -> list[dict]:
    """dtypes → LayerMetadata.fields（跳过 geometry，保留真实列名与类型）。"""
    specs: list[dict] = []
    for col in gdf.columns:
        if col == "geometry":
            continue
        dtype = str(gdf[col].dtype)
        specs.append(
            {
                "name": str(col),
                "type": _FIELD_TYPE_MAP.get(dtype, "string"),
                "description": f"属性字段 {col}",
            }
        )
    return specs


def _geometry_type(geoms: gpd.GeoSeries) -> str:
    """真实几何类型 → GeometryType 字符串（Polygon/LineString/Point）。"""
    for gt in ("Polygon", "LineString", "Point"):
        if geoms.geom_type.eq(gt).any():
            return gt
    # Multi 几何兜底：取含 "Polygon/Multi" 的第一个
    for geom in geoms:
        t = geom.geom_type if geom is not None else ""
        if "Polygon" in t:
            return "Polygon"
        if "LineString" in t:
            return "LineString"
        if "Point" in t:
            return "Point"
    return "Point"


def _bbox(gdf: gpd.GeoDataFrame) -> list[float]:
    total = gdf.total_bounds  # [minx, miny, maxx, maxy]
    return [float(v) for v in total]


def build_one(entry: dict) -> dict | None:
    """转换单个矢量图层：读 shp → 重投影 4326 → 写 GeoJSON → 返回元数据 dict。"""
    src = PLATFORM_SRC / entry["path"]
    if not src.exists():
        print(f"  ✗ 缺失：{src}", file=sys.stderr)
        return None
    try:
        gdf = gpd.read_file(src)
    except Exception as exc:
        print(f"  ✗ 读取失败：{src} → {exc}", file=sys.stderr)
        return None
    if gdf.empty:
        print(f"  ✗ 空图层：{entry['id']}", file=sys.stderr)
        return None
    # 统一 EPSG:4326（行政/详规为 4547/32650，POI/路网已是 4326）
    if gdf.crs is not None and str(gdf.crs).upper() != "EPSG:4326":
        try:
            gdf = gdf.to_crs("EPSG:4326")
        except Exception as exc:
            print(f"  ✗ 重投影失败：{entry['id']} → {exc}", file=sys.stderr)
            return None
    # datetime 列转字符串（json 无法序列化 Timestamp），其余原样保留
    gdf = gdf.copy()
    for col in gdf.columns:
        if str(gdf[col].dtype).startswith("datetime64"):
            gdf[col] = gdf[col].astype(str).replace("NaT", "")
    # 中文列名/值原样保留（ensure_ascii=False）；压缩输出控制文件体积
    geojson_str = gdf.to_json(na="drop")
    geojson = json.loads(geojson_str)
    (GEOJSON_DIR / f"{entry['id']}.json").write_text(
        json.dumps(geojson, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return {
        "id": entry["id"],
        "name": entry["name"],
        "source": "platform",
        "geometry": _geometry_type(gdf.geometry),
        "color": entry["color"],
        "visible": False,  # 平台数据默认收起，用户按需点亮
        "feature_count": int(len(gdf)),
        "crs": "EPSG:4326",
        "description": entry["description"],
        "fields": _field_specs(gdf),
        "bbox": _bbox(gdf),
        "readonly": True,
        "lazy": True,  # 前端按需拉取几何（平台图层都不进 bulk 端点）
    }


def main() -> int:
    GEOJSON_DIR.mkdir(parents=True, exist_ok=True)
    print(f"输入目录：{PLATFORM_SRC}")
    print(f"输出目录：{OUT_DIR}")
    layers: list[dict] = []
    for entry in MANIFEST:
        if entry.get("kind") == "raster":
            # DEM：只注册元数据，几何/切片渲染后置
            layers.append(
                {
                    "id": entry["id"],
                    "name": entry["name"],
                    "source": "platform",
                    "geometry": "Polygon",
                    "color": entry["color"],
                    "visible": False,
                    "feature_count": 0,
                    "crs": "EPSG:4326",
                    "description": entry["description"],
                    "fields": [],
                    "readonly": True,
                    "lazy": True,
                    "kind": "raster",
                    "bbox": None,
                }
            )
            print(f"  ✓ {entry['id']:24s}（栅格元数据，渲染后置）")
            continue
        print(f"  → {entry['id']:24s} {entry['path']}")
        meta = build_one(entry)
        if meta is None:
            continue
        size_mb = (GEOJSON_DIR / f"{entry['id']}.json").stat().st_size / 1024 / 1024
        print(f"  ✓ {meta['feature_count']:>7d} 要素  {meta['geometry']:9s} {size_mb:6.1f}MB")
        layers.append(meta)

    (OUT_DIR / "layers.json").write_text(
        json.dumps(layers, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n完成：{len(layers)}/{len(MANIFEST)} 个平台图层 → {OUT_DIR / 'layers.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
