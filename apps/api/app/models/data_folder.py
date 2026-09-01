"""本地数据文件夹接入（第一步「只读发现」）的模型。

区分「数据目录（发现了哪些数据）」与「地图图层（已加载、可分析）」：
本模块只描述前者。扫描得到的每个数据集按四态标注可用性，供前端目录页展示；
第二步才把这些数据集「启用为图层」，第三步做大型数据按范围查询（均不在本轮）。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class DatasetStatus(str, Enum):
    """数据集四态（计划文档 §3.5）。

    - discovered  ：已发现，但尚未完成全部检查（过渡态）
    - enableable  ：可直接启用（规模、格式、字段、坐标满足当前加载能力）
    - query_only  ：仅可查询（较大，不适合完整加载，只能按范围/任务读取）
    - unavailable ：暂不可用（格式/坐标/字段/依赖不满足，附 reason）
    """

    discovered = "discovered"
    enableable = "enableable"
    query_only = "query_only"
    unavailable = "unavailable"


class FieldBrief(BaseModel):
    """数据字段摘要（名称 + 推断类型），不承载真实数值。"""

    name: str
    type: str  # number | string | boolean | date | other


class RasterBrief(BaseModel):
    """栅格影像元数据摘要（只读探测，不含像素数据）。"""

    width: int
    height: int
    bands: int
    dtype: str | None = None
    crs: str | None = None
    res_x: float | None = None
    res_y: float | None = None
    bounds: list[float] | None = None  # [minx, miny, maxx, maxy]
    nodata: float | None = None
    color_interpretation: list[str] | None = None
    preview_image: str | None = None  # data-url（PNG base64）
    preview_note: str | None = None


class FolderDataset(BaseModel):
    """一个被发现的数据集（只读元数据，不含几何/行数据）。"""

    dataset_id: str  # 稳定编号（同一目录重扫不变）
    name: str  # 显示名（文件 stem）
    rel_path: str  # 相对数据根目录的路径（正斜杠分隔）
    format: str  # csv | xlsx | geojson | shp | shp_zip | gpkg | gdb | tif | other
    size: int  # 字节（shapefile 为配套文件合计）
    fields: list[FieldBrief] = Field(default_factory=list)
    crs: str | None = None  # 如 "EPSG:4326" / "EPSG:4490"（CGCS2000），未知为 None
    coord_columns: list[str] | None = None  # CSV 坐标列名 [lng, lat]
    geometry: str | None = None  # point | line | polygon
    feature_count: int | None = None  # 要素/行数；大型数据未统计则为 None
    status: DatasetStatus
    reason: str | None = None  # unavailable 时的原因（对产品人员可理解）
    warnings: list[str] = Field(default_factory=list)  # 质量警告，如坐标需转换
    # ── 第二步「小数据启用」：启用后进地图图层体系 ─────────────────
    enabled: bool = False  # 是否已启用为项目图层
    layer_id: str | None = None  # 启用后的图层 id（供前端跳转/定位图层）
    # ── File Geodatabase 内部图层 ─────────────────────────────────
    # 非 gdb 数据集为 None；gdb 图层记录其内部图层名，rel_path 形如
    # 「…/乐活大道数据库.gdb/深圳POI」，前端按目录树展开，启用时用 fiona 读该图层。
    gdb_layer: str | None = None
    # ── 栅格影像元数据（tif 格式探测结果，不启用为计算图层）────────────
    raster: RasterBrief | None = None


class FolderSource(BaseModel):
    """一个已链接的数据源（本机目录 + 扫描快照）。"""

    source_id: str
    alias: str  # 数据源别名（默认取目录名）
    root_path: str  # 本机绝对路径（仅存本机 workspace 配置，不进 Git）
    created_at: str
    last_scanned_at: str
    datasets: list[FolderDataset] = Field(default_factory=list)


class LinkFolderRequest(BaseModel):
    root_path: str = Field(min_length=1, description="要链接的本地目录绝对路径")
    alias: str | None = Field(default=None, max_length=60, description="数据源别名（缺省取目录名）")


class AllowlistAddRequest(BaseModel):
    root_path: str = Field(min_length=1, description="加入允许访问白名单的目录绝对路径")
