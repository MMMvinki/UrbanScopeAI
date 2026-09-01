"""本地数据文件夹接入（第一步「只读发现」）。

职责边界：
- 只读扫描本机数据目录，产出「数据目录」（发现了哪些数据 + 可用状态），
  **不注册地图图层、不加载几何、不复制原始数据**。
- 安全：只能访问「允许根目录」之下的路径；白名单本机配置，不硬编码进 Git。
- 持久化：数据源配置 + 白名单落在 workspace（已在 .gitignore），重启后懒加载恢复。

第二步「小数据启用」、第三步「大型数据按范围查询」由后续分支实现，本模块预留
DatasetStatus.enableable / query_only 的状态判定，但不执行启用动作。
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import re
import zipfile
from datetime import date, datetime
from pathlib import Path

from app.geojson_utils import clean_layer_id
from app.models.data_folder import (
    DatasetStatus,
    FieldBrief,
    FolderDataset,
    FolderSource,
    RasterBrief,
)
from app.models.layer import LayerSource
from app.workspace import WORKSPACE_ROOT

_STATUS_CN = {
    DatasetStatus.discovered: "已发现",
    DatasetStatus.enableable: "可直接启用",
    DatasetStatus.query_only: "仅可查询",
    DatasetStatus.unavailable: "暂不可用",
}

# ── 持久化位置（workspace 内，已在 .gitignore，不进 Git）────────────────
_ALLOWLIST_FILE = WORKSPACE_ROOT / "data_sources_allowlist.json"
_SOURCES_DIR = WORKSPACE_ROOT / "data_sources"
_DOWNLOAD_DIR_FILE = WORKSPACE_ROOT / "download_dir.json"

# ── 阈值（可环境变量覆盖，供测试用更小值）───────────────────────────────
# 行数统计上限：超过此字节数不流式统计行数（避免读 299MB POI）。
_ROWCOUNT_MAX_BYTES = int(os.environ.get("URBANSCOPE_DATAFOLDER_ROWCOUNT_MAX_BYTES", 100 * 1024 * 1024))
# 「可直接启用」大小上限：超过则标「仅可查询」（第二步启用时也复用此门槛）。
_ENABLE_MAX_BYTES = int(os.environ.get("URBANSCOPE_DATAFOLDER_ENABLE_MAX_BYTES", 120 * 1024 * 1024))
# gdb 内部图层按要素数判定「可直接启用」上限（gdb 是单文件，无法按图层分文件大小）
_ENABLE_MAX_FEATURES = int(os.environ.get("URBANSCOPE_DATAFOLDER_ENABLE_MAX_FEATURES", 100000))
# 矢量文件（shp/gpkg/geojson）按要素数的「可直接启用」上限：文件小但要素极多（如 25 万面
# 建筑、39 万线道路只有几十 MB）时，全量上地图会物化上 GB 级 GeoJSON 拖垮浏览器。
# 超过 → 标「仅可查询」，点「预览启用」流式抽稀。此值放得比 gdb 略宽：点状/轻量数据
# （如 10 万点）直接启用可接受，gdb 因共享一个大文件、按图层字节数无法估算而更严格。
_ENABLE_MAX_FEATURES_VECTOR = int(os.environ.get("URBANSCOPE_DATAFOLDER_ENABLE_MAX_FEATURES_VECTOR", 150000))
# 表格类（无坐标 CSV/XLSX）按行数的「可直接启用」上限：压缩体积小但行数极多的表格
# 全量启用会物化出巨大 GeoJSON（双份副本峰值），超此行数 → 标「仅可查询」，抽稀到 _PREVIEW_MAX_FEATURES。
_ENABLE_MAX_FEATURES_TABLE = int(os.environ.get("URBANSCOPE_DATAFOLDER_ENABLE_MAX_FEATURES_TABLE", 150000))
# 大型数据「抽稀预览启用」的目标要素数（query_only 数据启用时抽样到此规模，不全量加载）
_PREVIEW_MAX_FEATURES = int(os.environ.get("URBANSCOPE_DATAFOLDER_PREVIEW_MAX_FEATURES", 50000))
# 文件夹级「全部启用」：硬上限 20 个 + 512MB 累计软预算（软启发式，非内存保证）
_ENABLE_ALL_HARD_LIMIT = 20
_ENABLE_ALL_DEFAULT_LIMIT = 20
_ENABLE_ALL_MAX_BYTES = 512 * 1024 * 1024

# ── 识别范围 ────────────────────────────────────────────────────────────
_SHAPEFILE_PARTS = {".shp", ".dbf", ".shx", ".prj", ".cpg", ".sbn", ".sbx", ".qix", ".fix", ".shp.xml"}
_CSV_EXT = {".csv", ".tsv", ".txt"}
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}

# 内存缓存（懒加载）：{source_id: FolderSource}
_sources_cache: dict[str, FolderSource] | None = None


# ═════════════════════════════ 白名单 ══════════════════════════════════

def _load_allowlist() -> list[str]:
    if not _ALLOWLIST_FILE.exists():
        return []
    try:
        raw = json.loads(_ALLOWLIST_FILE.read_text(encoding="utf-8"))
        return [str(r) for r in raw.get("roots", []) if str(r).strip()]
    except (json.JSONDecodeError, OSError):
        return []


def _save_allowlist(roots: list[str]) -> None:
    _ALLOWLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ALLOWLIST_FILE.write_text(
        json.dumps({"roots": roots}, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _reject_drive_root(path: Path) -> bool:
    """拒绝驱动器根 / 文件系统根（如 C:\\ 或 /），避免把整块盘放进白名单。"""
    return path.parent == path or path.anchor == str(path)


def add_allowlist_root(raw: str) -> list[str]:
    """加入白名单；拒绝不存在/非目录/驱动器根。返回更新后的白名单。"""
    path = Path(str(raw)).expanduser()
    if not path.exists():
        raise DataFolderError(f"目录不存在：{path}")
    if not path.is_dir():
        raise DataFolderError(f"路径不是目录：{path}")
    resolved = str(path.resolve())
    if _reject_drive_root(path.resolve()):
        raise DataFolderError("不允许把整块磁盘加入白名单，请指定具体的数据目录。")
    roots = _load_allowlist()
    if resolved not in roots:
        roots.append(resolved)
        _save_allowlist(roots)
    return roots


def list_allowlist() -> list[str]:
    return _load_allowlist()


def _is_allowed(path: Path) -> bool:
    """路径必须等于或在某个白名单根目录之下（commonpath 归一化，防路径穿越）。"""
    resolved = str(path.resolve())
    for root in _load_allowlist():
        try:
            common = os.path.commonpath([resolved, os.path.normpath(root)])
        except ValueError:
            continue  # 跨盘符无法比较，跳过
        if os.path.normpath(common) == os.path.normpath(root):
            return True
    return False


class DataFolderError(ValueError):
    """数据目录操作校验失败，向用户返回友好中文 message。"""


# ═════════════════════════════ 数据源持久化 ═════════════════════════════

def _source_path(source_id: str) -> Path:
    return _SOURCES_DIR / f"{source_id}.json"


def _load_sources() -> dict[str, FolderSource]:
    global _sources_cache
    if _sources_cache is not None:
        return _sources_cache
    result: dict[str, FolderSource] = {}
    if _SOURCES_DIR.is_dir():
        for p in sorted(_SOURCES_DIR.glob("*.json")):
            try:
                src = FolderSource(**json.loads(p.read_text(encoding="utf-8")))
                result[src.source_id] = src
            except (json.JSONDecodeError, OSError, ValueError):
                continue  # 单个配置损坏不致命
    _sources_cache = result
    return result


def _save_sources() -> None:
    _SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    for src in _load_sources().values():
        _source_path(src.source_id).write_text(
            src.model_dump_json(indent=2), encoding="utf-8"
        )


def _unique_source_id(alias: str, root_path: str) -> str:
    base = clean_layer_id(alias)
    existing = _load_sources()
    if base in existing and existing[base].root_path != str(Path(root_path).resolve()):
        suffix = hashlib.md5(str(root_path).encode()).hexdigest()[:6]
        base = f"{base}_{suffix}"
    return base


def _unique_dataset_id(rel_path: str, taken: set[str]) -> str:
    stem = clean_layer_id(Path(rel_path).stem)
    if stem not in taken:
        return stem
    suffix = hashlib.md5(rel_path.encode()).hexdigest()[:6]
    return f"{stem}_{suffix}"


def _other_source_ids(source_id: str) -> set[str]:
    """其它已链接数据源的 dataset_id 集合，供扫描时跨源去重（两目录同名文件启用不再互相覆盖）。"""
    return {
        d.dataset_id
        for sid, s in _load_sources().items()
        if sid != source_id
        for d in s.datasets
    }


# ═════════════════════════════ 扫描器 ═════════════════════════════════

def _detect_encoding(head: bytes) -> str:
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if head.startswith(b"\xff\xfe") or head.startswith(b"\xfe\xff"):
        return "utf-16"
    try:
        head.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "gb18030"


def _read_csv_head(path: Path) -> tuple[list[str], list[list[str]], str]:
    """只读前 8KB 探测编码/分隔符/表头 + 前几行，绝不全量加载。"""
    head = path.open("rb").read(8192)
    encoding = _detect_encoding(head)
    text = head.decode(encoding, errors="replace")
    try:
        delim = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
    except csv.Error:
        delim = ","
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    headers = [h.strip() for h in (next(reader, None) or [])]
    rows = [r for _, r in zip(range(5), reader)]
    return headers, rows, encoding


def _count_csv_rows(path: Path, encoding: str, size: int) -> int | None:
    """流式统计行数（不存内存）；超过阈值返回 None，避免读大文件。"""
    if size > _ROWCOUNT_MAX_BYTES:
        return None
    try:
        with path.open("r", encoding=encoding, errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return None


def _count_csv_logical_rows(path: Path, encoding: str, size: int) -> int | None:
    """无坐标 Table CSV 的逻辑数据行数（不含表头，完整 csv.reader 流，正确处理引号内换行）。

    与 _count_csv_rows（物理行、含表头）不同：Table 的 feature_count / 阈值判断 /
    original_feature_count 必须用「逻辑数据记录数」，否则带引号换行字段会被多算、表头造成
    off-by-one。同样受字节阈值约束，超限返回 None（不廉价读大文件）。
    """
    if size > _ROWCOUNT_MAX_BYTES:
        return None
    try:
        head = path.open("rb").read(8192)
        text = head.decode(encoding, errors="replace")
        try:
            delim = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
        except csv.Error:
            delim = ","
        with path.open("r", encoding=encoding, errors="replace", newline="") as f:
            reader = csv.reader(f, delimiter=delim)
            next(reader, None)  # 跳过表头
            return sum(1 for _ in reader)
    except OSError:
        return None


def _geom_kind(fiona_geom: str | None) -> str | None:
    if not fiona_geom:
        return None
    g = fiona_geom.lower()
    if "point" in g:
        return "point"
    if "line" in g:
        return "line"
    if "polygon" in g:
        return "polygon"
    return None


def _crs_label(crs_wkt: str | None) -> tuple[str | None, str | None]:
    """从 .prj WKT 解析坐标系统：返回 (label, warning)。"""
    if not crs_wkt:
        return None, None
    try:
        from pyproj import CRS

        crs = CRS.from_wkt(crs_wkt)
    except Exception:  # noqa: BLE001
        return None, None
    epsg = None
    try:
        epsg = crs.to_epsg()
    except Exception:  # noqa: BLE001
        epsg = None
    if epsg == 4326:
        return "EPSG:4326", None
    if epsg is not None:
        return f"EPSG:{epsg}", "坐标系非 WGS84，启用时需转换"
    name = crs.name or ""
    if name:
        return name, "坐标系统名称识别到但无标准 EPSG，启用时需转换"
    return None, "坐标系统无法识别"


def _fiona_fields(schema_props: dict) -> list[FieldBrief]:
    """fiona schema properties → 字段摘要。"""
    fields: list[FieldBrief] = []
    for name, ftype in (schema_props or {}).items():
        fields.append(FieldBrief(name=name, type=_py_to_brief(str(ftype))))
    return fields


def _py_to_brief(ftype: str) -> str:
    t = (ftype or "").lower()
    if t in ("int", "integer", "float", "real", "number", "decimal"):
        return "number"
    if t in ("bool", "boolean"):
        return "boolean"
    if t in ("date", "datetime", "time"):
        return "date"
    return "string"


def _csv_field_types(headers: list[str], rows: list[list[str]]) -> list[FieldBrief]:
    fields: list[FieldBrief] = []
    for i, h in enumerate(headers):
        ftype = "string"
        for row in rows:
            if i < len(row):
                v = (row[i] or "").strip()
                if v:
                    ftype = "number" if _looks_number(v) else "string"
                    break
        fields.append(FieldBrief(name=h, type=ftype))
    return fields


def _looks_number(v: str) -> bool:
    try:
        float(v.replace(",", ""))
        return True
    except ValueError:
        return False


def _scan_csv(path: Path, rel_path: str, size: int) -> FolderDataset:
    headers, rows, encoding = _read_csv_head(path)
    if not headers:
        return _unavailable(path, rel_path, "csv", size, "CSV 无表头")
    from app.tools.csv_geojson import LNG_PATTERNS, LAT_PATTERNS, _detect_column

    lng = _detect_column(headers, LNG_PATTERNS)
    lat = _detect_column(headers, LAT_PATTERNS)
    fields = _csv_field_types(headers, rows)
    if not lng or not lat:
        # 无坐标 CSV → 表格图层（属性表可预览、Agent 可查询，不上地图渲染）。
        # 行政区级 OD/来源表也走此分支，仅在 warning 标注其身份（不再标 unavailable）。
        # 用「逻辑数据行数」（不含表头、正确处理引号内换行）做 feature_count 与阈值判断。
        feature_count = _count_csv_logical_rows(path, encoding, size)
        # 除字节阈值外，行数超阈值也标「仅可查询」：小体积、百万行表格全量启用会物化巨大 GeoJSON
        table_status = DatasetStatus.query_only if (
            size > _ENABLE_MAX_BYTES or (feature_count or 0) > _ENABLE_MAX_FEATURES_TABLE
        ) else DatasetStatus.enableable
        return FolderDataset(
            dataset_id="", name=path.stem, rel_path=rel_path, format="csv", size=size,
            fields=fields, crs=None, coord_columns=None, geometry="table",
            feature_count=feature_count,
            status=table_status,
            warnings=["无坐标列：注册为表格图层（属性表/Agent 可查询，不上地图渲染；"
                      "如为行政区级 OD/来源表，需关联行政区边界后上图）"],
        )

    feature_count = _count_csv_rows(path, encoding, size)
    warnings: list[str] = []
    if feature_count is None:
        warnings.append("文件较大，未统计行数")
    warnings.append(f"坐标列 {lng}/{lat}，坐标系未标注，启用前需确认")

    status = DatasetStatus.query_only if size > _ENABLE_MAX_BYTES else DatasetStatus.enableable
    return FolderDataset(
        dataset_id="",  # 由上层填充
        name=path.stem,
        rel_path=rel_path,
        format="csv",
        size=size,
        fields=fields,
        crs=None,
        coord_columns=[lng, lat],
        geometry="point",
        feature_count=feature_count,
        status=status,
        warnings=warnings,
    )


def _xlsx_row_count(ws, rows) -> tuple[int | None, bool]:
    """XLSX 数据行数（不含表头）：read-only 模式优先用 max_row；不可靠时流式数到阈值+1 即停。

    返回 (feature_count, too_many_rows)。feature_count 为 None 表示「超过阈值 / 无法廉价统计」，
    too_many_rows 单独给出，供状态判定（小压缩包、百万行数据不得误判全量）。
    """
    try:
        mr = ws.max_row
        if isinstance(mr, int) and mr > 1:
            count = mr - 1  # 减表头行
            return count, count > _ENABLE_MAX_FEATURES_TABLE
    except Exception:  # noqa: BLE001
        pass
    try:
        n = 0
        for _ in rows:  # rows 迭代器已消费表头，这里数剩余数据行
            n += 1
            if n > _ENABLE_MAX_FEATURES_TABLE:
                return None, True  # 超阈值即停，避免百万行全读
        return n, False
    except Exception:  # noqa: BLE001
        return None, False


def _scan_xlsx(path: Path, rel_path: str, size: int) -> FolderDataset:
    try:
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        try:
            header_row = next(rows)
        except StopIteration:
            header_row = None
        feature_count, too_many_rows = _xlsx_row_count(ws, rows)
        wb.close()
    except Exception as exc:  # noqa: BLE001
        return _unavailable(path, rel_path, "xlsx", size, f"读取 XLSX 失败：{exc}")
    headers = [str(h) for h in (header_row or []) if h is not None]
    if not headers:
        return _unavailable(path, rel_path, "xlsx", size, "XLSX 无表头")
    fields = [FieldBrief(name=h, type="string") for h in headers]
    from app.tools.csv_geojson import LNG_PATTERNS, LAT_PATTERNS, _detect_column

    lng = _detect_column(headers, LNG_PATTERNS)
    lat = _detect_column(headers, LAT_PATTERNS)
    # 除字节阈值外，行数超阈值也标「仅可查询」（小压缩包、百万行数据误走全量的防护）
    big = size > _ENABLE_MAX_BYTES or too_many_rows
    if not lng or not lat:
        table_status = DatasetStatus.query_only if big else DatasetStatus.enableable
        return FolderDataset(
            dataset_id="", name=path.stem, rel_path=rel_path, format="xlsx", size=size,
            fields=fields, crs=None, coord_columns=None, geometry="table",
            feature_count=feature_count, status=table_status,
            warnings=["无坐标列：注册为表格图层（属性表/Agent 可查询，不上地图渲染；"
                      "如为行政区级 OD/来源表，需关联行政区边界后上图）"],
        )
    status = DatasetStatus.query_only if big else DatasetStatus.enableable
    return FolderDataset(
        dataset_id="", name=path.stem, rel_path=rel_path, format="xlsx", size=size,
        fields=fields, coord_columns=[lng, lat], geometry="point",
        feature_count=feature_count, status=status,
        warnings=["坐标列坐标系未标注，启用前需确认"],
    )


def _scan_shapefile(main_shp: Path, rel_path: str, size: int) -> FolderDataset:
    """用 fiona 只读 schema/要素数/CRS，不加载全部几何。"""
    import fiona

    try:
        with fiona.open(main_shp) as src:
            geom = _geom_kind(src.schema.get("geometry"))
            fields = _fiona_fields(src.schema.get("properties"))
            crs, crs_warning = _crs_label(src.crs_wkt)
            count = len(src)  # 元数据迭代，不物化几何
    except Exception as exc:  # noqa: BLE001
        return _unavailable(main_shp, rel_path, "shp", size, f"读取 Shapefile 失败：{exc}")

    warnings: list[str] = []
    if crs_warning:
        warnings.append(crs_warning)
    if crs is None:
        return _unavailable(main_shp, rel_path, "shp", size, "坐标系统无法识别", fields=fields)

    status = DatasetStatus.query_only if size > _ENABLE_MAX_BYTES else DatasetStatus.enableable
    if geom is None:
        return _unavailable(main_shp, rel_path, "shp", size, "几何类型无法识别", fields=fields)
    if count is not None and count > _ENABLE_MAX_FEATURES_VECTOR:
        status = DatasetStatus.query_only
        warnings.append(f"{count} 个要素，数据较大，启用时自动抽稀预览")
    return FolderDataset(
        dataset_id="", name=main_shp.stem, rel_path=rel_path, format="shp", size=size,
        fields=fields, crs=crs, geometry=geom, feature_count=count,
        status=status, warnings=warnings,
    )


def _scan_geojson(path: Path, rel_path: str, size: int) -> FolderDataset:
    import fiona

    try:
        with fiona.open(path) as src:
            geom = _geom_kind(src.schema.get("geometry"))
            fields = _fiona_fields(src.schema.get("properties"))
            crs, crs_warning = _crs_label(src.crs_wkt)
            count = len(src)
    except Exception as exc:  # noqa: BLE001
        return _unavailable(path, rel_path, "geojson", size, f"解析 GeoJSON 失败：{exc}")
    warnings: list[str] = [w for w in [crs_warning] if w]
    status = DatasetStatus.query_only if size > _ENABLE_MAX_BYTES else DatasetStatus.enableable
    if geom is None:
        return _unavailable(path, rel_path, "geojson", size, "非地理 JSON 或几何类型无法识别", fields=fields)
    if count is not None and count > _ENABLE_MAX_FEATURES_VECTOR:
        status = DatasetStatus.query_only
        warnings.append(f"{count} 个要素，数据较大，启用时自动抽稀预览")
    return FolderDataset(
        dataset_id="", name=path.stem, rel_path=rel_path, format="geojson", size=size,
        fields=fields, crs=crs, geometry=geom, feature_count=count,
        status=status, warnings=warnings,
    )


def _scan_zip_shapefile(path: Path, rel_path: str, size: int) -> FolderDataset:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            shp_names = [n for n in names if n.lower().endswith(".shp")]
            if not shp_names:
                return _unavailable(path, rel_path, "shp_zip", size, "压缩包内没有 .shp 文件")
            crs, crs_warning = None, None
            for n in names:
                if n.lower().endswith(".prj"):
                    try:
                        crs, crs_warning = _crs_label(zf.read(n).decode("utf-8", errors="replace"))
                    except Exception:  # noqa: BLE001
                        pass
                    break
    except Exception as exc:  # noqa: BLE001
        return _unavailable(path, rel_path, "shp_zip", size, f"读取压缩包失败：{exc}")
    warnings: list[str] = [w for w in [crs_warning] if w]
    status = DatasetStatus.query_only if size > _ENABLE_MAX_BYTES else DatasetStatus.enableable
    return FolderDataset(
        dataset_id="", name=path.stem, rel_path=rel_path, format="shp_zip", size=size,
        fields=[], crs=crs, geometry=None, feature_count=None,
        status=status, warnings=warnings,
    )


def _scan_gpkg(path: Path, rel_path: str, size: int) -> FolderDataset:
    import fiona

    try:
        layers = fiona.listlayers(path)
        if not layers:
            return _unavailable(path, rel_path, "gpkg", size, "GeoPackage 内没有图层")
        with fiona.open(path, layer=layers[0]) as src:
            geom = _geom_kind(src.schema.get("geometry"))
            fields = _fiona_fields(src.schema.get("properties"))
            crs, crs_warning = _crs_label(src.crs_wkt)
            count = len(src)
    except Exception as exc:  # noqa: BLE001
        return _unavailable(path, rel_path, "gpkg", size, f"读取 GeoPackage 失败：{exc}")
    warnings: list[str] = [w for w in [crs_warning] if w]
    status = DatasetStatus.query_only if size > _ENABLE_MAX_BYTES else DatasetStatus.enableable
    if count is not None and count > _ENABLE_MAX_FEATURES_VECTOR:
        status = DatasetStatus.query_only
        warnings.append(f"{count} 个要素，数据较大，启用时自动抽稀预览")
    return FolderDataset(
        dataset_id="", name=path.stem, rel_path=rel_path, format="gpkg", size=size,
        fields=fields, crs=crs, geometry=geom, feature_count=count,
        status=status, warnings=warnings,
    )


def _scan_gdb(path: Path, rel_path: str, size: int) -> list[FolderDataset]:
    """把 File Geodatabase 当作「图层文件夹」展开：用 fiona 的 OpenFileGDB 驱动
    只读列出内部要素类，每个要素类作为一个数据集（零新依赖）。"""
    import fiona

    try:
        layers = fiona.listlayers(path)
    except Exception as exc:  # noqa: BLE001
        return [_unavailable(path, rel_path, "gdb", size, f"读取 GDB 失败：{exc}")]
    if not layers:
        return [_unavailable(path, rel_path, "gdb", size, "GDB 内没有图层")]

    datasets: list[FolderDataset] = []
    for layer_name in layers:
        layer_rel = f"{rel_path}/{layer_name}"
        try:
            with fiona.open(path, layer=layer_name) as src:
                geom = _geom_kind(src.schema.get("geometry"))
                fields = _fiona_fields(src.schema.get("properties"))
                crs, crs_warning = _crs_label(src.crs_wkt)
                count = len(src)
        except Exception as exc:  # noqa: BLE001
            datasets.append(FolderDataset(
                dataset_id="", name=layer_name, rel_path=layer_rel, format="gdb", size=size,
                fields=[], crs=None, geometry=None, feature_count=None,
                status=DatasetStatus.unavailable, reason=f"读取图层失败：{exc}",
                warnings=[], gdb_layer=layer_name,
            ))
            continue

        warnings = [w for w in [crs_warning] if w]
        if geom is None:
            reason = "几何类型无法识别"
            status = DatasetStatus.unavailable
        elif crs is None:
            reason = "坐标系统无法识别"
            status = DatasetStatus.unavailable
        elif count is not None and count > _ENABLE_MAX_FEATURES:
            reason = None
            status = DatasetStatus.query_only
            warnings.append(f"{count} 个要素，数据较大，仅可查询")
        else:
            reason = None
            status = DatasetStatus.enableable

        datasets.append(FolderDataset(
            dataset_id="", name=layer_name, rel_path=layer_rel, format="gdb", size=size,
            fields=fields, crs=crs, geometry=geom, feature_count=count,
            status=status, reason=reason, warnings=warnings, gdb_layer=layer_name,
        ))
    return datasets


_THUMB_MAX_SIZE = (256, 256)  # 缩略图输出 (高, 宽) 上限
_THUMB_MAX_BYTES = 200 * 1024  # 编码后 PNG data-url 字节上限
_CHUNKED_MIN_PIXELS = 20_000_000  # 源像素超过此值走分块读（约 277MB 底图）


def _scan_tif(path: Path, rel_path: str, size: int) -> FolderDataset:
    """扫描 GeoTIFF：探测元数据 + 缩略图（委托 _probe_tif）。"""
    return _probe_tif(path, rel_path, size)


def _probe_tif(path: Path, rel_path: str, size: int) -> FolderDataset:
    """探测 GeoTIFF 元数据 + 缩略图（O(1) 元数据 + 受控 out_shape 缩略图，不加载全部像素）。

    status 仍 unavailable（不上图渲染），附 raster 元数据供前端详情区展示。
    依赖 rasterio >=1.3.11（numpy 2 兼容）；import 失败应明确失败，不静默降级。
    """
    try:
        import rasterio
    except Exception as exc:  # noqa: BLE001 —— ImportError / DLL 加载失败 / ABI 不兼容都归为 dataset unavailable
        # 二进制/ABI 错误（如「DLL load failed while importing _rgba」）不能把整个目录扫描打 500，
        # 转为单条数据集 unavailable + 清晰升级提示。
        msg = f"栅格依赖 rasterio 加载失败：{exc}"
        if any(key in str(exc).lower() for key in ("dll", "abi", "numpy", "version", "binary")):
            msg += "。请升级 rasterio 与 numpy 至兼容版本（pip install -U rasterio numpy），并重启后端。"
        return _unavailable(path, rel_path, "tif", size, msg)

    try:
        with rasterio.open(path) as src:
            try:
                crs = src.crs.to_string() if src.crs else None
            except Exception:  # noqa: BLE001
                crs = None
            brief = RasterBrief(
                width=src.width,
                height=src.height,
                bands=src.count,
                dtype=str(src.dtypes[0]) if src.dtypes else None,
                crs=crs,
                res_x=float(src.res[0]),
                res_y=float(src.res[1]),
                bounds=[src.bounds.left, src.bounds.bottom, src.bounds.right, src.bounds.top],
                nodata=src.nodata,
                color_interpretation=[ci.name for ci in src.colorinterp] if getattr(src, "colorinterp", None) else None,
            )
            try:
                brief.preview_image = _tif_thumbnail(src)
            except Exception as exc:  # noqa: BLE001
                brief.preview_note = f"缩略图生成失败：{exc}"
    except Exception as exc:  # noqa: BLE001
        return _unavailable(path, rel_path, "tif", size, f"读取栅格失败：{exc}")

    return FolderDataset(
        dataset_id="", name=path.stem, rel_path=rel_path, format="tif", size=size,
        fields=[], crs=brief.crs, geometry=None, feature_count=None,
        status=DatasetStatus.unavailable,
        reason="栅格影像：可预览元数据与缩略图，暂不启用为计算图层（瓦片渲染后续支持）",
        warnings=[], raster=brief,
    )


def _tif_thumbnail(src) -> str | None:
    """从 rasterio 打开的源生成缩略图 PNG data-url（受控 out_shape，禁止全量 read）。

    只取用于显示的 1 或 3 个波段；小图直接 read(out_shape)，大图走 _tif_thumbnail_chunked。
    过滤 nodata/NaN 后做 2/98 百分位拉伸。
    """
    import numpy as np
    from PIL import Image

    count = src.count
    display_bands = min(3, count) if count >= 3 else 1
    thumb_h, thumb_w = _THUMB_MAX_SIZE
    ratio = min(thumb_h / src.height, thumb_w / src.width)
    out_h = max(1, int(src.height * ratio))
    out_w = max(1, int(src.width * ratio))

    if src.width * src.height > _CHUNKED_MIN_PIXELS:
        img = _tif_thumbnail_chunked(src, out_h, out_w, display_bands)
    elif display_bands == 1:
        arr = src.read(1, out_shape=(out_h, out_w), masked=True)
        data = np.ma.masked_invalid(arr.astype(np.float64))
        p2, p98 = np.percentile(data.compressed(), (2, 98)) if data.count() else (0, 1)
        norm = np.clip((data - p2) / max(p98 - p2, 1e-9), 0, 1)
        gray = (norm.filled(0) * 255).astype(np.uint8)
        img = Image.fromarray(gray, mode="L").convert("RGB")
    else:
        arr = src.read(indexes=list(range(1, display_bands + 1)), out_shape=(display_bands, out_h, out_w), masked=True)
        data = np.ma.masked_invalid(arr.astype(np.float64))
        rgb = []
        for b in range(display_bands):
            band = data[b]
            p2, p98 = np.percentile(band.compressed(), (2, 98)) if band.count() else (0, 1)
            norm = np.clip((band - p2) / max(p98 - p2, 1e-9), 0, 1)
            rgb.append((norm.filled(0) * 255).astype(np.uint8))
        img = Image.merge("RGB", [Image.fromarray(ch) for ch in rgb])

    if img is None:
        return None
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    if buf.tell() > _THUMB_MAX_BYTES:
        return None
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _tif_thumbnail_chunked(src, out_h: int, out_w: int, display_bands: int):
    """大图分块缩略图：每个 window 同时指定受控 out_shape，只读显示波段。

    按源行分块，每块 window + out_shape=(display_bands, chunk_out_h, out_w)，
    块间纵向拼接。绝不先读 bands×源宽×dtype 再 Python 等距取列（那样仍依赖源宽/波段/dtype，
    并非真正与文件大小解耦）。
    """
    import numpy as np
    from PIL import Image
    from rasterio.windows import Window

    # 分块数不超过源高度：超宽、低高度 TIFF（如 20000×3）若固定 8 块会产生零高度 window
    n_chunks = min(8, src.height)
    chunks: list = []
    for c in range(n_chunks):
        row_start = c * src.height // n_chunks
        row_end = (c + 1) * src.height // n_chunks
        win_h = row_end - row_start
        chunk_out_h = max(1, round(out_h * win_h / src.height))
        window = Window(0, row_start, src.width, win_h)
        if display_bands == 1:
            arr = src.read(1, window=window, out_shape=(chunk_out_h, out_w), masked=True)
            chunks.append(np.ma.masked_invalid(arr.astype(np.float64)))
        else:
            arr = src.read(indexes=list(range(1, display_bands + 1)), window=window, out_shape=(display_bands, chunk_out_h, out_w), masked=True)
            chunks.append(np.ma.masked_invalid(arr.astype(np.float64)))

    if display_bands == 1:
        data = np.ma.concatenate(chunks, axis=0)
        p2, p98 = np.percentile(data.compressed(), (2, 98)) if data.count() else (0, 1)
        norm = np.clip((data - p2) / max(p98 - p2, 1e-9), 0, 1)
        gray = (norm.filled(0) * 255).astype(np.uint8)
        return Image.fromarray(gray, mode="L").convert("RGB")

    data = np.ma.concatenate(chunks, axis=1)  # (bands, out_h, out_w)
    rgb = []
    for b in range(display_bands):
        band = data[b]
        p2, p98 = np.percentile(band.compressed(), (2, 98)) if band.count() else (0, 1)
        norm = np.clip((band - p2) / max(p98 - p2, 1e-9), 0, 1)
        rgb.append((norm.filled(0) * 255).astype(np.uint8))
    return Image.merge("RGB", [Image.fromarray(ch) for ch in rgb])


def _unavailable(path: Path, rel_path: str, fmt: str, size: int, reason: str, fields=None) -> FolderDataset:
    return FolderDataset(
        dataset_id="", name=path.stem, rel_path=rel_path, format=fmt, size=size,
        fields=fields or [], crs=None, geometry=None, feature_count=None,
        status=DatasetStatus.unavailable, reason=reason, warnings=[],
    )


def _fmt_of(suffix: str) -> str:
    return suffix.lstrip(".").lower() if suffix else "other"


def _iter_walk(root: Path):
    """递归遍历，跳过无关目录；产出 (path, rel_path, is_dir)。"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            p = Path(dirpath) / name
            rel = p.relative_to(root).as_posix()
            yield p, rel


def scan_folder(root: Path, taken: set[str] | None = None) -> list[FolderDataset]:
    """只读扫描目录 → 数据目录（数据集列表）。不注册图层、不加载几何。

    taken：已占用的 dataset_id（如其它已链接数据源的 id）。传入后本目录与其它源重名的
    数据集（如两个目录都有 buildings.shp）会被加后缀去重，避免启用为图层时互相覆盖。
    """
    root = Path(root).resolve()
    shp_groups: dict[str, dict[str, Path]] = {}  # (dir, stem) -> {ext: path}
    standalone: list[tuple[Path, str]] = []  # 非 shp 配套的普通数据文件
    gdb_dirs: list[tuple[Path, str]] = []

    for p, rel in _iter_walk(root):
        suffix = p.suffix.lower()
        if suffix in _SHAPEFILE_PARTS:
            key = f"{p.parent.resolve()}::{p.stem}"
            shp_groups.setdefault(key, {})[suffix] = p
        elif suffix in _CSV_EXT or suffix in {".xlsx", ".geojson", ".json", ".zip", ".gpkg", ".tif", ".tiff"}:
            standalone.append((p, rel))

    # gdb 目录（单独检测，不深入其内部文件）
    for dirpath, dirnames, _filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for d in list(dirnames):
            if d.lower().endswith(".gdb"):
                gdb_dirs.append((Path(dirpath) / d, (Path(dirpath) / d).relative_to(root).as_posix()))
                dirnames.remove(d)

    datasets: list[FolderDataset] = []
    taken: set[str] = set(taken or [])

    def finalize(ds: FolderDataset) -> None:
        ds.dataset_id = _unique_dataset_id(ds.rel_path, taken)
        taken.add(ds.dataset_id)
        datasets.append(ds)

    # Shapefile 配套合并
    for key, parts in shp_groups.items():
        shp = parts.get(".shp")
        if shp is None:
            continue  # 只有 .dbf 等零散文件，无主 .shp，跳过
        size = sum(pt.stat().st_size for pt in parts.values())
        rel = shp.relative_to(root).as_posix()
        finalize(_scan_shapefile(shp, rel, size))

    # 普通数据文件
    for p, rel in standalone:
        suffix = p.suffix.lower()
        size = p.stat().st_size
        if suffix in _CSV_EXT:
            finalize(_scan_csv(p, rel, size))
        elif suffix == ".xlsx":
            finalize(_scan_xlsx(p, rel, size))
        elif suffix in {".geojson", ".json"}:
            finalize(_scan_geojson(p, rel, size))
        elif suffix == ".zip":
            finalize(_scan_zip_shapefile(p, rel, size))
        elif suffix == ".gpkg":
            finalize(_scan_gpkg(p, rel, size))
        elif suffix in {".tif", ".tiff"}:
            finalize(_scan_tif(p, rel, size))

    # GDB 目录（展开为内部图层，每个图层一个数据集）
    for p, rel in gdb_dirs:
        size = _dir_size(p)
        for ds in _scan_gdb(p, rel, size):
            finalize(ds)

    # 稳定排序：先按状态权重，再按路径
    order = {DatasetStatus.enableable: 0, DatasetStatus.query_only: 1, DatasetStatus.discovered: 2, DatasetStatus.unavailable: 3}
    datasets.sort(key=lambda d: (order.get(d.status, 9), d.rel_path.lower()))
    return datasets


def _dir_size(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


# ═════════════════════════════ 对外操作 ═════════════════════════════

def link_source(root_path: str, alias: str | None = None) -> FolderSource:
    """链接一个本地目录：只读扫描 → 持久化。

    安全模型：本机单用户部署，用户手动链接目录即视为明确授权，自动记入白名单
    （白名单作为「已允许目录」记账，未来 Agent 扫描工具据此设边界，用户当前无感）。
    唯一硬性拒绝：驱动器根（防止误扫整块盘）。
    """
    path = Path(str(root_path)).expanduser()
    if not path.exists():
        raise DataFolderError(f"目录不存在：{path}")
    if not path.is_dir():
        raise DataFolderError(f"路径不是目录：{path}")
    resolved = path.resolve()
    if _reject_drive_root(resolved):
        raise DataFolderError("不允许链接整个磁盘，请选择具体的数据目录。")
    if not _is_allowed(resolved):
        add_allowlist_root(str(resolved))
    alias = (alias or resolved.name).strip() or resolved.name
    source_id = _unique_source_id(alias, str(resolved))
    datasets = _reconcile_enabled(scan_folder(resolved, taken=_other_source_ids(source_id)), str(resolved))
    now = datetime.now().isoformat(timespec="seconds")
    sources = _load_sources()
    if source_id in sources:
        src = sources[source_id]
        src.alias = alias
        src.last_scanned_at = now
        src.datasets = datasets
    else:
        src = FolderSource(
            source_id=source_id, alias=alias, root_path=str(resolved),
            created_at=now, last_scanned_at=now, datasets=datasets,
        )
        sources[source_id] = src
    _save_sources()
    return src


def rescan_source(source_id: str) -> FolderSource:
    src = get_source(source_id)
    root = Path(src.root_path)
    if not root.is_dir():
        raise DataFolderError(f"原目录已不存在：{src.root_path}，请取消链接后重新链接。")
    src.datasets = _reconcile_enabled(scan_folder(root, taken=_other_source_ids(source_id)), src.root_path)
    src.last_scanned_at = datetime.now().isoformat(timespec="seconds")
    _save_sources()
    return src


def unlink_source(source_id: str) -> None:
    """取消链接（仅删配置与快照；已启用图层不受影响——第二步才涉及）。"""
    if get_source(source_id) is None:
        return
    _load_sources().pop(source_id, None)
    _save_sources()
    p = _source_path(source_id)
    if p.exists():
        p.unlink(missing_ok=True)


def get_source(source_id: str) -> FolderSource | None:
    return _load_sources().get(source_id)


def list_sources() -> list[FolderSource]:
    sources = _load_sources()
    for src in sources.values():
        _reconcile_enabled(src.datasets, src.root_path)
    return list(sources.values())


# ═════════════════════════════ 下载目录（第三阶段） ═════════════════════════

def set_download_folder(raw: str) -> dict:
    """设置「下载目录」：联网取数（OSM/高德/datasource）默认落盘到该本地文件夹。

    目录自动加入白名单 + 链接为数据源（alias「下载数据」），这样下载的数据与
    「链接本地数据目录」统一在左侧数据面板管理。返回 {path, source_id}。
    """
    path = Path(str(raw)).expanduser()
    if not path.exists():
        raise DataFolderError(f"目录不存在：{path}")
    if not path.is_dir():
        raise DataFolderError(f"路径不是目录：{path}")
    resolved = path.resolve()
    if _reject_drive_root(resolved):
        raise DataFolderError("不允许链接整个磁盘，请选择具体的数据目录。")
    if not _is_allowed(resolved):
        add_allowlist_root(str(resolved))
    _DOWNLOAD_DIR_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DOWNLOAD_DIR_FILE.write_text(json.dumps({"path": str(resolved)}, ensure_ascii=False), encoding="utf-8")
    src = link_source(str(resolved), alias="下载数据")
    return {"path": str(resolved), "source_id": src.source_id}


def get_download_folder() -> str | None:
    if not _DOWNLOAD_DIR_FILE.exists():
        return None
    try:
        return json.loads(_DOWNLOAD_DIR_FILE.read_text(encoding="utf-8")).get("path")
    except (json.JSONDecodeError, OSError):
        return None


def rescan_download_folder() -> None:
    """下载落盘后重扫下载目录对应的数据源，让新下载的数据即时出现在面板。

    若该数据源被用户取消链接，则自动重新链接（下载目录配置仍有效，下载会继续落到那）。
    """
    dl = get_download_folder()
    if not dl:
        return
    target = Path(dl).resolve()
    for src in list_sources():
        if Path(src.root_path).resolve() == target:
            try:
                rescan_source(src.source_id)
            except DataFolderError:
                pass
            return
    try:
        link_source(str(target), alias="下载数据")
    except DataFolderError:
        pass


def _sample_csv_to_fc(path: Path, target: int) -> tuple[dict, str | None]:
    """流式抽样读大型 CSV → FeatureCollection，不全量载入内存。返回 (fc, 坐标列描述)。"""
    import csv as csv_mod

    from app.tools.csv_geojson import LAT_PATTERNS, LNG_PATTERNS, _detect_column, _parse_property

    head = path.open("rb").read(8192)
    encoding = _detect_encoding(head)
    text = head.decode(encoding, errors="replace")
    try:
        delim = csv_mod.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
    except csv_mod.Error:
        delim = ","
    headers = [h.strip() for h in (next(csv_mod.reader(io.StringIO(text), delimiter=delim), None) or [])]
    lng = _detect_column(headers, LNG_PATTERNS)
    lat = _detect_column(headers, LAT_PATTERNS)
    if not lng or not lat:
        return {"type": "FeatureCollection", "features": []}, None
    lat_idx = headers.index(lat)
    lng_idx = headers.index(lng)

    # 流式统计行数（不存内存）
    total = 0
    with path.open("r", encoding=encoding, errors="replace") as f:
        next(f, None)
        for _ in f:
            total += 1
    step = max(1, total // target)

    features: list[dict] = []
    with path.open("r", encoding=encoding, errors="replace") as f:
        next(f, None)
        for i, line in enumerate(f):
            if i % step != 0:
                continue
            row = next(csv_mod.reader([line], delimiter=delim), None)
            if not row:
                continue
            try:
                latv = float(row[lat_idx])
                lngv = float(row[lng_idx])
            except (ValueError, IndexError):
                continue
            if not (-90 <= latv <= 90 and -180 <= lngv <= 180):
                continue
            props = {
                headers[j]: _parse_property(row[j])
                for j in range(min(len(row), len(headers)))
                if j not in (lat_idx, lng_idx)
            }
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lngv, latv]},
                "properties": props,
                "id": i,
            })
            if len(features) >= target:
                break
    return {"type": "FeatureCollection", "features": features}, f"{lng}/{lat}"


def _sample_xlsx_to_fc(path: Path, target: int) -> tuple[dict, str | None]:
    """流式抽样读大型 Excel → FeatureCollection（openpyxl read_only，不全量载入内存）。"""
    import random

    import openpyxl

    from app.tools.csv_geojson import LAT_PATTERNS, LNG_PATTERNS, _detect_column, _parse_property

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except StopIteration:
        wb.close()
        return {"type": "FeatureCollection", "features": []}, None
    headers = [str(h) if h is not None else f"col{i}" for i, h in enumerate(header_row)]
    lng = _detect_column(headers, LNG_PATTERNS)
    lat = _detect_column(headers, LAT_PATTERNS)
    if not lng or not lat:
        wb.close()
        return {"type": "FeatureCollection", "features": []}, None
    lat_idx, lng_idx = headers.index(lat), headers.index(lng)

    features: list[dict] = []
    i = 0
    for row in rows:
        i += 1
        try:
            latv = float(row[lat_idx])
            lngv = float(row[lng_idx])
        except (ValueError, TypeError, IndexError):
            continue
        if not (-90 <= latv <= 90 and -180 <= lngv <= 180):
            continue
        props = {
            headers[j]: _parse_property(row[j])
            for j in range(min(len(row), len(headers)))
            if j not in (lat_idx, lng_idx)
        }
        feat = {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lngv, latv]},
            "properties": props,
            "id": i,
        }
        if len(features) < target:
            features.append(feat)
        else:
            # 蓄水池抽样：固定内存 target，均匀保留（xlsx 无法廉价数总行数，故不走等距步长）
            k = random.randint(0, i - 1)
            if k < target:
                features[k] = feat
    wb.close()
    return {"type": "FeatureCollection", "features": features}, f"{lng}/{lat}"


def _safe_table_prop(value):
    """XLSX 单元格值保值：原生数字/布尔保留，datetime/date 转 ISO 字符串，其余转 str。

    CSV 走原样保留（_table_fc_from_rows 里 str 直存），不走本函数，避免邮编/行政代码/
    前导零 ID/超大编号在 _parse_property 下失真（数值比较器可按需临时转数值）。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _table_fc_from_rows(rows: list[dict], headers: list[str]) -> dict:
    """把表格行构建成无几何 FeatureCollection（CSV 值原样保留，不统一转数值）。"""
    features: list[dict] = []
    for i, row in enumerate(rows):
        props = {h: row.get(h) for h in headers}
        features.append({"type": "Feature", "geometry": None, "properties": props, "id": i})
    return {"type": "FeatureCollection", "features": features}


def _table_csv_to_fc_full(path: Path) -> dict:
    """流式读完整无坐标 CSV → 表格 FeatureCollection（完整文件句柄 DictReader，值原样）。

    单次遍历直接构造 features，不先 `rows = list(reader)` 再二次复制成 FC —— 消除大表
    全量启用时的双份全量内存峰值。
    """
    head = path.open("rb").read(8192)
    encoding = _detect_encoding(head)
    with path.open("r", encoding=encoding, errors="replace", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delim = ","
        reader = csv.DictReader(f, delimiter=delim)
        headers = list(reader.fieldnames or [])
        features: list[dict] = []
        for i, row in enumerate(reader):
            props = {h: row.get(h) for h in headers}
            features.append({"type": "Feature", "geometry": None, "properties": props, "id": i})
    return {"type": "FeatureCollection", "features": features}


def _sample_table_csv_to_fc(path: Path, target: int) -> dict:
    """流式抽样读大型无坐标 CSV → 表格 FeatureCollection。

    必须对完整文件句柄用 csv.DictReader（不能逐物理行 reader([line])），否则带引号换行
    字段会被拆坏。等距步长抽样到 target，值原样保留。
    """
    head = path.open("rb").read(8192)
    encoding = _detect_encoding(head)
    with path.open("r", encoding=encoding, errors="replace", newline="") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            delim = ","
        reader = csv.DictReader(f, delimiter=delim)
        headers = list(reader.fieldnames or [])
        total = sum(1 for _ in reader)
    step = max(1, total // target)
    features: list[dict] = []
    with path.open("r", encoding=encoding, errors="replace", newline="") as f:
        reader = csv.DictReader(f, delimiter=delim)
        for i, row in enumerate(reader):
            if i % step != 0:
                continue
            props = {h: row.get(h) for h in headers}
            features.append({"type": "Feature", "geometry": None, "properties": props, "id": i})
            if len(features) >= target:
                break
    return {"type": "FeatureCollection", "features": features}


def _table_xlsx_to_fc_full(path: Path) -> dict:
    """读完整无坐标 XLSX → 表格 FeatureCollection（原生数字/布尔保留，日期转 ISO）。"""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except StopIteration:
        wb.close()
        return {"type": "FeatureCollection", "features": []}
    headers = [str(h) if h is not None else f"col{i}" for i, h in enumerate(header_row)]
    features: list[dict] = []
    for i, row in enumerate(rows):
        props = {
            (headers[j] if j < len(headers) else f"col{j}"): _safe_table_prop(value)
            for j, value in enumerate(row)
        }
        features.append({"type": "Feature", "geometry": None, "properties": props, "id": i})
    wb.close()
    return {"type": "FeatureCollection", "features": features}


def _sample_table_xlsx_to_fc(path: Path, target: int) -> dict:
    """流式抽样读大型无坐标 XLSX → 表格 FeatureCollection（read_only + 固定内存蓄水池）。"""
    import random

    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except StopIteration:
        wb.close()
        return {"type": "FeatureCollection", "features": []}
    headers = [str(h) if h is not None else f"col{i}" for i, h in enumerate(header_row)]
    features: list[dict] = []
    i = 0
    for row in rows:
        i += 1
        props = {
            (headers[j] if j < len(headers) else f"col{j}"): _safe_table_prop(value)
            for j, value in enumerate(row)
        }
        feat = {"type": "Feature", "geometry": None, "properties": props, "id": i}
        if len(features) < target:
            features.append(feat)
        else:
            k = random.randint(0, i - 1)
            if k < target:
                features[k] = feat
    wb.close()
    return {"type": "FeatureCollection", "features": features}


def _ingest_table(root: Path, dataset, layer_id: str, alias: str, desc: str, preview: bool) -> dict:
    """表格图层（无坐标 CSV/XLSX）启用：属性表可预览、Agent 可查询，不上地图渲染。

    query_only（大表）抽样到 _PREVIEW_MAX_FEATURES；enableable 流式全量构建 features
    （不先读完整 rows 再复制一份 FC 的双份峰值）。复用 register_feature_collection，
    其「not geometry_types → Table」检测自动落 table + lazy + fields + crs N/A。
    """
    from app.upload_service import register_feature_collection

    file_path = root / Path(dataset.rel_path)
    if not file_path.is_file():
        raise DataFolderError(f"原始文件已不存在：{dataset.rel_path}，请重新扫描。")

    target = _PREVIEW_MAX_FEATURES
    if dataset.format == "csv":
        fc = _sample_table_csv_to_fc(file_path, target) if preview else _table_csv_to_fc_full(file_path)
    else:  # xlsx
        fc = _sample_table_xlsx_to_fc(file_path, target) if preview else _table_xlsx_to_fc_full(file_path)
    if not fc["features"]:
        raise DataFolderError("表格读取失败：没有有效行")

    name = f"{dataset.name}（抽稀预览）" if preview else dataset.name
    return register_feature_collection(
        fc,
        layer_id=layer_id,
        name=name,
        source=LayerSource.folder,
        description=desc + ("（抽稀预览）" if preview else ""),
        generated_by="本地数据目录",
        # 表格无坐标不上地图渲染，保持 visible 默认 True（点开即预览属性表）
        data_path=str(file_path),
        rel_path=dataset.rel_path,
        data_type=dataset.format,
        # 抽稀预览（query_only 大表）标记样本口径：Agent 查询只报样本命中数，不得当作全量总数
        sampled=preview,
        original_feature_count=dataset.feature_count,
    )


def _sample_vector_to_fc(path: Path, layer: str | None, target: int) -> dict:
    """fiona 切片抽样读矢量（shp/gpkg/geojson/gdb 图层）→ FeatureCollection。

    用 fiona 的 `src[start:stop:step]` 切片只取抽到的 ~target 个要素 —— 既不全量
    物化几何进内存（gpd.read_file 的旧实现违背「非完整加载」承诺），也比 Python 层
    全量迭代快一个量级（85 万线 6s vs 155s）。按源 CRS 需要时把抽样到的要素重投影到
    EPSG:4326。
    """
    import fiona
    from pyproj import CRS, Transformer
    from shapely.geometry import mapping, shape
    from shapely.ops import transform as shp_transform

    with fiona.open(path, "r", layer=layer) as src:
        try:
            total = len(src)
        except Exception:  # noqa: BLE001
            total = None
        transformer = None
        if src.crs is not None:
            try:
                c = CRS.from_user_input(src.crs)
                if c.to_epsg() != 4326:
                    transformer = Transformer.from_crs(c, "EPSG:4326", always_xy=True)
            except Exception:  # noqa: BLE001
                transformer = None
        if total and total > target:
            step = max(1, total // target)
            raw = src[0:total:step][:target]
            ids = range(0, total, step)
        else:
            raw = list(src)
            ids = range(len(raw))
        features: list[dict] = []
        for i, feat in zip(ids, raw):
            g = feat.get("geometry")
            if g is None:
                continue
            if transformer is not None:
                try:
                    g = mapping(shp_transform(transformer.transform, shape(g)))
                except Exception:  # noqa: BLE001
                    # 重投影失败 → 按原坐标读（仍需归一为纯 dict，fiona Geometry 不可直接 json 序列化）
                    try:
                        g = mapping(shape(g))
                    except Exception:  # noqa: BLE001
                        continue
            else:
                # fiona 的 geometry 是 fiona.model.Geometry（dict 子类），json.dumps 认不出，
                # 必须经 shapely 往返归一成纯 dict。
                try:
                    g = mapping(shape(g))
                except Exception:  # noqa: BLE001
                    continue
            # fiona 的 properties 是 dict 子类且日期值可能是 datetime —— 归一为纯 dict 纯标量
            props = feat.get("properties") or {}
            properties = {
                k: (v if isinstance(v, (str, int, float, bool, type(None))) else str(v))
                for k, v in props.items()
            }
            features.append({
                "type": "Feature",
                "geometry": g,
                "properties": properties,
                "id": i,
            })
            if len(features) >= target:
                break
    return {"type": "FeatureCollection", "features": features}


def _ingest_gdb_layer(
    gdb_file: Path, layer_name: str, layer_id: str, name: str, alias: str, rel_path: str
) -> dict:
    """读 gdb 内部图层 → GeoJSON → 注册为项目图层（复用 register_feature_collection）。"""
    import geopandas as gpd

    from app.upload_service import register_feature_collection

    gdf = gpd.read_file(gdb_file, layer=layer_name)
    if gdf.crs is not None:
        try:
            if gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs("EPSG:4326")
        except Exception:  # noqa: BLE001
            pass  # 无法转换时按原坐标读，交给下游容错
    fc = json.loads(gdf.to_json())
    return register_feature_collection(
        fc,
        layer_id=layer_id,
        name=name,
        source=LayerSource.folder,
        description=f"本地数据目录「{alias}」：{rel_path}",
        generated_by="本地数据目录",
        data_path=str(gdb_file),
        rel_path=rel_path,
        data_type="gdb",
    )


def _ingest_preview(root: Path, dataset, layer_id: str, alias: str, desc: str) -> dict:
    """大型数据（query_only）抽稀预览启用：抽样到 _PREVIEW_MAX_FEATURES，不全量加载。

    表格类（csv/xlsx）流式抽样坐标行；矢量类（shp/gpkg/geojson/gdb 图层）用 fiona
    迭代抽样。绝不把全量几何读进内存（gpd.read_file 的旧实现违背「非完整加载」承诺）。
    """
    from app.upload_service import register_feature_collection

    target = _PREVIEW_MAX_FEATURES
    if dataset.format in ("csv", "xlsx"):
        file_path = root / Path(dataset.rel_path)
        if dataset.format == "csv":
            fc, _coord = _sample_csv_to_fc(file_path, target)
        else:
            fc, _coord = _sample_xlsx_to_fc(file_path, target)
        if not fc["features"]:
            raise DataFolderError("抽稀读取失败：没有有效坐标行")
    else:
        if dataset.gdb_layer is not None:
            # gdb 内部图层：rel_path = <gdb_rel>/<图层名>，实际文件是 <gdb_rel> 目录
            file_path = root / Path(dataset.rel_path).parent
            layer = dataset.gdb_layer
        else:
            file_path = root / Path(dataset.rel_path)
            layer = None
        fc = _sample_vector_to_fc(file_path, layer, target)
        if not fc["features"]:
            raise DataFolderError("抽稀读取失败：没有有效要素")

    return register_feature_collection(
        fc,
        layer_id=layer_id,
        name=f"{dataset.name}（抽稀预览）",
        source=LayerSource.folder,
        description=f"{desc}（抽稀预览，上限 {target} 要素）",
        generated_by="本地数据目录",
        visible=False,  # 大图层启用只注册、默认不预览：勾选眼睛才载入（第2项）
        lazy=True,  # 抽稀预览不进 bulk 渲染链：前端 refreshLayersIncremental/ensureVisibleGeojson 不得误预取
        sampled=True,  # 样本口径：Agent 查询只报样本命中数，不得当全量总数
        original_feature_count=dataset.feature_count,
        data_path=str(file_path),
        rel_path=dataset.rel_path,
        data_type=dataset.format,
    )


def _layer_matches_dataset(layer, dataset: FolderDataset, root: Path | None) -> bool:
    """图层是否确属该目录数据集：source=folder 且 data_path/rel_path 与数据集对应。

    用于 enable 冲突时判断「已有图层是不是自己的」以及 disable 删除前校验目标归属，
    防止目录启用覆盖 / 停用误删 uploaded/generated 等其他来源的图层。
    root 为 None 时（个别无来源上下文）仅校验 source + rel_path，不做磁盘路径比对。
    """
    if layer.source != LayerSource.folder:
        return False
    if layer.rel_path != dataset.rel_path:
        return False
    if root is None:
        return True
    if dataset.gdb_layer is not None:
        # gdb 内部图层：data_path 记录的是 gdb 目录本身（rel_path 的父路径）
        expected = str((root / Path(dataset.rel_path).parent).resolve())
    else:
        expected = str((root / Path(dataset.rel_path)).resolve())
    return bool(layer.data_path) and Path(layer.data_path).resolve() == Path(expected)


def _resolve_enable_layer_id(source_id: str, dataset: FolderDataset, root: Path) -> str:
    """计算启用后的最终图层 id：期望 id 已被非本 dataset 图层占用时生成稳定后缀。

    原则（P0-4，只在本 folder enable 边界处理，不改全局 register_generated 覆盖语义）：
    1. 已记录的 layer_id 且图层仍存在并属于本 dataset → 复用（重复启用不产生新 id）；
    2. 期望 id（dataset_id）空闲或已是本 dataset 图层 → 原样使用；
    3. 期望 id 被 uploaded/generated 等其他来源占用 → 生成含 source_id 的稳定短哈希后缀，
       写回 dataset.layer_id，不覆盖别人的图层。
    """
    from app.data_registry import DATA_REGISTRY

    if dataset.layer_id:
        existing = DATA_REGISTRY.get_layer(dataset.layer_id)
        if existing is not None and _layer_matches_dataset(existing, dataset, root):
            return dataset.layer_id
    base = clean_layer_id(dataset.dataset_id)
    existing = DATA_REGISTRY.get_layer(base)
    if existing is None or _layer_matches_dataset(existing, dataset, root):
        return base
    suffix = hashlib.md5(f"{source_id}:{dataset.rel_path}".encode("utf-8")).hexdigest()[:6]
    candidate = f"{base}_{suffix}"
    n = 2
    while True:
        ex = DATA_REGISTRY.get_layer(candidate)
        if ex is None or _layer_matches_dataset(ex, dataset, root):
            return candidate
        candidate = f"{base}_{suffix}{n}"
        n += 1


def enable_dataset(source_id: str, dataset_id: str) -> dict:
    """把「可直接启用」的数据集启用为项目图层（第二步「小数据启用」）。

    普通文件复用 upload_service._ingest_path；gdb 内部图层用 geopandas 读该图层。
    source=folder 标记来源。仅可查询（query_only）/暂不可用（unavailable）一律拒绝，
    绝不全量转 GeoJSON。最终 layer id 若与既有非本数据集图层冲突会加稳定后缀，不覆盖他人图层。
    """
    from app.data_registry import DATA_REGISTRY
    from app.upload_service import UploadError, _ingest_path

    src = get_source(source_id)
    if src is None:
        raise DataFolderError("数据源不存在")
    dataset = next((d for d in src.datasets if d.dataset_id == dataset_id), None)
    if dataset is None:
        raise DataFolderError("数据集不存在")
    if dataset.status == DatasetStatus.unavailable:
        raise DataFolderError(f"该数据暂不可用：{dataset.reason or '格式/坐标/依赖条件不满足'}。")
    preview = dataset.status == DatasetStatus.query_only

    root = Path(src.root_path)
    layer_id = _resolve_enable_layer_id(source_id, dataset, root)
    desc = f"本地数据目录「{src.alias}」：{dataset.rel_path}"
    try:
        if dataset.geometry == "table":
            result = _ingest_table(root, dataset, layer_id, src.alias, desc, preview)
        elif preview:
            result = _ingest_preview(root, dataset, layer_id, src.alias, desc)
        elif dataset.gdb_layer is not None:
            gdb_file = root / Path(dataset.rel_path).parent  # rel_path = <gdb_rel>/<图层名>
            result = _ingest_gdb_layer(
                gdb_file, dataset.gdb_layer, layer_id, dataset.name, src.alias, dataset.rel_path
            )
        else:
            file_path = root / Path(dataset.rel_path)
            if not file_path.is_file():
                raise DataFolderError(f"原始文件已不存在：{dataset.rel_path}，请重新扫描。")
            result = _ingest_path(
                file_path,
                name=dataset.name,
                layer_id=layer_id,
                source=LayerSource.folder,
                description=desc,
                generated_by="本地数据目录",
                data_path=str(file_path),
                rel_path=dataset.rel_path,
                data_type=dataset.format,
            )
    except UploadError as exc:
        raise DataFolderError(f"启用失败：{exc}") from exc
    except DataFolderError:
        raise
    except Exception as exc:  # noqa: BLE001 —— 损坏文件 / 编码 / 读取异常一律转友好中文，不裸 500
        raise DataFolderError(f"启用失败（{type(exc).__name__}）：{exc}") from exc

    dataset.enabled = True
    dataset.layer_id = layer_id
    _save_sources()
    return dict(result, layer_id=layer_id)


def mark_layer_removed(layer_id: str) -> None:
    """三条删除路径（HTTP DELETE / Agent remove_layer / 目录 disable）共用：
    按 layer_id 反查所有数据源的 dataset，置 enabled=False、layer_id=None 并落盘。

    目录的 enabled 是运行态派生状态，持久快照只作缓存；图层被任何入口删除后，
    目录状态必须同步，否则重扫/项目切换后会出现「目录显示已启用但图层不存在」。
    """
    changed = False
    for src in _load_sources().values():
        for d in src.datasets:
            if d.layer_id == layer_id:
                d.enabled = False
                d.layer_id = None
                changed = True
    if changed:
        _save_sources()


def _reconcile_enabled(
    datasets: list[FolderDataset], root_path: str | None = None
) -> list[FolderDataset]:
    """扫描后按当前 DATA_REGISTRY 校准 enabled/layer_id（运行态注册表为准）。

    重扫/重复链接/项目切换后，全局目录快照可能与当前项目注册表不一致：
    预期 layer_id 在注册表中且为 folder 来源 → enabled=True；否则清掉（外部删除/
    项目切换后图层已不在当前项目）。

    目录启用遇到 ID 冲突会生成稳定后缀图层 id（如 dup_986d29），此时不能只按规范化
    dataset_id 反查（会被同名 uploaded 图层挡掉）—— 额外按 rel_path/来源匹配本 dataset
    的 folder 图层，恢复正确的 enabled/layer_id。
    """
    from app.data_registry import DATA_REGISTRY

    root = Path(root_path) if root_path else None
    for d in datasets:
        lid = d.layer_id or clean_layer_id(d.dataset_id)
        layer = DATA_REGISTRY.get_layer(lid) if lid else None
        match = None
        if layer is not None and _layer_matches_dataset(layer, d, root):
            match = layer
        elif root is not None:
            # 期望 id 被非本 dataset 图层占用 / layer_id 未记录 → 按 rel_path+来源匹配 folder 图层
            for lyr in DATA_REGISTRY.list_layers():
                if _layer_matches_dataset(lyr, d, root):
                    match = lyr
                    break
        if match is not None:
            d.enabled = True
            d.layer_id = match.id
        else:
            d.enabled = False
            d.layer_id = None
    return datasets


def disable_dataset(source_id: str, dataset_id: str) -> dict:
    """停用一个已启用数据集：移除其图层 + 目录状态置为未启用（幂等）。

    未启用/注册表已无图层 → 幂等成功（不是错误）；已启用 → remove_layer 移除图层。
    删除前验证目标是 source=folder 且路径/rel_path 与该数据集对应；若该 layer_id 已被
    其他来源（uploaded/generated 等）占用，拒绝删除并返回明确错误，不得误删别人的图层。
    """
    from app.data_registry import DATA_REGISTRY

    src = get_source(source_id)
    if src is None:
        raise DataFolderError("数据源不存在")
    dataset = next((d for d in src.datasets if d.dataset_id == dataset_id), None)
    if dataset is None:
        raise DataFolderError("数据集不存在")

    layer_id = dataset.layer_id
    layer = DATA_REGISTRY.get_layer(layer_id) if layer_id else None
    layer_exists = layer is not None
    removed_layer_id: str | None = None
    if layer_exists:
        root = Path(src.root_path)
        if not _layer_matches_dataset(layer, dataset, root):
            raise DataFolderError(
                f"图层「{layer_id}」来源为 {layer.source.value}，与本数据集（{dataset.rel_path}）不匹配，"
                "已拒绝停用，避免误删其他图层。如需删除该图层请在图层面板操作。"
            )
        DATA_REGISTRY.remove_layer(layer_id)
        removed_layer_id = layer_id  # 实际删除的图层 id（可能是同名冲突后的后缀 id）

    already_disabled = not dataset.enabled and not layer_exists
    dataset.enabled = False
    dataset.layer_id = None
    _save_sources()

    if already_disabled:
        return {"ok": True, "already_disabled": True, "message": "已是停用状态", "removed_layer_id": None}
    return {
        "ok": True,
        "already_disabled": False,
        "message": f"已停用「{dataset.name}」",
        "removed_layer_id": removed_layer_id,
    }


def _estimate_table_load_bytes(d: FolderDataset) -> int:
    """Table 图层驻留内存估算（保守，杜绝「少量超宽行的大文件被估成几 KB」）。

    至少取 max(行数估算, 文件字节估算)：每行字节按 max(250, 文件字节/行数) 放大，
    超宽行（一行几十 MB）不会被行数估算掩盖；最终再与原始文件字节取 max，
    100MB Table 绝不估成几 KB。

    query_only 抽样只保留 min(原始行数, _PREVIEW_MAX_FEATURES) 行；原始行数未知时用
    _PREVIEW_MAX_FEATURES 作为保守下限（可能全量保留到上限行）。
    """
    if d.status == DatasetStatus.query_only:
        rows = min(d.feature_count or _PREVIEW_MAX_FEATURES, _PREVIEW_MAX_FEATURES)
    else:
        rows = d.feature_count or _PREVIEW_MAX_FEATURES  # 全量启用，未知行数用保守下限
    per_row = max(250, d.size // max(rows, 1))
    return max(rows * per_row, d.size)


def _estimate_load_bytes(d: FolderDataset) -> int:
    """估算启用一个数据集后的驻留内存字节（软启发式，非内存保证）。

    query_only 普通矢量按抽稀规模（_PREVIEW_MAX_FEATURES）估算；enableable 普通文件按 size；
    enableable gdb 图层按 feature_count 估算（不把整库 size 重复累计几十次）；
    Table（全量/抽稀）走 Table 专属估算（行数 + 文件字节双重保守下限）。
    """
    if d.geometry == "table":
        return _estimate_table_load_bytes(d)
    if d.status == DatasetStatus.query_only:
        return _PREVIEW_MAX_FEATURES * 250
    if d.gdb_layer is not None:
        return (d.feature_count or _ENABLE_MAX_FEATURES) * 250
    return d.size


def enable_all_datasets(source_id: str, *, limit: int | None = None, rel_path: str | None = None, dry_run: bool = False) -> dict:
    """文件夹级全部启用：候选（enableable/query_only 且未启用，含表格，排除 tif）逐个启用。

    硬上限 20（min(max(limit or 20, 1), 20)，不能被调用方绕过）；512MB 累计软预算
    （query_only 按抽稀规模、gdb 图层按要素数、Table 按行数估算，不重复累计整库大小）。
    预算对每个候选都生效（含首个），单项预计超预算同样被拦截。顺序执行只降瞬时峰值，
    最终驻留内存仍累计，UI 需诚实提示。单项失败记录后继续。

    rel_path 非空时仅启用该子目录前缀下的数据集（须拼 '/' 分隔符防「城市」误匹配
    「城市化」）；None = 全量行为不变。

    dry_run=True 只计算并返回计划（planned_count/estimated_features/capped 等），不真正启用，
    供前端确认框展示与后端一致的确定计划，避免「前端估算 20 个、后端预算截断」的偏差。
    """
    src = get_source(source_id)
    if src is None:
        raise DataFolderError("数据源不存在")

    requested = limit if limit is not None else _ENABLE_ALL_DEFAULT_LIMIT
    effective_limit = min(max(requested, 1), _ENABLE_ALL_HARD_LIMIT)

    candidates = [
        d for d in src.datasets
        if d.status in (DatasetStatus.enableable, DatasetStatus.query_only)
        and d.format != "tif"
        and not d.enabled
        and (not rel_path or d.rel_path.startswith(rel_path + "/"))
    ]

    planned: list[FolderDataset] = []
    estimated_bytes = 0
    estimated_features = 0
    capped = False
    capped_by_limit = False
    capped_by_bytes = False
    for d in candidates:
        if len(planned) >= effective_limit:
            capped = True
            capped_by_limit = True
            break
        est = _estimate_load_bytes(d)
        if estimated_bytes + est > _ENABLE_ALL_MAX_BYTES:
            # 首项也受预算约束：任何单项预计超过 512MB 都必须被拦截，不得豁免
            capped = True
            capped_by_bytes = True
            break
        planned.append(d)
        estimated_bytes += est
        estimated_features += _PREVIEW_MAX_FEATURES if d.status == DatasetStatus.query_only else (d.feature_count or 0)

    enabled_count = 0
    failed: list[dict] = []
    if not dry_run:
        for d in planned:
            try:
                enable_dataset(source_id, d.dataset_id)
                enabled_count += 1
            except DataFolderError as exc:
                failed.append({"dataset_id": d.dataset_id, "error": str(exc)})

    omitted = max(0, len(candidates) - len(planned))
    return {
        "enabled": enabled_count,
        "count": enabled_count,
        "failed": failed,
        "total_candidates": len(candidates),
        "planned_count": len(planned),
        "omitted_count": omitted,
        "estimated_features": estimated_features,
        "estimated_bytes": estimated_bytes,
        "capped": capped,
        "capped_by_limit": capped_by_limit,
        "capped_by_bytes": capped_by_bytes,
        "dry_run": dry_run,
    }


def disable_all_datasets(source_id: str, rel_path: str | None = None) -> dict:
    """文件夹级全部停用：遍历已启用数据集逐个 disable_dataset，返回 {disabled, count}。

    rel_path 非空时仅停用该子目录前缀下已启用的数据集（同 rel_path + '/' 规则，
    不误动兄弟文件夹）；None = 全量行为不变。
    """
    src = get_source(source_id)
    if src is None:
        raise DataFolderError("数据源不存在")
    enabled_ids = [
        d.dataset_id for d in src.datasets
        if d.enabled and (not rel_path or d.rel_path.startswith(rel_path + "/"))
    ]
    for dataset_id in enabled_ids:
        disable_dataset(source_id, dataset_id)
    return {"disabled": len(enabled_ids), "count": len(enabled_ids)}


def browse_folder_dialog() -> str | None:
    """弹出系统原生文件夹选择器（本机 GUI），返回用户选中的目录路径；取消返回 None。

    仅 Windows 生效（平台为本地部署，后端进程就在用户本机，可直接弹原生对话框）。
    其余平台返回 None，前端回退为手动输入路径。
    """
    import sys

    if sys.platform != "win32":
        return None
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        path = filedialog.askdirectory(title="选择要链接的数据目录")
    finally:
        root.destroy()
    return path or None
