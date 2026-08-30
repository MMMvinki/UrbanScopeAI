from enum import Enum

from pydantic import BaseModel, Field


class GeometryType(str, Enum):
    point = "Point"
    line = "LineString"
    polygon = "Polygon"


class LayerSource(str, Enum):
    platform = "platform"
    uploaded = "uploaded"
    generated = "generated"
    drawn = "drawn"  # 用户在地图上手绘点/线/面创建的图层


class FieldSpec(BaseModel):
    name: str
    type: str
    description: str | None = None


class LayerMetadata(BaseModel):
    id: str
    name: str
    source: LayerSource = LayerSource.platform
    geometry: GeometryType
    color: str = "#0F172A"
    visible: bool = True
    feature_count: int = 0
    crs: str = "EPSG:4490"
    description: str | None = None
    generated_by: str | None = None
    fields: list[FieldSpec] = Field(default_factory=list)
    # ── 平台数据（2026-08-17）：只读底座 + 惰性几何 ─────────────────
    readonly: bool = False  # True=平台内置数据：数据内容只读，样式/名字可改
    lazy: bool = False  # True=几何按需加载（重图层不进 bulk 端点，前端 /layers/{id}/geojson 惰性取）
    kind: str = "vector"  # vector | raster（栅格图层：tile_url 瓦片金字塔渲染）
    bbox: list[float] | None = None  # [minx,miny,maxx,maxy]，真实范围（供定位/范围判断）
    tile_url: str | None = None  # raster 图层瓦片 URL 模板（{z}/{x}/{y} 占位）
    # ── 样式模型（Sprint 2）：单值样式 + 分档设色描述 ─────────────────
    # 全部可选带默认，向后兼容：既有构造点（demo_data / register_generated / workspace 恢复）
    # 不传这些字段也能正常构造；缺省时前端按 color/visible 渲染。
    opacity: float | None = None  # 整体透明度 0..1（兼容旧值；新样式优先用 fillOpacity/strokeOpacity）
    fillOpacity: float | None = None  # 填充不透明度 0..1（None=跟随 opacity）
    strokeOpacity: float | None = None  # 描边不透明度 0..1（None=跟随 opacity）
    strokeColor: str | None = None  # 描边/线色
    strokeWidth: float | None = None  # 描边线宽
    radius: float | None = None  # 点半径
    fillColor: str | None = None  # 填充色（面）
    styleMode: str | None = None  # single | graduated | categorized
    visualVariables: dict | None = None  # renderer 描述：graduated{field,method,classes,palette,breaks} / size_variable / opacity_variable
    labelField: str | None = None  # 标注字段
    # ── 手绘增强（2026-08-30）：自动字段单位偏好 ─────────────────
    units: dict[str, str] | None = None  # {"length":"m"|"km","area":"m2"|"hm2"|"km2"}，供编辑时按同单位重算


class LayerPatch(BaseModel):
    """PATCH /layers/{id} 的写面：名称/可见性/样式字段，全部可选。

    用 exclude_unset 区分「未传」与「显式置 null」（如清除 graduated 样式时发 styleMode: null）。
    """

    name: str | None = None
    visible: bool | None = None
    color: str | None = None
    opacity: float | None = None
    fillOpacity: float | None = None
    strokeOpacity: float | None = None
    strokeColor: str | None = None
    strokeWidth: float | None = None
    radius: float | None = None
    fillColor: str | None = None
    styleMode: str | None = None
    visualVariables: dict | None = None
    labelField: str | None = None
    units: dict[str, str] | None = None


class LayerSchema(BaseModel):
    layer_id: str
    name: str
    geometry: GeometryType
    crs: str
    feature_count: int
    fields: list[FieldSpec] = Field(default_factory=list)
    sample: list[dict[str, object]] = Field(default_factory=list)


class DrawLayerRequest(BaseModel):
    """POST /layers/draw 入参：前端手绘点/线/面 → 注册为图层。

    geojson 接受 FeatureCollection / 裸 Feature / 裸 Geometry 任意形态，
    load_feature_collection 统一归一。fields 可显式传；缺省时按要素 properties 推断。
    """

    layer_id: str | None = None
    name: str
    crs: str = "EPSG:4326"
    geojson: dict
    color: str = "#0F172A"
    fields: list[FieldSpec] | None = None
    units: dict[str, str] | None = None  # 手绘自动字段单位偏好（创建即持久化，编辑时按同单位重算）


class UpdateLayerGeojsonRequest(BaseModel):
    """PUT /layers/{id}/geojson 入参：编辑已建（非只读）图层的几何后保存。

    geojson 为前端 TerraDraw collect 回的 FeatureCollection；fields 显式传，缺省按 properties 推断；
    units 记录本次编辑所选的长度/面积单位，供后续编辑按同单位重算。
    """

    geojson: dict
    fields: list[FieldSpec] | None = None
    units: dict[str, str] | None = None
