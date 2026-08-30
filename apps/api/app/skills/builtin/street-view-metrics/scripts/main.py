# -*- coding: utf-8 -*-
"""内置可执行 Skill：街景绿视率与天空可视因子（路网布点 → 百度全景 → fastseg 语义分割 → 占比点图层）。

真计算流程：
  研究范围(bbox/多边形图层) → 道路(图层或 Overpass) → 沿路网按 spacing_m 布点
  → 每点取 4 朝向(quad)/单张 360°(full) 百度全景图 → fastseg(Cityscapes 19 类)逐像素分类
  → 按类别汇总植物/天空/建筑/道路占比 → 点图层上图(按 green_view_ratio 分层设色)。

运行契约（skill_runner）：`--input in.json --output out.json`。
input.json wrapper: {"workspace", "skill_id", "params"}。
产物写入 workspace/data/layers/<基础id>_streetview.json，经 output.json 声明图层。

坐标约定：一切取图坐标用 WGS-84(百度 coordtype=wgs84ll 免转换)；图层几何按 EPSG:4490 读写。
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import Point
from shapely.ops import unary_union

# skill_runner 以 runpy.run_path(scripts/main.py) 执行——脚本在独立的 skill 目录里，
# 同目录的 panorama.py 未必在 sys.path 上，需显式插入（app 根已由 store.run 注入 PYTHONPATH）。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from panorama import (  # noqa: E402
    densify_geometry,
    fetch_osm_roads,
    fetch_panorama,
    get_ak,
    green_and_sky,
    load_model,
    segment_image,
)


def _load_wgs_gdf(layer_id: str):
    """加载图层为 EPSG:4490(≈WGS-84) GeoDataFrame；注册表没有时兜底读工作区/平台文件。"""
    import geopandas as gpd

    from app.data_registry import DATA_REGISTRY

    geojson = DATA_REGISTRY.get_geojson(layer_id)
    if geojson is not None:
        try:
            return gpd.GeoDataFrame.from_features(geojson.get("features") or []).set_crs("EPSG:4490")
        except Exception:
            pass
    for cand in (
        Path("data/layers") / f"{layer_id}.json",
        Path("..") / "platform_data" / "geojson" / f"{layer_id}.json",
    ):
        if not cand.is_file():
            continue
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
            feats = data.get("features") or []
            if not feats:
                continue
            return gpd.GeoDataFrame.from_features(feats).set_crs("EPSG:4490")
        except Exception:
            continue
    return None


def _union_geom(gdf):
    """张合 GeoDataFrame 几何为一个(可能 Multi)可判包含的几何。"""
    geoms = [g for g in gdf.geometry if g is not None and not g.is_empty]
    if not geoms:
        return None
    return unary_union(geoms)


def _fail(output_path: Path, message: str) -> None:
    json.dump({"success": False, "summary": message}, open(output_path, "w", encoding="utf-8"), ensure_ascii=False)


def _sample_along_roads(roads, spacing_m: float) -> list[list[float]]:
    """道路几何集合 → 沿路采样点(WGS-84 [lng,lat] 列表)。去重、去线头尾噪声。"""
    pts: list[list[float]] = []
    for feat in roads:
        geom = feat.get("geometry") if isinstance(feat, dict) else None
        if not geom:
            continue
        seg = densify_geometry(geom, spacing_m)
        pts.extend(seg)
    out: list[list[float]] = []
    seen = set()
    for lng, lat in pts:
        key = (round(lng, 6), round(lat, 6))
        if key in seen:
            continue
        seen.add(key)
        out.append([lng, lat])
    return out


def _clip_to_area(points, area_geom) -> list[list[float]]:
    if area_geom is None:
        return points
    out = []
    for lng, lat in points:
        # 面积大时逐点 shapely 判含较慢，但常规范围可接受；用欧氏很近的 WGS-84 点在面内做判断。
        try:
            if area_geom.contains(Point(lng, lat)):
                out.append([lng, lat])
        except Exception:
            continue
    return out


def _resolve_bbox(params, area_gdf) -> list[float] | None:
    """bbox 参数优先；否则从研究范围多边形求总包围盒 [西, 南, 东, 北]。"""
    if params.get("bbox"):
        b = params["bbox"]
        if len(b) >= 4:
            return [float(b[0]), float(b[1]), float(b[2]), float(b[3])]
    if area_gdf is not None and not area_gdf.empty:
        w, s, e, n = area_gdf.total_bounds
        return [float(w), float(s), float(e), float(n)]
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        params = (json.load(f) or {}).get("params") or {}

    spacing_m = float(params.get("spacing_m") or 50)
    view_mode = str(params.get("view_mode") or "quad").strip().lower()
    fov = float(params.get("fov") or 90)
    pitch = float(params.get("pitch") if params.get("pitch") is not None else -5)
    max_points = int(params.get("max_points") or 60)
    ak = get_ak(params.get("baidu_ak"))

    if not ak:
        _fail(Path(args.output), "未配置百度 AK：请在 apps/api/.env 设置 BAIDU_AK，或传参 baidu_ak")
        return

    # ── 1. 研究范围 ──
    area_id = str(params.get("study_area_layer_id") or "").strip()
    area_gdf = _load_wgs_gdf(area_id) if area_id else None
    area_geom = _union_geom(area_gdf) if area_gdf is not None else None

    # ── 2. 道路 → 采样点 ──
    roads_id = str(params.get("roads_layer_id") or "").strip()
    if roads_id:
        roads_gdf = _load_wgs_gdf(roads_id)
        if roads_gdf is None or roads_gdf.empty:
            _fail(Path(args.output), f"道路图层 {roads_id} 没有可用的线几何")
            return
        feat_dict = json.loads(roads_gdf.to_crs("EPSG:4490").to_json())
        roads = feat_dict.get("features") or []
    else:
        bbox = _resolve_bbox(params, area_gdf)
        if bbox is None:
            _fail(Path(args.output), "缺少研究范围：请传 bbox 或 study_area_layer_id")
            return
        try:
            roads = fetch_osm_roads(bbox)
        except Exception as exc:
            _fail(Path(args.output), f"从 OSM 抓取路网失败：{str(exc)[:200]}")
            return
        if not roads:
            _fail(Path(args.output), "研究范围内未从 OSM 抓到可行驶道路，请改传 roads_layer_id 或调整范围")
            return

    points = _sample_along_roads(roads, spacing_m)
    points = _clip_to_area(points, area_geom)

    if not points:
        _fail(Path(args.output), "沿路网布点结果为 0：roads 或研究范围不符，请调整")
        return

    # 超量截断（均匀降抽，保留空间上的分布形态，不硬塞尾部）
    truncated = len(points) > max_points
    if truncated:
        idx = np.linspace(0, len(points) - 1, num=max_points).round().astype(int)
        points = [points[i] for i in sorted(set(idx.tolist()))]

    # ── 3. 分割 ──
    model = load_model()
    if model is None:
        _fail(Path(args.output), "语义分割模型加载失败（fastseg/torch 未装或下载失败）")
        return

    headings = [0, 90, 180, 270] if view_mode == "quad" else [0]
    used_fov = 360.0 if view_mode == "full" else fov
    used_pitch = 0.0 if view_mode == "full" else pitch

    features = []
    skipped = 0
    for i, (lng, lat) in enumerate(points):
        agg: dict[str, float] = {}
        n_ok = 0
        for hd in headings:
            img, meta = fetch_panorama(lng, lat, ak=ak, heading=hd, pitch=used_pitch, fov=used_fov)
            if img is None:
                skipped += 1
                continue
            try:
                ratios, _labels = segment_image(model, img)
            except Exception:
                skipped += 1
                continue
            for name, r in ratios.items():
                agg[name] = agg.get(name, 0.0) + r
            n_ok += 1
        if n_ok == 0:
            continue
        # 归一化到视图均值
        agg = {name: v / n_ok for name, v in agg.items()}
        m = green_and_sky(agg)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(lng, 6), round(lat, 6)]},
                "properties": {
                    "lng": round(lng, 6),
                    "lat": round(lat, 6),
                    "green_view_ratio": round(m["green_view_ratio"], 4),
                    "sky_view_factor": round(m["sky_view_factor"], 4),
                    "building_ratio": round(m["building_ratio"], 4),
                    "road_ratio": round(m["road_ratio"], 4),
                    "sidewalk_ratio": round(m["sidewalk_ratio"], 4),
                    "terrain_ratio": round(m["terrain_ratio"], 4),
                    "view_mode": view_mode,
                    "n_views": n_ok,
                },
            }
        )
        print(f"  [{i + 1}/{len(points)}] ({lng:.5f},{lat:.5f}) 绿视率={m['green_view_ratio']:.3f} 天空率={m['sky_view_factor']:.3f}", flush=True)

    if not features:
        _fail(Path(args.output), f"全部点位取图/分割失败（可能无全景覆盖或 AK 无效），跳过 {skipped} 次")
        return

    geojson = {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:4490"}},
        "features": features,
    }

    # ── 4. 写图层文件 ──
    from app.workspace import LAYER_DATA_DIR

    base = area_id or roads_id or (f"{points[0][0]:.4f}_{points[0][1]:.4f}")
    layer_id = f"{base}_streetview"
    LAYER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (LAYER_DATA_DIR / f"{layer_id}.json").write_text(json.dumps(geojson, ensure_ascii=False), encoding="utf-8")

    # ── 5. 成果图表（可选，失败不致命）──
    _plot_distribution(layer_id, features)

    # 汇总统计
    greens = [f["properties"]["green_view_ratio"] for f in features]
    skys = [f["properties"]["sky_view_factor"] for f in features]

    summary = (
        f"街景分析完成：{len(features)} 个采样点（{'4 朝向' if view_mode == 'quad' else '360° 全景'}，间距 {spacing_m:g}m"
        f"{'，超量降抽至 ' + str(max_points) if truncated else ''}）"
        f"；绿视率均值 {np.mean(greens):.3f}（{np.min(greens):.3f}~{np.max(greens):.3f}），"
        f"天空率均值 {np.mean(skys):.3f}（{np.min(skys):.3f}~{np.max(skys):.3f}）"
    )
    result = {
        "success": True,
        "summary": summary,
        "layer_id": layer_id,
        "stats": {
            "points": len(features),
            "skipped_fetches": skipped,
            "spacing_m": spacing_m,
            "view_mode": view_mode,
            "green_view_ratio": {"mean": float(np.mean(greens)), "min": float(np.min(greens)), "max": float(np.max(greens))},
            "sky_view_factor": {"mean": float(np.mean(skys)), "min": float(np.min(skys)), "max": float(np.max(skys))},
        },
        "layers": [
            {
                "layer_id": layer_id,
                "name": f"{base} 街景绿视率",
                "path": f"data/layers/{layer_id}.json",
                "feature_count": len(features),
                "geometry": "Point",
                "fields": [
                    {"name": "green_view_ratio", "type": "number", "description": "绿视率（植被像素占比）"},
                    {"name": "sky_view_factor", "type": "number", "description": "天空可视因子（天空像素占比）"},
                    {"name": "building_ratio", "type": "number", "description": "建筑占比"},
                    {"name": "road_ratio", "type": "number", "description": "道路占比"},
                    {"name": "sidewalk_ratio", "type": "number", "description": "人行道占比"},
                    {"name": "terrain_ratio", "type": "number", "description": "裸地/地形占比"},
                ],
                "style": {
                    "mode": "graduated",
                    "field": "green_view_ratio",
                    "method": "quantile",
                    "classes": 5,
                },
                "description": f"沿路网每 {spacing_m:g}m 布点，百度街景 {view_mode} 视角分割的绿视率/天空率（Cityscapes 池化）",
            }
        ],
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, default=str)

    # ── 6. 成果栏图表事件（runner 注入 emit_chart）──
    _emit_charts(layer_id, features)


def _plot_distribution(layer_id: str, features) -> None:
    """绿视率分布直方图（可选：失败不致命）。"""
    try:
        import matplotlib

        matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        matplotlib.rcParams["axes.unicode_minus"] = False
        import matplotlib.pyplot as plt

        greens = [f["properties"]["green_view_ratio"] for f in features]
        if len(greens) < 2:
            return
        plt.figure(figsize=(8, 4))
        plt.hist(greens, bins=25, color="#16A34A", edgecolor="white", alpha=0.85)
        plt.xlabel("绿视率（植被像素占比）")
        plt.ylabel("采样点数")
        plt.title(f"{layer_id} 绿视率分布")
        plt.tight_layout()
        save_plot(f"{layer_id} 绿视率分布", filename=f"{layer_id}_green_hist")
    except Exception:
        pass


def _emit_charts(layer_id: str, features) -> None:
    """成果栏：绿视率 vs 天空率对比（runner 注入 emit_chart）。"""
    try:
        xs = [str(i + 1) for i in range(len(features))]
        greens = [f["properties"]["green_view_ratio"] for f in features]
        skys = [f["properties"]["sky_view_factor"] for f in features]
        emit_chart(
            f"{layer_id} 绿视率-天空率",
            chart_type="line",
            x_labels=xs,
            series=[
                {"name": "绿视率", "data": greens},
                {"name": "天空可视因子", "data": skys},
            ],
            y_label="占比",
            height=320,
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()
