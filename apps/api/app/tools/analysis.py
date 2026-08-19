"""确定性分析工具集：空间叠加 / 最近距离 / 密度 / 供需 / buffer / 统计 / 数据清单。

真计算优先（app.gis.engine，GeoPandas/Shapely 对真实几何运算），几何数据缺失
回退 stable_rate 演示数值（防御，不挂）。buffer 的真实几何会写入 DATA_REGISTRY，
供前端拉取渲染。所有处理函数接受 dict 入参（LLM 工具调用传入），统一返回 ToolResult。
"""

from app.data_registry import DATA_REGISTRY
from app.gis import engine
from app.models.tool import ToolResult
from app.tools.coverage import stable_rate


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
    ratio = stable_rate(target.id)
    return ToolResult(
        success=True,
        output={
            "target_layer_id": target.id,
            "overlay_layer_id": overlay.id,
            "ratio": ratio,
            "output_layer_id": f"{target.id}_result",
        },
        message=f"{target.name} 与 {overlay.name} 叠加占比 {ratio}%（演示数值）",
    )


def run_nearest_distance(params: dict) -> ToolResult:
    target = _layer(str(params.get("target_layer_id", "")))
    reference = _layer(str(params.get("reference_layer_id", "")))
    if target is None or reference is None:
        return _fail("最近距离图层解析失败")
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
    avg_distance = stable_rate(target.id) * 10
    return ToolResult(
        success=True,
        output={
            "target_layer_id": target.id,
            "reference_layer_id": reference.id,
            "avg_distance_m": avg_distance,
            "output_layer_id": f"{target.id}_result",
        },
        message=f"{target.name} 到最近{reference.name}平均距离 {avg_distance} 米（演示数值）",
    )


def run_density(params: dict) -> ToolResult:
    layer = _layer(str(params.get("layer_id", "")))
    if layer is None:
        return _fail(f"未找到图层：{params.get('layer_id')}")
    real = engine.density(layer.id)
    if real is not None:
        return ToolResult(
            success=True,
            output={"layer_id": layer.id, "density_index": real["density_index"]},
            message=f"{layer.name} 密度指数 {real['density_index']}",
        )
    index = stable_rate(layer.id)
    return ToolResult(
        success=True,
        output={"layer_id": layer.id, "density_index": index},
        message=f"{layer.name} 密度指数 {index}（演示数值）",
    )


def run_supply_demand(params: dict) -> ToolResult:
    facility = _layer(str(params.get("facility_layer_id", "")))
    population = _layer(str(params.get("population_layer_id", "")))
    if facility is None or population is None:
        return _fail("供需图层解析失败")
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
    satisfaction = stable_rate(facility.id)
    return ToolResult(
        success=True,
        output={
            "facility_layer_id": facility.id,
            "population_layer_id": population.id,
            "satisfaction_percent": satisfaction,
        },
        message=f"{facility.name} 对{population.name}供需满足度 {satisfaction}%（演示数值）",
    )


def run_buffer(params: dict) -> ToolResult:
    layer = _layer(str(params.get("layer_id", "")))
    if layer is None:
        return _fail(f"未找到图层：{params.get('layer_id')}")
    distance = int(params.get("distance", 800))
    _, geojson = engine.buffer(layer.id, distance)
    output_layer_id = f"{layer.id}_buffer"
    if geojson is not None:
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
    clipped = engine.clip(target.id, clip_layer.id)
    if clipped is None:
        return _fail(f"裁剪计算失败：目标「{target.name}」或边界「{clip_layer.name}」没有可用的几何数据")
    clip_ratio, geojson, feature_count = clipped

    layer_id = clean_layer_id(f"{target.id}_clip")
    name = str(params.get("output_name") or "").strip() or f"{target.name}裁剪"
    _features, geometry_types, _bbox = load_feature_collection(geojson)
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
        description=f"{target.name} 裁剪到 {clip_layer.name} 范围（保留 {clip_ratio}%）",
        generated_by="spatial_clip 工具",
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
    layer_ids = list(params.get("layer_ids", []))
    agg = str(params.get("agg", "coverage_rate"))
    layers, error = _resolve(layer_ids)
    if error is not None:
        return error
    summary: dict[str, object] = {}
    for layer in layers:
        value: object = stable_rate(layer.id)
        if agg == "coverage_rate":
            real = engine.coverage(layer.id, 800)
            if real is not None:
                value = real["coverage_rate"]
        elif agg == "avg_distance":
            real = engine.nearest_distance(layer.id, "transit_stations")
            value = real["avg_distance_m"] if real is not None else stable_rate(layer.id) * 10
        elif agg == "density":
            real = engine.density(layer.id)
            value = real["density_index"] if real is not None else stable_rate(layer.id)
        elif agg == "satisfaction":
            real = engine.supply_demand(layer.id, "population_grid")
            value = real["satisfaction_percent"] if real is not None else stable_rate(layer.id)
        summary[layer.name] = value
    return ToolResult(
        output={"agg": agg, "summary": summary, "count": len(layers)},
        message=f"统计 {len(layers)} 个图层（{agg}）",
    )
