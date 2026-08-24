"""双变量空间自相关工具：双变量 Moran's I（全局）+ LISA（局部）。

用 esda.Moran_BV / Moran_Local_BV + libpysal 权重做真计算（两库已装）：
- 多边形图层 → Queen 邻接权重（空岛/失败回退 Rook，再回退 KNN）；
- 点图层 → KNN 权重（k=min(8, n-1)）。

两种输入模式：
- 模式 A：单图层 + x_field/y_field（同一图层两个数值字段，如社区的两个人人数）；
- 模式 B：两个点图层 → 共享渔网聚合（各格计数 x_count/y_count）→ 对渔网算双变量 Moran
  （如 餐饮 POI vs 购物 POI 的分布关联），全空格剔除避免稀疏伪邻域。

输出：一个带 lisa_cluster 字段的生成多边形图层，五色分类（HH 红 / HL 橙 / LH 蓝 /
LL 紫 / NS 灰），返回全局 Moran's I + 伪 p 值。output_layer_id 让 executor 自动上图。
"""

from __future__ import annotations

import json
import re
from typing import Any

import numpy as np

from app.data_registry import DATA_REGISTRY
from app.geojson_utils import clean_layer_id
from app.models.layer import FieldSpec, GeometryType, LayerMetadata, LayerSource
from app.models.tool import ToolResult
from app.tools.context import get_current_context
from app.workspace import LAYER_DATA_DIR

# LISA 簇标签：esda q 象限码 1=HH 2=LH 3=LL 4=HL；p>=0.05 → NS
_QUAD_LABELS = {1: "HH", 2: "LH", 3: "LL", 4: "HL"}
_CLUSTER_COLORS = {
    "HH": "#d73027",
    "HL": "#fc8d59",
    "LH": "#91bfdb",
    "LL": "#4575b4",
    "NS": "#bdbdbd",
}
_GRID_CELL_LIMIT = 50000
_PROJ = "EPSG:32650"  # UTM 50N（深圳 114°E）


def _fail(message: str) -> ToolResult:
    return ToolResult(success=False, output={"error": message}, message=message)


def _load_gdf(layer_id: str) -> Any | None:
    """加载图层为 UTM 米制 GeoDataFrame（复用 gis 引擎，geometry 真列）。"""
    from app.gis.engine import load_gdf

    gdf = load_gdf(layer_id)
    if gdf is None or gdf.empty:
        return None
    return gdf


def _numeric_columns(gdf: Any) -> list[str]:
    return [c for c in gdf.columns if np.issubdtype(gdf[c].dtype, np.number)]


def _build_weights(gdf: Any, geometry_type: str) -> tuple[Any, str]:
    """构造行标准化的空间权重，返回 (w, 实际用到的权重类型)。

    多边形优先 Queen（空岛/失败回退 Rook，再回退 KNN）；点图层 KNN。
    KNN 能兜住一切（孤立单元、有洞的渔网、非邻接面），保证总有可用权重。
    """
    import libpysal

    n = len(gdf)
    k = min(8, max(1, n - 1))
    attempts: list[tuple[str, Any]] = []
    if geometry_type == "polygon":
        attempts = [
            ("queen", lambda: libpysal.weights.Queen.from_dataframe(gdf)),
            ("rook", lambda: libpysal.weights.Rook.from_dataframe(gdf)),
        ]
    attempts.append(("knn", lambda: libpysal.weights.KNN.from_dataframe(gdf, k=k)))
    last_exc: Exception | None = None
    for label, builder in attempts:
        try:
            w = builder()
            if len(w.islands) > 0:
                last_exc = RuntimeError(f"{label}: {len(w.islands)} 个孤立单元无邻居")
                continue
            cards = list(w.cardinalities.values()) if w.cardinalities is not None else []
            if w.n > 0 and sum(cards) > 0:
                w.transform = "r"
                return w, label
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
    if last_exc is not None:
        raise RuntimeError(f"空间权重构造失败：{last_exc}")
    raise RuntimeError("空间权重为空")


def _compute(x: np.ndarray, y: np.ndarray, w: Any, permutations: int) -> dict:
    """全局双变量 Moran's I + 局部 LISA。返回指标与逐单元结果。"""
    from esda.moran import Moran_BV, Moran_Local_BV

    mbv = Moran_BV(x, y, w, permutations=permutations)
    lbv = Moran_Local_BV(x, y, w, permutations=permutations, geoda_quads=True)
    p = np.asarray(lbv.p_sim, dtype=float)
    q = np.asarray(lbv.q, dtype=float)
    labels: list[str] = []
    for i in range(len(x)):
        if not np.isfinite(p[i]) or p[i] >= 0.05:
            labels.append("NS")
        else:
            labels.append(_QUAD_LABELS.get(int(q[i]), "NS"))
    return {
        "global_i": float(mbv.I),
        "global_p": float(mbv.p_sim) if mbv.p_sim is not None else None,
        "lisa_i": np.asarray(lbv.Is, dtype=float),
        "lisa_p": p,
        "clusters": np.array(labels),
    }


def _mode_a(
    layer_id: str,
    x_field: str,
    y_field: str,
    permutations: int,
    weights: str,
) -> tuple[dict, Any, dict]:
    """模式 A：单图层两字段。返回 (结果 dict, 输出 GDF, 元信息)。"""
    gdf = _load_gdf(layer_id)
    if gdf is None:
        raise ValueError(f"图层不存在或为空：{layer_id}")
    if x_field not in gdf.columns or y_field not in gdf.columns:
        raise ValueError(f"图层 {layer_id} 没有字段 {x_field} / {y_field}（可用数值字段：{'、'.join(_numeric_columns(gdf))}）")
    frame = gdf.copy()
    frame["x"] = frame[x_field].astype(float)
    frame["y"] = frame[y_field].astype(float)
    before = len(frame)
    frame = frame.replace([np.inf, -np.inf], np.nan).dropna(subset=["x", "y"])
    if len(frame) < 3:
        raise ValueError(f"有效单元不足（{len(frame)}，剔除 {before - len(frame)} 个空值），无法计算")
    x = frame["x"].to_numpy(dtype=float)
    y = frame["y"].to_numpy(dtype=float)
    if x.std() == 0 or y.std() == 0:
        raise ValueError("x/y 字段方差为 0（取值全相同），双变量自相关无意义")
    geom_type = getattr(DATA_REGISTRY.get_layer(layer_id), "geometry", None)
    is_polygon = str(geom_type).lower().startswith("polygon") if geom_type else False
    w, wlabel = _build_weights(frame, "polygon" if is_polygon else "point")
    result = _compute(x, y, w, permutations)
    out = frame.to_crs("EPSG:4490").copy()
    out["lisa_i"] = result["lisa_i"]
    out["lisa_p"] = np.round(result["lisa_p"], 4)
    out["lisa_cluster"] = result["clusters"]
    meta = {
        "layer_id": layer_id,
        "x_field": x_field,
        "y_field": y_field,
        "n_before": before,
        "n_units": len(frame),
        "n_dropped": before - len(frame),
        "weights": wlabel,
    }
    return result, out[["x", "y", "lisa_i", "lisa_p", "lisa_cluster", "geometry"]], meta


def _fishnet(gdfs: list[Any], cell: float) -> Any:
    """两个点图层联合包围盒上的共享渔网（米制）。格数超限抛错。"""
    import geopandas as gpd
    from shapely.geometry import box

    minx = min(g.total_bounds[0] for g in gdfs)
    miny = min(g.total_bounds[1] for g in gdfs)
    maxx = max(g.total_bounds[2] for g in gdfs)
    maxy = max(g.total_bounds[3] for g in gdfs)
    ncols = int(np.floor((maxx - minx) / cell)) + 1
    nrows = int(np.floor((maxy - miny) / cell)) + 1
    if ncols * nrows > _GRID_CELL_LIMIT:
        raise ValueError(
            f"渔网格数 {ncols * nrows} 超过上限 {_GRID_CELL_LIMIT}，请增大 cell_size_m"
        )
    xs = minx + np.arange(ncols) * cell
    ys = miny + np.arange(nrows) * cell
    polys = [box(x0, y0, x0 + cell, y0 + cell) for y0 in ys for x0 in xs]
    grid = gpd.GeoDataFrame(geometry=polys, crs=gdfs[0].crs)
    grid["cell_id"] = np.arange(len(grid))
    return grid


def _count_per_cell(points: Any, grid: Any) -> np.ndarray:
    """点图层 → 每个渔网格的要素数（对齐 grid 行序）。"""
    import geopandas as gpd

    joined = gpd.sjoin(points[["geometry"]], grid[["cell_id", "geometry"]], predicate="within", how="left")
    idx = joined["cell_id"].dropna().astype(int).to_numpy()
    return np.bincount(idx, minlength=len(grid)).astype(float)


def _mode_b(
    x_layer_id: str,
    y_layer_id: str,
    cell_size_m: int,
    permutations: int,
) -> tuple[dict, Any, dict]:
    """模式 B：两个点图层 → 共享渔网聚合 → 双变量 Moran。"""
    gx = _load_gdf(x_layer_id)
    gy = _load_gdf(y_layer_id)
    if gx is None or gy is None:
        raise ValueError(f"图层缺失：{x_layer_id} / {y_layer_id}")
    cell = max(int(cell_size_m or 300), 100)
    grid = _fishnet([gx, gy], cell)
    cx = _count_per_cell(gx, grid)
    cy = _count_per_cell(gy, grid)
    keep = (cx + cy) > 0
    if keep.sum() < 3:
        raise ValueError(f"有效格网不足（{int(keep.sum())} 个非空格），请减小 cell_size_m")
    x = cx[keep]
    y = cy[keep]
    if x.std() == 0 or y.std() == 0:
        raise ValueError("两图层格网计数方差为 0，双变量自相关无意义")
    grid_keep = grid.loc[keep].copy()
    w, wlabel = _build_weights(grid_keep, "polygon")
    result = _compute(x, y, w, permutations)
    out = grid_keep.to_crs("EPSG:4490").copy()
    out["x"] = x.astype(int)
    out["y"] = y.astype(int)
    out["lisa_i"] = result["lisa_i"]
    out["lisa_p"] = np.round(result["lisa_p"], 4)
    out["lisa_cluster"] = result["clusters"]
    meta = {
        "x_layer_id": x_layer_id,
        "y_layer_id": y_layer_id,
        "cell_size_m": cell,
        "n_units": len(x),
        "n_dropped": int((~keep).sum()),
        "weights": wlabel,
    }
    return result, out[["x", "y", "lisa_i", "lisa_p", "lisa_cluster", "geometry"]], meta


def _register(out: Any, base: str, meta: dict, message_suffix: str) -> ToolResult:
    """注册生成图层（_bmi 后缀）+ 五色 LISA 分类配色 + 物化 + 返回 ToolResult。"""
    fc = json.loads(out.to_json())
    layer_id = clean_layer_id(f"{base}_bmi")
    cluster_counts: dict[str, int] = {}
    for label in out["lisa_cluster"]:
        cluster_counts[label] = cluster_counts.get(label, 0) + 1
    fields = [
        FieldSpec(name="x", type="number", description=meta.get("x_field") or "x 变量计数"),
        FieldSpec(name="y", type="number", description=meta.get("y_field") or "y 变量计数"),
        FieldSpec(name="lisa_i", type="number", description="局部双变量 Moran's I"),
        FieldSpec(name="lisa_p", type="number", description="伪 p 值（置换检验）"),
        FieldSpec(name="lisa_cluster", type="string", description="LISA 簇：HH/LH/LL/HL/NS"),
    ]
    layer = LayerMetadata(
        id=layer_id,
        name=base,
        source=LayerSource.generated,
        geometry=GeometryType.polygon,
        color="#0F172A",
        visible=True,
        feature_count=len(out),
        crs="EPSG:4490",
        fields=fields,
        description=f"{base}：双变量空间自相关（{len(out)} 个单元，{message_suffix}）",
        generated_by="bivariate_moran 工具",
    )
    DATA_REGISTRY.register_generated(layer, geojson=fc)
    try:
        LAYER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        (LAYER_DATA_DIR / f"{layer_id}.json").write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass  # 物化失败不致命，内存注册已让地图可见

    # 五色 LISA 分类配色：固定语义色（HH 红 / HL 橙 / LH 蓝 / LL 紫 / NS 灰），只含出现的簇
    try:
        from app.tools.layer_style import apply_renderer_style

        classes = [
            {"value": label, "color": _CLUSTER_COLORS[label]}
            for label in ("HH", "HL", "LH", "LL", "NS")
            if cluster_counts.get(label)
        ]
        if classes:
            style = {
                "styleMode": "categorized",
                "visualVariables": {
                    "renderer": "categorized",
                    "categorized": {"field": "lisa_cluster", "classes": classes},
                },
            }
            apply_renderer_style(layer_id, style, emit=get_current_context().emit)
    except Exception:  # noqa: BLE001
        pass

    result = meta.get("_result", {})
    global_i = result.get("global_i")
    global_p = result.get("global_p")
    message = (
        f"双变量 Moran's I = {global_i:.3f}"
        f"（伪 p = {global_p:.3f}）" if global_p is not None else f"双变量 Moran's I = {global_i:.3f}"
    ) + f"，识别 LISA 簇 {cluster_counts}，已生成五色分类图层「{base}」"
    return ToolResult(
        success=True,
        output={
            "layer_id": layer_id,
            "output_layer_id": layer_id,
            "name": base,
            "feature_count": len(out),
            "global_i": global_i,
            "global_p": global_p,
            "n_units": meta.get("n_units", len(out)),
            "n_dropped": meta.get("n_dropped", 0),
            "cluster_counts": cluster_counts,
        },
        message=message,
    )


def run_bivariate_moran(params: dict) -> ToolResult:
    """双变量空间自相关入口：模式 A（单图层两字段）或模式 B（两图层渔网）。"""
    try:
        permutations = max(99, min(9999, int(params.get("permutations") or 999)))
    except (TypeError, ValueError):
        permutations = 999
    weights = str(params.get("weights") or "auto").strip().lower()
    base = str(params.get("name") or "").strip().rstrip("双变量自相关").rstrip("自相关").strip()

    x_layer_id = str(params.get("x_layer_id") or "").strip()
    y_layer_id = str(params.get("y_layer_id") or "").strip()
    layer_id = str(params.get("layer_id") or "").strip()
    x_field = str(params.get("x_field") or "").strip()
    y_field = str(params.get("y_field") or "").strip()

    try:
        if layer_id and x_field and y_field:
            # 模式 A：单图层两字段
            result, out, meta = _mode_a(layer_id, x_field, y_field, permutations, weights)
            if not base:
                src = DATA_REGISTRY.get_layer(layer_id)
                base = f"{src.name if src else layer_id}·{x_field}×{y_field}"
            meta["_result"] = result
            return _register(out, base, meta, f"{x_field} 与 {y_field} 双变量自相关")
        if x_layer_id and y_layer_id:
            # 模式 B：两图层 → 渔网
            cell = max(100, int(params.get("cell_size_m") or 300))
            result, out, meta = _mode_b(x_layer_id, y_layer_id, cell, permutations)
            if not base:
                sx = DATA_REGISTRY.get_layer(x_layer_id)
                sy = DATA_REGISTRY.get_layer(y_layer_id)
                base = f"{(sx.name if sx else x_layer_id)}×{(sy.name if sy else y_layer_id)}"
            meta["_result"] = result
            return _register(out, base, meta, f"{cell}m 渔网聚合双变量自相关")
        return _fail(
            "参数不足：模式 A 需 layer_id + x_field + y_field（同一多边形/点图层的两个数值字段）；"
            "模式 B 需 x_layer_id + y_layer_id（两个点图层，自动渔网聚合）。"
        )
    except ValueError as exc:
        return _fail(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _fail(f"双变量自相关计算失败：{exc}")
