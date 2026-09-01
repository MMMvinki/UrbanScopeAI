"""真实 GIS 计算引擎：基于 GeoPandas / Shapely，对确定性 GeoJSON 做真几何运算。

- 数据源：app/demo_data/geojson/*.json（确定性生成，见 app/scripts/generate_demo_geojson.py）
  以及执行器运行时注册的结果图层（DATA_REGISTRY._geojson）；
- 计算都在 UTM 50N（EPSG:32650，米制）中进行，保证 buffer / 面积 / 距离的量纲正确，
  输出时再转回 EPSG:4490 经纬度 GeoJSON；
- 任何图层缺失返回 None，由调用方（工具）回退演示数值，绝不抛错挂死。

说明：GeoPandas/Shapely 负责矢量几何运算（buffer、相交、距离），
DuckDB Spatial 用于后续大规模属性/空间聚合（本阶段引擎以 GeoPandas 为主）。
"""

from __future__ import annotations

import json

import geopandas as gpd

from app.data_registry import DATA_REGISTRY

# 研究范围在北京市区（约 116.34E，UTM 50N 覆盖 114°-120°E）
_PROJ = "EPSG:32650"


def load_gdf(layer_id: str) -> gpd.GeoDataFrame | None:
    """加载图层几何为 UTM 米制 GeoDataFrame；图层缺失或无真实几何返回 None。"""
    geojson = DATA_REGISTRY.get_geojson(layer_id)
    if geojson is None:
        return None
    features = geojson.get("features") or []
    # 表格图层（无几何）特征全为 null geometry，GeoPandas 会生成无效几何且 set_crs 可能失败，
    # 直接返回 None，交给调用方拒绝（不落入 stable_rate 演示兜底）。
    if features and all((f.get("geometry") or None) is None for f in features):
        return None
    try:
        gdf = gpd.GeoDataFrame.from_features(features)
    except Exception:
        return None
    gdf = gdf.set_crs("EPSG:4490").to_crs(_PROJ)
    return gdf


def to_geojson(gdf: gpd.GeoDataFrame) -> dict:
    """GeoDataFrame → EPSG:4490 经纬度 GeoJSON（dict，供注册表/前端直接使用）。

    gpd.to_json() 返回的是 JSON 字符串，必须 json.loads 成 dict，
    否则生成的图层几何会以字符串形态存入注册表，导致
    /layers/geojson 的 dict[str, dict] 响应校验失败（500）→ 地图空白。
    """
    return json.loads(gdf.to_crs("EPSG:4490").to_json())


def _dissolve(gdf: gpd.GeoDataFrame):
    """合并为单一几何（用 union_all 兼容较新的 geopandas）。"""
    union = gdf.union_all()
    return union


def buffer(facility_layer_id: str, distance_m: int):
    """设施点 → 真实 buffer 多边形（米制），返回 (buffer_gdf, geojson)。"""
    facilities = load_gdf(facility_layer_id)
    if facilities is None:
        return None, None
    buffered = facilities.buffer(distance_m)
    out = gpd.GeoDataFrame(geometry=buffered, crs=_PROJ)
    return out, to_geojson(out)


def coverage(
    facility_layer_id: str,
    distance_m: int,
    base_layer_id: str = "shenzhen_communities",
):
    """设施服务半径覆盖率（真计算）。

    口径：buffer(设施, distance) ∩ 居住社区面积 / 居住社区总面积（%）。
    base 默认 shenzhen_communities（社区边界 ≈ 居住承载区）；demo 时代的
    "residential" 图层已随平台迁移不存在，硬编码会让 coverage 永远走演示兜底
    （e2e 暴露：产物图层 0 要素 + 地图空白）。返回 dict（含 output geojson）或 None。
    """
    facilities = load_gdf(facility_layer_id)
    base = load_gdf(base_layer_id)
    if facilities is None or base is None:
        return None
    buffered = _dissolve(facilities.buffer(distance_m))
    base_union = _dissolve(base)
    covered = buffered.intersection(base_union)
    total_area = base_union.area
    covered_area = covered.area
    rate = round(covered_area / total_area * 100, 1) if total_area else 0.0
    # 输出图层用完整服务范围（union of buffers），便于地图展示
    buffer_out = gpd.GeoDataFrame(geometry=[buffered], crs=_PROJ)
    return {
        "coverage_rate": rate,
        "covered_area_km2": round(covered_area / 1e6, 2),
        "uncovered_area_km2": round((total_area - covered_area) / 1e6, 2),
        "distance": distance_m,
        "output_layer_id": f"{facility_layer_id}_coverage",
        "geojson": to_geojson(buffer_out),
    }


def clip(target_layer_id: str, mask_layer_id: str):
    """把目标图层裁剪到掩膜图层范围（真几何裁剪，米制）。

    掩膜取 mask 全部要素的几何并集；保留与掩膜相交的目标几何（点/线/面都支持），
    空几何剔除。返回 (clip_ratio, geojson, feature_count)：clip_ratio = 保留占比（%），
    面目标按面积、点/线按要素数（面积恒 0，避免"保留 0.0%"误导）；geojson 为
    EPSG:4490 dict，feature_count 为裁剪后的要素数；任一图层缺失返回 None。
    """
    target = load_gdf(target_layer_id)
    mask = load_gdf(mask_layer_id)
    if target is None or mask is None:
        return None
    mask_union = mask.geometry.union_all()
    total_area = float(target.area.sum())
    clipped = gpd.clip(target, mask_union, keep_geom_type=False)
    clipped = clipped[~clipped.geometry.is_empty].reset_index(drop=True)
    kept_area = float(clipped.area.sum())
    # 面目标用面积占比；点/线面积恒 0，退化为要素数保留率，避免"保留 0.0%"误导
    if total_area > 0:
        ratio = round(kept_area / total_area * 100, 1)
    else:
        ratio = round(len(clipped) / len(target) * 100, 1) if len(target) else 0.0
    return ratio, to_geojson(clipped), len(clipped)


def nearest_distance(target_layer_id: str, reference_layer_id: str):
    """目标要素到最近参考设施的平均距离（真计算，米）。"""
    target = load_gdf(target_layer_id)
    reference = load_gdf(reference_layer_id)
    if target is None or reference is None:
        return None
    target_pts = target.geometry.centroid
    ref_pts = reference.geometry.centroid
    mins: list[float] = []
    for point in target_pts.geometry:
        mins.append(float(point.distance(ref_pts.geometry).min()))
    avg = round(sum(mins) / len(mins), 0) if mins else 0.0
    return {"avg_distance_m": int(avg), "output_layer_id": f"{target_layer_id}_result"}


def spatial_join(target_layer_id: str, overlay_layer_id: str):
    """空间叠加：overlay 与 target 相交面积占 target 面积比例（%）。"""
    target = load_gdf(target_layer_id)
    overlay = load_gdf(overlay_layer_id)
    if target is None or overlay is None:
        return None
    t = _dissolve(target)
    o = _dissolve(overlay)
    inter = t.intersection(o)
    ratio = round(inter.area / t.area * 100, 1) if t.area else 0.0
    return {"ratio": ratio, "output_layer_id": f"{target_layer_id}_result"}


def density(layer_id: str):
    """空间密度指数：人口网格取 人口加权密度，其他图层取 面积密度相对值。"""
    gdf = load_gdf(layer_id)
    if gdf is None:
        return None
    if layer_id == "population_grid" and "pop" in gdf.columns:
        total_pop = float(gdf["pop"].sum())
        total_area_km2 = float(gdf.area.sum() / 1e6)
        index = round(total_pop / total_area_km2, 1) if total_area_km2 else 0.0
    else:
        total_area_km2 = float(gdf.area.sum() / 1e6)
        index = round(len(gdf) / total_area_km2, 2) if total_area_km2 else 0.0
    return {"density_index": index}


def supply_demand(facility_layer_id: str, population_layer_id: str):
    """供需匹配：设施服务覆盖下的人口占比（真计算，按人口网格加权）。

    口径：buffer(设施, 800m) 覆盖的人口网格人口 / 总人口（%）。
    """
    population = load_gdf(population_layer_id)
    if population is None or "pop" not in population.columns:
        return None
    buffered = _dissolve(load_gdf(facility_layer_id).buffer(800)) if load_gdf(facility_layer_id) is not None else None
    if buffered is None:
        return None
    pop_points = gpd.GeoDataFrame(
        population.copy(),
        geometry=population.geometry.centroid,
        crs=_PROJ,
    )
    served = pop_points[pop_points.geometry.within(buffered)]
    total_pop = float(population["pop"].sum())
    served_pop = float(served["pop"].sum())
    satisfaction = round(served_pop / total_pop * 100, 1) if total_pop else 0.0
    return {"satisfaction_percent": satisfaction}


def kernel_density(
    point_gdf: gpd.GeoDataFrame,
    area_gdf: gpd.GeoDataFrame | None,
    cell_size_m: int = 50,
    bw_method: str = "scott",
    bandwidth_m: float | None = None,
):
    """点图层 → 核密度（KDE）网格多边形图层（真计算，UTM 米制）。

    口径：scipy.stats.gaussian_kde 对点坐标建连续核密度表面，在覆盖研究范围
    （area 边界或点 bbox）的正则网格上采样；每格 density = 该格核密度值
    × N × 1e6（= 每平方公里点数，便于解读）。area 提供时先把点裁到范围，
    防止范围外远点系统性高估区域密度。

    带宽与网格是两回事：
    - bandwidth_m（米）：核平滑半径（用户说「带宽 20m」就传它）。gaussian_kde 的
      bw_method 语义是缩放因子不是绝对米数，这里内部换算成固定带宽；
    - cell_size_m（米）：密度面的采样格宽（用户说「网格 20m」就传它）。默认 50m；
    - bw_method：仅 scott/silverman（自动带宽），传数值/非法字符串会被忽略或回退。
      默认自动带宽但封顶 150m——scott 对街道级点群常给 300m+ 糊片（用户感知「太粗」），
      封顶后第一版聚焦可看，且保留稀疏数据的自适应（不颗粒化）。

    网格上限（防 GeoJSON 膨胀）：无掩膜时 bbox 网格 > 5 万直接拒；有掩膜时先生成
    bbox 网格再按格心裁剪，裁剪后仍 > 5 万才拒（20m 掩膜场景不再被误杀）。

    返回 (网格多边形 gdf[含 density 字段], EPSG:4490 GeoJSON dict, {max,mean,median})；
    点数不足 / 网格超限 / KDE 奇异返回 (None, None, None)。
    """
    import numpy as np
    from scipy.stats import gaussian_kde
    from shapely.geometry import Point, box

    if point_gdf is None or len(point_gdf) < 2:
        return None, None, None
    cell = max(int(cell_size_m or 50), 20)  # 网格下限 20m，防超小网格爆量

    pts = point_gdf.copy()
    cents = pts.geometry.centroid
    pts["_cx"] = cents.x
    pts["_cy"] = cents.y

    if area_gdf is not None and len(area_gdf):
        area_union = area_gdf.geometry.union_all()
        inside = pts[cents.within(area_union)]
        if len(inside) >= 2:
            pts = inside
        minx, miny, maxx, maxy = area_gdf.total_bounds
    else:
        minx, miny, maxx, maxy = pts.total_bounds

    coords = np.vstack([pts["_cx"].values, pts["_cy"].values])
    if coords.shape[1] < 2:
        return None, None, None

    # 带宽语义换算：用户给的「带宽 X 米」→ gaussian_kde 的标量缩放因子。
    # gaussian_kde 标量 b 使 std → b×std_data，故 b = X / std_data 时带宽恰为 X 米。
    std0 = float(np.sqrt(np.mean(np.var(coords, axis=1)))) or 1.0
    if bandwidth_m is not None:
        bm = max(float(bandwidth_m), 1.0)  # 至少 1m
        bw = bm / std0
    elif bw_method in ("scott", "silverman"):
        # 自动带宽但封顶 150m：scott 对街道级点群（如餐饮 POI）常给出 300m+ 带宽，
        # 热点摊成糊片 = 用户感知「太粗」。封顶让默认第一版聚焦可看；同时保留自适应
        # （点少/数据稀疏时自动带宽小，不会把稀疏点颗粒化）。
        try:
            kde_tmp = gaussian_kde(coords, bw_method=bw_method)
        except (np.linalg.LinAlgError, ValueError):
            bw = bw_method  # 交给下方 try 再兜底回退
        else:
            auto_bw = kde_tmp.factor * std0
            bw = min(auto_bw, 150.0) / std0
    else:
        bw = bw_method  # 非法/数值 bw_method：下方 gaussian_kde 会抛，回退 scott
    try:
        kde = gaussian_kde(coords, bw_method=bw)
    except (np.linalg.LinAlgError, ValueError):
        try:
            kde = gaussian_kde(coords, bw_method="scott")
        except (np.linalg.LinAlgError, ValueError):
            return None, None, None

    # 缺省范围（点 bbox）外扩 3×带宽 + 2×格宽，避免边缘砍掉真实密度
    if area_gdf is None or not len(area_gdf):
        cov = kde.covariance  # 2×2
        bw_scale = float(np.sqrt(np.diag(cov)).max())
        pad = max(3.0 * bw_scale, 2.0 * cell)
        minx -= pad
        miny -= pad
        maxx += pad
        maxy += pad

    ncols = int(np.floor((maxx - minx) / cell)) + 1
    nrows = int(np.floor((maxy - miny) / cell)) + 1
    total_cells = nrows * ncols
    has_mask = area_gdf is not None and len(area_gdf)
    if total_cells > 50000 and not has_mask:
        return None, None, None  # 无掩膜：bbox 网格即最终网格，超限直接拒
    if total_cells > 200000:
        return None, None, None  # 硬上限：掩膜场景也不允许 bbox 网格膨胀爆内存

    xs = minx + np.arange(ncols) * cell + cell / 2.0
    ys = miny + np.arange(nrows) * cell + cell / 2.0
    X, Y = np.meshgrid(xs, ys)
    Z = kde.evaluate(np.vstack([X.ravel(), Y.ravel()])).reshape(nrows, ncols)
    Z *= float(coords.shape[1]) * 1e6  # → 每平方公里点数

    keep = area_gdf.geometry.union_all() if (area_gdf is not None and len(area_gdf)) else None
    polys: list = []
    dens: list[float] = []
    for r in range(nrows):
        for c in range(ncols):
            x0 = minx + c * cell
            y0 = miny + r * cell
            if keep is not None and not keep.contains(Point(x0 + cell / 2.0, y0 + cell / 2.0)):
                continue  # 只保留格心在范围内的格，边界干净
            polys.append(box(x0, y0, x0 + cell, y0 + cell))
            dens.append(float(Z[r, c]))
    if not polys:
        return None, None, None
    if len(polys) > 50000:
        return None, None, None  # 掩膜裁剪后仍超限才拒（防掩膜前误杀 20m 网格）

    grid = gpd.GeoDataFrame({"density": dens}, geometry=polys, crs=_PROJ)
    stats = {
        "max": round(float(Z.max()), 2),
        "mean": round(float(Z.mean()), 2),
        "median": round(float(np.median(Z)), 2),
    }
    return grid, to_geojson(grid), stats
