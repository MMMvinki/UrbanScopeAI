"""commercial-supply-profile 聚焦验收：纯函数、真实输入、失败语义与三次确定性。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.data_registry import DATA_REGISTRY  # noqa: E402
from app.skills.store import STORE  # noqa: E402


SKILL_DIR = API_ROOT / "app" / "skills" / "builtin" / "commercial-supply-profile"
MAIN_PATH = SKILL_DIR / "scripts" / "main.py"
META_PATH = SKILL_DIR / "skill.json"


def load_module():
    spec = importlib.util.spec_from_file_location("commercial_supply_profile_main", MAIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(label: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"FAIL {label}: {detail}")
    print(f"PASS {label}" + (f" | {detail}" if detail else ""))


def actual_output(result: dict) -> dict:
    out = result.get("output") or {}
    return out if isinstance(out, dict) else {}


def core_fingerprint(stats: dict) -> str:
    raw = json.dumps(stats, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


module = load_module()

# 1) 稳定网格夹具
check("稳定网格使用全局原点", module.stable_cell_id(199850.0, 2497550.0) == "g_1998_24975")

# 2) SkillStore 发现和静态契约
skill = STORE.get("commercial-supply-profile")
check("SkillStore 可发现", skill is not None and skill.kind == "executable")
validation = STORE.validate("commercial-supply-profile")
check("Skill 静态校验", not validation.errors and validation.syntax_ok and validation.protocol_ok, str(validation.errors))

# 3) 失败必须真实失败，且不注册图层
missing = STORE.run("commercial-supply-profile", {"commercial_path": "missing-commercial.shp"})
missing_out = actual_output(missing)
check("缺文件返回失败", missing.get("success") is False and missing_out.get("stop_code") == "STOP_INVALID_INPUT")
check("缺文件无结果图层", not missing.get("layers") and not missing_out.get("layers"))

unknown_crs = STORE.run("commercial-supply-profile", {"commercial_crs": ""})
unknown_crs_out = actual_output(unknown_crs)
check("CRS 未提供返回失败", unknown_crs.get("success") is False and "CRS" in str(unknown_crs_out.get("summary")))

bad_grid = STORE.run("commercial-supply-profile", {"cell_size_m": 250})
bad_grid_out = actual_output(bad_grid)
check("非 100m 网格返回失败", bad_grid.get("success") is False and "100m" in str(bad_grid_out.get("summary")))

# 4) 真实数据三次 SkillStore 集成运行
meta_before = META_PATH.read_bytes()
runs = [STORE.run("commercial-supply-profile", {}) for _ in range(3)]
outputs = [actual_output(item) for item in runs]
check("三次真实运行均成功", all(item.get("success") is True for item in runs))
check("三次均注册真实图层", all(item.get("output_layer_id") == "commercial_supply_profile_100m" and item.get("layers") for item in runs))
fingerprints = [core_fingerprint(out.get("stats") or {}) for out in outputs]
check("三次核心统计完全一致", len(set(fingerprints)) == 1, fingerprints[0])
check("builtin 元数据运行后不漂移", META_PATH.read_bytes() == meta_before)

out = outputs[0]
stats = out["stats"]
evidence = out["evidence"]
inputs = stats["inputs"]
grid_count = stats["grid_count"]
supply_classes = stats["supply_classes"]

# 与 commercial-vitality 已验证商业口径一致的固定数字
check("网格数守恒", grid_count == 2498)
check("商业源与过滤数", inputs["commercial_source_records"] == 103659 and inputs["commercial_isdelete_0_records"] == 61197)
check("商业落区/入格数", inputs["commercial_points_in_boundary"] == 61186 and inputs["commercial_points_assigned_to_center_selected_grid"] == 61003)
check("商业聚合守恒", inputs["commercial_points_aggregated"] == stats["total_commercial_points"] == 61003)

# 阈值（正密度中位数）
check("商业密度阈值", abs(stats["thresholds"]["commercial_density_km2"] - 1500) < 0.01 and stats["thresholds"]["positive_supply_cells"] == 1651)

# 高低供给分类守恒与面积
high = supply_classes["high_supply"]
low = supply_classes["low_supply"]
check("高/低供给网格守恒", high["grid_count"] + low["grid_count"] == grid_count, f"H={high['grid_count']} L={low['grid_count']}")
check("高/低供给点位守恒", high["commercial_points"] + low["commercial_points"] == stats["total_commercial_points"])
check("高供给格数与点位", high["grid_count"] == 834 and high["commercial_points"] == 57395)
check("低供给格数与点位", low["grid_count"] == 1664 and low["commercial_points"] == 3608)
check("每类面积=网格数*0.01 km2", abs(high["area_km2"] - high["grid_count"] * 0.01) < 1e-9 and abs(low["area_km2"] - low["grid_count"] * 0.01) < 1e-9)
check("每类密度与点数一致", abs(high["mean_density_km2"] - high["commercial_points"] / (high["grid_count"] * 0.01)) < 1e-6)

# 数据就绪 / 血缘 / CRS
check("输入哈希齐全", all(v.get("sha256") for v in evidence["input_versions"].values()))
check("CRS 转换链齐全", evidence["crs"] == {
    "study_area_file": "EPSG:4326",
    "commercial_file": "EPSG:4547",
    "analysis": "EPSG:32650",
    "output": "EPSG:4326",
})

# 解释边界（不等于营业额 / 不判经营 / 不推消费等级）
limits = evidence["limitations"]
check("明确不等于营业额", any("营业额" in item for item in limits))
check("明确不判经营好坏", any("经营好坏" in item for item in limits))
check("明确不推消费等级", any("消费等级" in item for item in limits))
check("排除派生答案", "*_activity_type fields" in evidence["excluded_inputs"])

layer = DATA_REGISTRY.get_layer("commercial_supply_profile_100m")
check("结果图层已注册", layer is not None and layer.feature_count == grid_count)
check("结果图层 CRS 元数据正确", layer is not None and layer.crs == "EPSG:4326")
# 网格成果语义标记（D5-A）：分析网格层 kind=grid，UI 标签「栅格/网格」，仍按矢量取几何
check("网格图层语义 kind=grid", layer is not None and layer.kind == "grid")
layer_path = API_ROOT / "app" / "workspace" / "data" / "layers" / "commercial_supply_profile_100m.json"
geojson = json.loads(layer_path.read_text(encoding="utf-8"))
check("GeoJSON 要素数与统计一致", len(geojson.get("features") or []) == grid_count)

# 分类商业 POI 点图层（与网格同口径裁剪）
poi_layer = DATA_REGISTRY.get_layer("commercial_points_study")
check("分类 POI 图层已注册", poi_layer is not None and poi_layer.feature_count == 61186)
check("分类 POI 图层 CRS", poi_layer is not None and poi_layer.crs == "EPSG:4326")
poi_path = API_ROOT / "app" / "workspace" / "data" / "layers" / "commercial_points_study.json"
poi_geojson = json.loads(poi_path.read_text(encoding="utf-8"))
check("POI 图层要素数与统计一致", len(poi_geojson.get("features") or []) == 61186)
cats = stats["commercial_points_in_boundary_category"]
check("分类构成守恒", int(cats["total"]) == 61186 and sum(int(v) for v in cats["by_category"].values()) == 61186)
check(
    "四大类固定分类数",
    cats["by_category"]["购物服务"] == 30626
    and cats["by_category"]["餐饮服务"] == 15788
    and cats["by_category"]["生活服务"] == 12997
    and cats["by_category"]["体育休闲服务"] == 1775,
)

print("REAL_STATS=" + json.dumps(stats, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
print("CORE_FINGERPRINT=" + fingerprints[0])
print("ALL PASS")
