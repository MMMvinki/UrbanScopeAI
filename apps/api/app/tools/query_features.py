"""query_features 工具：按属性条件查询图层要素（真计算，不编造）。

对应「这个图层里有哪些、叫什么、满足什么条件」的探查类需求：
用户问"深圳医疗 POI 里有几家叫 '人民医院' 的？"时，Agent 调本工具按
filter 条件从真实几何/属性里筛出要素，返回命中数与样本属性（几何不回传，
避免大图层把响应撑爆）。filter 语法与 extract_features 完全一致（同一匹配器）。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from app.data_registry import DATA_REGISTRY
from app.geojson_utils import load_feature_collection
from app.models.tool import ToolResult
from app.tools.extract_features import _matches


class QueryFeaturesParams(BaseModel):
    layer_id: str
    filter: dict[str, Any] = Field(default_factory=dict)
    limit: int = 20


def run_query_features(params: QueryFeaturesParams | dict) -> ToolResult:
    """按属性条件查询图层要素，返回命中数 + 前 limit 条样本属性。"""
    if isinstance(params, dict):
        params = QueryFeaturesParams(**params)

    layer_id = params.layer_id.strip()
    layer = DATA_REGISTRY.get_layer(layer_id)
    if layer is None:
        return ToolResult(success=False, message=f"未找到图层：{layer_id}")

    conditions = params.filter or {}
    known_fields = DATA_REGISTRY.known_field_names(layer_id)
    if conditions:
        unknown = [f for f in conditions if f not in known_fields]
        if unknown:
            return ToolResult(
                success=False,
                message=f"图层「{layer.name}」没有字段：{', '.join(unknown)}。可用字段：{', '.join(sorted(known_fields)) or '（无）'}",
            )

    geojson = DATA_REGISTRY.get_geojson(layer_id)
    if geojson is None:
        return ToolResult(success=False, message=f"图层「{layer.name}」无几何数据")
    features, _geometry_types, _bbox = load_feature_collection(geojson)
    if not features:
        return ToolResult(success=False, message=f"图层「{layer.name}」没有要素")

    limit = max(1, min(params.limit, 50))
    total_matched = 0
    samples: list[dict] = []
    for feature in features:
        if conditions and not _matches(feature.get("properties") or {}, conditions):
            continue
        total_matched += 1
        if len(samples) < limit:
            props = {k: v for k, v in (feature.get("properties") or {}).items() if v is not None}
            # 属性里的长文本截断，保持卡片可读
            for key, value in props.items():
                if isinstance(value, str) and len(value) > 60:
                    props[key] = value[:60] + "…"
            samples.append(props)

    condition_note = f"（条件：{json.dumps(conditions, ensure_ascii=False)}）" if conditions else ""
    sampled = bool(getattr(layer, "sampled", False))
    if sampled:
        # 抽稀样本图层：图层本身只是样本（最多 _PREVIEW_MAX_FEATURES 条），命中数只能叫
        # 「样本命中」，绝不能冒充全量总数；sample_size 报告样本规模，供下游判断口径。
        sample_size = layer.feature_count if layer.feature_count is not None else len(features)
        return ToolResult(
            success=True,
            output={
                "layer_id": layer_id,
                "layer_name": layer.name,
                "sampled": True,
                "sample_matched": total_matched,
                "sample_size": sample_size,
                "sample_count": len(samples),
                "samples": samples,
                "filter": conditions,
            },
            message=f"图层「{layer.name}」为抽稀样本（样本 {sample_size} 条，非全量数据），"
                    f"样本中命中 {total_matched} 条{condition_note}，已返回前 {len(samples)} 条样本",
        )
    return ToolResult(
        success=True,
        output={
            "layer_id": layer_id,
            "layer_name": layer.name,
            "total_matched": total_matched,
            "sample_count": len(samples),
            "samples": samples,
            "filter": conditions,
        },
        message=f"图层「{layer.name}」共 {total_matched} 个要素满足条件{condition_note}，已返回前 {len(samples)} 条样本",
    )
