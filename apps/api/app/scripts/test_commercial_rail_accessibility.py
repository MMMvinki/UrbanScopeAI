"""commercial-rail-accessibility 确定性验收：纯函数、真实输入、失败语义、坐标与累计统计。

不依赖 Mapbox 网络：纯函数用合成几何；真实数据路径只测读取/裁剪/失败停止；
真实路网等时圈成功链由独立冒烟测试（需 MAPBOX_TOKEN + 网络）另行验证。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import geopandas as gpd  # noqa: E402
from shapely.geometry import Point  # noqa: E402

from app.data_registry import DATA_REGISTRY  # noqa: E402
from app.skills.store import STORE  # noqa: E402

SKILL_DIR = API_ROOT / "app" / "skills" / "builtin" / "commercial-rail-accessibility"
MAIN_PATH = SKILL_DIR / "scripts" / "main.py"


def load_module():
    spec = importlib.util.spec_from_file_location("commercial_rail_accessibility_main", MAIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL {label}: {detail}")
    print(f"PASS {label}" + (f" | {detail}" if detail else ""))


module = load_module()

# ── 1) 出入口标签清理：缺值绝不输出 'nan'，出口编号只作标识不作站名 ──
check("标签清理：都缺→地铁出入口", module.clean_entrance_label(None, None) == "地铁出入口")
check("标签清理：nan→地铁出入口", module.clean_entrance_label("nan", "nan") == "地铁出入口")
check("标签清理：None+name", module.clean_entrance_label(None, " C ") == "出入口 C")
check("标签清理：ref 优先", module.clean_entrance_label("D1", "某站名") == "出入口 D1")
check("标签清理：不含 nan", "nan" not in module.clean_entrance_label("nan", "nan"))
check(
    "标签清理：空串",
    module.clean_entrance_label("", "") == "地铁出入口",
)

# ── 2) 硬上限：max_centers > 5 必须停止 ──
for bad in (0, -1, 6, 100):
    try:
        module.run_analysis({"max_centers": bad}, Path(API_ROOT) / "app" / "workspace")
        check(f"硬上限 {bad} 抛错", False, "应抛 AccessibilityError")
    except module.AccessibilityError:
        pass
print("PASS 硬上限（max_centers 越界抛错）")

# ── 3) 缺文件诚实失败 ──
try:
    module.run_analysis({"subway_entrance_path": "missing-subway.shp"}, Path(API_ROOT) / "app" / "workspace")
    check("缺地铁文件抛错", False)
except module.AccessibilityError:
    pass
print("PASS 缺地铁文件抛错")

# ── 4) 中心选取：最多 max_centers、真实几何、确定性、按 500m POI 数降序 ──
def make_points(coords_cats):
    return gpd.GeoDataFrame(
        {"category": [c for _, c in coords_cats], "objectid": [str(i) for i in range(len(coords_cats))]},
        geometry=[Point(x, y) for (x, y), _ in coords_cats],
        crs="EPSG:4326",
    )

# 深圳研究区附近：一个「富」出入口周围密布商业点，一个「贫」出入口无商业点
entr = gpd.GeoDataFrame(
    {
        "entrance_id": ["1", "2", "3"],
        "label": ["出入口 A", "出入口 B", "出入口 C"],
        "ref_clean": ["A", "B", "C"],
        "name_clean": ["", "", ""],
    },
    geometry=[Point(114.10, 22.55), Point(114.11, 22.56), Point(114.12, 22.57)],
    crs="EPSG:4326",
)
poi = make_points(
    [((114.10, 22.55), "餐饮服务")] * 10 + [((114.11, 22.56), "购物服务")] * 2
)
selected = module.select_center_entrances(entr, poi, 5)
check("中心数 ≤ max_centers", len(selected) <= 5)
check("中心数 = 实际候选数", len(selected) == 3)
check("中心为真实出入口几何", set(selected["entrance_id"]) == {"1", "2", "3"})
check("按 500m POI 数降序", int(selected.iloc[0]["poi_500m"]) >= int(selected.iloc[-1]["poi_500m"]))
check("富出入口优先", selected.iloc[0]["entrance_id"] == "1")
again = module.select_center_entrances(entr, poi, 5)
check("中心选取确定性", list(selected["entrance_id"]) == list(again["entrance_id"]))
limited = module.select_center_entrances(entr, poi, 2)
check("max_centers 截断", len(limited) == 2)

# ── 5) 累计统计：5 ≤ 10 ≤ 15，15 外 = 总量 - 15 ──
from shapely.geometry import box  # noqa: E402

center = Point(114.10, 22.55)
center_m = gpd.GeoSeries([center], crs="EPSG:4326").to_crs("EPSG:32650").iloc[0]
def ring_4326(radius_m):
    return gpd.GeoSeries([center_m.buffer(radius_m)], crs="EPSG:32650").to_crs("EPSG:4326").iloc[0]

unions = {5: ring_4326(200), 10: ring_4326(400), 15: ring_4326(600)}
test_poi = gpd.GeoDataFrame(
    {
        "category": ["餐饮服务", "购物服务", "生活服务", "体育休闲服务"],
        "objectid": ["p0", "p1", "p2", "p3"],
    },
    geometry=[
        Point(114.10, 22.55),            # p0 5 分钟内
        Point(114.10, 22.55 + 0.0027),   # p1 约 300m，5~10 分钟
        Point(114.10, 22.55 + 0.0045),   # p2 约 500m，10~15 分钟
        Point(114.13, 22.60),            # p3 远在 15 分钟外
    ],
    crs="EPSG:4326",
)
stats = module.cumulative_stats(test_poi, unions)
bands = stats["bands"]
c5 = bands["5"]["cumulative_count"]
c10 = bands["10"]["cumulative_count"]
c15 = bands["15"]["cumulative_count"]
check("累计单调不减", 0 <= c5 <= c10 <= c15, f"5={c5} 10={c10} 15={c15}")
check("5 分钟内数量", c5 == 1, str(c5))
check("10 分钟累计", c10 == 2, str(c10))
check("15 分钟累计", c15 == 3, str(c15))
check("15 分钟外 = 总量 - 15", stats["outside_15min"]["count"] == 4 - c15)
check("分类构成守恒", sum(bands["5"]["by_category"].values()) == c5)

# ── 6) 真实数据读取（不请求 Mapbox）：出入口裁剪、标签无 nan、POI 分类 ──
repo_root = module._repo_root()
study = gpd.read_file(repo_root / "docs" / "workstreams" / "analysis-foundation" / "data" / "candidate_lohas_study_area.geojson")
boundary_4326 = study.geometry.union_all()
subway_path = module._resolve_path(repo_root, None, module.DEFAULT_SUBWAY)
entrances = module.load_entrances(subway_path, boundary_4326)
check("研究范围内出入口数 = 176", len(entrances) == 176)
check("出入口标签无 nan", not entrances["label"].str.contains("nan", na=False).any())
check("出入口标签均非空", entrances["label"].str.strip().ne("").all())
check("出入口输出 CRS = EPSG:4326", str(entrances.crs) == "EPSG:4326")
check("模块输出 CRS 常量 = EPSG:4326", module.OUTPUT_CRS == "EPSG:4326")

# ── 7) Mapbox 失败诚实停止（monkeypatch 请求失败，不碰 network）──
orig_call = module._call_isochrone
orig_token = module._mapbox_token
module._mapbox_token = lambda: "dummy-token"
def _fail_call(*_a, **_k):
    raise RuntimeError("mock mapbox failure")
module._call_isochrone = _fail_call
try:
    result = module.run_analysis(
        {"max_centers": 1}, Path(API_ROOT) / "app" / "workspace"
    )
    check("Mapbox 全失败 → success=False", result.get("success") is False)
    check("失败停止码", result.get("stop_code") == "STOP_ISOCHRONE_FAILED")
    check("失败无图层", not result.get("layers"))
    check("失败不使用演示覆盖率", "stable_rate" not in json.dumps(result, ensure_ascii=False))
finally:
    module._call_isochrone = orig_call
    module._mapbox_token = orig_token

# ── 8) Skill 契约与静态校验 ──
skill = STORE.get("commercial-rail-accessibility")
check("SkillStore 可发现", skill is not None and skill.kind == "executable")
validation = STORE.validate("commercial-rail-accessibility")
check("Skill 静态校验", not validation.errors and validation.syntax_ok and validation.protocol_ok, str(validation.errors))

print("ALL PASS")
