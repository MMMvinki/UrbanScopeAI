"""coverage 工具：计算设施服务半径覆盖率。

真计算优先（app.gis.engine：GeoPandas buffer + 相交）。几何数据缺失时**诚实失败**
（返回 success=False + reason），绝不回退演示数值冒充成功。真计算会把结果图层的
服务范围几何写入 DATA_REGISTRY，供前端 /layers/{id}/geojson 拉取渲染。返回值包含：
结果图层 id、覆盖率、覆盖面积、未覆盖面积。
"""

from pydantic import BaseModel

from app.data_registry import DATA_REGISTRY
from app.gis import engine
from app.models.layer import GeometryType
from app.models.tool import ToolResult


class CoverageParams(BaseModel):
    layer_id: str
    distance: int = 800


def run_coverage(params: CoverageParams | dict) -> ToolResult:
    """接受 Pydantic 模型或 dict（LLM 工具调用传入 dict）。"""
    if isinstance(params, dict):
        params = CoverageParams(**params)
    layer = DATA_REGISTRY.get_layer(params.layer_id)
    if layer is None:
        return ToolResult(
            success=False,
            output={"layer_id": params.layer_id, "reason": "missing_param"},
            message=f"未找到图层：{params.layer_id}",
        )
    if layer.geometry == GeometryType.table:
        return ToolResult(success=False, message=f"「{layer.name}」是表格图层，无空间几何，无法计算覆盖率")
    # base 图层是覆盖率的比对底盘；缺失会让 engine.coverage 返回 None 而被误判为
    # 「图层在但退化」。先显式预检，把「base 缺失」这一原因说清楚。
    base_layer = DATA_REGISTRY.get_layer(engine.DEFAULT_COVERAGE_BASE)
    if base_layer is None:
        return ToolResult(
            success=False,
            output={"layer_id": params.layer_id, "reason": "missing_source"},
            message=f"无法计算覆盖率：比对底盘图层「{engine.DEFAULT_COVERAGE_BASE}」不存在或未注册",
        )

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

    return ToolResult(
        success=False,
        output={"layer_id": params.layer_id, "reason": "no_geometry"},
        message=f"无法计算覆盖率：图层「{layer.name}」缺少可用几何数据（或与底盘 {engine.DEFAULT_COVERAGE_BASE} 无有效相交），请核对图层数据或改用其他分析",
    )
