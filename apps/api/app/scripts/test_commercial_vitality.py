"""commercial-vitality 聚焦验收：纯函数、真实输入、失败语义与三次确定性。"""

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


SKILL_DIR = API_ROOT / "app" / "skills" / "builtin" / "commercial-vitality"
MAIN_PATH = SKILL_DIR / "scripts" / "main.py"
META_PATH = SKILL_DIR / "skill.json"


def load_module():
    spec = importlib.util.spec_from_file_location("commercial_vitality_main", MAIN_PATH)
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

# 1) 固定分类夹具（A-04 / 阈值边界）
supply = [False, False, True, True]
day = [False, True, False, True]
night = [True, False, False, True]
check(
    "昼间四象限固定夹具",
    [module.classify_supply_vitality(s, v) for s, v in zip(supply, day)]
    == ["S_L_V_L", "S_L_V_H", "S_H_V_L", "S_H_V_H"],
)
check(
    "夜间四象限固定夹具",
    [module.classify_supply_vitality(s, v) for s, v in zip(supply, night)]
    == ["S_L_V_H", "S_L_V_L", "S_H_V_L", "S_H_V_H"],
)
check(
    "昼夜节律固定夹具",
    [module.classify_day_night(d, n) for d, n in zip(day, night)]
    == ["D_L_N_H", "D_H_N_L", "D_L_N_L", "D_H_N_H"],
)
check("缺活力进入 ND", module.classify_supply_vitality(True, None) == "ND")
check("稳定网格使用全局原点", module.stable_cell_id(199850.0, 2497550.0) == "g_1998_24975")

# 2) SkillStore 发现和静态契约
skill = STORE.get("commercial-vitality")
check("SkillStore 可发现", skill is not None and skill.kind == "executable")
validation = STORE.validate("commercial-vitality")
check("Skill 静态校验", not validation.errors and validation.syntax_ok and validation.protocol_ok, str(validation.errors))
source_text = MAIN_PATH.read_text(encoding="utf-8")
check("主场景不引用 stable_rate", "stable_rate" not in source_text)

# 3) 失败必须真实失败，且不注册图层
missing = STORE.run("commercial-vitality", {"vitality_path": "missing-vitality.csv"})
missing_out = actual_output(missing)
check("缺文件返回失败", missing.get("success") is False and missing_out.get("stop_code") == "STOP_INVALID_INPUT")
check("缺文件无结果图层", not missing.get("layers") and not missing_out.get("layers"))

unknown_crs = STORE.run("commercial-vitality", {"commercial_crs": ""})
unknown_crs_out = actual_output(unknown_crs)
check("CRS 未提供返回失败", unknown_crs.get("success") is False and "CRS" in str(unknown_crs_out.get("summary")))
check("CRS 失败无结果图层", not unknown_crs.get("layers") and not unknown_crs_out.get("layers"))

bad_grid = STORE.run("commercial-vitality", {"cell_size_m": 250})
bad_grid_out = actual_output(bad_grid)
check("非 100m 网格返回失败", bad_grid.get("success") is False and "100m" in str(bad_grid_out.get("summary")))

with tempfile.TemporaryDirectory(prefix="commercial_vitality_test_") as tmp:
    bad_csv = Path(tmp) / "missing-night.csv"
    bad_csv.write_text(
        "grid_id,lon,lat,coord_fix,all_day_09_18_avg\n"
        "g1,114.08,22.55,gcj02_to_wgs84,1\n",
        encoding="utf-8",
    )
    missing_field = STORE.run("commercial-vitality", {"vitality_path": str(bad_csv)})
    missing_field_out = actual_output(missing_field)
    check("缺字段返回失败", missing_field.get("success") is False and "缺少必需字段" in str(missing_field_out.get("summary")))
    check("缺字段无结果图层", not missing_field.get("layers") and not missing_field_out.get("layers"))

# 4) 真实数据三次 SkillStore 集成运行
meta_before = META_PATH.read_bytes()
runs = [STORE.run("commercial-vitality", {}) for _ in range(3)]
outputs = [actual_output(item) for item in runs]
check("三次真实运行均成功", all(item.get("success") is True for item in runs))
check("三次均注册真实图层", all(item.get("output_layer_id") == "commercial_vitality_100m_all" and item.get("layers") for item in runs))
fingerprints = [core_fingerprint(out.get("stats") or {}) for out in outputs]
check("三次核心统计完全一致", len(set(fingerprints)) == 1, fingerprints[0])
check("builtin 元数据运行后不漂移", META_PATH.read_bytes() == meta_before)

out = outputs[0]
stats = out["stats"]
evidence = out["evidence"]
inputs = stats["inputs"]
grid_count = stats["grid_count"]
day_counts = stats["supply_day_quadrants"]
night_counts = stats["supply_night_quadrants"]
rhythm_counts = stats["day_night_types"]

# A-01 数据就绪 / 血缘
check("A-01 三项输入哈希齐全", all(v.get("sha256") for v in evidence["input_versions"].values()))
check("A-01 CRS 转换链齐全", evidence["crs"] == {
    "study_area_file": "EPSG:4326",
    "commercial_file": "EPSG:4547",
    "vitality_assumption": "EPSG:4326",
    "analysis": "EPSG:32650",
    "output": "EPSG:4326",
})
check("A-01 候选坐标限制被记录", any("candidate" in item for item in evidence["limitations"]))

# A-02 商业供给
check("A-02 商业源与过滤数", inputs["commercial_source_records"] == 103659 and inputs["commercial_isdelete_0_records"] == 61197)
check("A-02 商业聚合守恒", inputs["commercial_points_aggregated"] == sum(v["commercial_points"] for v in day_counts.values()))
check("A-02 商业阈值来自正密度网格", stats["thresholds"]["positive_supply_cells"] >= 10)

# A-03 活力描述
check("A-03 活力候选行数和落区数", inputs["vitality_source_records"] == 3182 and inputs["vitality_points_in_boundary"] == 2921)
check("A-03 昼夜字段来自同一 time_scope", stats["time_scope"] == "all" and stats["time_fields"] == {
    "day": "all_day_09_18_avg", "night": "all_night_19_22_avg"
})
check("A-03 昼夜有效格一致", stats["thresholds"]["day_valid_cells"] == stats["thresholds"]["night_valid_cells"])

# A-04 分类守恒、地图与数字同源
check("A-04 昼间分类守恒", sum(v["grid_count"] for v in day_counts.values()) == grid_count)
check("A-04 夜间分类守恒", sum(v["grid_count"] for v in night_counts.values()) == grid_count)
check("A-04 昼夜节律守恒", sum(v["grid_count"] for v in rhythm_counts.values()) == grid_count)
check("A-04 优先核查格可定位", bool(stats["priority_cells"]["day_high_vitality_low_supply"]) and bool(stats["priority_cells"]["night_high_vitality_low_supply"]))

# C-05 / C-06 克制解释与答案泄露边界
check("C-05 明确非因果", any("not causation" in item for item in evidence["limitations"]))
check("C-06 排除派生答案", "*_activity_type fields" in evidence["excluded_inputs"] and "historical supply-demand layers" in evidence["excluded_inputs"])

layer = DATA_REGISTRY.get_layer("commercial_vitality_100m_all")
check("结果图层已注册", layer is not None and layer.feature_count == grid_count)
check("结果图层 CRS 元数据正确", layer is not None and layer.crs == "EPSG:4326")
layer_path = API_ROOT / "app" / "workspace" / "data" / "layers" / "commercial_vitality_100m_all.json"
geojson = json.loads(layer_path.read_text(encoding="utf-8"))
check("GeoJSON 要素数与统计一致", len(geojson.get("features") or []) == grid_count)

print("REAL_STATS=" + json.dumps(stats, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
print("CORE_FINGERPRINT=" + fingerprints[0])
print("ALL PASS")
