"""population-workforce-profile 聚焦验收：坐标契约、纯函数、真实输入、失败语义与三次确定性。"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[2]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.data_registry import DATA_REGISTRY  # noqa: E402
from app.skills.store import STORE  # noqa: E402


SKILL_DIR = API_ROOT / "app" / "skills" / "builtin" / "population-workforce-profile"
MAIN_PATH = SKILL_DIR / "scripts" / "main.py"
META_PATH = SKILL_DIR / "skill.json"


def load_module():
    spec = importlib.util.spec_from_file_location("population_workforce_profile_main", MAIN_PATH)
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


def find_dim(structure: dict, code: str) -> dict:
    for dim in structure["dimensions"]:
        if dim["code"] == code:
            return dim
    raise AssertionError(f"dimension not found: {code}")


def cat_prop(dim: dict, label: str) -> float:
    for c in dim["categories"]:
        if c["label"] == label:
            return c["proportion"]
    raise AssertionError(f"category not found: {label}")


module = load_module()

# 1) 坐标契约：标准 GCJ02→WGS84（真实地面；与 _gcj02_to_wgs84.csv 残差 <1.3m）
w1 = module.gcj02_to_wgs84(114.11294773, 22.55949068)
check("GCJ02->WGS84 sample lon", abs(w1[0] - 114.10783863) < 1e-6, f"got {w1[0]}")
check("GCJ02->WGS84 sample lat", abs(w1[1] - 22.56217527) < 1e-6, f"got {w1[1]}")
check("GCJ02->WGS84 not identity (in China)", abs(w1[0] - 114.11294773) > 1e-3)
check("out_of_china guard", module._out_of_china(114.0, 22.0) is False)

# 2) 稳定网格（100m 全局锚）
check("stable_cell_id", module.stable_cell_id(199750.0, 2497550.0, 100) == "g_1997_24975")

# 3) SkillStore 发现和静态契约
skill = STORE.get("population-workforce-profile")
check("SkillStore discover", skill is not None and skill.kind == "executable")
validation = STORE.validate("population-workforce-profile")
check("skill static validation", not validation.errors and validation.syntax_ok and validation.protocol_ok, str(validation.errors))

# 4) 失败必须真实失败，且不注册图层
missing = STORE.run("population-workforce-profile", {"home_count_path": "missing-home.txt"})
missing_out = actual_output(missing)
check("missing home file fails", missing.get("success") is False and missing_out.get("stop_code") == "STOP_INVALID_INPUT")
check("missing home file no layer", not missing.get("layers") and not missing_out.get("layers"))

bad_grid = STORE.run("population-workforce-profile", {"cell_size_m": 500})
bad_grid_out = actual_output(bad_grid)
check("non-100m grid fails", bad_grid.get("success") is False and "100m" in str(bad_grid_out.get("summary")))

unknown_crs = STORE.run("population-workforce-profile", {"study_area_crs": ""})
unknown_crs_out = actual_output(unknown_crs)
check("CRS missing fails", unknown_crs.get("success") is False and "CRS" in str(unknown_crs_out.get("summary")))

# 5) 真实数据三次 SkillStore 集成运行
meta_before = META_PATH.read_bytes()
runs = [STORE.run("population-workforce-profile", {}) for _ in range(3)]
outputs = [actual_output(item) for item in runs]
check("three real runs succeed", all(item.get("success") is True for item in runs))
check(
    "three register real layer",
    all(item.get("output_layer_id") == "population_workforce_profile_100m" and item.get("layers") for item in runs),
)
fingerprints = [core_fingerprint(out.get("stats") or {}) for out in outputs]
check("three core stats identical", len(set(fingerprints)) == 1, fingerprints[0])
check("builtin meta not drifted", META_PATH.read_bytes() == meta_before)

out = outputs[0]
stats = out["stats"]
evidence = out["evidence"]
counts = stats["counts"]

# 数量与候选范围裁剪（坐标契约正确性的集成验证）
check("grid count", stats["grid_count"] == 2336)
check("date caliber", stats["date_caliber"] == "202604")
check("home source", counts["home_source_grids"] == 2751 and counts["home_source_population"] == 721048)
check("home in range", counts["home_in_range_grids"] == 2583 and counts["home_in_range_population"] == 701305)
check("work source", counts["work_source_grids"] == 2785 and counts["work_source_population"] == 515978)
check("work in range", counts["work_in_range_grids"] == 2622 and counts["work_in_range_population"] == 503632)
check("home/work cells", counts["home_cells"] == 2244 and counts["work_cells"] == 2273)

# 职住比
check("jobs/resident ratio", abs(stats["jobs_resident_ratio"] - 0.718135) < 1e-6, str(stats["jobs_resident_ratio"]))
check("jobs/resident per-cell median", abs(stats["jobs_resident_per_cell_median"] - 0.5695) < 1e-4, str(stats["jobs_resident_per_cell_median"]))

# 结构（人口加权条件分布 + 缺失比例）
home_struct = stats["structure"]["home"]
work_struct = stats["structure"]["work"]
check("home structure label", home_struct["label"] == "居住人群" and work_struct["label"] == "就业人群")
check("home profile join", home_struct["joined_grids"] == 2560 and home_struct["missing_grids"] == 23 and home_struct["missing_population"] == 25)
check("work profile join", work_struct["joined_grids"] == 2611 and work_struct["missing_grids"] == 11 and work_struct["missing_population"] == 11)

gender_home = find_dim(home_struct, "性别")
check("home gender male", abs(cat_prop(gender_home, "男") - 0.614310) < 1e-6)
check("home gender coverage", abs(gender_home["coverage"] - 0.999964) < 1e-6, str(gender_home["coverage"]))

edu_home = find_dim(home_struct, "教育水平")
edu_work = find_dim(work_struct, "教育水平")
check("home edu bachelor+", abs(cat_prop(edu_home, "本科及以上") - 0.097047) < 1e-6)
check("work edu bachelor+", abs(cat_prop(edu_work, "本科及以上") - 0.120059) < 1e-6)
check("home vs work edu difference", cat_prop(edu_work, "本科及以上") > cat_prop(edu_home, "本科及以上"))

age_home = find_dim(home_struct, "年龄阶段")
check("home age 25-34 dominant", abs(cat_prop(age_home, "25-34") - 0.530115) < 1e-6)

income_home = find_dim(home_struct, "收入水平")
check("home income missing ratio present", income_home["missing_ratio"] > 0, str(income_home["missing_ratio"]))

# 群体（居住口径）
groups = stats["groups"]
check("youth proportion", abs(groups["youth"]["proportion"] - 0.605819) < 1e-6)
check("youth estimate", groups["youth"]["estimate"] == 424864, str(groups["youth"]["estimate"]))
check("high_edu proportion", abs(groups["high_edu"]["proportion"] - 0.097044) < 1e-6)
check("high_edu estimate", groups["high_edu"]["estimate"] == 68057, str(groups["high_edu"]["estimate"]))
check("mid_high_income proportion", abs(groups["mid_high_income"]["proportion"] - 0.746526) < 1e-6)
check("mid_high_income estimate", groups["mid_high_income"]["estimate"] == 523543, str(groups["mid_high_income"]["estimate"]))
check("group median thresholds", abs(groups["youth"]["median_threshold"] - 109.3) < 0.1 and abs(groups["high_edu"]["median_threshold"] - 19.3) < 0.1 and abs(groups["mid_high_income"]["median_threshold"] - 154.3) < 0.1)
check("group high cells bounded", 1 <= groups["youth"]["high_cells"] < stats["grid_count"])

# 证据：坐标契约 / 口径 / 血缘 / CRS / 原始来源审计
check("input hashes complete", all(v.get("sha256") for v in evidence["input_versions"].values()))
check("coordinate contract present", "GCJ-02" in evidence["crs"]["coordinate_contract"] and "GCJ02→WGS84" in evidence["crs"]["coordinate_contract"])
check("CRS chain", evidence["crs"]["analysis"] == "EPSG:32650" and evidence["crs"]["output"] == "EPSG:4326")
check("population caliber", "估算" in evidence["population_caliber"] and "候选范围筛选后" in evidence["population_caliber"])
check("date caliber evidence", evidence["date_caliber"] == "202604")
audit = evidence["source_audit"]
for key in ("home_count", "work_count", "home_profile", "work_profile"):
    a = audit[key]
    check(f"audit {key} fields", all(k in a for k in ("original_file", "original_sha256", "used_fields")))
check("audit home count", audit["home_count"]["original_grid_count"] == 2751 and audit["home_count"]["in_range_population"] == 701305)
check("audit home profile missing", audit["home_profile"]["missing_grids"] == 23)

# 解释边界（坐标契约 / 日期口径 / 条件分布 / 不推断个人身份）
limits = evidence["limitations"]
check("not authoritative total", any("权威总量" in item or "不作为权威总量" in item for item in limits))
check("grid aggregate not survey", any("网格汇总条件分布" in item for item in limits))
check("no cross-date compare", any("不横比" in item for item in limits))
check("excluded bd09", any("bd09" in item for item in evidence["excluded_inputs"]))
check("excluded OD/commute", any("通勤" in item or "OD" in item for item in evidence["excluded_inputs"]))

# 结果图层
layer = DATA_REGISTRY.get_layer("population_workforce_profile_100m")
check("result layer registered", layer is not None and layer.feature_count == stats["grid_count"])
check("result layer CRS", layer is not None and layer.crs == "EPSG:4326")
layer_path = API_ROOT / "app" / "workspace" / "data" / "layers" / "population_workforce_profile_100m.json"
geojson = json.loads(layer_path.read_text(encoding="utf-8"))
check("geojson feature count", len(geojson.get("features") or []) == stats["grid_count"])
features = geojson["features"]
prop_keys = set(features[0]["properties"].keys())
check("layer fields", {"home_count", "work_count", "jobs_per_resident", "youth_resident_count", "high_edu_resident_count", "mid_high_income_resident_count"}.issubset(prop_keys))

print("REAL_STATS=" + json.dumps(stats, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
print("CORE_FINGERPRINT=" + fingerprints[0])
print("ALL PASS")
