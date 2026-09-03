"""rail-network-reachability 聚焦验收：纯函数（聚簇/代表/面积守恒/覆盖率单调）、
路由（全区口径命中新 Skill、72D 场景 2 仍走 single-corridor）、全失败诚实停止。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

import geopandas as gpd  # noqa: E402
from shapely.geometry import Point, box  # noqa: E402

from app.agent.comprehensive_analysis import route_analysis_request  # noqa: E402
from app.skills.store import STORE  # noqa: E402

SKILL_DIR = API_ROOT / "app" / "skills" / "builtin" / "rail-network-reachability"
MAIN_PATH = SKILL_DIR / "scripts" / "main.py"


def load_module():
    spec = importlib.util.spec_from_file_location("rail_network_main", MAIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL {label}: {detail}")
    print(f"PASS {label}" + (f" | {detail}" if detail else ""))


module = load_module()

# 1) SkillStore 发现与静态契约
skill = STORE.get("rail-network-reachability")
check("SkillStore 可发现", skill is not None and skill.kind == "executable")
validation = STORE.validate("rail-network-reachability")
check("Skill 静态校验", not validation.errors and validation.syntax_ok and validation.protocol_ok, str(validation.errors))

# 2) 出入口聚簇（纯函数，确定性）
coords = [(0.0, 0.0), (0.0, 150.0), (0.0, 400.0), (1000.0, 1000.0)]
clusters = module.cluster_entrances(coords, 300.0)
check("300m 聚簇分组", len(clusters) == 2 and len(clusters[0]) == 3 and len(clusters[1]) == 1, str(clusters))
check("聚簇确定性（两次一致）", module.cluster_entrances(coords, 300.0) == clusters)
reps = module.representative_indices(clusters, coords)
check("代表为真实出入口", sorted(reps) == [1, 3], f"reps={reps}")
check("簇内含口数正确", {len(c) for c in clusters} == {1, 3})

# 3) 可达统计纯函数：覆盖率随分钟单调 + 面积守恒（合成几何，非路网）
boundary = box(0.0, 0.0, 2000.0, 2000.0)
fused = {
    5: box(300.0, 300.0, 700.0, 700.0),
    10: box(200.0, 200.0, 800.0, 800.0),
    15: box(100.0, 100.0, 900.0, 900.0),
}
commercial = gpd.GeoDataFrame(
    {"category": ["餐饮服务"] * 8},
    geometry=[
        Point(350, 350), Point(650, 350), Point(250, 250), Point(150, 150),
        Point(950, 950), Point(1100, 1100), Point(1200, 1200), Point(1999, 1999),
    ],
    crs="EPSG:32650",
)
stats = module.reachability_stats(boundary, fused, commercial, area_km2=4.0)
check("覆盖率随分钟单调", stats["bands"]["5"]["coverage_rate"] < stats["bands"]["10"]["coverage_rate"] < stats["bands"]["15"]["coverage_rate"])
check("累计商业点单调", stats["bands"]["5"]["cumulative_count"] <= stats["bands"]["10"]["cumulative_count"] <= stats["bands"]["15"]["cumulative_count"])
cons = stats["conservation"]
check("面积守恒（各带+难达=研究区）", abs(cons["sum"] - cons["study"]) < 0.1, f"{cons}")
check("15 分钟可及商业点", stats["bands"]["15"]["cumulative_count"] == 4)
check("难达区面积>0", stats["hard_to_reach_km2"] > 0)
check("≤15 覆盖分类构成存在", set(stats["total_by_category"].keys()) == {"餐饮服务"})

# 4) 路由：全区口径 → 新 Skill；72D 场景 2 仍走 single-corridor
net = route_analysis_request("请分析候选研究范围的全区地铁可达率与难达区")
check("全区可达路由新 Skill", net is not None and net.route_rule == "R-RAIL-NETWORK-REACHABILITY", str(getattr(net, "route_rule", None)))
scenario2 = route_analysis_request(
    "请分析候选研究范围内商业设施与地铁出入口的步行可达关系。"
    "地图显示地铁出入口点、分类商业 POI，以及选定出入口或出入口组的 5、10、15 分钟真实步行等时圈"
)
check("72D 场景 2 仍走 single-corridor", scenario2 is not None and scenario2.route_rule == "R-COMMERCIAL-RAIL-ACCESSIBILITY", str(getattr(scenario2, "route_rule", None)))

# 5) 全失败诚实停止语义（不调网络：直接验证 IsochroneAllFailed 存在与停止码路径）
check("全失败异常类存在", hasattr(module, "IsochroneAllFailed") and issubclass(module.IsochroneAllFailed, RuntimeError))

print("ALL PASS")
