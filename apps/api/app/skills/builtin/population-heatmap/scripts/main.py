# -*- coding: utf-8 -*-
"""内置可执行 Skill：人口热力图（慧眼人口点 → 按人数加权连续渐变热力面图层）+ 人群画像饼图。

与 kernel-density 的本质区别：核密度只数「点密度」（每点当 1），会抹掉人口本值；
本 skill 把慧眼「人数」点物化下来，注册 kind=heatmap，由 deck.gl HeatmapLayer
（getWeight=人数，GPU 加权高斯平滑，radiusPixels 即可调热力半径）渲染连续渐变热力面。
**不做任何后端 KDE/插值** —— 纯物化 + 声明渲染语义，避免把人口真值抹成点疏密。

人群画像（2026-09-02 补充）：慧眼源自带画像**比率**列（性别/年龄/工作/行业/资产），
均为 0~1 小数且**每网格组内加总=1.0**。该类别人口 = 人数 × 比率，按空间范围聚合成
**饼图**（emit_chart，前端 ECharts 可悬浮/动画）。教育/消费/人生阶段/收入 数据残缺或
跳号语义不明，**一律不用**（避免把残缺分布画成貌似完整的饼）。行业取 Top-8 + 「其他」
防 23 片标签堆叠。

运行契约（skill_runner）：`--input in.json --output out.json`。
input.json wrapper: {"workspace", "skill_id", "params"}。
产物写入 workspace/data/layers/<源id>_pop_heat.json，经 output.json 声明 layers[]。
"""

import argparse
import json
from pathlib import Path

import geopandas as gpd

from app.data_sources import get_source, get_source_meta

_PROJ = "EPSG:32650"

# ---- 人群画像：可靠完整组（列名 → 显示名）。每网格比率加总=1.0，均为 0~1 小数。 ----
_PROFILE_GROUPS = [
    {
        "key": "gender",
        "title": "性别构成",
        "cols": [("性别_男", "男"), ("性别_女", "女")],
    },
    {
        "key": "age",
        "title": "年龄阶段构成",
        "cols": [
            ("年龄阶段_1", "0-14岁"), ("年龄阶段_2", "15-24岁"), ("年龄阶段_3", "25-34岁"),
            ("年龄阶段_4", "35-44岁"), ("年龄阶段_5", "45-54岁"), ("年龄阶段_6", "55-64岁"),
            ("年龄阶段_7", "65岁及以上"),
        ],
    },
    {
        "key": "occupation",
        "title": "工作类型构成",
        "cols": [
            ("工作_生产", "生产制造"), ("工作_文职", "文职技能"), ("工作_专业", "专业技术"),
            ("工作_管理", "管理岗位"), ("工作_个体", "个体经营"), ("工作_服务", "服务行业"),
        ],
    },
    {
        "key": "industry",
        "title": "行业构成",
        "cols": [
            ("行业_农林", "农林牧渔"), ("行业_能源", "能源"), ("行业_食品", "食品"),
            ("行业_纺织", "纺织"), ("行业_建材", "建材"), ("行业_医药", "医药"),
            ("行业_机械", "机械"), ("行业_汽车", "汽车"), ("行业_IT通", "IT/通信"),
            ("行业_建筑", "建筑"), ("行业_交通", "交通运输"), ("行业_餐饮", "餐饮"),
            ("行业_家电", "家电"), ("行业_日化", "日化"), ("行业_金融", "金融"),
            ("行业_生活", "生活服务"), ("行业_住宿", "住宿"), ("行业_广告", "广告"),
            ("行业_法律", "法律"), ("行业_科学", "科学研究"), ("行业_教育", "教育"),
            ("行业_文化", "文化"), ("行业_社会", "社会服务"),
        ],
    },
    {
        "key": "car",
        "title": "车辆拥有构成",
        "cols": [("资产_有车", "有车"), ("资产_无车", "无车")],
    },
]

# 饼图扇区上限：长尾维度（行业）砍到 Top-8 + 「其他」，避免标签重叠堆死。
_PIE_MAX_SLICES = 8


def _fail(output_path: Path, message: str) -> None:
    json.dump({"success": False, "summary": message}, open(output_path, "w", encoding="utf-8"), ensure_ascii=False)


def _source_label(source_id: str) -> str:
    """源 id → 中文名（居住/工作），用于图层名。"""
    meta = get_source_meta(source_id) or {}
    name = str(meta.get("name") or source_id)
    if source_id == "huoyan_resident":
        return "居住人口"
    if source_id == "huoyan_worker":
        return "工作人口"
    return name


def _resolve_weight_field(source_id: str, requested: str | None) -> str | None:
    """确定加权字段：请求值优先，否则用清单 pop_field（慧眼=人数）。

    非 data_sources id（已注册图层/目录数据集）拿不到 pop_field 时，默认统一用「人数」——
    人口点图层人口列约定为「人数」；若无此列，后续读源会自然失败并给明确报错。
    """
    if requested:
        return requested
    meta = get_source_meta(source_id) or {}
    pf = meta.get("pop_field")
    if pf:
        return pf
    return "人数"


def _profile_columns() -> list[str]:
    """人群画像组里所有比率列名（用于读盘时一并请求）。"""
    return [c for group in _PROFILE_GROUPS for c, _ in group["cols"]]


def build_profile(gdf: gpd.GeoDataFrame, weight_field: str) -> dict:
    """按画像组统计该类别人口：人数 × 比率（比率列均为 0~1，组内加总≈1）。

    返回 {key: {"title": ..., "slices": [(显示名, 人数), ...]}}；加权列缺失或整组
    无有效列/组合计为 0 时整组跳过。绝不回退演示数值。
    """
    if weight_field not in gdf.columns:
        return {}
    w = gdf[weight_field].astype(float).fillna(0.0)
    profile: dict = {}
    for group in _PROFILE_GROUPS:
        slices: list[tuple[str, float]] = []
        for col, label in group["cols"]:
            if col not in gdf.columns:
                continue
            ratio = gdf[col].astype(float).fillna(0.0)
            pop = float((w * ratio).sum())
            if pop <= 0:
                continue
            slices.append((label, round(pop, 1)))
        if slices:
            profile[group["key"]] = {"title": group["title"], "slices": slices}
    return profile


def emit_profile_charts(profile: dict) -> int:
    """每个可用画像组一张**饼图**（Top-8 + 其他），emit_chart 交前端 ECharts 渲染。

    emit_chart 由 skill_runner 注入为模块级全局；单图失败不致命（热力图照常）。
    """
    emitted = 0
    for info in profile.values():
        slices = sorted(info["slices"], key=lambda x: x[1], reverse=True)
        if len(slices) > _PIE_MAX_SLICES:
            top = slices[:_PIE_MAX_SLICES - 1]
            rest = slices[_PIE_MAX_SLICES - 1:]
            slices = top + [("其他", round(sum(v for _, v in rest), 1))]
        labels = [str(label) for label, _ in slices]
        data = [v for _, v in slices]
        try:
            emit_chart(
                title=str(info["title"]),
                chart_type="pie",
                x_labels=labels,
                series=[{"name": str(info["title"]), "data": data}],
            )
            emitted += 1
        except Exception:
            pass
    return emitted


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        params = (json.load(f) or {}).get("params") or {}

    source_id = str(params.get("population_source") or params.get("source_id") or "").strip()
    if not source_id:
        _fail(Path(args.output), "缺少参数 population_source（huoyan_resident / huoyan_worker，或带人数列的图层 id）")
        return

    # 自动还原原始源：agent 常把本 skill 已生成的热力图图层（*_pop_heat）误当「人口源」传入。
    # 这些图层本来就是本 skill 的产物（layer_id = f"{原始源}_pop_heat"），去掉后缀即原始源标识。
    # 与其拒绝后让 agent 去猜 huoyan_resident，不如直接确定性还原 —— 无需 LLM 推理，也避免
    # agent 反复用同一个 *_pop_heat 卡死（历史 run 中「读取失败或无点」循环的根因之一）。
    if source_id.endswith("_pop_heat"):
        source_id = source_id[: -len("_pop_heat")].strip()
    elif _is_heatmap_layer(source_id):
        # 非 *_pop_heat 但仍登记为 heatmap 的图层：无法确定其原始源，诚实拒绝并给出正确 id。
        _fail(
            Path(args.output),
            f"数据源 {source_id} 是本 skill 的热力图层（kind=heatmap），不能当人口源自引用。"
            "请用原始数据源 id：huoyan_resident（居住）/ huoyan_worker（工作），"
            "或另一个非 heatmap 的带『人数』列点图层。",
        )
        return

    weight_field = _resolve_weight_field(source_id, params.get("weight_field"))
    if not weight_field:
        _fail(Path(args.output), f"数据源 {source_id} 未声明人口字段（pop_field），请显式传 weight_field")
        return

    # 空间范围：优先 area 图层 bbox，其次显式 bbox，否则全量读（人口 10 万级可接受）
    bbox = _resolve_bbox(params)
    # 读盘：加权列 + 全部画像比率列（get_source 只读这些列，不整层全载）。
    read_cols = [weight_field] + _profile_columns()
    pts = get_source(source_id, columns=read_cols, bbox=bbox)
    if pts is None or len(pts) == 0:
        # 兜底：非 data_sources id（已注册点图层 / 目录数据集图层）→ 走注册表 load_gdf。
        # agent 常传裁剪出的图层 id（如 futian_resident_points）或目录数据集 id，get_source 不认，
        # 但只要是带「人数」列的已注册点图层就应能物化。注意此路径只读 [geometry, weight_field]，
        # 无画像列 → 画像图自动跳过（仅出热力图）。
        pts = _load_registered_points(source_id, weight_field, bbox)
    if pts is None or len(pts) == 0:
        _fail(Path(args.output), f"数据源 {source_id} 读取失败或无点（可能 bbox 过滤后为空，或客流大图层被拒）")
        return

    # 裁到研究范围多边形（area_layer_id 给了才裁；用点-in-面，非 bbox 粗裁）
    pts_wgs = pts  # get_source 已保证 EPSG:4326
    area_id = str(params.get("area_layer_id") or "").strip()
    if area_id:
        from app.gis import engine

        area_gdf = engine.load_gdf(area_id)  # UTM 米制
        if area_gdf is not None and len(area_gdf) > 0:
            pts_wgs = _clip_points_to_area(pts, area_gdf)

    total_pop = float(pts_wgs[weight_field].sum())

    # 人群画像（人数×比率）——在最终空间范围（计算前后一致）上聚合成饼图
    profile = build_profile(pts_wgs, weight_field)

    # 物化点 GeoJSON（EPSG:4490，与平台一致；保留人数加权字段）
    geojson = _points_to_geojson(pts_wgs, weight_field)

    from app.workspace import LAYER_DATA_DIR

    layer_id = f"{source_id}_pop_heat"
    LAYER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (LAYER_DATA_DIR / f"{layer_id}.json").write_text(
        json.dumps(geojson, ensure_ascii=False), encoding="utf-8"
    )

    label = _source_label(source_id)
    # 分位数口径的摘要（避免 max 被单个超大格点带偏，给用户一个稳健的下限参考）
    pop_series = pts_wgs[weight_field].astype(float)
    stats = {
        "count": int(len(pts_wgs)),
        "total_pop": float(total_pop),
        "max": float(pop_series.max()) if len(pop_series) else 0.0,
        "mean": float(pop_series.mean()) if len(pop_series) else 0.0,
        "weight_field": weight_field,
    }

    result = {
        "success": True,
        "summary": (
            f"{label}热力图已生成：{len(pts_wgs)} 个慧眼网格点（按「{weight_field}」加权），"
            f"合计 {total_pop:,.0f} 人，单点 max={stats['max']:.0f}、mean={stats['mean']:.0f}。"
            "热力半径默认 25px、热力强度 2.5，可在地图样式里调。"
            "（本 skill 的默认热力样式，非矢量点大小）"
        ),
        "layer_id": layer_id,
        "stats": stats,
        "layers": [
            {
                "layer_id": layer_id,
                "name": f"{label}热力图",
                "path": f"data/layers/{layer_id}.json",
                "feature_count": len(pts_wgs),
                "geometry": "Point",
                "kind": "heatmap",
                "weight_field": weight_field,
                "lazy": True,  # 人口点 10 万级：不进 bulk 端点，前端显/open 时单取
                "fields": [
                    {"name": weight_field, "type": "number", "description": "该网格点人口（绝对量）"}
                ],
                "description": f"{label}热力图（慧眼 {source_id}，{weight_field} 加权）",
                # 本 skill 的默认热力样式（非矢量点大小）：radius=deck HeatmapLayer radiusPixels，
                # strokeWidth=热力强度（alias：intensity）。前端无样式时也兜底 25/2.5，这里显式下发保证持久化。
                "style": {"radius": 25, "strokeWidth": 2.5},
            }
        ],
        "profile": profile,  # 人群画像：{组: {title, slices}}，供摘要/复述；饼图经 emit_chart 出
    }
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, default=str)

    # ── 成果栏图表事件（runner 注入 emit_chart）──
    n_profiles = emit_profile_charts(profile)
    if n_profiles:
        print(f"[population-heatmap] ✅ 产出 {n_profiles} 张人群画像饼图")


def _is_heatmap_layer(source_id: str) -> bool:
    """判断 source_id 是否为已注册的 kind=heatmap 图层（自引用护栏）。"""
    from app.data_registry import DATA_REGISTRY

    layer = DATA_REGISTRY.get_layer(source_id)
    return layer is not None and getattr(layer, "kind", None) == "heatmap"


def _load_registered_points(
    source_id: str, weight_field: str, bbox: tuple[float, float, float, float] | None
) -> gpd.GeoDataFrame | None:
    """非 data_sources id 兜底读源：走注册表 load_gdf（已注册点图层 / 目录数据集图层）。

    get_source 只认 data_sources.json 条目（慧眼 huoyan_*）；agent 可能传任意带人数列的
    已注册点图层 id（裁剪产物、目录数据集启用后的图层）。这里兜住它：能读到且带 weight_field
    才返回，否则 None 交给上层报「读取失败」。此路径不读画像列（注册图层未必有画像比率），
    画像图交给 build_profile 按「列是否存在」自动跳过。
    """
    try:
        from app.gis import engine

        gdf = engine.load_gdf(source_id)
    except Exception:
        return None
    if gdf is None or len(gdf) == 0 or weight_field not in gdf.columns:
        return None
    # 统一到 EPSG:4326；平台图层多为 4326，engine 可能已转或保持原样
    epsg = gdf.crs.to_epsg() if gdf.crs is not None else None
    if epsg not in (None, 4326):
        try:
            gdf = gdf.to_crs("EPSG:4326")
        except Exception:
            pass
    if bbox:
        try:
            gdf = gdf[
                (gdf.geometry.x >= bbox[0]) & (gdf.geometry.x <= bbox[2]) & (gdf.geometry.y >= bbox[1])
                & (gdf.geometry.y <= bbox[3])
            ]
        except Exception:
            pass
    return gdf[["geometry", weight_field]].copy()


def _resolve_bbox(params: dict) -> tuple[float, float, float, float] | None:
    """bbox 参数：accept list → tuple，含 4 个数才返回。"""
    raw = params.get("bbox")
    if not isinstance(raw, (list, tuple)) or len(raw) < 4:
        return None
    try:
        return (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    except (TypeError, ValueError):
        return None


def _clip_points_to_area(pts: gpd.GeoDataFrame, area_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """把 EPSG:4326 点裁到研究范围多边形内（点-in-面）。

    area_gdf 来自 engine.load_gdf（UTM 米制 EPSG:32650），需先把点转成同投影做 sjoin。
    """
    try:
        pts_utm = pts.to_crs(_PROJ)
        joined = gpd.sjoin(pts_utm, area_gdf[["geometry"]], predicate="within", how="inner")
        if len(joined) == 0:
            return pts.iloc[0:0]
        # 回 4326 保留原经纬度列
        return joined.drop(columns=["index_right"]).to_crs("EPSG:4326")
    except Exception:
        # 兜底：裁剪失败退化为全量点（诚实不减总体，只是范围略大）
        return pts


def _points_to_geojson(pts: gpd.GeoDataFrame, weight_field: str) -> dict:
    """EPSG:4326 点 GeoDataFrame → FeatureCollection（每要素 properties={weight_field: 人数}）。

    只带 geometry + 加权字段，结果图层体积最小化（10 万点不塞冗余属性列）。
    """
    geom_col = pts.geometry.name
    pts = pts.dropna(subset=[geom_col])
    features = []
    for geom, weight in zip(pts.geometry, pts[weight_field]):
        if geom is None or geom.is_empty:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [round(float(geom.x), 6), round(float(geom.y), 6)],
                },
                "properties": {weight_field: float(weight) if weight is not None else 0.0},
            }
        )
    return {"type": "FeatureCollection", "features": features}


if __name__ == "__main__":
    main()
