"""register_layer 工具：把工作区内已下载/已生成的 GeoJSON 发布为地图图层。

背景：osm_call / datasource_call / execute_code 落盘的 GeoJSON 文件不会自动
出现在地图上（/layers 只读内存 DATA_REGISTRY）。本工具把任意工作区 GeoJSON
注册进 DATA_REGISTRY（元数据 + 真实几何），前端图层面板立即可见；同时物化
一份到 workspace/data/layers/<layer_id>.json 供 execute_code 复用，也让跨重启
的图层索引能恢复它。

参数：
- file_path：要发布的 GeoJSON 文件（相对 workspace 或绝对路径，如
  .workdir/osm/download_bbox_xxx.geojson）
- layer_id：可选，目标图层 id（默认由文件名推导，去除 download_bbox_ 等前缀）
- name：可选，显示名称（默认取 layer_id）
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from app.data_registry import DATA_REGISTRY, save_layer_meta
from app.geojson_utils import clean_layer_id, geometry_type_for, load_feature_collection
from app.models.layer import LayerMetadata, LayerSource
from app.models.tool import ToolResult
from app.workspace import LAYER_DATA_DIR, resolve_in_workspace


class RegisterLayerParams(BaseModel):
    file_path: str
    layer_id: str | None = None
    name: str | None = None


def run_register_layer(params: RegisterLayerParams | dict) -> ToolResult:
    """发布工作区内 GeoJSON 为地图图层（元数据 + 几何注册进 DATA_REGISTRY）。"""
    if isinstance(params, dict):
        params = RegisterLayerParams(**params)

    try:
        path = resolve_in_workspace(params.file_path)
    except ValueError as exc:
        return ToolResult(success=False, message=f"路径越界：{exc}")
    if not path.is_file():
        return ToolResult(success=False, message=f"文件不存在：{path}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return ToolResult(success=False, message=f"解析 GeoJSON 失败：{exc}")

    features, geometry_types, bbox = load_feature_collection(data)
    if not features:
        return ToolResult(success=False, message=f"GeoJSON 没有要素：{path}")

    fc: dict = {"type": "FeatureCollection", "features": features}
    if isinstance(data, dict) and "crs" in data:
        fc["crs"] = data["crs"]

    layer_id = params.layer_id or clean_layer_id(
        path.stem.removeprefix("download_bbox_").removeprefix("download_features_")
    )
    name = params.name or layer_id

    layer = LayerMetadata(
        id=layer_id,
        name=name,
        source=LayerSource.uploaded,
        geometry=geometry_type_for(geometry_types),
        color="#0F172A",
        visible=True,
        feature_count=len(features),
        crs="EPSG:4326",
        description=f"从 {path.name} 发布（{', '.join(sorted(geometry_types)) or '未知'}）",
        generated_by="register_layer 工具",
        data_path=str(path),  # 记录源数据磁盘路径（下载目录等），供「文件路径」视图归类 + 回文件夹定位
        data_type=path.suffix.lstrip("."),
    )
    DATA_REGISTRY.register_generated(layer, geojson=fc)

    # 物化一份到 workspace/data/layers/，供 execute_code 以相对路径复用 + 跨重启索引恢复
    try:
        LAYER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        (LAYER_DATA_DIR / f"{layer_id}.json").write_text(
            json.dumps(fc, ensure_ascii=False), encoding="utf-8"
        )
        # 保存最终注册对象（含自动颜色/继承样式），保证侧车与注册表一致
        final_layer = DATA_REGISTRY.get_layer(layer_id)
        if final_layer is not None:
            save_layer_meta(final_layer)
    except OSError:
        pass  # 物化失败不致命，内存注册已让地图可见

    return ToolResult(
        success=True,
        output={
            "layer_id": layer_id,
            "output_layer_id": layer_id,  # 兼容 executor 的生成图层检测（按 output_layer_id 聚合）
            "name": name,
            "feature_count": len(features),
            "geometry_types": sorted(geometry_types),
            "bbox": bbox,
            "file_path": str(path),
        },
        message=f"已发布图层 {name}（{len(features)} 要素），已同步到地图",
    )
