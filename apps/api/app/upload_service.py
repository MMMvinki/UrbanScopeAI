"""用户文件上传服务：把上传的 CSV/GeoJSON 落盘并注册为地图图层。

供 POST /layers/upload 使用（Sprint 1 最小上传入口）。项目功能上线后，
上传物将落到当前项目的数据空间，本服务作为其前置基础设施复用。

复用：
- csv_geojson 的坐标列自动检测 + CSV/Excel 解析（Lat/Lng 自动识别）；
- geojson_utils 的 FeatureCollection 归一 / 几何类型识别 / 图层 id 规整；
- register_layer 的"注册 + 物化到 data/layers"模式。
"""

from __future__ import annotations

import json
import tempfile
import uuid
from pathlib import Path

from app.data_registry import DATA_REGISTRY, save_layer_meta
from app.geojson_utils import (
    build_fields_from_features,
    clean_layer_id,
    geometry_type_for,
    load_feature_collection,
)
from app.models.layer import GeometryType, LayerMetadata, LayerSource
from app.tools import csv_geojson as csv_tool
from app.workspace import LAYER_DATA_DIR, WORKSPACE_ROOT

_UPLOAD_DIR = WORKSPACE_ROOT / "data" / "uploads"


class UploadError(ValueError):
    """上传校验/解析失败，向用户返回友好 message。"""


def _save_upload(filename: str, content: bytes) -> Path:
    """上传内容落到工作区 uploads 目录；防路径穿越，重名加短随机后缀。"""
    safe = Path(filename).name  # 只保留文件名，剥离任何路径成分
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = _UPLOAD_DIR / safe
    if path.exists():
        path = _UPLOAD_DIR / f"{path.stem}_{uuid.uuid4().hex[:6]}{path.suffix}"
    path.write_bytes(content)
    return path


def _geojson_from_csv(path: Path) -> dict:
    """CSV/Excel → FeatureCollection（复用 csv_geojson 的列自动检测与解析）。"""
    if path.suffix.lower() == ".xlsx":
        rows, headers = csv_tool._read_xlsx(path)
    else:
        rows, headers = csv_tool._read_csv(path, None)
    if not headers:
        raise UploadError(f"CSV 无表头：{path.name}")
    lat_col = csv_tool._detect_column(headers, csv_tool.LAT_PATTERNS)
    lng_col = csv_tool._detect_column(headers, csv_tool.LNG_PATTERNS)
    if not lat_col or not lng_col:
        raise UploadError(
            f"无法自动识别经纬度列。表头：{'、'.join(headers)}"
            "（支持 lat/latitude/y/纬度 与 lng/lon/longitude/x/经度；"
            "带前缀变体如 WGS84_Lat / GCJ02_Lng 也会自动识别）"
        )
    fc, _skipped = csv_tool._build_geojson(rows, lat_col, lng_col)
    if not fc["features"]:
        raise UploadError(f"CSV 中没有有效坐标行：{path.name}")
    return fc


def _geojson_from_file(path: Path) -> dict:
    """GeoJSON/JSON → FeatureCollection（容错裸 Feature/裸 Geometry）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise UploadError(f"解析 GeoJSON 失败：{exc}") from exc
    features, _types, _bbox = load_feature_collection(data)
    if not features:
        raise UploadError(f"GeoJSON 没有要素：{path.name}")
    fc: dict = {"type": "FeatureCollection", "features": features}
    if isinstance(data, dict) and "crs" in data:
        fc["crs"] = data["crs"]
    return fc


def _geojson_from_gdf(gdf, filename: str) -> dict:
    """GeoDataFrame → FeatureCollection（尽量重投影到 EPSG:4326）。"""
    try:
        if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs("EPSG:4326")
    except Exception:
        pass  # 无 CRS/无法转换时按原坐标读取，交给下游容错
    try:
        return json.loads(gdf.to_json())
    except Exception as exc:  # noqa: BLE001
        raise UploadError(f"解析 {filename} 失败：{exc}") from exc


def _geojson_from_shp(path: Path) -> dict:
    """Shapefile（.shp 或 zip 包裹的多文件格式）→ FeatureCollection。"""
    import shutil
    import zipfile

    import geopandas as gpd

    src: Path = path
    tmp_dir: str | None = None
    if path.suffix.lower() == ".zip":
        tmp_dir = tempfile.mkdtemp(prefix="shp_upload_")
        try:
            with zipfile.ZipFile(path) as zf:
                zf.extractall(tmp_dir)
            candidates = [p for p in Path(tmp_dir).rglob("*.shp") if p.is_file()]
            if not candidates:
                raise UploadError(f"压缩包内没有 .shp 文件：{path.name}")
            src = candidates[0]
        except UploadError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise UploadError(f"解压 shapefile 失败：{exc}") from exc
    try:
        gdf = gpd.read_file(src)
    except Exception as exc:  # noqa: BLE001
        raise UploadError(f"读取 shapefile 失败：{exc}") from exc
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    return _geojson_from_gdf(gdf, path.name)


def _geojson_from_gpkg(path: Path) -> dict:
    """GeoPackage（.gpkg）→ FeatureCollection。"""
    import geopandas as gpd

    try:
        gdf = gpd.read_file(path)
    except Exception as exc:  # noqa: BLE001
        raise UploadError(f"读取 GPKG 失败：{exc}") from exc
    return _geojson_from_gdf(gdf, path.name)


def _ingest_path(
    path: Path,
    name: str | None = None,
    layer_id: str | None = None,
    source: LayerSource = LayerSource.uploaded,
    description: str | None = None,
    generated_by: str | None = None,
    data_path: str | None = None,
    rel_path: str | None = None,
    data_type: str | None = None,
) -> dict:
    """从已落盘文件解析 + 注册为地图图层。

    upload（立即注册）、register_uploaded_file 工具（Agent 按需注册）与数据目录
    「小数据启用」共用：解析文件 → FeatureCollection → 注册进 DATA_REGISTRY → 物化
    到 workspace/data/layers/。返回 {layer_id, name, feature_count, geometry_types, crs}。

    layer_id/source/description/generated_by 为可选覆写，缺省走「文件上传」语义，
    供数据目录启用时标记来源（source=folder）。均向后兼容。
    """
    suffix = path.suffix.lower()

    if suffix in (".csv", ".xlsx", ".tsv", ".txt"):
        fc = _geojson_from_csv(path)
    elif suffix in (".geojson", ".json"):
        fc = _geojson_from_file(path)
    elif suffix in (".shp", ".zip"):
        fc = _geojson_from_shp(path)
    elif suffix == ".gpkg":
        fc = _geojson_from_gpkg(path)
    else:
        raise UploadError(
            f"不支持的文件类型：{suffix}"
            "（支持 .csv / .xlsx / .geojson / .json / .gpkg / .shp / .zip(shapefile)；"
            "暂不支持 Word/PDF，请转 CSV/GeoJSON）"
        )

    features, _geometry_types, _bbox = load_feature_collection(fc)
    if not features:
        raise UploadError(f"文件没有可用的几何要素：{path.name}")

    layer_id = layer_id or clean_layer_id(path.stem)
    layer_name = (name or path.stem).strip() or layer_id
    return register_feature_collection(
        fc,
        layer_id=layer_id,
        name=layer_name,
        source=source,
        description=description or f"用户上传 {path.name}",
        generated_by=generated_by or "文件上传",
        data_path=data_path or str(path),
        rel_path=rel_path,
        data_type=data_type or path.suffix.lstrip("."),
    )


def register_feature_collection(
    fc: dict,
    *,
    layer_id: str,
    name: str,
    source: LayerSource = LayerSource.uploaded,
    description: str | None = None,
    generated_by: str | None = None,
    visible: bool = True,
    lazy: bool | None = None,
    sampled: bool = False,
    original_feature_count: int | None = None,
    data_path: str | None = None,
    rel_path: str | None = None,
    data_type: str | None = None,
) -> dict:
    """把 FeatureCollection 注册为地图图层 + 物化到 workspace/data/layers/。

    供 _ingest_path（上传/工具）与数据目录 gdb 图层启用共用。
    visible=False 用于「抽稀预览」大图层：默认隐藏，用户勾选眼睛才上地图（默认不预览）。
    lazy=None 时按既有兼容行为派生（表格 → lazy=True）；显式传 lazy=True 用于「抽稀预览」
    大图层，使其不进 bulk 渲染链、前端增量刷新/可见性兜底不误预取其几何。
    sampled/original_feature_count 标记抽稀样本口径：query_features 对 sampled 图层只报样本命中数。
    data_path 记录源数据磁盘路径（倒灌 folder/uploaded 图层定位用）。
    rel_path 记录 folder 源图层在数据源内的相对路径（「文件路径视图」细分文件夹用）。
    返回 {layer_id, name, feature_count, geometry_types, crs}。
    """
    features, geometry_types, bbox = load_feature_collection(fc)
    if not features:
        raise UploadError("没有可用的几何要素")
    is_table = not geometry_types  # 无坐标表格（CSV/XLSX 无经纬度列）注册为表格图层
    layer = LayerMetadata(
        id=layer_id,
        name=name,
        source=source,
        geometry=GeometryType.table if is_table else geometry_type_for(geometry_types),
        color="#0F172A",
        visible=visible,
        feature_count=len(features),
        crs="N/A" if is_table else "EPSG:4326",
        description=description or "图层",
        generated_by=generated_by or "导入",
        lazy=is_table if lazy is None else lazy,  # 表格强制 lazy；抽稀预览显式 lazy
        sampled=sampled,
        original_feature_count=original_feature_count,
        bbox=bbox,  # 真实范围随 GeoJSON 落盘（无坐标 Table 为 None）
        fields=build_fields_from_features(features),
        data_path=data_path,
        rel_path=rel_path,
        data_type=data_type,
    )
    DATA_REGISTRY.register_generated(layer, geojson=fc)

    # 物化到 workspace/data/layers/（execute_code 相对路径复用 + 跨重启索引恢复）
    try:
        LAYER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        (LAYER_DATA_DIR / f"{layer_id}.json").write_text(
            json.dumps(fc, ensure_ascii=False), encoding="utf-8"
        )
        # 保存 DATA_REGISTRY 返回的最终对象（已含继承样式/自动颜色），避免保存未上色的局部旧对象
        final_layer = DATA_REGISTRY.get_layer(layer_id)
        if final_layer is not None:
            save_layer_meta(final_layer)
    except OSError:
        pass  # 物化失败不致命，内存注册已让地图可见

    return {
        "layer_id": layer_id,
        "name": name,
        "feature_count": len(features),
        "geometry_types": sorted(geometry_types),
        "crs": "N/A" if is_table else "EPSG:4326",
    }


def ingest_upload(filename: str, content: bytes, layer_name: str | None = None) -> dict:
    """上传 → 立即注册图层（图层面板「添加数据」入口）。"""
    original_stem = Path(filename).stem  # 显示名取原始文件名（不含随机去重后缀）
    path = _save_upload(filename, content)
    return _ingest_path(path, name=layer_name or original_stem)


def stage_upload(filename: str, content: bytes) -> dict:
    """仅暂存文件到工作区 uploads 目录：不解析、不注册图层、不上地图。

    Agent 对话框上传走这里：只挂附件 chip，等 Agent 真正要用时再通过
    register_uploaded_file 工具按需解析并注册为图层。返回暂存引用
    {staged_id, original_name, path, size}，path 为 workspace 相对路径。

    同名重传覆盖原暂存文件（而非 _save_upload 的去重后缀），保证图层 id/名称
    干净（oct_poi 而不是 oct_poi_ab12cd）；已注册的同名图层由 register_generated
    原地更新几何，不产生重名图层。
    """
    safe = Path(filename).name  # 只保留文件名，剥离任何路径成分
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = _UPLOAD_DIR / safe
    path.write_bytes(content)
    return {
        "staged_id": path.stem,
        "original_name": Path(filename).name,
        "path": f"data/uploads/{path.name}",
        "size": path.stat().st_size,
    }
