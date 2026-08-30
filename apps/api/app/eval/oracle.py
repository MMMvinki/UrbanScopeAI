"""oracle：tool 级执行器（真值 oracle）。

把一条 tool 级 case 真实执行：run_tool(tool, params) → 记录
ToolResult + 期间注册的生成图层（真实要素数/bbox/字段），供 rule.py 断言。

设计原则（3.1 / 3.5）：真值来自确定性执行，不从模型期望来。
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path

from app.data_registry import DATA_REGISTRY
from app.tools import run_tool
from app.tools.context import ToolContext
from app.workspace import WORKSPACE_ROOT, ensure_workspace


def _geojson_stats(geojson: dict) -> dict:
    """从 FeatureCollection 算 feature_count / bbox / 字段集。"""
    features = geojson.get("features", []) if isinstance(geojson, dict) else []
    bbox: list[float] | None = None
    fields: set[str] = set()
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for f in features:
        props = f.get("properties") or {}
        fields.update(k for k in props.keys() if k not in ("__id__", "id"))
        geom = f.get("geometry") or {}
        for coord in _walk_coords(geom):
            x, y = coord[0], coord[1]
            if x < minx:
                minx = x
            if y < miny:
                miny = y
            if x > maxx:
                maxx = x
            if y > maxy:
                maxy = y
    if features and minx != float("inf"):
        bbox = [round(minx, 6), round(miny, 6), round(maxx, 6), round(maxy, 6)]
    return {"feature_count": len(features), "bbox": bbox, "fields": sorted(fields)}


def _walk_coords(geom: dict):
    """递归遍历任意 GeoJSON geometry 的坐标点序列。"""
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if gtype in ("Point",):
        yield coords
    elif gtype in ("MultiPoint", "LineString"):
        for c in coords:
            yield c
    elif gtype in ("MultiLineString", "Polygon"):
        for ring in coords:
            for c in ring:
                yield c
    elif gtype in ("MultiPolygon",):
        for poly in coords:
            for ring in poly:
                for c in ring:
                    yield c
    elif gtype in ("GeometryCollection",):
        for g in coords or []:
            yield from _walk_coords(g)


def _write_setup(case) -> None:
    """写 case.setup 声明的夹具文件（自包含评估集）。"""
    for item in case.setup or []:
        path = Path(item["path"])
        if not path.is_absolute():
            path = WORKSPACE_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item["content"], encoding="utf-8")


def execute_tool_case(case, ctx: ToolContext | None = None) -> dict:
    """执行一条 tool 级 case，返回 outcome record（rule.py 的输入）。"""
    ensure_workspace()
    _write_setup(case)

    before = {l.id for l in DATA_REGISTRY.list_layers()}
    record: dict = {
        "id": case.id,
        "tool": case.tool,
        "params": case.params,
        "success": True,
        "message": None,
        "output": {},
        "error": None,
        "trace": None,
        "generated": [],
    }
    try:
        # ctx 为空时注入默认 workspace 上下文：工具按 WORKSPACE_ROOT 解析相对路径
        # （csv_to_geojson 等依赖 get_current_context().workspace_path，行为与真实运行一致）
        eff_ctx = ctx if ctx is not None else ToolContext(workspace_path=WORKSPACE_ROOT)
        result = run_tool(case.tool, case.params, ctx=eff_ctx)
        record["success"] = bool(result.success)
        record["message"] = result.message
        record["output"] = result.output
    except Exception as exc:  # 工具崩溃：判定为 crash，任何 expected_outcome 都不过
        record["success"] = False
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["trace"] = traceback.format_exc()

    for layer in DATA_REGISTRY.list_layers():
        if layer.id in before:
            continue
        try:
            geojson = DATA_REGISTRY.get_geojson(layer.id)
        except Exception:
            geojson = None
        stats = _geojson_stats(geojson) if geojson else {"feature_count": 0, "bbox": None, "fields": []}
        record["generated"].append(
            {
                "layer_id": layer.id,
                "name": layer.name,
                "geometry": layer.geometry,
                **stats,
            }
        )
    return record


def run_tool_plan(params: dict) -> dict:
    """（Phase 1）plan-only 尚未实现；占位避免误用。"""
    raise NotImplementedError("plan-only 评估在 Phase 1 落地")
