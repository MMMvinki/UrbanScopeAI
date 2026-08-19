# -*- coding: utf-8 -*-
"""内置可执行 Skill：核密度分析（点图层 → KDE 网格多边形图层）。

真计算：app.gis.engine.kernel_density（UTM 米制网格 + scipy gaussian_kde），
结果按 density 字段分层设色，注册为数据图层上图。

运行契约（skill_runner）：`--input in.json --output out.json`。
input.json wrapper: {"workspace", "skill_id", "params"}。
产物写入 workspace/data/layers/<点id>_kde.json，经 output.json 声明图层。
"""

import argparse
import json
from pathlib import Path

import geopandas as gpd

from app.gis import engine

_PROJ = "EPSG:32650"


def _load_gdf(layer_id: str) -> gpd.GeoDataFrame | None:
    """加载图层为 UTM 米制 gdf；注册表没有时兜底读 workspace / platform_data 文件。"""
    gdf = engine.load_gdf(layer_id)
    if gdf is not None:
        return gdf
    for cand in (
        Path("data/layers") / f"{layer_id}.json",  # 工作区生成/上传图层
        Path("..") / "platform_data" / "geojson" / f"{layer_id}.json",  # 平台只读底座
    ):
        if not cand.is_file():
            continue
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
            features = data.get("features") or []
            if not features:
                continue
            return (
                gpd.GeoDataFrame.from_features(features)
                .set_crs("EPSG:4490")
                .to_crs(_PROJ)
            )
        except Exception:
            continue
    return None


def _fail(output_path: Path, message: str) -> None:
    json.dump({"success": False, "summary": message}, open(output_path, "w", encoding="utf-8"), ensure_ascii=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        params = (json.load(f) or {}).get("params") or {}

    point_id = str(params.get("point_layer_id") or params.get("layer_id") or "").strip()
    area_id = str(params.get("area_layer_id") or "").strip()
    if not point_id:
        _fail(Path(args.output), "缺少参数 point_layer_id（要分析的平台/工作区点图层 id）")
        return

    bw_method = str(params.get("bw_method") or "scott")
    raw_bw = params.get("bandwidth_m")
    bandwidth_m = float(raw_bw) if raw_bw not in (None, "", 0) else None

    point_gdf = _load_gdf(point_id)
    if point_gdf is None or len(point_gdf) == 0:
        _fail(Path(args.output), f"图层 {point_id} 没有可用的点几何")
        return
    area_gdf = _load_gdf(area_id) if area_id else None

    # 自适应格网：研究范围大时小格网会撞引擎 5 万格上限，从请求值起逐级放宽
    # （真计算不变，只是采样分辨率退档，保证操作对任意范围「能跑出结果」）。
    # 默认 50m：街道级点群下≈5.7k 格、带宽封顶 150m 聚焦（比 300m 粗网格热点清晰得多）。
    cell_size_m = int(params.get("cell_size_m") or 50)
    tried = [cell_size_m] + [c for c in (500, 800, 1000) if c != cell_size_m]
    grid_gdf = geojson = stats = None
    used_cell = cell_size_m
    for cell in tried:
        grid_gdf, geojson, stats = engine.kernel_density(point_gdf, area_gdf, cell, bw_method, bandwidth_m)
        if grid_gdf is not None:
            used_cell = cell
            break
    if grid_gdf is None or geojson is None:
        _fail(Path(args.output), "核密度计算失败：点数不足或网格上限内始终无法采样，请检查参数")
        return

    from app.workspace import LAYER_DATA_DIR

    layer_id = f"{point_id}_kde"
    LAYER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (LAYER_DATA_DIR / f"{layer_id}.json").write_text(
        json.dumps(geojson, ensure_ascii=False), encoding="utf-8"
    )

    # 密度分布直方图 → 成果栏（runner 注入的 save_plot 落盘 + 事件回父进程）
    _plot_histogram(point_id, grid_gdf)

    bw_label = f"{bandwidth_m:g}m" if bandwidth_m else f"自动({bw_method}，封顶150m)"
    result = {
        "success": True,
        "summary": (
            f"{point_id} 核密度已生成（{len(grid_gdf)} 个网格单元，{used_cell}m 格网，带宽 {bw_label}）；"
            f"密度 max={stats['max']:.1f}、mean={stats['mean']:.1f}（每平方公里点数）"
        ),
        "layer_id": layer_id,
        "stats": {
            "max": stats["max"],
            "mean": stats["mean"],
            "median": stats["median"],
            "cell_size_m": used_cell,
            "bandwidth_m": bandwidth_m,
            "bw_method": bw_method,
            "grid_count": len(grid_gdf),
        },
        "layers": [
            {
                "layer_id": layer_id,
                "name": f"{point_id} 核密度",
                "path": f"data/layers/{layer_id}.json",
                "feature_count": len(grid_gdf),
                "geometry": "Polygon",
                "fields": [
                    {"name": "density", "type": "number", "description": "核密度值（每平方公里点数）"}
                ],
                "style": {
                    "mode": "graduated",
                    "field": "density",
                    "method": "quantile",
                    "classes": 5,
                },
                "description": f"{point_id} 核密度网格（{used_cell}m 格网，{bw_method} 带宽）",
            }
        ],
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, default=str)


def _plot_histogram(point_id: str, grid_gdf: gpd.GeoDataFrame) -> None:
    """密度分布直方图（可选，失败不致命：图只是锦上添花）。"""
    try:
        import matplotlib

        matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        matplotlib.rcParams["axes.unicode_minus"] = False
        import matplotlib.pyplot as plt
        import numpy as np

        values = grid_gdf["density"].to_numpy()
        values = values[values > 0]
        if values.size < 2:
            return
        plt.figure(figsize=(8, 4))
        plt.hist(values, bins=30, color="#0EA5E9", edgecolor="white")
        plt.xlabel("核密度（每平方公里点数）")
        plt.ylabel("网格数")
        plt.title(f"{point_id} 核密度分布")
        plt.tight_layout()
        save_plot(f"{point_id} 核密度分布", filename=f"{point_id}_kde_hist")
    except Exception:
        pass


if __name__ == "__main__":
    main()
