"""CSV/Excel → GeoJSON 转换工具。

裁剪自开源实现，适配 ToolResult / ToolContext（移植来源见 NOTICE.md）：
- 坐标列名自动检测（lat/latitude/y/纬度 与 lng/lon/longitude/x/经度）；
- 自定义分隔符（csv.Sniffer 自动探测）；.xlsx 走 openpyxl 分支；
- 输入/输出路径必须解析在 workspace 内（L2 边界）。
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from app.models.tool import ToolResult
from app.tools.context import get_current_context

LAT_PATTERNS = [
    r"^lat$", r"^latitude$", r"^lat_?d$", r"^y$", r"^纬度$",
    r"^lat_wgs84$", r"^point_y$", r"^lat_dd$",
]
LNG_PATTERNS = [
    r"^lng$", r"^lon$", r"^long$", r"^longitude$", r"^lng_?d$",
    r"^x$", r"^经度$", r"^lon_wgs84$", r"^point_x$", r"^lon_dd$",
]

# 归一化模糊识别的别名（按优先级排）：覆盖 WGS84_Lat / GCJ02_Lng / lon_84 等带前缀变体。
# 注意：不包含 x/y 单字母，避免子串误匹配普通列（x/y 只在精确锚定层生效）。
_LAT_ALIASES = ("latitude", "lat", "纬度")
_LNG_ALIASES = ("longitude", "long", "lon", "lng", "经度")


def _normalize_header(name: str) -> str:
    """列名归一化：小写、去下划线/空格等非字母数字字符（保留中文）。"""
    return re.sub(r"[^a-z0-9一-鿿]", "", name.lower())


def _has_alias(normalized: str, alias: str) -> bool:
    """归一化列名是否在词边界包含别名（防 relationship→lat、along→long 误判）。"""
    idx = normalized.find(alias)
    while idx != -1:
        before_ok = idx == 0 or not normalized[idx - 1].isalpha()
        after_ok = (idx + len(alias)) == len(normalized) or not normalized[idx + len(alias)].isalpha()
        if before_ok and after_ok:
            return True
        idx = normalized.find(alias, idx + 1)
    return False


def _detect_column(headers: list[str], patterns: list[str]) -> str | None:
    # 1) 精确锚定（优先，行为不变）：lat/latitude/y/纬度 与 lng/lon/longitude/x/经度
    for pattern in patterns:
        for h in headers:
            if re.match(pattern, h.strip(), re.IGNORECASE):
                return h
    # 2) 归一化模糊匹配：WGS84_Lng / GCJ02_Lat / LON_WGS84 等带前缀/大小写变体
    is_lng = any(word in p for p in patterns for word in ("lng", "lon", "long"))
    aliases = _LNG_ALIASES if is_lng else _LAT_ALIASES
    normed: dict[str, str] = {}
    for h in headers:
        normed.setdefault(_normalize_header(h), h)  # 归一化后同名保留首列
    for alias in aliases:
        for normalized, original in normed.items():
            if _has_alias(normalized, alias):
                return original
    return None


def _workspace_path():
    ctx = get_current_context()
    if ctx.workspace_path:
        return Path(str(ctx.workspace_path)).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_input_path(raw_path: str) -> Path:
    workspace = _workspace_path()
    path = Path(raw_path).expanduser()
    resolved = path.resolve() if path.is_absolute() else (workspace / path).resolve()
    if workspace != resolved and workspace not in resolved.parents:
        raise ValueError(f"input_path 必须位于 workspace 内: {raw_path}")
    return resolved


def _resolve_output_path(raw_path: str | None, input_path: Path) -> Path:
    workspace = _workspace_path()
    if raw_path:
        path = Path(raw_path).expanduser()
        resolved = path.resolve() if path.is_absolute() else (workspace / path).resolve()
    else:
        resolved = input_path.with_suffix(".geojson").resolve()
    if workspace != resolved and workspace not in resolved.parents:
        raise ValueError(f"output_path 必须位于 workspace 内: {raw_path or resolved}")
    if resolved.suffix.lower() not in {".geojson", ".json"}:
        resolved = resolved.with_suffix(".geojson")
    return resolved


def _parse_property(value: str | None) -> Any:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        return float(text) if "." in text else int(text)
    except ValueError:
        return text


def _build_geojson(rows: list[dict], lat_col: str, lng_col: str) -> tuple[dict, int]:
    features: list[dict] = []
    skipped = 0
    for i, row in enumerate(rows):
        try:
            lat = float(row[lat_col])
            lng = float(row[lng_col])
        except (ValueError, TypeError):
            skipped += 1
            continue
        if lat < -90 or lat > 90 or lng < -180 or lng > 180:
            skipped += 1
            continue
        properties = {
            key: _parse_property(value)
            for key, value in row.items()
            if key not in (lat_col, lng_col)
        }
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": properties,
            "id": i,
        })
    return {"type": "FeatureCollection", "features": features}, skipped


def run_csv_to_geojson(params: dict) -> ToolResult:
    """转换 CSV/Excel（带经纬度列）为 GeoJSON 并落盘 workspace。"""
    raw_input = str(params.get("input_path", "")).strip()
    if not raw_input:
        return ToolResult(success=False, message="缺少必填参数 input_path")
    lat_column = params.get("lat_column") or None
    lng_column = params.get("lng_column") or None
    delimiter = params.get("delimiter") or None

    try:
        path = _resolve_input_path(raw_input)
        if not path.exists():
            return ToolResult(success=False, message=f"CSV 文件不存在: {raw_input}")

        if path.suffix.lower() == ".xlsx":
            rows, headers = _read_xlsx(path)
        else:
            rows, headers = _read_csv(path, delimiter)

        if not headers:
            return ToolResult(success=False, message=f"CSV 无表头: {raw_input}")

        lat_col = lat_column or _detect_column(headers, LAT_PATTERNS)
        lng_col = lng_column or _detect_column(headers, LNG_PATTERNS)
        if not lat_col or not lng_col:
            return ToolResult(
                success=False,
                message=(
                    f"无法自动识别坐标列。表头: {headers}。"
                    "请用 lat_column/lng_column 指定（支持 lat/latitude/y/纬度 与 lng/lon/longitude/x/经度；"
                    "带前缀变体如 WGS84_Lat / GCJ02_Lng 也会自动识别）。"
                ),
            )

        geojson, skipped = _build_geojson(rows, lat_col, lng_col)
        if not geojson["features"]:
            return ToolResult(success=False, message=f"CSV 中无有效坐标行: {raw_input}")

        out_path = _resolve_output_path(params.get("output_path"), path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(geojson, f, ensure_ascii=False)

        return ToolResult(
            success=True,
            output={
                "output_path": str(out_path),
                "path": str(out_path),
                "feature_count": len(geojson["features"]),
                "skipped_rows": skipped,
                "geometry_type": "Point",
                "lat_column": lat_col,
                "lng_column": lng_col,
            },
            message=f"已转换 {len(geojson['features'])} 行到 GeoJSON（跳过 {skipped} 行无效数据）→ {out_path}。",
        )
    except Exception as exc:
        return ToolResult(success=False, message=f"CSV 转换失败: {exc}")


def _detect_encoding(head: bytes) -> str:
    """BOM / 试探 utf-8 → 兜底 gb18030，与数据目录扫描器保持一致（数据目录的 GBK CSV 也能启用）。"""
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        return "utf-16"
    try:
        head.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "gb18030"


def _read_csv(path: Path, delimiter: str | None) -> tuple[list[dict], list[str]]:
    with open(path, "rb") as f:
        head = f.read(8192)
    encoding = _detect_encoding(head)
    with open(path, "r", encoding=encoding, errors="replace") as f:
        if not delimiter:
            sample = f.read(4096)
            f.seek(0)
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except csv.Error:
                delimiter = ","
        reader = csv.DictReader(f, delimiter=delimiter)
        headers = list(reader.fieldnames or [])
        return list(reader), headers


def _read_xlsx(path: Path) -> tuple[list[dict], list[str]]:
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    try:
        headers_row = next(rows)
    except StopIteration:
        return [], []
    headers = [str(h) if h is not None else f"col{i}" for i, h in enumerate(headers_row)]
    data: list[dict] = []
    for row in rows:
        record = {}
        for i, value in enumerate(row):
            key = headers[i] if i < len(headers) else f"col{i}"
            record[key] = value
        data.append(record)
    wb.close()
    return data, headers
