"""Datasource Provider：从预定义 catalog 拉取行政区划等公开 GeoJSON。

裁剪自开源实现，适配 ToolResult / ToolContext（移植来源见 NOTICE.md）：
- 命令：list（列出全部）/ get（查单个源元数据）/ fetch（下载，>40KB 落盘到 workspace/.workdir/data/）。
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from app.models.tool import ToolResult
from app.providers.base import DataProvider
from app.tools.context import ToolContext

logger = logging.getLogger("app.datasource")
_INLINE_GEOJSON_MAX_BYTES = 40 * 1024

CATALOG_PATH = Path(__file__).resolve().parent / "catalog.json"

DATASOURCE_COMMANDS = ("list", "get", "fetch")


def _load_catalog() -> list[dict[str, str]]:
    if not CATALOG_PATH.exists():
        logger.warning("Datasource catalog not found: %s", CATALOG_PATH)
        return []
    with open(CATALOG_PATH, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    if not isinstance(catalog, list):
        raise ValueError("catalog.json 必须是 JSON 数组")
    return catalog


def _build_catalog_description() -> str:
    try:
        catalog = _load_catalog()
    except Exception:
        return "(catalog 不可用)"
    if not catalog:
        return "(空 catalog)"
    lines = []
    for i, item in enumerate(catalog, 1):
        lines.append(f"  {i}. {item['name']} — {item['description']}")
    return "\n".join(lines)


def _find_source(name: str) -> dict[str, str] | None:
    catalog = _load_catalog()
    for item in catalog:
        if item["name"] == name:
            return item
    for item in catalog:
        if name.lower() in item["name"].lower():
            return item
    return None


def _workspace_path(ctx: ToolContext) -> Path:
    if ctx.workspace_path:
        return Path(str(ctx.workspace_path)).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_output_path(ctx: ToolContext, raw_path: str | None, source_name: str) -> Path:
    from app.data_download import resolve_download_output

    return resolve_download_output(raw_path, source_name, _workspace_path(ctx), "data")


def _feature_summary(geojson: dict[str, Any]) -> dict[str, Any]:
    features = geojson.get("features") if isinstance(geojson, dict) else None
    if not isinstance(features, list):
        return {"feature_count": 0, "geometry_types": [], "bbox": None}

    geometry_types: set[str] = set()
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    def visit_coords(coords: Any) -> None:
        nonlocal min_x, min_y, max_x, max_y
        if not coords:
            return
        if isinstance(coords, list) and coords and isinstance(coords[0], (int, float)):
            if len(coords) >= 2:
                x = float(coords[0])
                y = float(coords[1])
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)
            return
        if isinstance(coords, list):
            for item in coords:
                visit_coords(item)

    for feat in features:
        geom = feat.get("geometry") if isinstance(feat, dict) else None
        if not isinstance(geom, dict):
            continue
        gtype = geom.get("type")
        if isinstance(gtype, str):
            geometry_types.add(gtype)
        visit_coords(geom.get("coordinates"))

    bbox = None if min_x == float("inf") else [min_x, min_y, max_x, max_y]
    return {"feature_count": len(features), "geometry_types": sorted(geometry_types), "bbox": bbox}


def _finalize_geojson_result(
    ctx: ToolContext,
    *,
    source: dict[str, str],
    params: dict[str, Any],
    geojson: dict[str, Any],
) -> ToolResult:
    raw_output_path = params.get("output_path") or params.get("save_path")
    return_geojson = bool(params.get("return_geojson", False))
    encoded = json.dumps(geojson, ensure_ascii=False, separators=(",", ":"))
    should_save = bool(raw_output_path) or len(encoded.encode("utf-8")) > _INLINE_GEOJSON_MAX_BYTES

    if not should_save:
        return ToolResult(
            success=True,
            output={"geojson": geojson, **{k: v for k, v in _feature_summary(geojson).items() if k != "geometry_types"}},
            message=f"已获取数据源 '{source.get('name')}'（内联返回）。",
        )

    output_path = _resolve_output_path(ctx, str(raw_output_path) if raw_output_path else None, source.get("name", "datasource"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(encoded, encoding="utf-8")
    from app.data_download import notify_download_saved

    notify_download_saved()
    summary = _feature_summary(geojson)
    output: dict[str, Any] = {
        "name": source.get("name"),
        "url": source.get("url"),
        "path": str(output_path),
        "output_path": str(output_path),
        "feature_count": summary["feature_count"],
        "geometry_types": summary["geometry_types"],
        "bbox": summary["bbox"],
        "bytes": len(encoded.encode("utf-8")),
    }
    if return_geojson:
        output["geojson"] = geojson
    return ToolResult(
        success=True,
        output=output,
        message=f"已获取数据源 '{source.get('name')}'，{summary['feature_count']} 个要素，保存到 {output_path}。",
    )


class DatasourceProvider(DataProvider):
    provider_id = "datasource"
    commands = DATASOURCE_COMMANDS

    def execute(self, command: str, params: dict, ctx: ToolContext) -> ToolResult:
        if command not in DATASOURCE_COMMANDS:
            return ToolResult(
                success=False,
                message=f"未知 datasource 命令：{command}（可用：list/get/fetch）",
            )
        try:
            return self._dispatch(command, params, ctx)
        except ValueError as exc:
            return ToolResult(success=False, message=f"datasource 参数无效: {exc}")
        except Exception as exc:  # 网络/解析错误
            logger.warning("[datasource] %s failed: %s", command, exc)
            return ToolResult(success=False, message=f"datasource 请求失败: {exc}")

    def _dispatch(self, command: str, params: dict, ctx: ToolContext) -> ToolResult:
        if command == "list":
            catalog = _load_catalog()
            return ToolResult(
                success=True,
                output={
                    "sources": [
                        {"name": item["name"], "description": item["description"], "url": item["url"]}
                        for item in catalog
                    ],
                    "count": len(catalog),
                },
                message=f"catalog 共 {len(catalog)} 个数据源。",
            )

        if command == "get":
            name = params.get("name", "")
            if not name:
                raise ValueError("'name' 参数为必填")
            source = _find_source(name)
            if source is None:
                raise ValueError(f"未找到数据源：'{name}'")
            return ToolResult(
                success=True,
                output={"name": source["name"], "description": source["description"], "url": source["url"]},
                message=source["description"],
            )

        if command == "fetch":
            import urllib.request

            name = params.get("name", "")
            if not name:
                raise ValueError("'name' 参数为必填")
            source = _find_source(name)
            if source is None:
                raise ValueError(f"未找到数据源：'{name}'")
            logger.info("Fetching datasource: %s from %s", source["name"], source["url"])
            req = urllib.request.Request(source["url"], headers={"User-Agent": "UrbanScopeAI/0.1"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return _finalize_geojson_result(ctx, source=source, params=params, geojson=data)

        raise ValueError(f"未处理的命令：{command}")
