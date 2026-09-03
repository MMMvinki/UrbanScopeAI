"""配置驱动的数据源清单：登记原始数据（慧眼人口/客流等），按需读盘并保真坐标。

设计原则（对齐文档 §3.4.7 / §3.3.3 / §9.4）：
- 数据在仓库外的「平台数据/」下（gitignored）。清单（data_sources.json）只存**相对路径**
  + 元数据，绝对路径永不落盘 → clone 后按 README 放回数据即可复现。
- 路径相对 **DATA_ROOT** 解析：默认 = 仓库根，可用环境变量 `URBANSCOPE_DATA_ROOT` 覆盖
  （给李工的数据目录留替代位，改 URI 只改这一处）。
- 大图层（客流 3.27M 行）**禁止全量载入**：get_source(columns=, bbox=) 用 pyogrio 前缀裁剪读，
  不读整层，也不转 GeoJSON（图上仍需按需聚合后再发布）。
- 坐标保真：原点位 CRS 是 EPSG:4326（WGS-84），与平台 geojson 一致，**不做任何坐标偏移**。
  注意这是列来自慧眼、geometry 直接是 WGS84，与平台 poi_* 的「列是 GCJ-02」不同（见 memory）。

本模块独立于 data_registry / engine（DATA_REGISTRY.get_geojson 只认 geojson；慧眼无 geojson，
塞进去会被 engine.load_gdf 误判图层退化）。skill 子进程直接 `from app.data_sources import get_source`。
"""

from __future__ import annotations

import json
import logging
import os
import re
from functools import lru_cache
from pathlib import Path

import geopandas as gpd

logger = logging.getLogger(__name__)

_MODULE_DIR = Path(__file__).resolve().parent
_CATALOG_PATH = _MODULE_DIR / "data_sources.json"

# 仓库根：从本模块向上找含 .git 的目录（不按固定 parents[2]/[3] 数层级 —— 那是
# platform-data-path-gotcha 的 parents[1] 陷阱：换目录结构就错位）。
def _find_repo_root() -> Path:
    for parent in _MODULE_DIR.parents:
        if (parent / ".git").exists():
            return parent
    return _MODULE_DIR.parents[3]  # 兜底：apps/api/app 上溯 3 级到仓库根

REPO_ROOT = _find_repo_root()

# DATA_ROOT：默认仓库根；环境变量覆盖（数据目录不在标准位置时用）
def _default_data_root() -> Path:
    override = os.environ.get("URBANSCOPE_DATA_ROOT")
    return Path(override).resolve() if override else REPO_ROOT


@lru_cache(maxsize=1)
def catalogue() -> dict:
    """读取数据源清单（顶层 dict，含 data_root_default / sources）。"""
    try:
        with _CATALOG_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        logger.exception("data_sources catalogue unreadable path=%s", _CATALOG_PATH)
        return {"sources": []}


def data_root() -> Path:
    """当前生效的数据根目录（默认仓库根，可用 URBANSCOPE_DATA_ROOT 覆盖）。"""
    return _default_data_root()


def list_sources() -> list[dict]:
    return catalogue().get("sources", [])


def _norm(text: str) -> str:
    """规范化：小写 + 去空白/连接符/后缀点，用于 Latin 名与目录 dataset_id 的可比对。

    链接数据文件夹（data_folder）暴露的 dataset_id 已经 lower + 清洗；清单 path 的 stem
    未清洗（如 `POI_餐饮` vs `poi餐饮`）。这里收口成同一种规范形再比，避免拉丁名对不上、
    中文名不受影响。仅作精确匹配外的兼容路径，不做子串包含（避免「人口」误匹配多个源）。
    """
    return re.sub(r"[\s_\-.]+", "", str(text)).lower()


def _match_spec(src: dict, s: str) -> bool:
    """规范条目是否接受别名 s：id 精确 / name / 文件 basename（含或不含后缀）。

    先做精确匹配（id / name / 文件 basename / stem 四路），再做规范化兼容（拉丁名大小写与
    连接符差异）。不做子串包含（避免「人口」这种词误匹配到多个源）。path 为平台外的相对
    路径（如 `平台数据/百度慧眼数据/居住人口数量及画像202508.shp`），basename 匹配让
    链接数据文件夹暴露的中文 dataset_id 能反查回本条目。
    """
    p = Path(str(src.get("path") or ""))
    if src.get("id") == s or src.get("name") == s or p.name == s or p.stem == s:
        return True
    ns = _norm(s)
    if not ns:
        return False
    return (
        _norm(src.get("id")) == ns
        or _norm(src.get("name")) == ns
        or _norm(p.name) == ns
        or _norm(p.stem) == ns
    )


def _resolve_source_spec(spec: str) -> dict | None:
    """把任意「源别名」解析成规范清单条目；无匹配返回 None 并**响亮告警**（C1）。

    链接数据文件夹（data_folder）暴露的中文 dataset_id（如 `居住人口数量及画像202508`）
    正是清单 `path` 去后缀的 stem；`name`（如 `居住人口数量及画像`）也是合法别名。
    据此让 agent 从目录里发现的名称能落回规范 id（huoyan_resident），而不必靠猜——
    否则人口热力 skill 收到 `population_source=居住人口数量及画像202508` 只会
    「读取失败或无点」。
    """
    if not spec or not str(spec).strip():
        return None
    s = str(spec).strip()
    for src in list_sources():
        if _match_spec(src, s):
            return src
    # 找不到不再静默：肉眼可查「对不上号」，也为诊断记录到底收到了什么别名。
    logger.warning("数据源别名未匹配：%s（清单内可试 id=%s）", s, [str(x.get("id")) for x in list_sources()][:10])
    return None


def get_source_meta(source_id: str) -> dict | None:
    """按 id（或其别名 name / 文件 basename）查 metadata；不存在返回 None。"""
    return _resolve_source_spec(source_id)


def resolve_path(source_id: str) -> Path | None:
    """list 中 source_id（或其 name/文件 basename 别名）对应的原始文件路径；缺 source/文件返回 None。"""
    meta = get_source_meta(source_id)
    if meta is None:
        return None
    raw = Path(data_root()) / meta["path"]
    if not raw.exists():
        logger.warning("data source %s not found at %s", source_id, raw)
        return None
    return raw


def get_source(
    source_id: str,
    *,
    columns: list[str] | None = None,
    bbox: tuple[float, float, float, float] | None = None,
    where: str | None = None,
    validate: bool = True,
) -> gpd.GeoDataFrame | None:
    """按需读盘：返回 EPSG:4326 的 GeoDataFrame（列 / 空间范围 / SQL 条件裁剪）。

    - columns：只读需要的列（不含 geometry 也由 pyogrio 补）。大图层**必须**给，避免读全列。
    - bbox：(minx, miny, maxx, maxy) 经纬度，WGS-84；只读该范围内要素。
    - where：原始 SQL where 子句（如客流按小时过滤 "小时 = 8"）。

    source 不存在 / 文件缺失 / 读盘失败均返回 None（调用方诚实失败，绝不抛错挂死）。
    feature_count 与清单不一致（validate=True）则记录 warning，不中断。
    """
    path = resolve_path(source_id)
    if path is None:
        return None
    meta = get_source_meta(source_id) or {}
    read_columns = columns
    # 防御：pyogrio 只认 tuple bbox；调用方（LLM/skill 脚本）传成 list 会抛
    # TypeError → 被下游误判成「pyogrio 不支持前缀读」→ 静默整层全载。这里归一化。
    if bbox is not None and not isinstance(bbox, tuple):
        bbox = tuple(bbox)
    # 大图层（客流 3.27M）一旦前缀读失败，**绝不能静默 full read 兜底**（OOM/几十秒）。
    # 按 feature_count 判定；这类源直接诚实失败返回 None，让调用方调整 bbox/where 重试。
    forbid_full_read = int(meta.get("feature_count") or 0) >= 500_000
    try:
        gdf = gpd.read_file(path, columns=read_columns, bbox=bbox, where=where, engine="pyogrio")
    except TypeError:
        if forbid_full_read:
            logger.warning(
                "prefix read failed for lazy huge source %s（columns/bbox/where 不被当前 pyogrio 接受）→ 拒绝全载",
                source_id,
            )
            return None
        # 旧版 pyogrio / geopandas 不认 bbox/where，只在小图层出现 → 回退到完整读
        logger.warning("pyogrio prefix read unsupported for %s, falling back to full read", source_id)
        try:
            gdf = gpd.read_file(path, columns=read_columns, engine="pyogrio")
        except Exception:
            return None
    except Exception:
        logger.exception("data source read failed id=%s", source_id)
        return None

    # 协调 CRS：源声明 EPSG:4326，读出的列坐标已是 WGS-84（不做任何偏移）
    if gdf.crs is None:
        gdf = gdf.set_crs(meta.get("crs") or "EPSG:4326")
    elif str(gdf.crs).upper() not in ("EPSG:4326", "OGC:CRS84"):
        # 源 CRS 非 4326（冗余防御）：真实读回 WGS84，注明来源文件不偏移
        logger.warning("data source %s read as %s (expected EPSG:4326)", source_id, gdf.crs)

    if validate and meta.get("feature_count"):
        actual = len(gdf)
        expected = int(meta["feature_count"])
        if actual != expected:
            logger.warning(
                "data source %s row count %d != manifest %d (may be bbox/where subset)", source_id, actual, expected
            )
    return gdf


def _known_columns(meta: dict) -> set[str]:
    """汇集清单里所有候选列名（ratio_family 各组的项 + education/income/consumption + 基础列）。"""
    known: set[str] = {c for c in (meta.get("pop_field"), meta.get("time_field")) if c}
    ratio = meta.get("ratio_family") or {}
    if isinstance(ratio, dict):
        for group in ratio.values():
            known.update(group or [])
    for key in ("education_columns", "income_columns", "consumption_columns"):
        known.update(meta.get(key) or [])
    return known


def has_columns(source_id: str, columns: list[str]) -> bool:
    """判断某源是否存在给定列（预检用，避免技能跑去读不存在的列报错）。"""
    meta = get_source_meta(source_id)
    if meta is None:
        return False
    return all(c in _known_columns(meta) for c in columns)
