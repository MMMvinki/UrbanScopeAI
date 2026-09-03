"""接缝契约回归测试（「不再需要人修」的守门，覆盖 Phase 1-3 的 A/B/C 线）。

运行：cd apps/api && .venv/Scripts/python.exe app/scripts/test_seam_stability.py

每条都是**跨组件接缝**的最小可复现：单独跑每个组件都对，坏在接缝缺契约。这些断言专治
历史反复出现的崩点：
- A4/B1 输出契约与成败判定（chart-only → KeyError('path')、声明图层被静默丢弃、已成功却判失败）；
- B2 工具层对账（已建却报失败 → 提升为产物成功，agent 不再转 execute_code 兜底）；
- C1 数据源拉丁名规范匹配（POI_餐饮 vs poi餐饮）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.agent.llm_executor import _reconcile_tool_result  # noqa: E402
from app.data_registry import DATA_REGISTRY  # noqa: E402
from app.data_sources import _match_spec, _resolve_source_spec  # noqa: E402
from app.models.layer import GeometryType, LayerMetadata, LayerSource  # noqa: E402
from app.models.tool import ToolResult  # noqa: E402
from app.skills.store import STORE, _normalize_skill_output  # noqa: E402
from app.workspace import LAYER_DATA_DIR  # noqa: E402

FAILS: list[str] = []
TOTAL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global TOTAL
    TOTAL += 1
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# ── 0. 数据源身份（C1：拉丁名规范化匹配）────────────

def test_source_identity() -> None:
    # 拉丁名大小写/连接符差异：清单 stem 未清洗，目录 dataset_id 已 lower+清洗 → 规范化后能对上
    src = {"id": "poi_餐饮", "name": "餐饮POI", "path": "平台数据/POI_餐饮/poi_餐饮.json"}
    check("C1_规范化匹配 poi餐饮->id", _match_spec(src, "poi餐饮"))
    check("C1_规范化匹配 POI餐饮->id", _match_spec(src, "POI餐饮"))
    check("C1_精确匹配 id", _match_spec(src, "poi_餐饮"))
    check("C1_不误匹配无关词", not _match_spec(src, "人口"))
    # 未知别名：不再静默，返回 None（不影响下游诚实失败），且不抛
    check("C1_未知别名返回 None", _resolve_source_spec("hunyuan_ghost_source") is None)


# ── 1. 输出契约归一化（A4：杜绝多态形状裸解包）──────

def test_normalize_output() -> None:
    # layers 声明但含非 dict / 缺 layer_id → 告警 + 收敛，不抛
    norm, warn = _normalize_skill_output(
        {"success": True, "layers": [{"layer_id": "ok"}, "bad", {"name": "no-id"}]}
    )
    check("A4_非 dict 图层项被剔", len(norm["layers"]) == 1, str(norm["layers"]))
    check("A4_缺 layer_id 告警", any("layer_id" in w for w in warn), str(warn))

    # success 字符串强转
    norm2, _ = _normalize_skill_output({"success": "false"})
    check("A4_success 字符串 false→bool", norm2["success"] is False)
    norm3, _ = _normalize_skill_output({"success": True})
    check("A4_success 布尔透传", norm3["success"] is True)

    # 非 dict 顶层 → 兜底对象，不抛
    norm4, _w = _normalize_skill_output(["not", "a", "dict"])
    check("A4_非 dict 顶层兜底", norm4.get("success") is False)


# ── 2. 图层注册（A2：无声丢弃 → 响亮警告 + 携带原因）─

def _materialize(layer_id: str, geojson: dict) -> None:
    LAYER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    (LAYER_DATA_DIR / f"{layer_id}.json").write_text(json.dumps(geojson, ensure_ascii=False), encoding="utf-8")


def test_register_layer() -> None:
    # 真图层：文件在 → 返回 (result, "")且 result 非空
    gid = "seam_test_heat"
    _materialize(
        gid,
        {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.34, 39.9]}, "properties": {"人数": 123}}
            ],
        },
    )
    res, warn = STORE._register_layer(
        {"layer_id": gid, "path": f"data/layers/{gid}.json", "geometry": "Point", "kind": "heatmap", "weight_field": "人数"},
        ctx=None,
    )
    check("A2_文件在图层注册成功", res is not None and warn == "", str(warn))
    check("A2_注册 id 一致", bool(res) and res["layer_id"] == gid, str(res))

    # 声明但文件不存在 → (None, 含「不存在」原因的 warning)
    res2, warn2 = STORE._register_layer({"layer_id": "seam_missing", "path": "data/layers/seam_missing.json"}, ctx=None)
    check("A2_缺文件返回 None", res2 is None)
    check("A2_缺文件带原因告警", "不存在" in (warn2 or ""), str(warn2))

    # 缺 layer_id → (None, 缺 id 告警)
    res3, warn3 = STORE._register_layer({"path": "data/layers/x.json"}, ctx=None)
    check("A2_缺 layer_id 返回 None", res3 is None and "layer_id" in (warn3 or ""), str(warn3))

    # 清理测试产物：`_register_layer` 会把物化 geojson + meta 侧车写进工作区，勿污染用户图层列表。
    for suffix in (".json", ".meta.json"):
        try:
            (LAYER_DATA_DIR / f"{gid}{suffix}").unlink()
        except OSError:
            pass


# ── 3. 工具层对账（B2：已建却报失败 → 产物成功）──────

def test_prune_removed_generated() -> None:
    """中间层被 remove_layer 删掉后必须从 generated 累积列表剔除（防收尾复活）。

    根因：收尾 _register_generated(generated) 会重注册列表里每个图层；OSM 原始/裁剪
    中间层虽被 remove_layer 删掉，却没有从 generated 累积列表移除，导致跑完又冒出来
    （"OSM建筑原始结果图层""OSM建筑原始裁剪"删不掉）。remove_layer 工具结果只有
    layer_id（无 output_layer_id），走不进输出累积分支，须按 layer_id 显式剔除。
    """
    from app.agent.llm_executor import _prune_removed_generated

    generated = [
        ("f39cbca5", "OSM建筑原始结果图层", 5244),
        ("f39cbca5_clip", "OSM建筑原始裁剪", 3528),
        ("规划范围内建筑功能分类", "规划范围内建筑功能分类", 3524),
    ]
    # 删 OSM 原始层 → 仅该 id 被剔除，产物保留
    _prune_removed_generated(generated, "f39cbca5")
    check("prune_删OSM原始层", len(generated) == 2 and all(g[0] != "f39cbca5" for g in generated), str(generated))
    # 删裁剪层 → 只剩产物
    _prune_removed_generated(generated, "f39cbca5_clip")
    check("prune_删裁剪层剩产物", len(generated) == 1 and generated[0][0] == "规划范围内建筑功能分类", str(generated))
    # 删一个本来就不在列表里的 id：无副作用
    before = list(generated)
    _prune_removed_generated(generated, "不存在的图层")
    check("prune_未知id无副作用", generated == before, str(generated))
    # 空 id：直接忽略
    before2 = list(generated)
    _prune_removed_generated(generated, "")
    check("prune_空id忽略", generated == before2, str(generated))


def test_reconcile() -> None:
    # output 带 issued_layers（skill 产物）但 success=False → 提升为成功 + warning
    r = ToolResult(success=False, output={"issued_layers": ["huoyan_resident_pop_heat"]}, message="读源失败")
    rec = _reconcile_tool_result(r)
    check("B2_issued_layers 非空对账为成功", rec.success is True)
    check("B2_原失败信息进 warning", any("对账" in w for w in rec.output.get("warnings", [])), str(rec.output))

    # output_layer_id 也认（register_layer 工具）
    r2 = ToolResult(success=False, output={"output_layer_id": "clip_1"}, message="err")
    rec2 = _reconcile_tool_result(r2)
    check("B2_output_layer_id 对账为成功", rec2.success is True)

    # 无任何产出 → 保持失败，不误提升
    r3 = ToolResult(success=False, output={}, message="err")
    rec3 = _reconcile_tool_result(r3)
    check("B2_无产出保持失败", rec3.success is False)

    # 已成功 → 原样
    r4 = ToolResult(success=True, output={"issued_layers": ["x"]}, message="ok")
    rec4 = _reconcile_tool_result(r4)
    check("B2_已成功原样透传", rec4.success is True and not rec4.output.get("warnings"))


# ── 3.5 字段名校验（修「没有字段：building/func_type」）：meta.fields 为空时并入真实 geojson 列 ──

def test_known_field_names() -> None:
    known_id = "seam_known_field"
    # meta.fields = []（OSM/生成图层常态），但真实 geojson 有 building/func_type/height 列
    layer = LayerMetadata(
        id=known_id,
        name="known-field-test",
        source="generated",
        fields=[],
        geometry="Polygon",
        visible=True,
    )
    DATA_REGISTRY.register_generated(
        layer,
        geojson={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [[[116.34, 39.9], [116.35, 39.9], [116.35, 39.91], [116.34, 39.9]]]},
                    "properties": {"building": True, "func_type": "住宅", "height": 42},
                }
            ],
        },
    )
    names = DATA_REGISTRY.known_field_names(known_id)
    check("known_字段并入 geojson 列", {"building", "func_type", "height"} <= names, str(sorted(names)))
    # 真不存在的字段仍会被拒 → 校验不失效
    check("known_伪字段仍拒", "no_such_col" not in names, str(sorted(names)))
    DATA_REGISTRY.remove_layer(known_id)


# ── 3.6 分类配色语义映射（修「公共服务被分到红系」：mapping 精确绑定，不再按频次贪）
# 根因：原实现 palette 顺序绑"频次第 N 高的取值"，低频语义类别（公共服务-教育）拿不到
# 想要的色。mapping 让「公共服务-教育→品红」一次到位，agent 不必调 palette 顺序猜。

def test_categorized_mapping() -> None:
    from app.tools.layer_style import build_categorized_style

    m_id = "seam_cat_map"
    fc = {"type": "FeatureCollection", "features": []}
    coords = [[[116.34, 39.9], [116.35, 39.9], [116.35, 39.91], [116.34, 39.9]]]
    for _ in range(100):  # 商业 频最高
        fc["features"].append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [coords]}, "properties": {"func": "商业"}})
    fc["features"].append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [coords]}, "properties": {"func": "公共服务-教育"}})  # 低频但语义关键
    DATA_REGISTRY.register_generated(
        LayerMetadata(id=m_id, name="cat-map", source=LayerSource.generated, geometry=GeometryType.polygon, visible=True, fields=[]),
        geojson=fc,
    )

    def colors(style) -> dict:
        return {c["value"]: c["color"] for c in style["visualVariables"]["categorized"]["classes"]}

    # 旧行为：按频次绑定 → 商业(频最高)拿 palette[0]
    s_old = build_categorized_style(m_id, "func", palette="#111111,#222222")
    c_old = colors(s_old)
    check("cat_无mapping按频次", c_old.get("商业") == "#111111", str(c_old))
    # 新行为：mapping 精确绑定，低频语义类别也能拿到指定色
    s_new = build_categorized_style(m_id, "func", mapping={"公共服务-教育": "#d9489f", "商业": "#e31a1c"})
    c_new = colors(s_new)
    check("cat_mapping精确绑定", c_new.get("公共服务-教育") == "#d9489f" and c_new.get("商业") == "#e31a1c", str(c_new))
    # mapping 未列出的取值 → 灰兜底，避免语义错配
    s_partial = build_categorized_style(m_id, "func", mapping={"公共服务-教育": "#d9489f"})
    check("cat_mapping缺省落灰", colors(s_partial).get("商业") == "#9CA3AF", str(colors(s_partial)))

    DATA_REGISTRY.remove_layer(m_id)


# ── 3.7 样式子属性保留分类配色（修「去边框把分类清空」：
# 根因是 apply_style_update 对"任意样式改动"都 styleMode=None + visualVariables=None，
# 分好类一句 update_layer_style(strokeWidth=0) 就把 categorized 清空 → agent 反复重设、
# 最后烘焙颜色造重复图层。现已改为仅改 color/fillColor 才回单值，描边/透明度/半径保留 renderer。）

def test_style_subproperty_preserves_renderer() -> None:
    from app.tools.layer_style import (
        apply_renderer_style,
        apply_style_update,
        build_categorized_style,
    )

    gid = "seam_cat_stroke"
    coords = [[[116.34, 39.9], [116.35, 39.9], [116.35, 39.91], [116.34, 39.9]]]
    fc = {"type": "FeatureCollection", "features": []}
    for func in ("居住", "商业", "医疗"):
        fc["features"].append(
            {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [coords]}, "properties": {"func_type": func}}
        )
    DATA_REGISTRY.register_generated(
        LayerMetadata(id=gid, name="cat-stroke", source=LayerSource.generated, geometry=GeometryType.polygon, visible=True, fields=[]),
        geojson=fc,
    )
    apply_renderer_style(gid, build_categorized_style(gid, "func_type", mapping={"居住": "#FFD700", "商业": "#FF4444", "医疗": "#00CED1"}))
    # 去边框：只改 strokeWidth，不应清空 categorized
    apply_style_update(gid, strokeWidth=0.0)
    layer = DATA_REGISTRY.get_layer(gid)
    check("style_去边框保留分类", layer.styleMode == "categorized" and layer.visualVariables is not None, str(layer.styleMode))
    # 换基础填充色：才真正回落到单值
    apply_style_update(gid, fillColor="#123456")
    layer2 = DATA_REGISTRY.get_layer(gid)
    check("style_换填充色回单值", layer2.styleMode is None and layer2.visualVariables is None, str(layer2.styleMode))
    DATA_REGISTRY.remove_layer(gid)


# ── 3.8 共享建筑功能分类器（修「building 写了功能却归成其他」：
# 根因是 agent 每次手写分类字典，漏 hotel/fire_station 等；且属性表被删成 3 列，
# amenity/shop/office 丢失 → 分类器只剩 building 可见。统一走 app.gis.labels 的
# classify_building_function，import 而非手写。）──

def test_classify_building_function() -> None:
    from app.gis.labels import classify_building_function

    cases = [
        # (属性, 期望类别) —— 覆盖 agent 历史上漏掉/误归的标签
        ({"building": "hotel"}, "商业"),                          # 曾漏 → 其他
        ({"building": "fire_station"}, "公共服务"),                # 曾漏 → 其他
        ({"building": "retail"}, "商业"),
        ({"building": "office", "amenity": ""}, "办公"),
        ({"building": "school"}, "教育"),
        ({"building": "yes"}, "其他"),                              # 无功能含义
        ({"building": "construction"}, "其他"),                     # 在建=功能未知
        ({"building": "roof"}, "其他"),
        ({"building": "tower"}, "其他"),
        ({"amenity": "hospital"}, "医疗"),                          # amenity 优先
        ({"shop": "supermarket", "building": "yes"}, "商业"),        # shop∈* 优先
        ({"office": "yes"}, "办公"),
        ({"landuse": "industrial"}, "工业"),
        ({"building": "commercial"}, "商业"),
    ]
    for props, want in cases:
        got = classify_building_function(props)
        check(f"labels_{props}->{want}", got == want, f"got {got}")

    # 全部无功能 → 不硬归类
    check("labels_空标签_其他", classify_building_function({}) == "其他", classify_building_function({}))


# ── 4. 端到端（数据在才跑）：population-heatmap 真链路 ──

def test_population_heatmap_e2e() -> None:
    from app.data_sources import get_source_meta

    meta = get_source_meta("huoyan_resident")
    if meta is None:
        print("[SKIP] population-heatmap e2e：huoyan_resident 数据源不可用（平台数据未挂载）")
        return
    try:
        result = STORE.run(
            "population-heatmap",
            {"population_source": "huoyan_resident"},
        )
    except Exception as exc:  # noqa: BLE001
        check("e2e_不抛异常", False, str(exc))
        return
    check("e2e_返回 success", result.get("success") is True, str(result.get("output") or {}))
    check("e2e_issued_layers 含热力层", "huoyan_resident_pop_heat" in (result.get("issued_layers") or []), str(result.get("issued_layers")))
    # plot_files 是干净 path 列表：混合 chart-only 事件不再 KeyError('path')
    check("e2e_plot_files 为干净列表", isinstance(result.get("plot_files"), list), str(result.get("plot_files") or []))
    # 汇总信息无重注册副作用
    out = result.get("output") or {}
    check("e2e_产出 summary", bool(out.get("summary")), str(out.get("summary") or ""))



def main() -> None:
    test_source_identity()
    test_normalize_output()
    test_register_layer()
    test_prune_removed_generated()
    test_reconcile()
    test_known_field_names()
    test_categorized_mapping()
    test_style_subproperty_preserves_renderer()
    test_classify_building_function()
    test_population_heatmap_e2e()
    print(f"\n{'=' * 40}\n{TOTAL} 项断言 | {len(FAILS)} 失败")
    if FAILS:
        print("失败项：")
        for f in FAILS:
            print(f"  ✗ {f}")
        sys.exit(1)
    print("PASS 全部通过")


if __name__ == "__main__":
    main()
