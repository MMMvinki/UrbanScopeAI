"""确定性分析工具集：空间叠加 / 最近距离 / 密度 / 供需 / buffer / 统计 / 数据清单。

真计算优先（app.gis.engine，GeoPandas/Shapely 对真实几何运算）。几何数据缺失时
**诚实失败**（返回 success=False + reason 或逐项 None + warnings），绝不回退演示数值
冒充成功。buffer 的真实几何会写入 DATA_REGISTRY，供前端拉取渲染。所有处理函数接受
dict 入参（LLM 工具调用传入），统一返回 ToolResult。
"""

from app.data_registry import DATA_REGISTRY
from app.gis import engine
from app.models.layer import GeometryType
from app.models.tool import ToolResult


def _layer(layer_id: str) -> object | None:
    return DATA_REGISTRY.get_layer(layer_id) or DATA_REGISTRY.get_layer_by_name(layer_id)


def _fail(message: str) -> ToolResult:
    return ToolResult(success=False, message=message)


def _resolve(layer_ids: list[str]) -> tuple[list, ToolResult | None]:
    """把入参里的图层 id/名称解析成 LayerMetadata；解析失败返回错误。"""
    found: list = []
    missing: list[str] = []
    for raw in layer_ids:
        layer = _layer(raw)
        if layer is None:
            missing.append(raw)
        else:
            found.append(layer)
    if missing:
        return found, _fail(f"未找到图层：{'、'.join(missing)}")
    return found, None


def _reject_tables(*layers) -> ToolResult | None:
    """空间工具入口拒绝表格图层：无空间几何，不得落入 stable_rate 演示数值。"""
    for layer in layers:
        if getattr(layer, "geometry", None) == GeometryType.table:
            return _fail(f"「{layer.name}」是表格图层，无空间几何，无法进行空间分析")
    return None


def run_list_datasets(params: dict) -> ToolResult:
    layers = DATA_REGISTRY.list_layers()
    return ToolResult(
        output={
            "layers": [
                {
                    "id": layer.id,
                    "name": layer.name,
                    "geometry": layer.geometry.value,
                    "feature_count": layer.feature_count,
                    "has_geojson": DATA_REGISTRY.get_geojson(layer.id) is not None,
                }
                for layer in layers
            ]
        },
        message=f"发现 {len(layers)} 个图层",
    )


def _layer_data_path(layer) -> str | None:
    """图层 GeoJSON 在 execute_code 里的可读路径（相对 workspace cwd）。

    平台图层（只读底座）在 platform_data/geojson/{id}.json —— execute_code 里
    gpd.read_file('../platform_data/geojson/{id}.json') 直接读（Agent 过去按图层 id
    去工作区 data/layers 找同名文件找不到 → 找数据死循环，见 platform-data-path-gotcha）。
    上传/生成图层按惯例在工作区 data/layers/{id}.json。
    """
    from app.models.layer import LayerSource

    if getattr(layer, "source", None) is LayerSource.platform:
        return f"../platform_data/geojson/{layer.id}.json"
    return f"data/layers/{layer.id}.json"


def run_get_dataset_schema(params: dict) -> ToolResult:
    layer = _layer(str(params.get("layer_id", "")))
    if layer is None:
        return _fail(f"未找到图层：{params.get('layer_id')}")
    return ToolResult(
        output={
            "layer_id": layer.id,
            "name": layer.name,
            "geometry": layer.geometry.value,
            "feature_count": layer.feature_count,
            "data_path": _layer_data_path(layer),
            "fields": [
                {"name": field.name, "type": field.type, "description": field.description}
                for field in layer.fields
            ],
        },
        message=f"{layer.name}：{layer.geometry.value}，{layer.feature_count} 要素",
    )


def run_spatial_join(params: dict) -> ToolResult:
    target = _layer(str(params.get("target_layer_id", "")))
    overlay = _layer(str(params.get("overlay_layer_id", "")))
    if target is None or overlay is None:
        return _fail("叠加图层解析失败")
    if err := _reject_tables(target, overlay):
        return err
    real = engine.spatial_join(target.id, overlay.id)
    if real is not None:
        return ToolResult(
            success=True,
            output={
                "target_layer_id": target.id,
                "overlay_layer_id": overlay.id,
                "ratio": real["ratio"],
                "output_layer_id": real["output_layer_id"],
            },
            message=f"{target.name} 与 {overlay.name} 叠加占比 {real['ratio']}%",
        )
    return _fail(f"无法计算 {target.name} 与 {overlay.name} 的空间叠加占比：目标或叠加图层缺少有效几何")


def run_nearest_distance(params: dict) -> ToolResult:
    target = _layer(str(params.get("target_layer_id", "")))
    reference = _layer(str(params.get("reference_layer_id", "")))
    if target is None or reference is None:
        return _fail("最近距离图层解析失败")
    if err := _reject_tables(target, reference):
        return err
    real = engine.nearest_distance(target.id, reference.id)
    if real is not None:
        return ToolResult(
            success=True,
            output={
                "target_layer_id": target.id,
                "reference_layer_id": reference.id,
                "avg_distance_m": real["avg_distance_m"],
                "output_layer_id": real["output_layer_id"],
            },
            message=f"{target.name} 到最近{reference.name}平均距离 {real['avg_distance_m']} 米",
        )
    return _fail(f"无法计算 {target.name} 到最近{reference.name} 的平均距离：目标或参考图层缺少有效几何")


def run_density(params: dict) -> ToolResult:
    layer = _layer(str(params.get("layer_id", "")))
    if layer is None:
        return _fail(f"未找到图层：{params.get('layer_id')}")
    if err := _reject_tables(layer):
        return err
    real = engine.density(layer.id)
    if real is not None:
        return ToolResult(
            success=True,
            output={"layer_id": layer.id, "density_index": real["density_index"]},
            message=f"{layer.name} 密度指数 {real['density_index']}",
        )
    return _fail(f"无法计算「{layer.name}」密度指数：图层缺少有效几何或面积分母")


def run_supply_demand(params: dict) -> ToolResult:
    facility = _layer(str(params.get("facility_layer_id", "")))
    population = _layer(str(params.get("population_layer_id", "")))
    if facility is None or population is None:
        return _fail("供需图层解析失败")
    if err := _reject_tables(facility, population):
        return err
    real = engine.supply_demand(facility.id, population.id)
    if real is not None:
        return ToolResult(
            success=True,
            output={
                "facility_layer_id": facility.id,
                "population_layer_id": population.id,
                "satisfaction_percent": real["satisfaction_percent"],
            },
            message=f"{facility.name} 对{population.name}供需满足度 {real['satisfaction_percent']}%",
        )
    return _fail(f"无法计算 {facility.name} 对 {population.name} 的供需满足度：人口图层需含 pop 字段，且双方需有有效几何")


def run_buffer(params: dict) -> ToolResult:
    layer = _layer(str(params.get("layer_id", "")))
    if layer is None:
        return _fail(f"未找到图层：{params.get('layer_id')}")
    distance = int(params.get("distance", 800))
    if distance <= 0:
        return _fail(f"缓冲区距离必须为正数：{distance}")
    if err := _reject_tables(layer):
        return err
    _, geojson = engine.buffer(layer.id, distance)
    if geojson is None:
        return _fail(f"无法生成「{layer.name}」的 {distance}m 缓冲区：图层缺少有效几何")
    output_layer_id = f"{layer.id}_buffer"
    DATA_REGISTRY.set_geojson(output_layer_id, geojson)
    return ToolResult(
        success=True,
        output={
            "source_layer_id": layer.id,
            "output_layer_id": output_layer_id,
            "distance": distance,
        },
        message=f"{layer.name} 已生成 {distance}m 缓冲区",
    )


def run_spatial_clip(params: dict) -> ToolResult:
    """空间裁剪：把目标图层裁剪到指定范围图层，生成裁剪结果图层。

    仿 extract_features 完整注册路径：读源几何 → 几何裁剪 → 建新图层 →
    register_generated → 物化到工作区 data/layers → 返回 output_layer_id。
    """
    import json

    from app.geojson_utils import clean_layer_id, geometry_type_for, load_feature_collection
    from app.models.layer import LayerMetadata, LayerSource
    from app.workspace import LAYER_DATA_DIR

    target = _layer(str(params.get("target_layer_id", "")))
    clip_layer = _layer(str(params.get("clip_layer_id", "")))
    if target is None or clip_layer is None:
        return _fail("裁剪图层解析失败：target_layer_id / clip_layer_id 至少一个不存在")
    if err := _reject_tables(target, clip_layer):
        return err
    clipped = engine.clip(target.id, clip_layer.id)
    if clipped is None:
        return _fail(f"裁剪计算失败：目标「{target.name}」或边界「{clip_layer.name}」没有可用的几何数据")
    clip_ratio, geojson, feature_count = clipped

    layer_id = clean_layer_id(f"{target.id}_clip")
    name = str(params.get("output_name") or "").strip() or f"{target.name}裁剪"
    _features, geometry_types, _bbox = load_feature_collection(geojson)
    # 裁剪热力图层时保留热力语义：把 kind=heatmap / weightField 透传给结果层。
    # 否则裁剪会把「热力面」退化成普通点层（ScatterplotLayer + 人数当点尺寸），
    # 用户要的是连续渐变热力面，不是「点大人多」的夸张点层。
    is_heatmap = getattr(target, "kind", None) == "heatmap"
    layer = LayerMetadata(
        id=layer_id,
        name=name,
        source=LayerSource.generated,
        geometry=geometry_type_for(geometry_types),
        color="#0F172A",
        visible=True,
        feature_count=feature_count,
        crs="EPSG:4490",
        fields=target.fields,
        description=f"{target.name} 裁剪到 {clip_layer.name} 范围（保留 {clip_ratio}%"
        f"{'，仍为热力面' if is_heatmap else ''}）",
        generated_by="spatial_clip 工具",
        kind=("heatmap" if is_heatmap else "vector"),
        weightField=(target.weightField if is_heatmap else None),
    )
    DATA_REGISTRY.register_generated(layer, geojson=geojson)
    try:
        LAYER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        (LAYER_DATA_DIR / f"{layer_id}.json").write_text(
            json.dumps(geojson, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass  # 物化失败不致命，内存注册已让地图可见

    return ToolResult(
        success=True,
        output={
            "layer_id": layer_id,
            "output_layer_id": layer_id,  # 必须等于注册 id：executor 生成图层检测 / _generated_info 依赖
            "name": name,
            "feature_count": feature_count,
            "clip_ratio": clip_ratio,
            "source_layer_id": target.id,
            "clip_layer_id": clip_layer.id,
        },
        message=f"{name}已生成（{feature_count} 个要素，保留 {clip_ratio}%）",
    )


def run_statistics(params: dict) -> ToolResult:
    """逐图层统计指定指标。缺失或算不出的图层该项记 None + warnings，绝不伪造数字。

    avg_distance 需显式 reference_layer_id（参考图层）；satisfaction 需显式
    population_layer_id（人口图层，须含 pop 列）。不再硬编码 transit_stations /
    population_grid（那两个图层已不存在，过去让这两个统计项永远走演示兜底）。
    """
    layer_ids = list(params.get("layer_ids", []))
    agg = str(params.get("agg", "coverage_rate"))
    layers, error = _resolve(layer_ids)
    if error is not None:
        return error
    if err := _reject_tables(*layers):
        return err
    warnings: list[str] = []
    summary: dict[str, object] = {}

    if agg == "avg_distance":
        reference_id = str(params.get("reference_layer_id", "")).strip()
        if not reference_id:
            return _fail("统计平均距离需提供 reference_layer_id（参考图层），且该图层必须存在")
        reference = _layer(reference_id)
        if reference is None:
            return _fail("统计平均距离需提供 reference_layer_id（参考图层），且该图层必须存在")
        for layer in layers:
            real = engine.nearest_distance(layer.id, reference.id)
            if real is None:
                summary[layer.name] = None
                warnings.append(f"{layer.name} 平均距离计算失败（无可用几何）")
            else:
                summary[layer.name] = real["avg_distance_m"]
    elif agg == "satisfaction":
        population_id = str(params.get("population_layer_id", "")).strip()
        if not population_id:
            return _fail("统计供需满足度需提供 population_layer_id（人口图层），且该图层必须存在")
        population = _layer(population_id)
        if population is None:
            return _fail("统计供需满足度需提供 population_layer_id（人口图层），且该图层必须存在")
        for layer in layers:
            real = engine.supply_demand(layer.id, population.id)
            if real is None:
                summary[layer.name] = None
                warnings.append(f"{layer.name} 供需满足度计算失败（人口图层需含 pop 列，或双方无有效几何）")
            else:
                summary[layer.name] = real["satisfaction_percent"]
    else:  # coverage_rate / density
        distance = int(params.get("distance", 800))
        for layer in layers:
            real = (
                engine.coverage(layer.id, distance)
                if agg == "coverage_rate"
                else engine.density(layer.id)
            )
            if real is None:
                summary[layer.name] = None
                warnings.append(f"{layer.name} 统计失败（无可用几何或分母）")
            else:
                summary[layer.name] = (
                    real["coverage_rate"] if agg == "coverage_rate" else real["density_index"]
                )

    output: dict[str, object] = {"agg": agg, "summary": summary, "count": len(layers)}
    if warnings:
        output["warnings"] = warnings
    return ToolResult(output=output, message=f"统计 {len(layers)} 个图层（{agg}）")
