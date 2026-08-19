# -*- coding: utf-8 -*-
"""内置可执行 Skill：点图层空间聚类（DBSCAN / K-Means）。

真计算：UTM 米制坐标上做 sklearn 聚类（DBSCAN 用米制 eps，K-Means 用 k）。
产物：`clustered` 图层（每点带 cluster 标签，噪声点已剔除，按 cluster 分类设色）
+ `centers` 图层（各簇质心点）。stats 含簇数、噪声占比等。

运行契约：`--input in.json --output out.json`。
"""

import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np

from app.gis import engine

_PROJ = "EPSG:32650"


def _load_gdf(layer_id: str) -> gpd.GeoDataFrame | None:
    gdf = engine.load_gdf(layer_id)
    if gdf is not None:
        return gdf
    for cand in (
        Path("data/layers") / f"{layer_id}.json",
        Path("..") / "platform_data" / "geojson" / f"{layer_id}.json",
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


def _json_safe(value):
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        params = (json.load(f) or {}).get("params") or {}

    layer_id = str(params.get("layer_id") or "").strip()
    method = str(params.get("method") or "dbscan").strip().lower()
    if not layer_id:
        _fail(Path(args.output), "缺少参数 layer_id（要聚类的点图层 id）")
        return
    if method not in ("dbscan", "kmeans"):
        _fail(Path(args.output), f"method 仅支持 dbscan / kmeans，收到：{method}")
        return

    gdf = _load_gdf(layer_id)
    if gdf is None or len(gdf) == 0:
        _fail(Path(args.output), f"图层 {layer_id} 没有可用的点几何")
        return
    n = len(gdf)
    if n < 2:
        _fail(Path(args.output), "点要素不足 2 个，无法聚类")
        return

    coords = np.column_stack([gdf.geometry.x, gdf.geometry.y])

    if method == "kmeans":
        from sklearn.cluster import KMeans

        k = int(params.get("k") or min(max(3, n // 500), 10))
        k = max(2, min(k, n))
        model = KMeans(n_clusters=k, random_state=0, n_init=10).fit(coords)
        labels = model.labels_
        centers = model.cluster_centers_
        noise_count = 0
        cluster_ids = list(range(k))
    else:  # dbscan
        from sklearn.cluster import DBSCAN

        eps = float(params.get("eps") or 300)  # 米
        min_samples = max(int(params.get("min_samples") or 5), 2)
        model = DBSCAN(eps=eps, min_samples=min_samples).fit(coords)
        labels = model.labels_
        cluster_ids = sorted(c for c in set(labels.tolist()) if c != -1)
        noise_count = int((labels == -1).sum())
        centers = (
            np.array([coords[labels == c].mean(axis=0) for c in cluster_ids])
            if cluster_ids
            else np.empty((0, 2))
        )

    # 剔除噪声点出图（DBSCAN -1；K-Means 无噪声）
    clustered = gdf.copy()
    clustered["cluster"] = labels
    clustered = clustered[clustered["cluster"] != -1]
    clustered_4490 = clustered.to_crs("EPSG:4490")

    center_pts = gpd.GeoDataFrame(
        geometry=gpd.points_from_xy(centers[:, 0], centers[:, 1]), crs=_PROJ
    )
    center_pts["cluster"] = cluster_ids if cluster_ids else []
    center_pts_4490 = center_pts.to_crs("EPSG:4490")

    from app.workspace import LAYER_DATA_DIR

    LAYER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    clustered_id = f"{layer_id}_clustered"
    centers_id = f"{layer_id}_centers"
    (LAYER_DATA_DIR / f"{clustered_id}.json").write_text(
        json.dumps(engine.to_geojson(clustered_4490), ensure_ascii=False), encoding="utf-8"
    )
    (LAYER_DATA_DIR / f"{centers_id}.json").write_text(
        json.dumps(engine.to_geojson(center_pts_4490), ensure_ascii=False), encoding="utf-8"
    )

    stats = {
        "method": method,
        "n_clusters": len(cluster_ids),
        "n_points": n,
        "noise_count": noise_count,
        "eps_m": eps if method == "dbscan" else None,
        "min_samples": min_samples if method == "dbscan" else None,
        "k": k if method == "kmeans" else None,
    }
    result = {
        "success": True,
        "summary": (
            f"{layer_id} 聚类完成（{method}）：{len(cluster_ids)} 个簇，"
            f"{noise_count} 个噪声点（占比 {noise_count / n * 100:.1f}%）"
        ),
        "stats": _json_safe(stats),
        "layers": [
            {
                "layer_id": clustered_id,
                "name": f"{layer_id} 聚类结果",
                "path": f"data/layers/{clustered_id}.json",
                "feature_count": int(len(clustered_4490)),
                "geometry": "Point",
                "fields": [{"name": "cluster", "type": "integer", "description": "聚类簇编号"}],
                "style": {"mode": "categorized", "field": "cluster"},
                "description": f"{layer_id} 的 {method} 聚类结果（噪声已剔除）",
            },
            {
                "layer_id": centers_id,
                "name": f"{layer_id} 聚类质心",
                "path": f"data/layers/{centers_id}.json",
                "feature_count": int(len(center_pts_4490)),
                "geometry": "Point",
                "fields": [{"name": "cluster", "type": "integer", "description": "质心所属簇"}],
                "style": {"mode": "categorized", "field": "cluster"},
                "description": f"{layer_id} 的 {method} 各簇质心点",
            },
        ],
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, default=_json_safe)


if __name__ == "__main__":
    main()
