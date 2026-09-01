"""coverage 工具：计算设施服务半径覆盖率。

真计算优先（app.gis.engine：GeoPandas buffer + 相交），几何数据缺失时回退
stable_rate 演示数值（防御，不挂）。真计算会把结果图层的服务范围几何写入
DATA_REGISTRY，供前端 /layers/{id}/geojson 拉取渲染。返回值包含：
结果图层 id、覆盖率、覆盖面积、未覆盖面积。
"""

from pydantic import BaseModel

from app.data_registry import DATA_REGISTRY
from app.gis import engine
from app.models.layer import GeometryType
from app.models.tool import ToolResult

# 演示研究范围总面积（km²，仅兜底路径使用）
_STUDY_AREA_KM2 = 15.2


class CoverageParams(BaseModel):
    layer_id: str
    distance: int = 800


def stable_rate(layer_id: str) -> int:
    """兜底：由图层 id 稳定推导一个演示覆盖率/占比（55–94%）。"""
    return 55 + sum(ord(char) for char in layer_id) % 40


def run_coverage(params: CoverageParams | dict) -> ToolResult:
    """接受 Pydantic 模型或 dict（LLM 工具调用传入 dict）。"""
    if isinstance(params, dict):
        params = CoverageParams(**params)
    layer = DATA_REGISTRY.get_layer(params.layer_id)
    if layer is None:
        return ToolResult(success=False, message=f"未找到图层：{params.layer_id}")
    if layer.geometry == GeometryType.table:
        return ToolResult(success=False, message=f"「{layer.name}」是表格图层，无空间几何，无法计算覆盖率")

    real = engine.coverage(params.layer_id, params.distance)
    if real is not None:
        # 真计算的输出几何写入注册表，供地图拉取（图层元数据由 executor 注册）
        DATA_REGISTRY.set_geojson(real["output_layer_id"], real["geojson"])
        return ToolResult(
            success=True,
            output={
                "source_layer_id": params.layer_id,
                "output_layer_id": real["output_layer_id"],
                "coverage_rate": real["coverage_rate"],
                "covered_area_km2": real["covered_area_km2"],
                "uncovered_area_km2": real["uncovered_area_km2"],
                "distance": real["distance"],
            },
            message=f"{layer.name} 在 {params.distance}m 服务半径下覆盖率 {real['coverage_rate']}%",
        )

    rate = stable_rate(params.layer_id)
    covered_km2 = round(_STUDY_AREA_KM2 * rate / 100, 1)
    uncovered_km2 = round(_STUDY_AREA_KM2 - covered_km2, 1)
    return ToolResult(
        success=True,
        output={
            "source_layer_id": params.layer_id,
            "output_layer_id": f"{params.layer_id}_coverage",
            "coverage_rate": rate,
            "covered_area_km2": covered_km2,
            "uncovered_area_km2": uncovered_km2,
            "distance": params.distance,
        },
        message=f"{layer.name} 在 {params.distance}m 服务半径下覆盖率 {rate}%（演示数值）",
    )
