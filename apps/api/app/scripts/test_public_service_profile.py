"""public-service-profile 聚焦验收：纯函数、真实输入、失败语义与三次确定性。"""

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


SKILL_DIR = API_ROOT / "app" / "skills" / "builtin" / "public-service-profile"
MAIN_PATH = SKILL_DIR / "scripts" / "main.py"
META_PATH = SKILL_DIR / "skill.json"


def load_module():
    spec = importlib.util.spec_from_file_location("public_service_profile_main", MAIN_PATH)
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

# 1) 稳定网格夹具（100m 全局锚，与其他板块同原点）
check("稳定网格使用全局原点", module.stable_cell_id(199750.0, 2497550.0, 100) == "g_1997_24975")

# 2) SkillStore 发现和静态契约
skill = STORE.get("public-service-profile")
check("SkillStore 可发现", skill is not None and skill.kind == "executable")
validation = STORE.validate("public-service-profile")
check("Skill 静态校验", not validation.errors and validation.syntax_ok and validation.protocol_ok, str(validation.errors))

# 3) 失败必须真实失败，且不注册图层
missing = STORE.run("public-service-profile", {"poi_path": "missing-poi.csv"})
missing_out = actual_output(missing)
check("缺主表文件返回失败", missing.get("success") is False and missing_out.get("stop_code") == "STOP_INVALID_INPUT")
check("缺主表文件无结果图层", not missing.get("layers") and not missing_out.get("layers"))

unknown_crs = STORE.run("public-service-profile", {"study_area_crs": ""})
unknown_crs_out = actual_output(unknown_crs)
check("CRS 未提供返回失败", unknown_crs.get("success") is False and "CRS" in str(unknown_crs_out.get("summary")))

bad_grid = STORE.run("public-service-profile", {"cell_size_m": 250})
bad_grid_out = actual_output(bad_grid)
check("非 100m 网格返回失败", bad_grid.get("success") is False and "100m" in str(bad_grid_out.get("summary")))

# 4) 真实数据三次 SkillStore 集成运行
meta_before = META_PATH.read_bytes()
runs = [STORE.run("public-service-profile", {}) for _ in range(3)]
outputs = [actual_output(item) for item in runs]
check("三次真实运行均成功", all(item.get("success") is True for item in runs))
check("三次均注册真实图层", all(item.get("output_layer_id") == "public_service_profile_100m" and item.get("layers") for item in runs))
fingerprints = [core_fingerprint(out.get("stats") or {}) for out in outputs]
check("三次核心统计完全一致", len(set(fingerprints)) == 1, fingerprints[0])
check("builtin 元数据运行后不漂移", META_PATH.read_bytes() == meta_before)

out = outputs[0]
stats = out["stats"]
evidence = out["evidence"]
counts = stats["counts"]
grid_count = stats["grid_count"]
groups = stats["groups"]
grids = stats["grids"]

# 四源范围核验与纳入/排除守恒（主表为原始总表）
check("网格数守恒", grid_count == 2498)
check("主表原始记录数与四域记录数", counts["source_records"] == 944688 and counts["domain_source_records"] == 83711)
check("范围内原始点守恒", counts["inside_study"] == 4484)
check("纳入+排除守恒", counts["included"] + counts["excluded"] == counts["inside_study"])
check("纳入数与排除数", counts["included"] == 1839 and counts["excluded"] == 2645)
check("入格设施点", counts["assigned_to_grid"] == 1830)

# 四大功能组数量
check("教育文化数量", groups["education_culture"]["count"] == 243)
check("医疗健康数量", groups["medical_health"]["count"] == 400)
check("体育活动数量", groups["sports"]["count"] == 456)
check("便民与应急数量", groups["convenience_emergency"]["count"] == 740)
check("各类总和=纳入", sum(v["count"] for v in groups.values()) == counts["included"])

# 网格相对分型与丰富度（D3 100m 重算口径）
check("无设施/资源较少/较多守恒", grids["no_facility_cells"] + grids["resource_low_cells"] + grids["resource_high_cells"] == grid_count)
check("无设施格数", grids["no_facility_cells"] == 1656)
check("资源相对较少/较多格数", grids["resource_low_cells"] == 424 and grids["resource_high_cells"] == 418)
check("相对分界中位数", grids["relative_median"] == 1)
rich = grids["type_richness_cells"]
check("丰富度守恒", rich["cells_0_types"] + rich["cells_1_types"] + rich["cells_2_types"] + rich["cells_3_types"] + rich["cells_4_types"] == grid_count)
check("丰富度分布", rich["cells_1_types"] == 579 and rich["cells_2_types"] == 215 and rich["cells_3_types"] == 44 and rich["cells_4_types"] == 4)

# D3 密度平滑多档（图层 applied 色带同源）
db = stats.get("density_bands") or {}
check(
    "density_bands 单色渐变同源",
    db.get("field") == "facility_density_km2"
    and db.get("classes") == 6
    and (db.get("palette") or [])[0] == "#ecfdf5"
    and sum(int(b.get("count", 0)) for b in db.get("bands", [])) == grid_count,
)

# 类别审计：明确排除清单出现在输出
excluded_labels = [e["label"] for e in stats["excluded"]]
check("排除-一般培训机构", any("培训机构" in label for label in excluded_labels))
check("排除-娱乐消费场所", any("酒吧" in label for label in excluded_labels))
check("排除-药店与医疗用品销售", any("药店" in label for label in excluded_labels))
check("排除-医疗美容/宠物医疗", any("医疗美容" in label for label in excluded_labels) and any("宠物医疗" in label for label in excluded_labels))

# 数据就绪 / 血缘 / CRS / 原始来源审计
check("输入哈希齐全", all(v.get("sha256") for v in evidence["input_versions"].values()))
check("CRS 转换链齐全", evidence["crs"]["analysis"] == "EPSG:32650" and evidence["crs"]["output"] == "EPSG:4326" and evidence["crs"]["poi"] == "EPSG:4326")
audit = evidence["source_audit"]
check("原始来源审计字段齐全", all(k in audit for k in ("original_file", "original_sha256", "used_fields", "classification_rule", "original_record_count", "domain_source_records", "in_range_records", "included", "excluded")))
check("原始来源审计数值", audit["original_record_count"] == 944688 and audit["in_range_records"] == 4484 and audit["included"] == 1839 and audit["excluded"] == 2645)
check("默认不做切片核对", evidence["crosscheck"]["enabled"] is False)

# 解释边界（不推断公办民办 / 不做覆盖容量 / 相对而非短缺）
limits = evidence["limitations"]
check("不推断公办民办", any("公办" in item and "民办" in item for item in limits))
check("明确不是短缺/缺口", any("短缺" in item or "缺口" in item for item in limits))
check("不做生活圈/可达/供需", any("15 分钟生活圈" in item for item in limits))
check("排除历史结论与产权推断", any("产权" in item for item in evidence["excluded_inputs"]))

layer = DATA_REGISTRY.get_layer("public_service_profile_100m")
check("结果图层已注册", layer is not None and layer.feature_count == grid_count)
check("结果图层 CRS 元数据正确", layer is not None and layer.crs == "EPSG:4326")
check("网格图层语义 kind=grid", layer is not None and layer.kind == "grid")
# C6：四功能组设施点图层导出并注册
pts_layer = DATA_REGISTRY.get_layer("public_service_points_study")
check("四功能组点图层已注册", pts_layer is not None and pts_layer.geometry.value == "Point")
check("点图层要素数=纳入数", pts_layer is not None and pts_layer.feature_count == counts["included"])
layer_path = API_ROOT / "app" / "workspace" / "data" / "layers" / "public_service_profile_100m.json"
geojson = json.loads(layer_path.read_text(encoding="utf-8"))
check("GeoJSON 要素数与统计一致", len(geojson.get("features") or []) == grid_count)

print("REAL_STATS=" + json.dumps(stats, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
print("CORE_FINGERPRINT=" + fingerprints[0])
print("ALL PASS")
