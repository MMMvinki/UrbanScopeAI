"""OSM 建筑功能标签 → 语义类别：唯一权威实现，供 skill 的 execute_code import。

背景：building-function-color 这类「提示型技能」让 agent 自己手写分类字典，
每次不完全一致（漏 hotel/fire_station 等 → 「building 写了功能却被归成其他」），
而且属性表也被删成仅剩几列。把分类逻辑沉淀为共享函数，agent 只需一行
`from app.gis.labels import classify_building_function`，杜绝手写字典漂移。

返回 9 个语义类别之一：居住 / 商业 / 办公 / 教育 / 医疗 / 公共服务 / 工业 / 交通 / 其他。
优先级（命中即停）：① amenity/shop/office 语义标签 → ② building 标签 → ③ landuse=industrial → ④ 其他。
"""

# building 具体功能值 → 语义类别（② 补漏：无 amenity/shop/office 时靠它归类）
BUILDING: dict[str, set[str]] = {
    "商业": {"commercial", "retail", "supermarket", "hotel", "shops"},
    "办公": {"office"},
    "教育": {"school", "college", "university", "kindergarten", "kindergarden", "school_house", "classrooms"},
    "医疗": {"hospital", "clinic"},
    "公共服务": {"civic", "government", "public", "fire_station", "police", "church", "mosque", "temple", "chapel", "community_centre"},
    "交通": {"train_station", "transportation", "station", "pavilion"},
    "居住": {"house", "residential", "apartments", "dormitory", "detached", "semidetached", "terrace", "bungalow", "cabin", "hut"},
    "工业": {"industrial", "warehouse", "manufacture", "factory", "barn"},
}

# amenity 语义标签 → 语义类别（① 最优先）
AMENITY: dict[str, set[str]] = {
    "商业": {"retail", "restaurant", "cafe", "bar", "bank", "fast_food", "marketplace", "supermarket"},
    "教育": {"school", "kindergarten", "college", "university", "library", "music_school"},
    "医疗": {"hospital", "clinic", "doctors", "dentist", "pharmacy"},
    "公共服务": {"government", "police", "fire_station", "post_office", "townhall", "courthouse", "embassy"},
    "交通": {"bus_station", "train_station", "parking", "charging_station", "taxi", "ferry_terminal"},
}

# OSM 导航元数据键：分类结果里要剔除，其余原始标签一律保留（修「属性表太简单」）。
NAV_KEYS = {
    "@id", "@relations", "source", "created_by", "version", "timestamp",
    "changeset", "user", "uid", "id", "osm_id", "osm_type",
}


def classify_building_function(props: dict) -> str:
    """把一行 OSM 属性（dict 或 pandas Series 皆可）归为 9 个语义类别之一。

    无功能信息（building=yes/roof/construction/tower/no 等，或标签全空）→ 「其他」，
    不硬归类，避免把无功能含义的值强塞进某类。
    """
    def _k(key: str) -> str:
        try:
            val = props.get(key)
        except AttributeError:  # pandas Series 用 get 亦可；保险起见
            val = getattr(props, key, None)
        return str(val or "").strip().lower()

    amenity, shop, office = _k("amenity"), _k("shop"), _k("office")
    if shop:
        return "商业"  # shop∈* 即商业（优先级最高）
    for func, vals in AMENITY.items():
        if amenity in vals:
            return func
    if office:
        return "办公"

    building = _k("building")
    for func, vals in BUILDING.items():
        if building in vals:
            return func

    if _k("landuse") == "industrial":
        return "工业"

    return "其他"


# 便捷入口：对整块 GeoJSON 的 properties 逐行打上 func_type（execute_code 可直接用）。
def label_geojson_features(features: list[dict]) -> list[dict]:
    """就地给每个 feature 的 properties 补充 func_type，返回同一列表。"""
    for feat in features:
        props = feat.setdefault("properties", {})
        props["func_type"] = classify_building_function(props)
    return features
