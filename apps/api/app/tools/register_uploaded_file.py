"""register_uploaded_file 工具：把暂存的上传文件按需发布为地图图层。

背景：Agent 对话框上传的文件先只暂存到 workspace/data/uploads/（POST /uploads/stage，
不解析、不注册图层、不上地图）。用户点发送后，Agent 真正需要用到这个文件时，
再通过本工具把它解析并注册为地图图层——复用 upload_service 的解析逻辑，支持
CSV/Excel/GeoJSON/SHP/GPKG（含 shapefile zip）。

返回 output_layer_id，llm_executor 会据此发 GENERATED_LAYER 事件 → 图层即时
上地图并调色（与 register_layer / 分析工具产出图层同一机制）。
"""

from __future__ import annotations

from pydantic import BaseModel

from app.models.tool import ToolResult
from app.workspace import resolve_in_workspace


class RegisterUploadedFileParams(BaseModel):
    file_path: str
    name: str | None = None


def run_register_uploaded_file(params: RegisterUploadedFileParams | dict) -> ToolResult:
    """把工作区内暂存的上传文件解析并注册为地图图层。"""
    # 延迟导入：upload_service 顶层依赖 app.tools（csv_geojson），此处顶层导入会成环；
    # 工具运行时 app.tools 已初始化完成，安全。
    from app.upload_service import UploadError, _ingest_path

    if isinstance(params, dict):
        params = RegisterUploadedFileParams(**params)

    try:
        path = resolve_in_workspace(params.file_path)
    except ValueError as exc:
        return ToolResult(success=False, message=f"路径越界：{exc}")
    if not path.is_file():
        return ToolResult(success=False, message=f"文件不存在：{path}")

    try:
        result = _ingest_path(path, name=params.name)
    except UploadError as exc:
        return ToolResult(success=False, message=str(exc))

    result = dict(result)
    result["output_layer_id"] = result["layer_id"]  # 兼容 executor 的生成图层检测
    result["file_path"] = str(path)
    return ToolResult(
        success=True,
        output=result,
        message=(
            f"已将上传文件「{path.name}」注册为图层 {result['layer_id']}"
            f"（{result['feature_count']} 要素），地图已显示。"
        ),
    )
