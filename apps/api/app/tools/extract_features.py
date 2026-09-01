"""extract_features 工具：从任意图层（平台数据/上传/生成）按属性过滤提取子集 → 新图层。

对应「基于平台数据创建新数据」需求：用户说"提取医疗类 POI 做成图层"时，
Agent 调本工具从 深圳POI 里按 行业/type_ 过滤出子集，注册为新图层（source=generated）
并上地图。确定性、全真计算：只做属性过滤，不产生任何编造数值。

filter 语法（多条件 AND）：
  {"name": "医院"}                    → 精确匹配（忽略大小写）
  {"name": {"op": "contains", "value": "医院"}}   → 包含匹配
  {"pop": {"op": "gt", "value": 1000}} → 数值比较
  ops: eq | ne | contains | startswith | gt | gte | lt | lte
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from app.data_registry import DATA_REGISTRY
from app.geojson_utils import clean_layer_id, geometry_type_for, load_feature_collection
from app.models.layer import GeometryType, LayerMetadata, LayerSource
from app.models.tool import ToolResult
from app.workspace import LAYER_DATA_DIR


class ExtractFeaturesParams(BaseModel):
    source_layer_id: str
    filter: dict[str, Any] = Field(default_factory=dict)
    output_name: str | None = None


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _eq(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return float(a) == float(b)
    return str(a).strip().lower() == str(b).strip().lower()


def _match_one(value: Any, op: str, target: Any) -> bool:
    """单条过滤条件；比较类 op 优先数值比较，失败回退字符串比较。"""
    op = (op or "eq").lower()
    if op == "eq":
        return _eq(value, target)
    if op == "ne":
        return not _eq(value, target)
    if op == "contains":
        if value is None or target is None:
            return False
        return str(target).lower() in str(value).lower()
    if op == "startswith":
        if value is None or target is None:
            return False
        return str(value).lower().startswith(str(target).lower())
    # gt / gte / lt / lte
    num_value = _to_float(value)
    num_target = _to_float(target)
    if num_value is not None and num_target is not None:
        if op == "gt":
            return num_value > num_target
        if op == "gte":
            return num_value >= num_target
        if op == "lt":
            return num_value < num_target
        if op == "lte":
            return num_value <= num_target
    return False


def _matches(props: dict[str, Any], conditions: dict[str, Any]) -> bool:
    for field, spec in conditions.items():
        value = props.get(field)
        if isinstance(spec, dict) and ("op" in spec or "value" in spec):
            op = str(spec.get("op", "eq"))
            target = spec.get("value")
        else:
            op, target = "eq", spec
        if not _match_one(value, op, target):
            return False
    return True


def run_extract_features(params: ExtractFeaturesParams | dict) -> ToolResult:
    """按属性条件从源图层提取子集，注册为 generated 新图层并上地图。"""
    if isinstance(params, dict):
        params = ExtractFeaturesParams(**params)

    source_id = params.source_layer_id.strip()
    source = DATA_REGISTRY.get_layer(source_id)
    if source is None:
        return ToolResult(success=False, message=f"未找到源图层：{source_id}")
    if source.geometry == GeometryType.table:
        return ToolResult(success=False, message=f"「{source.name}」是表格图层，无空间几何，无法提取为空间图层")

    conditions = params.filter or {}
    if not conditions:
        return ToolResult(success=False, message="需要 filter 属性条件，如 {\"type_\": \"医院\"}")
    # filter 字段必须是源图层真实字段
    known_fields = {f.name for f in source.fields}
    unknown = [f for f in conditions if f not in known_fields]
    if unknown:
        return ToolResult(
            success=False,
            message=f"源图层「{source.name}」没有字段：{', '.join(unknown)}。可用字段：{', '.join(sorted(known_fields)) or '（无）'}",
        )

    geojson = DATA_REGISTRY.get_geojson(source_id)
    if geojson is None:
        return ToolResult(success=False, message=f"源图层「{source.name}」无几何数据")
    features, geometry_types, _bbox = load_feature_collection(geojson)
    if not features:
        return ToolResult(success=False, message=f"源图层「{source.name}」没有要素")

    selected = [
        feature for feature in features if _matches(feature.get("properties") or {}, conditions)
    ]
    if not selected:
        return ToolResult(
            success=False,
            message=f"没有要素满足条件：{json.dumps(conditions, ensure_ascii=False)}",
        )

    layer_id = clean_layer_id(f"extract_{source_id}")
    name = params.output_name or f"{source.name}·提取子集"
    fc: dict = {"type": "FeatureCollection", "features": selected}
    layer = LayerMetadata(
        id=layer_id,
        name=name,
        source=LayerSource.generated,
        geometry=geometry_type_for(geometry_types),
        color="#0F172A",
        visible=True,
        feature_count=len(selected),
        crs="EPSG:4326",
        description=f"从「{source.name}」提取子集（{json.dumps(conditions, ensure_ascii=False)}）",
        generated_by="extract_features 工具",
        fields=source.fields,
    )
    DATA_REGISTRY.register_generated(layer, geojson=fc)

    # 物化到 workspace/data/layers/，execute_code 可读 + 跨重启索引恢复
    try:
        LAYER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        (LAYER_DATA_DIR / f"{layer_id}.json").write_text(
            json.dumps(fc, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        pass  # 物化失败不致命，内存注册已让地图可见

    return ToolResult(
        success=True,
        output={
            "layer_id": layer_id,
            "output_layer_id": layer_id,  # 兼容 executor 的生成图层检测
            "name": name,
            "source_layer_id": source_id,
            "feature_count": len(selected),
            "filter": conditions,
        },
        message=f"已从「{source.name}」提取 {len(selected)} 个要素，生成图层「{name}」并上地图",
    )
