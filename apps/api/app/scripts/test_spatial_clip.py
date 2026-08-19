"""spatial_clip 工具确定性验证（真几何裁剪 + 完整注册 + 几何类型推断）。

运行：cd apps/api && .venv/Scripts/python.exe app/scripts/test_spatial_clip.py
验证项：
1. 点目标（poi_餐饮）裁到粤海街道 → 成功、output_layer_id == 源id_clip、几何=point、要素>0；
2. 线目标（shenzhen_roads）裁到粤海街道 → 几何=line、要素>0；
3. 面目标（粤海）裁到南山街道 → 几何=polygon、clip_ratio 合理；
4. 注册带真实 geojson、磁盘物化文件存在、clip_ratio ∈ [0,100]；
5. 图层缺失 → success=False 不挂死。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.data_registry import DATA_REGISTRY  # noqa: E402
from app.gis import engine  # noqa: E402
from app.models.layer import GeometryType, LayerMetadata, LayerSource  # noqa: E402
from app.tools.analysis import run_spatial_clip  # noqa: E402
from app.workspace import LAYER_DATA_DIR  # noqa: E402

FAILS: list[str] = []
PASSES = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASSES
    PASSES += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# ── 准备掩膜：粤海街道 + 南山街道（从 shenzhen_streets 过滤）──
streets_gdf = engine.load_gdf("shenzhen_streets")
yuehai = streets_gdf[streets_gdf["JDNAME"] == "粤海"]
nanshan = streets_gdf[streets_gdf["JDNAME"] == "南山"]
check("粤海街道存在", len(yuehai) >= 1, f"{len(yuehai)} 要素")
check("南山街道存在", len(nanshan) >= 1, f"{len(nanshan)} 要素")
if len(yuehai) == 0 or len(nanshan) == 0:
    print("无法构造掩膜，终止"); sys.exit(1)


def _reg(id_, name, gdf):
    DATA_REGISTRY.register_generated(
        LayerMetadata(
            id=id_, name=name, source=LayerSource.uploaded,
            geometry=GeometryType.polygon, color="#0F172A", visible=True,
            feature_count=len(gdf), crs="EPSG:4490", generated_by="test_spatial_clip",
        ),
        geojson=engine.to_geojson(gdf),
    )


_reg("yuehai_street", "粤海街道", yuehai)
_reg("nanshan_street", "南山街道", nanshan)

# ── 1) 点目标 → 粤海街道 ──
poi_count_before = DATA_REGISTRY.get_layer("poi_餐饮").feature_count
res = run_spatial_clip({"target_layer_id": "poi_餐饮", "clip_layer_id": "yuehai_street"})
check("点目标裁剪成功", res is not None and res.success, res.message if res else "")
lid = res.output.get("output_layer_id") if res else None
check("output_layer_id == poi_餐饮_clip", lid == "poi_餐饮_clip", str(lid))
if lid:
    layer = DATA_REGISTRY.get_layer(lid)
    check("图层已注册且几何类型=point", layer is not None and layer.geometry == GeometryType.point,
          f"{layer.geometry if layer else None}")
    gj = DATA_REGISTRY.get_geojson(lid)
    n = len(gj["features"]) if gj else 0
    check("注册带真实 geojson 且要素>0", gj is not None and n > 0, f"{n} 要素")
    check("feature_count 与 geojson 一致", gj is not None and res.output.get("feature_count") == n,
          f"{res.output.get('feature_count')} vs {n}")
    ratio = res.output.get("clip_ratio")
    check("clip_ratio ∈ [0,100]", isinstance(ratio, (int, float)) and 0 <= ratio <= 100, str(ratio))
    check("点目标比率用计数法（>0 而非面积恒 0）", isinstance(ratio, (int, float)) and ratio > 0, str(ratio))
    disk = LAYER_DATA_DIR / f"{lid}.json"
    check("磁盘物化文件存在", disk.is_file() and disk.stat().st_size > 0, str(disk))
    check("源图层未被破坏", DATA_REGISTRY.get_layer("poi_餐饮").feature_count == poi_count_before,
          f"{poi_count_before} 不变")

# ── 2) 线目标 → 粤海街道 ──
res_line = run_spatial_clip({"target_layer_id": "shenzhen_roads", "clip_layer_id": "yuehai_street"})
check("线目标裁剪成功", res_line is not None and res_line.success, res_line.message if res_line else "")
if res_line is not None and res_line.success:
    ll = DATA_REGISTRY.get_layer(res_line.output["output_layer_id"])
    check("线目标几何类型=line", ll is not None and ll.geometry == GeometryType.line, f"{ll.geometry if ll else None}")
    check("线目标要素>0", res_line.output.get("feature_count", 0) > 0, str(res_line.output.get("feature_count")))

# ── 3) 面目标：粤海 → 粤海（自裁剪，确定性 100% 面积保留）──
res_poly = run_spatial_clip({"target_layer_id": "yuehai_street", "clip_layer_id": "yuehai_street"})
check("面目标裁剪成功", res_poly is not None and res_poly.success, res_poly.message if res_poly else "")
if res_poly is not None and res_poly.success:
    lp = DATA_REGISTRY.get_layer(res_poly.output["output_layer_id"])
    check("面目标几何类型=polygon", lp is not None and lp.geometry == GeometryType.polygon, f"{lp.geometry if lp else None}")
    check("面目标 clip_ratio == 100（面积法）", res_poly.output.get("clip_ratio", 0) == 100.0,
          f"{res_poly.output.get('clip_ratio')}%")
    gj_p = DATA_REGISTRY.get_geojson(res_poly.output["output_layer_id"])
    check("面目标要素保持 1", gj_p is not None and len(gj_p["features"]) == 1,
          f"{len(gj_p['features']) if gj_p else 0} 要素")

# ── 4) output_name 自定义 ──
res_named = run_spatial_clip({"target_layer_id": "poi_餐饮", "clip_layer_id": "yuehai_street", "output_name": "粤海餐饮范围"})
check("自定义 output_name 生效", res_named is not None and res_named.success and res_named.output.get("name") == "粤海餐饮范围",
      res_named.output.get("name") if res_named else "")

# ── 5) 边界：图层缺失 → 失败不挂死 ──
res_missing = run_spatial_clip({"target_layer_id": "no_such_layer", "clip_layer_id": "yuehai_street"})
check("缺失图层失败不挂死", res_missing is not None and not res_missing.success, res_missing.message if res_missing else "")
res_bad_mask = run_spatial_clip({"target_layer_id": "poi_餐饮", "clip_layer_id": "no_such_mask"})
check("缺失掩膜失败不挂死", res_bad_mask is not None and not res_bad_mask.success, res_bad_mask.message if res_bad_mask else "")

print(f"\n{'=' * 44}\n结果：{len(FAILS)} FAIL / {PASSES} PASS")
sys.exit(1 if FAILS else 0)
