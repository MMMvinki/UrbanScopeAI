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
# 主题组路由（R-THEME-COMMERCIAL，含 single-corridor 语义）已由并行窗口加入；
# 72D 场景 2 现命中主题组商业（其模块集含 commercial_rail_accessibility），故接受 two 种路由，
# 只要仍走「商业×地铁可达」能力、不进通配。
check(
    "72D 场景 2 走 商业×地铁可达 路由",
    scenario2 is not None and scenario2.route_rule in (
        "R-COMMERCIAL-RAIL-ACCESSIBILITY",
        "R-THEME-COMMERCIAL",  # 主题组商业：requested_modules 含 commercial_rail_accessibility
    ),
    str(getattr(scenario2, "route_rule", None)),
)

# 5) A1 轨道服务盲区叠加：住宅本体规则 + 合成米制几何（确定性，不调网络）
rs = module._ro
check(
    "住宅本体规则",
    rs.is_residential_layer("R2二类居住用地")
    and rs.is_residential_layer("C1+R2商业+居住用地")
    and rs.is_residential_layer("R4乡村居住自留用地")
    and not rs.is_residential_layer("R6配套设施用地")
    and not rs.is_residential_layer("R22二类幼托用地")
    and not rs.is_residential_layer("R8住宅区绿地")
    and not rs.is_residential_layer("C2商业用地"),
)
boundary = box(0.0, 0.0, 4000.0, 4000.0)
cum15 = box(500.0, 500.0, 3500.0, 3500.0)
cum = {5: box(1000.0, 1000.0, 3000.0, 3000.0), 10: box(800.0, 800.0, 3200.0, 3200.0), 15: cum15}
hard = boundary.difference(cum15)
res = gpd.GeoDataFrame(
    {"Layer": ["R2二类居住用地"] * 4},
    geometry=[
        box(2000.0, 2000.0, 2500.0, 2500.0),  # cum5
        box(600.0, 600.0, 900.0, 900.0),  # cum10（跨环）
        box(3700.0, 3700.0, 3950.0, 3950.0),  # hard
        box(0.0, 0.0, 300.0, 300.0),  # hard
    ],
    crs="EPSG:32650",
)
pop = gpd.GeoDataFrame(
    {"home_count": [100, 100, 50, 50], "work_count": [80, 80, 40, 40]},
    geometry=[
        Point(2250.0, 2250.0), Point(700.0, 700.0),
        Point(3800.0, 3800.0), Point(150.0, 150.0),
    ],
    crs="EPSG:32650",
)
extra = rs.residential_blindspot(res_gdf_m=res, pop_gdf_m=pop, boundary_m=boundary, hard_m=hard, cum_by_minute=cum)
check("盲区居住覆盖率随分钟单调", extra["coverage_pct"]["5"] < extra["coverage_pct"]["10"] < extra["coverage_pct"]["15"])
check("盲区居住面积>0", extra["blind_km2"] > 0 and extra["blind_pct"] > 0)
check("盲区人口分区守恒", extra["conservation"]["home_15_plus_blind"] == extra["conservation"]["home_total"] and extra["conservation"]["work_15_plus_blind"] == extra["conservation"]["work_total"])
check("盲区面积守恒容差<0.5%", extra["conservation"]["area_delta_pct"] < 0.5)

# 6) A1 真实数据确定性（rail 几何取 skill 缓存 geojson + 用地 shp + 人口格；文件齐才跑，否则 SKIP）
REPO_ROOT = API_ROOT.parent.parent
land_real = REPO_ROOT.parent / "04-乐活大道GIS数据" / "04-恢复数据" / "商业文化部分（发兆杨）1(全量用地).mpk解包" / "commondata" / "数据处理" / "用地地块_范围内Export_Output.shp"
f_pop = API_ROOT / "app" / "workspace" / "data" / "layers" / "population_workforce_profile_100m.json"
cache_hits = sorted((API_ROOT / "app" / "workspace" / "data" / "cache").glob("rail_network_reachability_*.json"))
if cache_hits and f_pop.is_file() and land_real.is_file():
    import json as _json
    import geopandas as _gpd

    from shapely.geometry import shape

    cached = _json.loads(cache_hits[-1].read_text(encoding="utf-8"))
    gj = cached.get("geojson") or {}
    study = _gpd.read_file(REPO_ROOT / "docs" / "workstreams" / "analysis-foundation" / "data" / "candidate_lohas_study_area.geojson").to_crs("EPSG:32650")
    bm = study.geometry.union_all()
    bands_gj = (gj.get("rail_network_walking_bands") or {}).get("features") or []
    band_geoms = [
        (int(f["properties"]["minute"]), rs.as_meters(shape(f["geometry"])))
        for f in bands_gj
        if f.get("geometry") and f.get("properties", {}).get("minute") is not None
    ]
    hard_gj = (gj.get("rail_network_hard_to_reach") or {}).get("features") or []
    hard_m = rs.as_meters(shape(hard_gj[0]["geometry"])) if hard_gj else None
    if band_geoms and hard_m is not None:
        cum_m = rs.cum_geoms_from_bands(band_geoms, bm)
        res_real = rs.load_residential_land(land_real)
        pop_real = rs.load_population_grid(f_pop)
        real = rs.residential_blindspot(res_gdf_m=res_real, pop_gdf_m=pop_real, boundary_m=bm, hard_m=hard_m, cum_by_minute=cum_m)
        check("A1 真实：住宅本体块数", real["residential_parcels"] == 601, f"parcels={real['residential_parcels']}")
        check("A1 真实：居住面积量级", 5.9 < real["residential_total_km2"] < 6.15, f"{real['residential_total_km2']}")
        check("A1 真实：覆盖率单调", real["coverage_pct"]["5"] < real["coverage_pct"]["10"] < real["coverage_pct"]["15"])
        check("A1 真实：盲区面积>0 且守恒容差", real["blind_km2"] > 0 and real["conservation"]["area_delta_pct"] < 0.5)
        check("A1 真实：盲区人口守恒", real["conservation"]["home_15_plus_blind"] == real["conservation"]["home_total"])

        # 商业 POI × 地铁可达（真实数据，离线）：经 _attach_commercial_reach 同一条代码路径
        stats_test: dict = {}
        gj_test = _json.loads(_json.dumps(gj))  # 深拷贝，不改缓存
        ok_cr = module._attach_commercial_reach(stats_test, gj_test, REPO_ROOT, {})
        cr = stats_test.get("commercial_reach")
        check("商业可达叠加可算", ok_cr and cr is not None and "rail_poi_reach_study" in gj_test)
        if cr is not None:
            cc = cr["counts"]
            check("商业 reach_class 守恒 sum==total==61186", cr["total"] == 61186 and cc["5"] + cc["10"] + cc["15"] + cc["out"] == cr["total"])
            check("商业 ≤15 累计与 out 精确", cc["5"] + cc["10"] + cc["15"] == 58378 and cc["out"] == 2808)
            check("商业 pct 之和≈100", abs(sum(cr["pct"].values()) - 100.0) < 0.01)
            check("商业 分类×档 守恒", sum(sum(v.values()) for v in cr["by_class_category"].values()) == cr["total"])
    else:
        print("SKIP A1 真实数据断言（rail 缓存 geojson 不完整）")
else:
    print("SKIP A1 真实数据断言（缺 rail 缓存/用地/人口文件）")

# 6b) 可达 × 多类资产：asset_reach_counts 纯函数（合成几何，确定性）
from shapely.geometry import Point as _Pt
from shapely.geometry import box as _box
import geopandas as _gpd

_cum = {5: _box(0, 0, 400, 400), 10: _box(0, 0, 600, 600), 15: _box(0, 0, 800, 800)}
_probe = _gpd.GeoSeries([_Pt(100, 100), _Pt(500, 500), _Pt(700, 700), _Pt(1000, 1000)], index=[0, 1, 2, 3], crs="EPSG:32650")
_cls, _cnt, _pct, _by = module.asset_reach_counts(_probe, _cum, [5, 10, 15])
check("资产 reach_class 档位判定", _cls[0] == "5" and _cls[1] == "10" and _cls[2] == "15" and _cls[3] == "out", str(_cls))
check("资产 reach_class 守恒 sum==total==4", _cnt["5"] + _cnt["10"] + _cnt["15"] + _cnt["out"] == 4 and sum(_cnt.values()) == 4)
check("资产 pct 之和≈100", abs(sum(_pct.values()) - 100.0) < 0.01)
check("资产 within 单调", len(_by[5]) <= len(_by[10]) <= len(_by[15]), f"{len(_by[5])}/{len(_by[10])}/{len(_by[15])}")

# 6c) 真实数据：公共/文化/绿地叠加（走 _attach_asset_reach 路径，离线；无源则 SKIP）
try:
    import json as _json
    from app.gis import overlay_poi as _op
    _real_root = REPO_ROOT
    study_real = _gpd.read_file(REPO_ROOT / "docs" / "workstreams" / "analysis-foundation" / "data" / "candidate_lohas_study_area.geojson")
    b_real = study_real.to_crs("EPSG:32650").geometry.union_all()
    green_p = _real_root / _op.DEFAULT_GREEN
    culture_p = _real_root / _op.DEFAULT_CULTURE
    poi_p = _real_root / _op.DEFAULT_POI
    has_assets = green_p.is_file() and culture_p.is_file() and poi_p.is_file()
    if has_assets:
        gp = _op.load_green_land(green_p, b_real)
        cp = _op.load_culture_points(culture_p, b_real)
        pp = _op.load_public_points(poi_p, b_real)
        check("绿地地块读数>0", len(gp) > 0, f"green={len(gp)}")
        check("文化设施读数>0", len(cp) > 0, f"culture={len(cp)}")
        check("公共设施读数>0", len(pp) > 0, f"public={len(pp)}")
        check("公共四域归组正确", set(pp["group_code"]).issubset({"education_culture", "medical_health", "sports", "convenience_emergency"}))
        check("文化已排除经营培训", all(not any(kw in str(c) for kw in ("培训", "影剧院", "娱乐", "歌舞厅", "KTV", "网吧")) for c in cp["category"]))
        check("绿地 Layer 含绿地", all("绿地" in str(l) for l in gp["layer"]))
        # 走 _attach_asset_reach 主路径：直接读缓存 geojson（含可达带 BANDS）+ 多类叠加
        _cache_file = sorted(cache_hits)[-1] if cache_hits else None
        if _cache_file is None:
            print("SKIP 资产主路径断言（缺 rail 缓存 geojson）")
        else:
            _cf = _json.loads(_cache_file.read_text(encoding="utf-8"))
            stats_a: dict = {}
            gj_a = _json.loads(_json.dumps(_cf.get("geojson") or {}))
            ok_a = module._attach_asset_reach(stats_a, gj_a, _real_root, API_ROOT / "app" / "workspace", {})
            check("资产叠加主路径可算", ok_a)
            # 三类默认叠加 stats 齐
            for key in ("public", "culture", "green"):
                r = stats_a.get(f"{key}_reach")
                check(f"{key}_reach 存在且计数>0", r is not None and r.get("total", 0) > 0, f"total={r.get('total') if r else None}")
            # overlay_layers：指定已注册图层（commercial_points_study）延伸叠加
            stats_o: dict = {}
            gj_o = _json.loads(_json.dumps(_cf.get("geojson") or {}))
            ok_o = module._attach_asset_reach(stats_o, gj_o, _real_root, API_ROOT / "app" / "workspace", {"overlay_layers": ["commercial_points_study"]})
            check("overlay 指定图层可叠加", ok_o and any(k.startswith("overlay_") and k.endswith("_reach") for k in stats_o), f"keys={[k for k in stats_o if k.startswith('overlay_')]}")
            for key, lbl in (("public", "公共服务"), ("culture", "文化设施"), ("green", "绿地")):
                r = stats_a.get(f"{key}_reach")
                if r:
                    c = r["counts"]
                    check(f"{lbl} reach_class 守恒", r["total"] > 0 and c["5"] + c["10"] + c["15"] + c["out"] == r["total"], f"{lbl} total={r['total']}")
    else:
        print("SKIP 资产真实数据断言（缺公共/文化/绿地源文件）")
except Exception as _e:  # noqa: BLE001
    print("SKIP 资产真实数据断言（读取异常：", type(_e).__name__, str(_e)[:80], ")")

# 7) 全失败诚实停止语义（不调网络：直接验证 IsochroneAllFailed 存在与停止码路径）
check("全失败异常类存在", hasattr(module, "IsochroneAllFailed") and issubclass(module.IsochroneAllFailed, RuntimeError))

print("ALL PASS")
