"""kernel-density 可执行 skill 确定性验证（真计算 + 注册 + 分层设色不被冲）。

运行：cd apps/api && .venv/Scripts/python.exe app/scripts/test_kernel_density.py
验证项：
1. engine.kernel_density：真实 poi_餐饮 + 粤海街道范围 → 网格图层、密度为正、GeoJSON 合法；
2. STORE.run("kernel-density", ...) 端到端：注册带真实 geojson、feature_count 一致、layer_id == 注册 id；
3. 分层设色写入侧车；模拟 executor 循环末的重注册后样式不被冲掉；
4. 边界：单点图层失败不挂死、cell_size_m 超小被 clamp；
5. 默认参数（300m 网格，skill.json/main.py 契约一致）成功出图。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.data_registry import DATA_REGISTRY  # noqa: E402
from app.gis import engine  # noqa: E402
from app.models.layer import GeometryType, LayerMetadata, LayerSource  # noqa: E402
from app.skills.store import STORE, SkillError  # noqa: E402
from app.workspace import LAYER_DATA_DIR  # noqa: E402

FAILS: list[str] = []
TOTAL = 0


def _materialize(layer_id: str, geojson: dict) -> None:
    """skill 在子进程真执行，看不到测试进程内存注册表 —— 把测试图层物化到磁盘供子进程读取。"""
    LAYER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (LAYER_DATA_DIR / f"{layer_id}.json").write_text(json.dumps(geojson, ensure_ascii=False), encoding="utf-8")


def check(name: str, cond: bool, detail: str = "") -> None:
    global TOTAL
    TOTAL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def run_skill(params: dict) -> dict:
    """调用可执行 skill（子进程真执行），返回 run 结果 dict（{success, output, ...}）。"""
    try:
        return STORE.run("kernel-density", params)
    except SkillError as exc:
        return {"success": False, "output": {"success": False, "error": str(exc)}}


def skill_output(result: dict) -> dict:
    return result.get("output") or {}


def skill_grid_count(result: dict) -> int:
    layers = skill_output(result).get("layers") or []
    if layers:
        return int(layers[0].get("feature_count") or 0)
    return int((skill_output(result).get("stats") or {}).get("grid_count") or 0)


# ── 准备：粤海街道范围（从平台 shenzhen_streets 过滤 JDNAME=粤海）──
streets_gdf = engine.load_gdf("shenzhen_streets")
yuehai = streets_gdf[streets_gdf["JDNAME"] == "粤海"]
check("粤海街道存在（shenzhen_streets）", len(yuehai) >= 1, f"{len(yuehai)} 要素")
if len(yuehai) == 0:
    print("无法构造粤海街道范围，终止"); sys.exit(1)

yuehai_gdf = yuehai.copy()
DATA_REGISTRY.register_generated(
    LayerMetadata(
        id="yuehai_street", name="粤海街道", source=LayerSource.uploaded,
        geometry=GeometryType.polygon, color="#0F172A", visible=True,
        feature_count=len(yuehai_gdf), crs="EPSG:4490", generated_by="test_kernel_density",
    ),
    geojson=engine.to_geojson(yuehai_gdf),
)
_materialize("yuehai_street", engine.to_geojson(yuehai_gdf))

# ── 1) engine.kernel_density 真计算 ──
p_gdf = engine.load_gdf("poi_餐饮")
a_gdf = engine.load_gdf("yuehai_street")
grid, geojson, stats = engine.kernel_density(p_gdf, a_gdf, cell_size_m=100)
check("KDE 返回网格图层", grid is not None and len(grid) > 0, f"{len(grid) if grid is not None else 'None'} 格")
check("网格规模合理（100m 格 ≈ 千级）", grid is not None and 500 < len(grid) < 5000, str(len(grid) if grid is not None else 0))
check("density 统计为正", stats is not None and stats["max"] > 0 and stats["mean"] > 0, str(stats))
check("GeoJSON 合法且带 density 字段",
      bool(geojson and geojson["features"] and "density" in geojson["features"][0]["properties"]),
      f"{len(geojson['features']) if geojson else 0} 要素")

# ── 2) STORE.run(kernel-density) 端到端 ──
result = run_skill({"point_layer_id": "poi_餐饮", "area_layer_id": "yuehai_street", "cell_size_m": 100})
out = skill_output(result)
check("execute_skill_script 运行成功", result.get("success") is True, str(out.get("error") or out.get("summary") or ""))
lid = out.get("layer_id")
check("layer_id == poi_餐饮_kde", lid == "poi_餐饮_kde", str(lid))
layer = DATA_REGISTRY.get_layer(lid)
check("图层已注册", layer is not None)
gj = DATA_REGISTRY.get_geojson(lid)
check("注册带真实 geojson", gj is not None and len(gj.get("features", [])) > 0, f"{len(gj['features']) if gj else 0} 要素")
check("feature_count 与 geojson 一致", gj is not None and skill_grid_count(result) == len(gj["features"]),
      f"{skill_grid_count(result)} vs {len(gj['features']) if gj else 0}")

# ── 3) 分层设色 + 模拟 executor 重注册不冲样式 ──
vis = (DATA_REGISTRY.get_layer(lid).visualVariables or {})
check("分层设色已写入（graduated）", vis.get("renderer") == "graduated", str(list(vis.keys())))
name = out.get("layers")[0]["name"] if out.get("layers") else "核密度"
replacement = LayerMetadata(
    id=lid, name=name, source=LayerSource.generated,
    geometry=GeometryType.polygon, color="#0F172A", visible=True,
    feature_count=skill_grid_count(result), crs="EPSG:4490",
    generated_by="LLM Agent 工具调用",  # 无样式字段，模拟 executor _register_generated
)
DATA_REGISTRY.register_generated(replacement)
vis2 = (DATA_REGISTRY.get_layer(lid).visualVariables or {})
check("重注册后 graduated 样式保留（不被冲）", vis2.get("renderer") == "graduated", str(list(vis2.keys())))

# ── 4) 边界用例 ──
# 单点图层 → 失败不挂死
def _gpd():
    import geopandas as _gpd
    from shapely.geometry import Point
    return _gpd.GeoDataFrame({"density": [0.0]}, geometry=[Point(113.93, 22.52)], crs="EPSG:4490")


DATA_REGISTRY.register_generated(
    LayerMetadata(
        id="single_point", name="单点测试", source=LayerSource.uploaded,
        geometry=GeometryType.point, color="#0F172A", visible=True,
        feature_count=1, crs="EPSG:4490", generated_by="test_kernel_density",
    ),
    geojson=engine.to_geojson(_gpd()),
)
_materialize("single_point", engine.to_geojson(_gpd()))
single_result = run_skill({"point_layer_id": "single_point", "cell_size_m": 100})
check("单点图层失败不挂死", single_result.get("success") is False, str(skill_output(single_result).get("summary") or ""))

# 无掩膜超小网格：5m 撞引擎上限 → skill 自适应退档到 500m 成功（不挂死、不报错，真计算换分辨率）
tiny = run_skill({"point_layer_id": "poi_餐饮", "cell_size_m": 5})
check("无掩膜超小网格自适应退档成功", tiny.get("success") is True, str(skill_output(tiny).get("summary") or ""))
tiny_cell = (skill_output(tiny).get("stats") or {}).get("cell_size_m")
check("退档网格 ≥ 500m（未硬顶 5m）", tiny.get("success") is True and tiny_cell is not None and tiny_cell >= 500, str(tiny_cell))

# 掩膜场景：20m 网格不再被误杀（先裁剪格心再判超限，掩膜后网格数 < 上限）——修复点
masked20 = run_skill({"point_layer_id": "poi_餐饮", "area_layer_id": "yuehai_street", "cell_size_m": 5})
check("掩膜场景 5m 网格成功（修复误杀）", masked20.get("success") is True, str(skill_output(masked20).get("summary") or ""))
if masked20.get("success") is True:
    m20 = DATA_REGISTRY.get_geojson(skill_output(masked20).get("layer_id"))
    check("掩膜后网格规模合理", m20 is not None and 0 < len(m20["features"]) < 50000, f"{len(m20['features']) if m20 else 0} 格")

# 带宽语义：bandwidth_m=50（平滑）max 应显著大于 bandwidth_m=2000（过度平滑抹平），且都成功
g_small, j_small, s_small = engine.kernel_density(
    engine.load_gdf("poi_餐饮"), engine.load_gdf("yuehai_street"), cell_size_m=100, bandwidth_m=50
)
g_big, j_big, s_big = engine.kernel_density(
    engine.load_gdf("poi_餐饮"), engine.load_gdf("yuehai_street"), cell_size_m=100, bandwidth_m=2000
)
check("固定带宽 50m 计算成功", g_small is not None and s_small is not None, str(s_small))
check("固定带宽 2000m 计算成功", g_big is not None and s_big is not None, str(s_big))
if s_small is not None and s_big is not None:
    check("带宽越大越平滑（max 更小）", s_small["max"] > s_big["max"], f"bw50 max={s_small['max']} vs bw2000 max={s_big['max']}")
    check("固定带宽 50m 非均匀抹平", s_small["max"] > 10, f"bw50 max={s_small['max']}（≤10 说明带宽语义又回退成缩放因子）")

# 端到端透传：execute_skill_script 收 bandwidth_m 且真应用（stats 自报 50）
bw_end = run_skill({"point_layer_id": "poi_餐饮", "area_layer_id": "yuehai_street", "cell_size_m": 100, "bandwidth_m": 50})
bw_stats = skill_output(bw_end).get("stats") or {}
check("execute_skill_script 透传 bandwidth_m", bw_end.get("success") is True, str(skill_output(bw_end).get("summary") or ""))
check("skill 实际应用带宽 50m（stats 自报）", bw_stats.get("bandwidth_m") == 50, str(bw_stats.get("bandwidth_m")))

# ── 5) 默认调精：不传网格/带宽 → 50m 网格 + scott 自动带宽封顶 150m ──
# scott 对街道级点群（3488 餐饮点）给 320m 带宽 → 热点糊片（用户感知「太粗」）；
# 封顶 150m 后 density_max 应明显 > scott 的 ~765，网格默认 50m。
def_res = run_skill({"point_layer_id": "poi_餐饮", "area_layer_id": "yuehai_street"})
check("默认参数成功（50m 网格 + 自动带宽封顶）", def_res.get("success") is True, str(skill_output(def_res).get("summary") or ""))
if def_res.get("success") is True:
    stats_d = skill_output(def_res).get("stats") or {}
    dg = DATA_REGISTRY.get_geojson(skill_output(def_res).get("layer_id"))
    check("默认 50m 网格规模（粤海 ≈ 5.7 千格）", dg is not None and 3000 < len(dg["features"]) < 8000, f"{len(dg['features']) if dg else 0} 格")
    check("默认走 50m 网格（非 300m）", stats_d.get("cell_size_m") == 50, str(stats_d.get("cell_size_m")))
    check("默认带宽封顶聚焦（density_max>1200，scott 320m≈765）", stats_d.get("max", 0) > 1200, str(stats_d.get("max")))

print(f"\n{'=' * 44}\n结果：{len(FAILS)} FAIL / {TOTAL - len(FAILS)} PASS")
sys.exit(1 if FAILS else 0)
