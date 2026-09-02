"""activity-temporal-profile 聚焦验收：纯函数、真实输入、失败语义与三次确定性。"""

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


SKILL_DIR = API_ROOT / "app" / "skills" / "builtin" / "activity-temporal-profile"
MAIN_PATH = SKILL_DIR / "scripts" / "main.py"
META_PATH = SKILL_DIR / "skill.json"


def load_module():
    spec = importlib.util.spec_from_file_location("activity_temporal_profile_main", MAIN_PATH)
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

# 1) 固定分类夹具（昼夜节律 / 阈值边界）
day = [False, True, False, True]
night = [True, False, False, True]
check(
    "昼夜节律固定夹具",
    [module.classify_day_night(d, n) for d, n in zip(day, night)]
    == ["D_L_N_H", "D_H_N_L", "D_L_N_L", "D_H_N_H"],
)
check("缺昼夜进入 ND", module.classify_day_night(None, True) == "ND")
check("稳定网格使用全局原点", module.stable_cell_id(199850.0, 2497550.0) == "g_1998_24975")

# 2) SkillStore 发现和静态契约
skill = STORE.get("activity-temporal-profile")
check("SkillStore 可发现", skill is not None and skill.kind == "executable")
validation = STORE.validate("activity-temporal-profile")
check("Skill 静态校验", not validation.errors and validation.syntax_ok and validation.protocol_ok, str(validation.errors))

# 3) 失败必须真实失败，且不注册图层
missing = STORE.run("activity-temporal-profile", {"vitality_path": "missing-activity.csv"})
missing_out = actual_output(missing)
check("缺文件返回失败", missing.get("success") is False and missing_out.get("stop_code") == "STOP_INVALID_INPUT")
check("缺文件无结果图层", not missing.get("layers") and not missing_out.get("layers"))

unknown_crs = STORE.run("activity-temporal-profile", {"vitality_crs": ""})
unknown_crs_out = actual_output(unknown_crs)
check("CRS 未提供返回失败", unknown_crs.get("success") is False and "CRS" in str(unknown_crs_out.get("summary")))

bad_grid = STORE.run("activity-temporal-profile", {"cell_size_m": 250})
bad_grid_out = actual_output(bad_grid)
check("非 100m 网格返回失败", bad_grid.get("success") is False and "100m" in str(bad_grid_out.get("summary")))

with tempfile.TemporaryDirectory(prefix="activity_profile_test_") as tmp:
    bad_csv = Path(tmp) / "missing-night.csv"
    bad_csv.write_text(
        "grid_id,lon,lat,coord_fix,all_day_09_18_avg\n"
        "g1,114.08,22.55,gcj02_to_wgs84,1\n",
        encoding="utf-8",
    )
    missing_field = STORE.run("activity-temporal-profile", {"vitality_path": str(bad_csv)})
    missing_field_out = actual_output(missing_field)
    check("缺字段返回失败", missing_field.get("success") is False and "缺少必需字段" in str(missing_field_out.get("summary")))
    check("缺字段无结果图层", not missing_field.get("layers") and not missing_field_out.get("layers"))

# 4) 真实数据三次 SkillStore 集成运行
meta_before = META_PATH.read_bytes()
runs = [STORE.run("activity-temporal-profile", {}) for _ in range(3)]
outputs = [actual_output(item) for item in runs]
check("三次真实运行均成功", all(item.get("success") is True for item in runs))
check("三次均注册真实图层", all(item.get("output_layer_id") == "activity_temporal_profile_100m_all" and item.get("layers") for item in runs))
fingerprints = [core_fingerprint(out.get("stats") or {}) for out in outputs]
check("三次核心统计完全一致", len(set(fingerprints)) == 1, fingerprints[0])
check("builtin 元数据运行后不漂移", META_PATH.read_bytes() == meta_before)

out = outputs[0]
stats = out["stats"]
evidence = out["evidence"]
inputs = stats["inputs"]
grid_count = stats["grid_count"]
rhythm_counts = stats["day_night_types"]

# 与 commercial-vitality 已验证活动/网格口径一致的固定数字
check("网格数与有效/无数据格守恒", grid_count == 2498 and stats["valid_cells"] == 2473 and stats["nd_cells"] == 25)
check("有效格+无数据格=网格总数", stats["valid_cells"] + stats["nd_cells"] == grid_count)
check("活动源行数", inputs["activity_source_records"] == 3182)
check("活动落区/入格数", inputs["activity_points_in_boundary"] == 2921 and inputs["activity_points_assigned_to_center_selected_grid"] == 2864)
check("活动聚合网格数", inputs["activity_aggregated_grid_count"] == 2473)

# 昼夜节律分类与 commercial-vitality 的 day_night_type 完全一致
expected_rhythm = {"D_H_N_L": 67, "D_L_N_H": 67, "D_H_N_H": 1170, "D_L_N_L": 1169, "ND": 25}
check(
    "五类节律数量与面积",
    all(rhythm_counts[k]["grid_count"] == v for k, v in expected_rhythm.items())
    and sum(rhythm_counts[k]["grid_count"] for k in expected_rhythm) == grid_count,
    str({k: rhythm_counts[k]["grid_count"] for k in expected_rhythm}),
)
check("每类面积=网格数*0.01 km2", all(
    abs(rhythm_counts[k]["area_km2"] - rhythm_counts[k]["grid_count"] * 0.01) < 1e-9 for k in expected_rhythm
))

# 阈值（与 commercial-vitality 全样本阈值一致，容忍显示舍入）
day_thr = stats["thresholds"]["day_activity"]
night_thr = stats["thresholds"]["night_activity"]
check("昼/夜阈值与已验证口径一致", abs(day_thr - 240.33) < 0.01 and abs(night_thr - 212.25) < 0.01, f"{day_thr}/{night_thr}")
check("昼/夜有效格一致", stats["thresholds"]["day_valid_cells"] == stats["thresholds"]["night_valid_cells"] == 2473)

# 数据就绪 / 血缘 / CRS
check("输入哈希齐全", all(v.get("sha256") for v in evidence["input_versions"].values()))
check("CRS 转换链齐全", evidence["crs"] == {
    "study_area_file": "EPSG:4326",
    "activity_assumption": "EPSG:4326",
    "analysis": "EPSG:32650",
    "output": "EPSG:4326",
})

# 解释边界（活动不是人口 / 不可推导人群 / 不判品质 / 候选坐标）
limits = evidence["limitations"]
check("明确活动不是人口", any("不是人口" in item for item in limits))
check("明确不可推导人群", any("年龄" in item and "收入" in item for item in limits))
check("明确不判空间品质", any("空间品质" in item for item in limits))
check("明确候选坐标版本", any("候选" in item and "gcj02_to_wgs84" in item for item in limits))
check("排除派生答案", "*_activity_type fields" in evidence["excluded_inputs"])

layer = DATA_REGISTRY.get_layer("activity_temporal_profile_100m_all")
check("结果图层已注册", layer is not None and layer.feature_count == grid_count)
check("结果图层 CRS 元数据正确", layer is not None and layer.crs == "EPSG:4326")
layer_path = API_ROOT / "app" / "workspace" / "data" / "layers" / "activity_temporal_profile_100m_all.json"
geojson = json.loads(layer_path.read_text(encoding="utf-8"))
check("GeoJSON 要素数与统计一致", len(geojson.get("features") or []) == grid_count)

# 5) wd / we 冒烟（需求要求支持工作日/周末）
for scope in ("wd", "we"):
    r = STORE.run("activity-temporal-profile", {"time_scope": scope})
    r_out = actual_output(r)
    r_stats = r_out.get("stats") or {}
    check(f"{scope} 冒烟成功", r.get("success") is True and r_stats.get("grid_count") == 2498)
    check(f"{scope} 图层已注册", r.get("output_layer_id") == f"activity_temporal_profile_100m_{scope}")
    check(f"{scope} 昼夜字段来自同一组", r_stats.get("time_fields") == {
        "day": f"{scope}_day_09_18_avg", "night": f"{scope}_night_19_22_avg"
    })

print("REAL_STATS=" + json.dumps(stats, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
print("CORE_FINGERPRINT=" + fingerprints[0])
print("ALL PASS")
