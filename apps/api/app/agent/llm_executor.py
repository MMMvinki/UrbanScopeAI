"""LLM 驱动的 Executor：Agentic 工具调用循环。

LLM 自主决定调用哪些工具及参数 → Tool Runtime 确定性执行 → 真实结果回传给
LLM 继续决策 → 直到 LLM 输出最终结论。Trace 与结果卡从真实工具输出组装，
数值不编造。任何失败返回 None，由 executor.execute 回退规则版。
"""

import json
import os
import re
from pathlib import Path
from typing import Any

from app.agent import llm_client
from app.agent.memory import MEMORY
from app.data_registry import DATA_REGISTRY, save_layer_meta
from app.geojson_utils import geometry_type_for, load_feature_collection
from app.mcp import get_gis_mcp_bridge
from app.memory_store import get_memory_lines, get_summary_lines
from app.models.agent import ExecuteRequest, TraceEvent
from app.models.events import EventType
from app.models.layer import GeometryType, LayerMetadata, LayerSource
from app.models.result import ExecutionResult, ResultGroup, ResultMetric, ResultWidget, WidgetType
from app.models.tool import ToolResult
from app.skills import skill_lines
from app.tools import run_tool
from app.tools.context import ToolContext
from app.tools.registry import TOOLS
from app.workspace import LAYER_DATA_DIR

MAX_ROUNDS = 10

# 单个实质计划步骤允许的轮次上限：连续超出视为该步停滞，提示 LLM 跳过并继续后续
# 步骤，防止一个难啃的步骤烧掉整个 run（loop engineering：预算按步骤分片而非全局一个数）。
STEP_BUDGET = 3

# 地图控制/图层样式工具：纯控制类，不产成果卡、不生成图层。
# LLM 走这些工具时 _build_group 会返回 None，需要在 tool loop 里识别为
# 「地图控制已完成」返回 trace-only result，否则会回退规则版把成果吞掉。
_MAP_CONTROL_TOOLS = {
    "add_layer",
    "zoom_to_layer",
    "set_layer_visibility",
    "show_only_layer",
    "update_layer_style",
    "set_graduated_style",
    "set_layer_visual_variables",
    # 2026-08-17 工具目录补的纯控制工具：只发地图事件，不产成果卡
    "remove_layer",
    "set_layer_order",
    "set_basemap",
}

# gis-mcp 工具命名空间前缀：避免与本地工具同名冲突（DeepSeek 要求 tool 名唯一）
_MCP_PREFIX = "gis_"

_OP_TITLES = {
    "coverage": "覆盖率分析",
    "overlay": "空间叠加分析",
    "nearest_distance": "最近距离分析",
    "density": "密度分析",
    "kernel_density": "核密度分析",
    "supply_demand": "供需匹配分析",
    "load_osm_buildings": "OSM 数据加载",
    "load_datasource": "数据加载",
    "general": "通用空间分析",
}


def _group_title(operation: str) -> str:
    return _OP_TITLES.get(operation, "通用空间分析")


def _split_paragraphs(content: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n+", content) if part.strip()][:10]


def _last_text(trace: list[TraceEvent]) -> str | None:
    """trace 最后一条非空 text 事件内容（final_answer 兜底，取给用户的结论文本）。"""
    for event in reversed(trace):
        if event.type == "text" and event.content and event.content.strip():
            return event.content.strip()
    return None


def _tool_lines() -> list[str]:
    """可用工具行：本地注册表 + gis-mcp 外部工具（与 tool schemas 同源）。"""
    lines = [
        f"- {tool.name}(...) {tool.description}"
        for tool in TOOLS
        if tool.status.value == "ready"
    ]
    bridge = get_gis_mcp_bridge()
    if bridge is not None and bridge.is_ready():
        lines.append("")
        lines.append("【MCP 空间工具（gis-mcp）】外部 GIS 工具：缓冲区 / 叠加 / 最近距离 / 栅格 / 空间统计 / 坐标转换等，均可调用（工具名带 gis_ 前缀，参数见工具 schema）。")
        for tool in bridge.tools()[:30]:
            lines.append(f"- {_MCP_PREFIX}{tool['name']}(...) {tool['description'][:70]}")
    return lines


def _system_prompt(request: ExecuteRequest, target_layers: list) -> str:
    layer_lines = []
    for layer in DATA_REGISTRY.list_layers():
        note = "（空间上下文，不分析它）" if layer.id == "study_area" else ""
        layer_lines.append(f"- {layer.id} {layer.name} {layer.geometry.value} {layer.feature_count} 要素{note}")
    memory_lines = get_summary_lines(request.conversation_id)
    memory_section = (
        ["【多轮记忆】此前几轮的分析摘要（对话上下文，执行与总结时可参考）", *memory_lines, ""]
        if memory_lines
        else []
    )
    # Sprint 2：结构化记忆（会话里创建过哪些图层 / 做过哪些样式操作）
    struct_lines = get_memory_lines(request.conversation_id)
    struct_section = (
        ["【图层与样式记忆】本会话已创建的图层及样式操作（后续轮次可沿用/参照）", *struct_lines, ""]
        if struct_lines
        else []
    )
    return "\n".join(
        [
            "你是 UrbanScope 空间分析平台的 Executor，负责执行 Planner 给出的分析计划。",
            "你可以调用确定性 GIS 工具获取真实计算结果；绝对不要编造覆盖率/距离/占比等数值——一切数值都必须来自工具返回结果。",
            "",
            "【当前项目图层】id / 名称 / 几何 / 要素数",
            *layer_lines,
            "",
            *memory_section,
            *struct_section,
            "【可用工具】（工具契约与调用 schema 一致，从注册表动态注入）",
            *_tool_lines(),
            "",
            "【当前可用技能】（可复用分析能力。计划步骤指向某 skill 时，用 execute_skill_script 传入其参数运行；运行产物会自动上图并进成果栏）",
            *skill_lines(),
            "",
            "【执行规则】",
            "1. 按计划逐项调用工具完成分析，拿到工具结果后再决定下一步；",
            "2. 若计划指定了 target_distances（各图层独立服务半径），coverage 请严格按它传 distance；",
            "3. 每个关键分析对象至少调用一次对应分析工具；",
            "4. 全部完成后，用 2-4 句中文总结结论，必须引用真实的工具返回数值；",
            "5. 工具调用之间可以用简短中文说明你的推理；",
            "6. 避免冗余：每个分析目标用一个最匹配的工具即可，同一图层同一类工具不要重复调用，也不要为凑数调用多余工具。",
            "7. 【禁止顶替缺失数据】缺失的数据源/分析对象（用户未同意跳过的）**禁止**用任何其他数据源或图层顶替分析，尤其不能用 POI 餐饮/购物等代替人口/人群类分析。分析对象在当前项目图层里没有对应数据时，停下来说明缺失，不要自行变通换数据源；用户已同意跳过的缺失对象，只分析其余可用部分并在结论里注明“因缺数据未分析”。",
            "8. 【图表产出】emit_chart 仅在对比类别/分区/取值点 ≥2 时使用（少于 2 个可比项用指标卡即可）；可比类别合并成一张多系列图表，不要拆成多个近似单图；避免输出几乎相同的冗余图表（后端已有指纹去重，但勿刻意制造近似图）；图表数量不设硬性上限。",
            "9. 【方法论技能】若计划某步是 get_skill（skill_id 指向 SKILL.md 驱动的方法论技能，如 skill-creator），该工具返回的 output.body 就是该技能 SKILL.md 方法论正文——按它引导执行；若正文要求先澄清需求而当前信息不足，直接输出澄清问题作为回复，不要硬编工具调用；技能落盘必须用 create_skill 工具（受控契约），不要用 execute_code 或其它方式绕过它。",
            "10. 【参数缺失追问】execute_skill_script 因缺少必填参数而失败（或用户指令未指明该 skill 的关键参数）时，不要臆造参数值、也不要放弃——直接向用户追问缺失的参数（列出该 skill 的必填与可选参数名，如 point_layer_id / cell_size_m），拿到答复后再执行；参数有明确默认值且对结果影响不大时可先按默认执行并在结论里注明。",
            "11. 【禁止复用旧结论】只要用户当前问题要求计算/可视化/重新计算（尤其本回合上传了新文件），即使对话历史或【多轮记忆】出现过相同或相近问题的结论，也必须重新调用工具执行、基于本次工具返回的真实数值作答；旧结论只作参考、绝不直接当答案复用，也不得因此跳过读取/注册新文件、执行计算等必要步骤。",
            "",
            "【execute_code 数据路径约定】",
            "1. 平台图层（只读底座，poi_*、shenzhen_* 等）在 '../platform_data/geojson/<图层id>.json'，"
            "直接 gpd.read_file('../platform_data/geojson/poi_餐饮.json') 读全量几何；"
            "每个图层的确切路径可用 get_dataset_schema 的 data_path 字段获取；",
            "2. 工作区 data/layers/ 只有本会话生成/上传的图层（如渔网、提取结果），"
            "文件名为 data/layers/<layer_id>.json；",
            "3. 不要在工作区里遍历文件系统找数据（find / os.walk）——文件位置已固定，直接读取即可；字段名以文件实际属性为准，不要臆测；",
            "4. 优先用内置工具（coverage/density/nearest_distance/spatial_join 等）完成分析；execute_code 仅用于内置工具覆盖不到的自定义计算。",
            "5. 【核密度 / 密度图层】用户要「核密度/密度图层/密度分布/栅格图」时命中 kernel-density 可执行技能——"
            "用 execute_skill_script（skill_id=kernel-density）代码直跑，它自动生成网格多边形图层发布到地图并按 density 分层设色，"
            "禁止自己写 gaussian_kde 存 .npy/.tif 文件（平台图层只支持 GeoJSON，那些载体上不了地图）。",
            "6. 【人口热力 / 热力图】用户要「人口热力/热力图/热度分布/哪里人多/人口密度热力」，或「人口/人群/常住 + 密度热力」组合时，"
            "命中 population-heatmap 可执行技能——用 execute_skill_script（skill_id=population-heatmap）代码直跑："
            "它读慧眼人口点（人数）生成 kind=heatmap 图层，前端按人数加权连续渐变热力面。"
            "严禁用 kernel-density（那是点位密度，每个点权重 1，会丢「5 万人格点 vs 5 人格点」的人口本值），"
            "更不要自己写 gaussian_kde/核密度存 .npy/.tif。"
            "population_source **必须是原始数据源 id**（huoyan_resident 居住 / huoyan_worker 工作），"
            "**绝不能**用已生成的热力图图层（*_pop_heat / 结果图层）当数据源。"
            "skill 产出的热力面**本身就是最终热力图**（deck.gl HeatmapLayer 连续渐变），"
            "**禁止**再额外用 execute_code 建网格多边形或 set_graduated_style 把它重新分档设色——"
            "那会退回已被否的「格子分类面」，丢掉连续渐变热力半径语义；要统计口径就用 skill 的 stats，不要重建网格。",
        ]
    )


def _user_prompt(request: ExecuteRequest) -> str:
    missing = list(request.data_check.missing) if request.data_check else []
    accepted = set(request.accept_missing or [])
    # 已跳过 = 用户同意跳过的缺失项（unmet 已在门3 放行，这里只区分呈现）
    skipped = [item for item in missing if item in accepted]
    other = [item for item in missing if item not in accepted]
    unmet = list(request.unmet_targets or [])
    tds = ", ".join(f"{key}={value}m" for key, value in request.target_distances.items()) or "无"
    lines = [
        "【分析计划】",
        f"- summary: {request.summary}",
        f"- operation: {request.operation}",
        f"- targets: {', '.join(request.targets)}",
        f"- distance: {request.distance}",
        f"- target_distances: {tds}",
        f"- required_data: {', '.join(request.required_data)}",
    ]
    if request.plan:
        lines.append("")
        lines.append(
            "【执行步骤清单】按顺序推进，每完成一步用一句话标记进展；某步做不了就明确跳过并在结论中注明；"
            "不要为凑数调用清单外的工具："
        )
        for step in request.plan:
            tool_part = f"（{step.tool}）" if step.tool else ""
            lines.append(f"- {step.step_id} {step.label}{tool_part}")
    if skipped:
        # 语义 = 用户放弃该目标：不得再围绕它做任何探查/准备，更不能用 POI 等图层
        # 间接替它分析（产品红线：人群画像 → 餐饮 POI 静默替换）。目标级放弃。
        lines.append(
            f"- 被放弃的分析对象（用户已选择跳过，**禁止**为它做任何探查/准备/顶替，尤其禁止用 POI 间接分析人群类目标）: {'、'.join(skipped)}"
        )
    if other:
        lines.append(f"- 其余缺失数据（仅提示，不要分析）: {'、'.join(other)}")
    if unmet:
        lines.append(f"- 未满足的分析对象: {'、'.join(unmet)}")
    lines.extend(
        [
            "",
            "执行规则：",
            f"1. 若本次的**核心分析目标**在「被放弃」列表里（如原要分析人群画像但已跳过），则不要继续围绕该目标组织任何工具调用，直接输出结论：先说明『因缺少该数据且您选择跳过，本次无法完成此分析』，再（可选）基于其余现有图层做一两个真实的基础观察作为补充；",
            "2. 若核心目标未缺失，照常执行；其余被跳过/缺失的分析对象只分析可用部分，结论注明“因缺数据未分析”；",
            "3. 输出最终中文结论。",
            "4. 【最终回复规范】给用户的最后一整段消息必须是**完整、面向用户的中文正文**：总结分析结论，"
            "或把本任务需要用户回答/确认的问题整段写出（含必要的前置说明）。"
            "禁止以「## 反思」、思考过程口头语、或孤立的 Markdown 表格行（如 `|---|---|---|`）结尾；"
            "不要再输出一段独立的「反思/复盘」（反思由系统另行记录，无需你写）；"
            "引导/推荐类正文要完整写完，不要把面向用户的话拆成只剩表格头或半句话。",
        ]
    )
    return "\n".join(lines)


def _tool_schemas() -> list[dict]:
    """工具 schema：本地注册表 TOOLS.json_schema + gis-mcp 外部工具（动态合并）。"""
    schemas = [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.json_schema,
            },
        }
        for tool in TOOLS
        if tool.status.value == "ready" and tool.json_schema
    ]
    bridge = get_gis_mcp_bridge()
    if bridge is not None and bridge.is_ready():
        for tool in bridge.schema_list():
            schema = dict(tool)
            schema["function"] = dict(tool["function"], name=f"{_MCP_PREFIX}{tool['function']['name']}")
            schemas.append(schema)
    return schemas


def _reconcile_tool_result(result: ToolResult) -> ToolResult:
    """B2 对账：工具已在抛失败前产出图层 → 提升为部分成功。

    有些工具（尤其 execute_skill_script）在报失败时其实已把图层注册上图（output 里带
    issued_layers / layers / output_layer_id）。此时硬抛 failure 会让 agent 不信产物、
    转 execute_code 手写（按点大小渲染）兜底——这正是「技能成功却总说失败/热力变成点大小」
    的根。这里按**真实产出**对账：有图层 → 升级为成功，原失败信息降级为 warning 文本，
    让 agent 既能接着用产物、又能看到原缺陷，绝不覆盖 success。
    """
    if result.success:
        return result
    output = result.output
    if not isinstance(output, dict):  # 防御：output 可能是非 dict（异常路径置 {} 等）
        output = {}
    issued = output.get("issued_layers") or []
    if not isinstance(issued, list):
        issued = []
    layers = output.get("layers") or []
    if isinstance(layers, list):
        issued = issued + [
            l.get("layer_id")
            for l in layers
            if isinstance(l, dict) and l.get("layer_id") and l.get("layer_id") not in issued
        ]
    output_layer_id = output.get("output_layer_id")
    has_work = bool(issued) or bool(output_layer_id)
    if not has_work:
        return result
    original = result.message or "工具已产出图层但上报失败"
    warnings = output.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    warnings = warnings + [f"工具已按真实产出对账为成功（原上报失败：{original}）"]
    return ToolResult(
        success=True,
        output={**output, "warnings": warnings},
        message=f"工具已成功产出图层（对账校正）：{original}",
    )


def _call_tool(name: str, args: dict, *, ctx: ToolContext | None = None) -> ToolResult:
    """统一工具入口：gis_* 前缀走 MCP 桥，其余走本地 Tool Runtime（透传 ctx）。

    返回前统一走 B2 对账：工具产出了图层但标失败 → 提升为成功（真实产出为准），
    让 pydai wrapper 与旧 loop（都经本函数）一致采用「产物之证」而非单布尔。
    """
    if name.startswith(_MCP_PREFIX):
        bridge = get_gis_mcp_bridge()
        mcp_name = name[len(_MCP_PREFIX):]
        if bridge is not None and bridge.is_ready() and mcp_name in bridge.tool_names():
            payload = bridge.call_tool(mcp_name, args)
            result = ToolResult(
                success=payload.get("success", False),
                output=payload.get("output", {}),
                message=payload.get("message", ""),
            )
        else:
            result = ToolResult(success=False, output={}, message=f"MCP 工具 {name} 未就绪或不存在")
    else:
        result = run_tool(name, args, ctx=ctx)
    return _reconcile_tool_result(result)


def _tool_result_payload(result: ToolResult) -> str:
    return json.dumps(
        {"success": result.success, "output": result.output, "message": result.message},
        ensure_ascii=False,
    )


def _generated_info(output_layer_id: str, args: dict) -> tuple[str, int]:
    base = output_layer_id
    word = "结果图层"
    for suffix, label in (("_coverage", "服务范围"), ("_buffer", "缓冲区"), ("_kde", "核密度"), ("_clip", "裁剪"), ("_iso", "等时圈"), ("_bmi", "双变量自相关"), ("_result", "结果图层")):
        if output_layer_id.endswith(suffix):
            base = output_layer_id[: -len(suffix)]
            word = label
            break
    # 计数优先读生成图层本身（KDE 网格的要素数 = 格数 ≠ 源点数，读 base 会错配）；
    # display 名仍基于 base 图层（避免「…核密度核密度」冗余拼接）。
    count_layer = DATA_REGISTRY.get_layer(output_layer_id) or DATA_REGISTRY.get_layer(base)
    if count_layer is None:
        source_id = str(args.get("layer_id") or args.get("target_layer_id") or "")
        count_layer = DATA_REGISTRY.get_layer(source_id)
    count = count_layer.feature_count if count_layer else 0
    name_layer = DATA_REGISTRY.get_layer(base) or count_layer
    display = f"{name_layer.name}{word}" if name_layer else output_layer_id
    return display, count


def _prune_removed_generated(generated: list[tuple[str, str, int]], removed_id: str) -> None:
    """把被 remove_layer 删掉的图层 id 从 generated 累积列表里剔除（就地修改）。

    收尾 _register_generated(generated) 会重注册列表里的每个图层；若中间层
    （OSM 原始/裁剪）虽被 remove_layer 删掉却仍留在 generated，就会被「复活」——
    这是「删不掉、跑完又冒出来」的根因。remove_layer 工具结果只有 layer_id 没
    output_layer_id，走不进输出累积分支，故这里显式按 layer_id 剔除。
    """
    if not removed_id:
        return
    generated[:] = [g for g in generated if g[0] != removed_id]


def _register_generated(generated: list[tuple[str, str, int]]) -> None:
    for layer_id, display_name, feature_count in generated:
        geometry = GeometryType.polygon
        # 几何类型不能硬编码 Polygon：execute_code / 分析工具生成的结果图层可能是线
        # （地铁线路、道路）或点（POI），误标面会让前端样式面板/兜底渲染按面处理。
        # 从物化副本（workspace/data/layers/<layer_id>.json）推断真实类型；
        # 文件缺失/解析失败时回退 Polygon（保守默认）。register_generated 会覆盖同名
        # 图层，推断也能纠正 register_layer 后被重复注册覆盖的情形。
        materialized = LAYER_DATA_DIR / f"{layer_id}.json"
        if materialized.is_file():
            try:
                data = json.loads(materialized.read_text(encoding="utf-8"))
                _features, geometry_types, _bbox = load_feature_collection(data)
                if geometry_types:
                    geometry = geometry_type_for(geometry_types)
            except (json.JSONDecodeError, OSError):
                pass
        # 保留工具 handler 注册时带的更丰富元数据（字段 schema、描述），
        # 避免此处重注册把 fields 冲成空、属性表/schema 对生成图层失效。
        old_layer = DATA_REGISTRY.get_layer(layer_id)
        # 若图层已由 register_layer/目录启用完整注册（uploaded/folder，带 data_path/来源语义），
        # 不再二次降级登记成 generated —— 否则 folder/uploaded 的来源徽章与路径信息会丢失。
        if old_layer is not None and old_layer.source in (LayerSource.uploaded, LayerSource.folder):
            continue
        DATA_REGISTRY.register_generated(
            LayerMetadata(
                id=layer_id,
                name=display_name,
                source=LayerSource.generated,
                geometry=geometry,
                color="#0F172A",
                visible=True,
                feature_count=feature_count,
                crs="EPSG:4490",
                fields=(old_layer.fields if old_layer and old_layer.fields else []),
                description=(old_layer.description if old_layer else None),
                generated_by="LLM Agent 工具调用",
                # 保留 register_layer 记录的源数据路径与格式，避免下载/抓取图层被重登记成无路径/无格式
                data_path=(old_layer.data_path if old_layer else None),
                rel_path=(old_layer.rel_path if old_layer else None),
                data_type=(old_layer.data_type if old_layer else None),
            )
        )
        # 保证最终内存 metadata 与侧车一致（register_generated 不会自动写侧车）
        final_layer = DATA_REGISTRY.get_layer(layer_id)
        if final_layer is not None:
            try:
                save_layer_meta(final_layer)
            except Exception:  # noqa: BLE001
                pass


def _fingerprint(name: str, args: dict) -> str:
    """工具调用指纹：参数精确序列化 + 数值归一（800 / 800.0 视为同一次调用）。

    只做精确匹配，不做字符串清洗——execute_code 改几行代码重试是合法行为，
    不能因近似归一化误伤。
    """

    def _norm(value: object) -> object:
        if isinstance(value, float) and value.is_integer():
            return int(value)
        if isinstance(value, list):
            return [_norm(v) for v in value]
        if isinstance(value, dict):
            return {k: _norm(v) for k, v in value.items()}
        return value

    try:
        return f"{name}:{json.dumps(_norm(args), sort_keys=True, ensure_ascii=False)}"
    except (TypeError, ValueError):
        return f"{name}:{hash(str(args))}"


def _force_conclude(messages: list[dict], trace: list[TraceEvent]) -> str | None:
    """轮次已用尽 / 重复死循环时，禁用工具让 LLM 基于已有结果总结。

    返回收尾文本（已追加进 trace）；失败 / 无内容返回 None（交由外层失败话术）。
    """
    messages.append(
        {
            "role": "user",
            "content": (
                "你已经完成了分析并获得了工具结果，轮次已用尽。"
                "请不要再调用任何工具，直接基于已有工具结果用 2-4 句中文输出最终结论。"
            ),
        }
    )
    try:
        response = llm_client.chat_completion(messages, tools=[])
    except Exception:
        return None
    message = response["choices"][0]["message"]
    content = message.get("content")
    if not content:
        return None
    for paragraph in _split_paragraphs(content):
        trace.append(TraceEvent(type="text", content=paragraph))
    return content


def _widget_from_call(
    index: int, name: str, args: dict, result: ToolResult
) -> tuple[ResultWidget, float, bool] | None:
    """工具输出 → 指标卡。返回 (widget, 数值, 是否为百分比) 或 None（非指标工具）。"""
    out = result.output
    tool_name = name  # 保留原始工具名（下方 name 会被重写为图层显示名）
    layer_id = str(
        args.get("layer_id")
        or args.get("target_layer_id")
        or args.get("facility_layer_id")
        or out.get("source_layer_id")
        or out.get("target_layer_id")
        or out.get("facility_layer_id")
        or ""
    )
    layer = DATA_REGISTRY.get_layer(layer_id) if layer_id else None
    name = layer.name if layer else (layer_id or "分析对象")
    wid = f"w-{index}"

    if "coverage_rate" in out:
        dist = out.get("distance", 800)
        return (
            ResultWidget(
                id=wid,
                title=f"{name}服务覆盖率",
                widget_type=WidgetType.metric,
                value=f"{out['coverage_rate']}%",
                subtitle=f"{dist}m 服务半径",
                layer_id=layer_id or None,
                evidence=[
                    f"{name}服务覆盖率 {out['coverage_rate']}%，按 {dist}m 服务半径计算",
                    f"覆盖 {out.get('covered_area_km2')} km² · 未覆盖 {out.get('uncovered_area_km2')} km²",
                ],
            ),
            float(out["coverage_rate"]),
            True,
        )
    if "avg_distance_m" in out:
        reference = DATA_REGISTRY.get_layer(str(out.get("reference_layer_id", "")))
        return (
            ResultWidget(
                id=wid,
                title=f"最近{name}距离",
                widget_type=WidgetType.metric,
                value=f"{out['avg_distance_m']} m",
                subtitle="平均距离",
                layer_id=layer_id or None,
                evidence=[f"到最近{name}的平均距离约 {out['avg_distance_m']} 米"],
            ),
            float(out["avg_distance_m"]),
            False,
        )
    if "ratio" in out:
        overlay = DATA_REGISTRY.get_layer(str(out.get("overlay_layer_id", "")))
        return (
            ResultWidget(
                id=wid,
                title=f"{name}叠加占比",
                widget_type=WidgetType.metric,
                value=f"{out['ratio']}%",
                subtitle="空间叠加",
                layer_id=layer_id or None,
                evidence=[f"{name} 与 {overlay.name if overlay else '叠加图层'} 叠加占比 {out['ratio']}%"],
            ),
            float(out["ratio"]),
            True,
        )
    if "density_index" in out:
        return (
            ResultWidget(
                id=wid,
                title=f"{name}密度指数",
                widget_type=WidgetType.metric,
                value=str(out["density_index"]),
                subtitle="相对密度",
                layer_id=layer_id or None,
                evidence=[f"{name}空间密度指数 {out['density_index']}（相对值）"],
            ),
            float(out["density_index"]),
            False,
        )
    if "satisfaction_percent" in out:
        population = DATA_REGISTRY.get_layer(str(out.get("population_layer_id", "")))
        return (
            ResultWidget(
                id=wid,
                title=f"{name}供需满足度",
                widget_type=WidgetType.metric,
                value=f"{out['satisfaction_percent']}%",
                subtitle="供需匹配",
                layer_id=layer_id or None,
                evidence=[f"{name} 对{population.name if population else '人口'}供需满足度 {out['satisfaction_percent']}%"],
            ),
            float(out["satisfaction_percent"]),
            True,
        )
    if tool_name == "osm_call" and "feature_count" in out and "path" in out:
        # OSM 下载类命令（download_bbox/download_features）：落盘并返回 feature_count
        count = int(out.get("feature_count", 0))
        geometry_types = out.get("geometry_types") or []
        return (
            ResultWidget(
                id=wid,
                title="OSM 数据下载",
                widget_type=WidgetType.metric,
                value=f"{count} 个要素",
                subtitle=f"key={args.get('key', 'OSM')}",
                evidence=[
                    f"已下载 {count} 个 OSM 要素",
                    f"几何类型：{', '.join(str(g) for g in geometry_types) or '未知'}",
                ],
            ),
            float(count),
            False,
        )
    if tool_name == "osm_call" and "results" in out:
        # OSM 地点搜索（search 命令）→ 地点解析清单
        results = out.get("results") or []
        items: list[str] = []
        for item in results[:5]:
            if isinstance(item, dict):
                label = item.get("display_name") or item.get("name") or ""
                coords = f"{item.get('lat')},{item.get('lon')}" if item.get("lat") else ""
                items.append(f"{label}{(' (' + coords + ')') if coords else ''}".strip())
        if items:
            return (
                ResultWidget(
                    id=wid,
                    title="地点解析",
                    widget_type=WidgetType.list,
                    value="已定位",
                    data={"items": items},
                    evidence=[f"解析到 {len(results)} 个地点"],
                ),
                0.0,
                False,
            )
    if tool_name == "query_features":
        # 要素查询：命中数指标卡 + 样本清单（真计算属性，不编造）
        samples = out.get("samples") or []
        layer_name = str(out.get("layer_name") or "图层")
        # 技术型内部字段不出现在样本卡里（对用户无意义），优先可读字段
        _NOISE_KEYS = {"OBJECTID", "id", "typecode", "FID", "SHAPE_Leng", "SHAPE_Area", "SHAPE_Length", "SHAPE_Area", "经度", "纬度", "lon", "lat"}
        items: list[str] = []
        for sample in samples[:5]:
            if isinstance(sample, dict):
                parts = [
                    f"{k}={v}"
                    for k, v in list(sample.items())[:6]
                    if v is not None and k not in _NOISE_KEYS
                ]
                items.append(" · ".join(parts) or "（空属性）")
        is_sampled = out.get("sampled") is True
        # 抽稀样本图层：只报样本命中数，绝不把样本命中数说成全量总数
        matched = out.get("sample_matched") if is_sampled else out.get("total_matched", 0)
        matched = matched if matched is not None else 0
        subtitle = "抽稀样本中命中 · 前若干条" if is_sampled else "满足条件 · 前若干条样本"
        evidence = (
            [f"「{layer_name}」为抽稀样本（样本 {out.get('sample_size', '?')} 条），样本中命中 {matched} 条（非全量总数）"]
            if is_sampled
            else [f"「{layer_name}」共 {matched} 个要素满足查询条件"]
        )
        evidence.append(f"已返回前 {out.get('sample_count', 0)} 条样本")
        return (
            ResultWidget(
                id=wid,
                title=f"{layer_name}要素查询",
                widget_type=WidgetType.list,
                value=f"{matched} 个要素" + ("（抽稀样本）" if is_sampled else ""),
                subtitle=subtitle,
                evidence=evidence,
                data={"items": items},
            ),
            float(matched),
            False,
        )
    return None


_FIGURE_REPR_RE = re.compile(r"<Figure size \d+x\d+ with \d+ Axes>")  # LLM 误传的 matplotlib repr


def _plot_title(plot: dict) -> str:
    """图表标题：优先 LLM caption，但过滤 matplotlib repr 等无意义串。"""
    caption = str(plot.get("caption") or "").strip()
    if caption and not _FIGURE_REPR_RE.search(caption):
        return caption
    return "分析图表"


def _plot_widgets(plots: list[dict]) -> list[ResultWidget]:
    """save_plot 保存的 matplotlib 图表 → image widget（静态路由 /artifacts/plots/<file>）。

    按文件名去重：LLM 失败重试 / 重复调用时同一张 PNG 只出一张卡。
    """
    widgets: list[ResultWidget] = []
    seen_filenames: set[str] = set()
    for index, plot in enumerate(plots):
        path = str(plot.get("path") or "")
        filename = Path(path).name if path else ""
        if not filename or filename in seen_filenames:
            continue
        seen_filenames.add(filename)
        widgets.append(
            ResultWidget(
                id=f"w-plot-{index}",
                title=_plot_title(plot),
                widget_type=WidgetType.image,
                subtitle="matplotlib 图表",
                data={"url": f"/artifacts/plots/{filename}", "path": path},
                evidence=[f"图表已生成并保存：{filename}"],
            )
        )
    return widgets


def _echarts_option(spec: dict) -> dict:
    """emit_chart spec → ECharts option（前端直接 setOption 渲染，含悬浮/缩放/动画）。

    布局约定：标题在顶，图例在底，x 轴标签全部显示（interval=0，防止 ECharts
    自动隐藏部分柱子底部的类目名），轴标签/图例都不被裁切（containLabel）。
    """
    chart_type = str(spec.get("chart_type") or "bar")
    title = str(spec.get("title") or "")
    x_labels = [str(x) for x in (spec.get("x_labels") or [])]
    series = list(spec.get("series") or [])
    y_label = str(spec.get("y_label")) if spec.get("y_label") else None

    option: dict = {
        "animation": True,
        "tooltip": {"trigger": "item" if chart_type == "pie" else "axis", "confine": True},
    }
    if title:
        option["title"] = {"text": title, "left": "center", "top": 6, "textStyle": {"fontSize": 14}}
    # 坐标轴类图表：图例在底部独占一行（grid.bottom 预留 40px），否则图例会
    # 与旋转后的 x 轴类目标签挤在同一块底部区域 → 文字遮盖、图例叠在标签上
    names = [str(s.get("name")) for s in series if s.get("name")] if chart_type != "pie" else []
    has_legend = bool(names)
    option["grid"] = {
        "left": 56,
        "right": 24,
        "top": 56,
        "bottom": 40 if has_legend else 0,
        "containLabel": True,
    }

    if chart_type == "pie":
        flat = []
        for s in series:
            data = list(s.get("data") or [])
            for name, value in zip(x_labels, data):
                if x_labels:
                    flat.append({"name": str(name), "value": value})
            if not x_labels:
                flat.extend({"name": str(s.get("name")), "value": v} for v in data)
        # 饼图图例 = 扇区名（不是系列名），否则 ECharts 报 legend 不匹配的警告
        option["legend"] = {
            "bottom": 0,
            "type": "scroll",
            "itemWidth": 14,
            "itemHeight": 10,
            "data": [str(item["name"]) for item in flat],
        }
        option["series"] = [
            {
                "type": "pie",
                "radius": ["38%", "62%"],
                "center": ["50%", "48%"],
                "data": flat,
                "label": {"show": True, "formatter": "{b}: {d}%"},
                "emphasis": {"label": {"show": True, "fontSize": 14, "fontWeight": "bold"}},
            }
        ]
    else:
        if has_legend:
            # bottom: 6 落在 grid.bottom 预留的 40px 图例带内，与 x 轴标签互不遮挡
            option["legend"] = {"bottom": 6, "type": "scroll", "itemWidth": 14, "itemHeight": 10, "data": names}
        mapped = []
        for s in series:
            item = {
                "type": chart_type,
                "name": str(s.get("name") or ""),
                "data": list(s.get("data") or []),
                "smooth": chart_type == "line",
            }
            if chart_type == "bar":
                item["barMaxWidth"] = 48
                item["itemStyle"] = {"borderRadius": [4, 4, 0, 0]}
            mapped.append(item)
        # interval=0：强制每个类目都显示标签，避免 ECharts 自动隐藏部分柱子底部名称
        option["xAxis"] = {
            "type": "category",
            "data": x_labels,
            "axisLabel": {"interval": 0, "rotate": 40 if len(x_labels) > 8 else 0, "hideOverlap": False},
        }
        option["yAxis"] = {"type": "value", "name": y_label}
        # 横向缩放（数据多时拖拽看局部）；柱/线都适用
        option["dataZoom"] = [{"type": "inside", "start": 0, "end": 100}]
        option["series"] = mapped
    return option


def _chart_widgets(charts: list[dict]) -> list[ResultWidget]:
    """emit_chart 结构化图表 → 交互 chart widget（前端 ECharts 渲染，一卡一图）。

    按「标题 + 图表类型 + x 轴 + 各系列数据」指纹去重：LLM 因序列化报错重试、
    或重复调用同一图表时，只保留第一张卡（实测同一 run 曾出现 2 张同标题柱状图）。
    """
    widgets: list[ResultWidget] = []
    seen: set[str] = set()
    for index, chart in enumerate(charts):
        spec = chart.get("spec")
        if not isinstance(spec, dict) or not spec.get("series"):
            continue
        try:
            fingerprint = json.dumps(
                [
                    str(spec.get("title")),
                    str(spec.get("chart_type")),
                    [str(x) for x in (spec.get("x_labels") or [])],
                    [[str(s.get("name")), [str(v) for v in (s.get("data") or [])]] for s in spec.get("series") or []],
                ],
                ensure_ascii=False,
                sort_keys=True,
            )
        except Exception:
            fingerprint = str(id(spec))
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        height = int(spec.get("height") or 320)
        widgets.append(
            ResultWidget(
                id=f"w-echart-{index}",
                title=str(spec.get("title") or "分析图表"),
                widget_type=WidgetType.chart,
                subtitle=f"交互图表 · {spec.get('chart_type')}",
                data={"option": _echarts_option(spec), "height": height},
                evidence=["交互式图表：支持悬浮查看数值、拖拽缩放、动画"],
            )
        )
    return widgets


def _build_group(
    request: ExecuteRequest,
    calls: list[tuple[str, dict, ToolResult]],
    generated: list[tuple[str, str, int]],
    plots: list[dict] | None = None,
    charts: list[dict] | None = None,
) -> ResultGroup | None:
    widgets: list[ResultWidget] = []
    series: list[dict] = []
    seen: set[tuple[str, str]] = set()
    series_seen: set[str] = set()
    # ── 统一结果格式：从 skill 产物收集结构化指标/发现/警告/参数/规则/来源/追问/停止原因 ──
    # 注：技能的结构化指标由「指标条」（group.metrics）承载，不重复生成逐张指标卡（避免与
    # 结果卡网格视觉重复）；skill 的数值已有 metric strip 可追溯。
    group_metrics: list[ResultMetric] = []
    group_findings: list[str] = []
    group_warnings: list[str] = []
    group_params: dict[str, object] = {}
    group_rules: list[str] = []
    group_source: str | None = None
    group_next: list[str] = []
    group_stop: str | None = None
    for index, (name, args, result) in enumerate(calls):
        if not result.success:
            continue
        if name == "execute_skill_script":
            out = result.output or {}
            for m in out.get("metrics") or []:
                if isinstance(m, dict):
                    group_metrics.append(
                        ResultMetric(**{k: v for k, v in m.items() if k in ResultMetric.model_fields})
                    )
            group_findings.extend(str(x) for x in out.get("findings") or [])
            group_warnings.extend(str(x) for x in out.get("warnings") or [])
            group_rules.extend(str(x) for x in out.get("rules") or [])
            if isinstance(out.get("params"), dict):
                group_params.update(out["params"])
            group_source = out.get("source") or group_source
            group_next.extend(str(x) for x in out.get("next_questions") or [])
            group_stop = out.get("stop_reason") or group_stop
        made = _widget_from_call(index, name, args, result)
        if made is None:
            continue
        widget, num, is_percent = made
        key = (widget.title, str(widget.value))
        if key in seen:
            continue  # 同一指标被重复调用时只保留一张卡
        seen.add(key)
        widgets.append(widget)
        if is_percent and widget.title not in series_seen:
            series_seen.add(widget.title)
            series.append({"name": widget.title, "value": num})
    # 图表产物：emit_chart 交互图表（一卡一图）优先，save_plot PNG（多子图已自动拆）作为静态图
    widgets.extend(_chart_widgets(charts or []))
    widgets.extend(_plot_widgets(plots or []))
    group_kwargs = {
        "metrics": group_metrics,
        "findings": group_findings,
        "warnings": group_warnings,
        "params": group_params,
        "rules": group_rules,
        "source": group_source,
        "next_questions": group_next,
        "stop_reason": group_stop,
    }
    if not widgets and not group_metrics:
        # 纯加载/发布类 run（osm_call + register_layer + execute_code 等）：无指标卡，
        # 但生成了图层 → 返回只含"结果图层"卡的成果组，避免整个 LLM 成果被丢弃后回退规则版。
        if not generated:
            return None
        widgets.append(
            ResultWidget(
                id="w-layers",
                title="结果图层",
                widget_type=WidgetType.map,
                value="已生成",
                subtitle="已同步到地图",
                data={"layer_ids": [item[0] for item in generated]},
                layer_id=generated[0][0],
                evidence=[f"{item[1]}（+{item[2]} 要素）" for item in generated],
            )
        )
        return ResultGroup(
            id=f"rg-{request.intent_id}", title=_group_title(request.operation), widgets=widgets, **group_kwargs
        )
    if series:
        widgets.append(
            ResultWidget(
                id="w-chart",
                title="指标对比",
                widget_type=WidgetType.chart,
                value=" / ".join(f"{s['name']}{int(s['value'])}%" for s in series),
                subtitle="对比",
                data={"series": series},
            )
        )
    if generated:
        widgets.append(
            ResultWidget(
                id="w-layers",
                title="结果图层",
                widget_type=WidgetType.map,
                value="已生成",
                subtitle="已同步到地图",
                data={"layer_ids": [item[0] for item in generated]},
                layer_id=generated[0][0],
                evidence=[f"{item[1]}（+{item[2]} 要素）" for item in generated],
            )
        )
    return ResultGroup(id=f"rg-{request.intent_id}", title=_group_title(request.operation), widgets=widgets, **group_kwargs)


# ── 图层样式 / 地图控制意图：改样式≠出图 ────────────────────────────────
# 用户说「把 XX 点改成红色 / 放大 / 描边」时，期望的是地图上那个图层的样式真的变化，
# 而不是成果栏里出现一张示意图。此类请求由确定性规则直接改图层样式（LAYER_STYLE 事件），
# 不走 LLM 分析出图，成果栏不产生任何卡片。

_STYLE_HINTS = (
    "样式", "颜色", "改色", "配色", "变红", "变蓝", "变绿", "变黄", "变紫", "变橙", "变黑", "变白",
    "红色", "蓝色", "绿色", "黄色", "紫色", "橙色", "黑色", "白色",
    "放大", "缩小", "变大", "变小", "描边", "加粗", "线宽", "变粗", "透明度", "透明",
    # Sprint 2：分层设色 / 渐变 / 暖色调（复杂样式意图，走样式引擎）
    "分层设色", "分层着色", "分层", "色阶", "渐变", "暖色", "暖色调", "去边框", "去掉边框", "无边框",
)
_COLOR_WORDS: dict[str, str] = {
    "红": "#ef4444", "蓝": "#3b82f6", "绿": "#22c55e", "黄": "#eab308",
    "紫": "#8b5cf6", "橙": "#f97316", "黑": "#0f172a", "白": "#ffffff", "灰": "#64748b",
}
# 图层名后缀剥离：名称→候选匹配键（越长越精确，命中取最长）
_NAME_SUFFIXES = ("结果图层", "范围面域", "服务范围", "餐饮POI", "1km内", "1km", "范围", "面域", "图层", "点", "POI")


def _norm_name(text: str) -> str:
    """去掉空格、'市'、常用虚词，用于名称模糊匹配（'深圳市福田会展中心'→'深圳福田会展中心'）。"""
    return re.sub(r"[\s市、，。的了吗这个那个]", "", text)


def _find_style_color(question: str) -> str | None:
    for word, hex_color in _COLOR_WORDS.items():
        if word in question:
            return hex_color
    return None


def _resolve_style_layer(question: str) -> Any | None:
    """从问题里找出用户想改样式的图层（名称/id 模糊匹配，取最长命中）。"""
    layers = DATA_REGISTRY.list_layers()
    q = _norm_name(question)
    best: Any = None
    best_len = 0
    for layer in layers:
        name = str(layer.name or "")
        for key in (name, layer.id):
            # 逐级剥后缀产生候选键
            base = key
            while True:
                matched = _norm_name(base)
                if matched and matched in q and len(matched) > best_len:
                    best = layer
                    best_len = len(matched)
                next_base = None
                for suffix in _NAME_SUFFIXES:
                    if base.endswith(suffix):
                        next_base = base[: -len(suffix)]
                        break
                if next_base is None or next_base == base:
                    break
                base = next_base
    return best


def _style_changes(question: str) -> dict[str, Any]:
    """从问题解析样式改动：颜色 / 点半径 / 描边 / 透明度。"""
    changes: dict[str, Any] = {}
    color = _find_style_color(question)
    if color:
        changes["color"] = color
        changes["fillColor"] = color
        changes["strokeColor"] = color
    if any(k in question for k in ("描边", "加粗", "线宽", "变粗")):
        changes["strokeWidth"] = 3
    if "放大" in question or "变大" in question:
        changes["radius"] = 18
    elif "缩小" in question or "变小" in question:
        changes["radius"] = 4
    if "透明" in question:
        changes["opacity"] = 0.25  # 兼容旧值（前端仍按 opacity ?? 默认兜底）
        changes["fillOpacity"] = 0.25  # 填充 / 描边不透明度分离后分别生效
        changes["strokeOpacity"] = 0.25
    return changes


def _try_style_request(
    request: ExecuteRequest,
    *,
    emit: Any | None = None,
) -> ExecutionResult | None:
    """检测并处理「改图层样式」请求；非样式请求返回 None（走正常 Agent 流程）。

    Sprint 2 扩展：除单值改色/缩放/描边/透明外，还能识别
    「按 X 属性分层设色 / 暖色调 / 透明度渐变 / 不要边框」的复杂意图，
    走样式引擎（layer_style.py）算分档断点 + 持久化 + emit LAYER_STYLE。
    """
    from app.data_registry import DATA_REGISTRY
    from app.tools import layer_style as ls

    question = (request.question or "").strip()
    # 护栏：分析类意图（核密度/密度分布）先放行给 Agent 跑分析，别被劫持成改样式的请求
    if re.search(r"(核密度|密度分布|密度分析|做.*密度|算.*密度)", question):
        return None
    if not question or not any(hint in question for hint in _STYLE_HINTS):
        return None
    layer = _resolve_style_layer(question)
    if layer is None:
        return None

    # 1) 单值样式改动（颜色/半径/描边/透明）
    changes = _style_changes(question)
    # 2) 分层设色（graduated）：「按 X 分层设色 / 渐变 / 色阶」
    graduated: dict | None = None
    if re.search(r"分层设色|分层着色|分档设色|色阶|按.*分层|渐变", question):
        field = ls.find_field(layer, question)
        if field:
            m = re.search(r"分成?\s*(\d+)\s*[级档]", question)
            classes = int(m.group(1)) if m else 5
            palette = ls.warm_ramp_name(question) or "viridis"
            # 「自然间断点 / 间断点」→ 真 Jenks（Fisher-Jenks），默认 quantile
            method = "jenks" if re.search(r"自然间断点|间断点|自然断点", question) else "quantile"
            try:
                graduated = ls.build_graduated_style(
                    layer.id, field, method=method, classes=classes, palette=palette
                )
            except ValueError:
                graduated = None  # 字段无数值 → 退化为单值样式，仍可描边/透明
    # 3) 透明度渐变（opacity_variable）
    opacity_var: dict | None = None
    if re.search(r"透明度.*渐变|渐变.*透明度|按.*透明度|透明度按", question):
        f = graduated["visualVariables"]["graduated"]["field"] if graduated else ls.find_field(layer, question)
        if f:
            try:
                opacity_var = ls.build_visual_variables(
                    layer.id, opacity_field=f, opacity_range=[0.3, 1.0]
                )
            except ValueError:
                opacity_var = None
    # 4) 去边框
    if re.search(r"不要边框|去掉边框|去边框|无边框", question):
        changes["strokeWidth"] = 0

    if not (graduated or opacity_var or changes):
        return None

    # 组装完整 style：graduated + opacity_variable 合并进 visualVariables；单值样式并列
    style: dict[str, Any] = dict(changes)
    if graduated or opacity_var:
        visual: dict[str, Any] = {}
        if graduated:
            visual.update(graduated["visualVariables"])  # {renderer, graduated{...}}
        if opacity_var:
            visual.update(opacity_var["visualVariables"])  # {opacity_variable{...}}
        style["styleMode"] = "graduated"
        style["visualVariables"] = visual

    DATA_REGISTRY.apply_layer_patch(layer.id, style)  # 持久化到样式侧车
    if emit is not None:
        emit(EventType.LAYER_STYLE, {"layer_id": layer.id, "style": style})

    # 结果文本：逐项描述实际应用了什么
    parts: list[str] = []
    color_note = _find_style_color(question)
    if color_note:
        parts.append(f"颜色改为 {color_note}")
    if graduated:
        g = graduated["visualVariables"]["graduated"]
        parts.append(
            f"按字段「{g['field']}」分成 {g['classes']} 级"
            + ("、暖色调" if ls.warm_ramp_name(question) else "")
        )
    if opacity_var:
        ov = opacity_var["visualVariables"]["opacity_variable"]
        parts.append(f"透明度按「{ov['field']}」渐变（{ov['range'][0]}~{ov['range'][1]}）")
    if "放大" in question or "变大" in question:
        parts.append("点半径放大")
    elif "缩小" in question or "变小" in question:
        parts.append("点半径缩小")
    if "描边" in question or "加粗" in question or "线宽" in question or "变粗" in question:
        parts.append("描边加粗")
    if changes.get("strokeWidth") == 0:
        parts.append("去掉边框")
    elif "透明" in question:
        parts.append("整体透明度调低")
    detail = "；".join(parts) if parts else "样式已更新"
    texts = [
        f"已更新图层「{layer.name}」的样式。",
        f"{detail}。地图上的显示已同步更新（成果栏不新增图表）。",
    ]
    if emit is not None:
        for text in texts:
            emit(EventType.THOUGHT_DELTA, {"text": text})
    return ExecutionResult(
        intent_id=request.intent_id or "style",
        trace=[TraceEvent(type="text", content=text) for text in texts],
        result_group=None,
        evidence=[],
        reflection="样式类请求按地图控制处理，直接更新图层样式，不产出成果图表。",
        final_answer="\n".join(texts),
    )


def try_execute(
    request: ExecuteRequest,
    *,
    ctx: ToolContext | None = None,
    emit: Any | None = None,  # EventEmitter.emit | None（None 时行为与现状完全一致）
) -> ExecutionResult | None:
    """用 LLM Agent 循环执行；失败或无 key 返回 None（回退规则版 executor）。

    emit 非空时，各关键点同步产出事件（真流式）；trace 构建保留作为终结载荷。
    ctx 非空时透传给工具调用。
    """
    # 样式 / 地图控制类请求：直接改图层样式，不出成果卡（用户明确不希望改样式变成图片）。
    # 必须先于 chat guard 判定——planner 常把「把 XX 改成红色」这类样式指令归为 operation=chat，
    # 若先走 chat 就会变成一句「我无法修改样式」的文本，而不是真的去改地图。
    style_result = _try_style_request(request, emit=emit)
    if style_result is not None:
        return style_result
    # 问候 / 闲聊：不进入工具循环，直接回一段文字
    if request.operation == "chat":
        reply = request.summary or "你好，请告诉我你想分析什么。"
        return ExecutionResult(
            intent_id=request.intent_id or "chat",
            trace=[TraceEvent(type="text", content=reply)],
            result_group=None,
            evidence=[],
            final_answer=reply,
        )
    if not llm_client.is_available():
        return None

    # PydanticAI 迁移开关：默认走新 loop（pydai/executor_agent.py，无轮次上限）；
    # PA_AGENT=0 回旧手写 loop。产品层（样式短路/chat 守卫/is_available）已在上面共用。
    if os.environ.get("PA_AGENT", "1") != "0":
        from app.agent.pydai.executor_agent import pa_try_execute

        return pa_try_execute(request, ctx=ctx, emit=emit)

    target_layers = [
        DATA_REGISTRY.get_layer(target)
        for target in request.targets
        if DATA_REGISTRY.get_layer(target) is not None
    ]
    missing = list(request.data_check.missing) if request.data_check else []
    trace: list[TraceEvent] = [
        TraceEvent(type="ran_command", content='check_readiness("auto")'),
        TraceEvent(
            type="created",
            content="数据检查报告",
            meta=f"+{len(target_layers)} 就绪 · {len(missing)} 缺失",
        ),
        TraceEvent(
            type="text",
            content=(f"缺失数据：{'、'.join(missing)}，先执行可用部分。" if missing else "所需数据已就绪，开始执行分析。"),
        ),
    ]
    if emit is not None:
        emit(
            EventType.DATA_CHECK,
            {
                "state": "ready" if not missing else "partial",
                "ready": [layer.id for layer in target_layers],
                "missing": missing,
            },
        )

    messages: list[dict] = [{"role": "system", "content": _system_prompt(request, target_layers)}]
    # 多轮上下文记忆：历史对话注入（问题已抽离进 user prompt，历史仅作上下文）
    for turn in (request.history or [])[-6:]:
        role = turn.get("role")
        content = turn.get("content")
        if role not in ("user", "assistant") or not content:
            continue
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": _user_prompt(request)})
    tools = _tool_schemas()
    calls: list[tuple[str, dict, ToolResult]] = []
    generated: list[tuple[str, str, int]] = []
    finished = False
    interrupted = False
    conclusion: str | None = None  # 最终回复（不含思考过程叙述），供 final_answer
    # 重复调用治理：指纹 → (上次是否成功, 上次结果)。同参数重复调用复用结果、不真执行，
    # 防 LLM 同一工具反复跑（用户实测「执行时重复几次」）。consecutive 记连续命中次数，
    # 连续 3 次强制收尾打破死循环。
    tool_cache: dict[str, tuple[bool, ToolResult]] = {}
    consecutive: dict[str, int] = {}
    # 按步分片预算（loop engineering：预算按计划步骤分配，单步卡死不拖死整个 run）。
    # 仅当 plan 含真实工具名的步骤时启用；step.tool 语义标签（readiness/results/analysis 等）
    # 不可执行，不进可调用集合、也不参与推进判定。reached 记「其工具已成功产出」的步骤号。
    plan_steps = list(request.plan)
    executable = {tool.name for tool in TOOLS if tool.status.value == "ready"}
    do_step_budget = bool(plan_steps) and any(s.tool in executable for s in plan_steps)
    reached: set[int] = set()
    rounds_in_step = 0

    try:
        for _ in range(MAX_ROUNDS):
            if emit is not None:
                # 真流式：LLM 内容增量实时走 THOUGHT_DELTA；execute_code 的参数增量走 CODE_DELTA
                # 防御：LLM 可能把超大 GeoJSON 原样嵌入代码参数，无限流式下发会撑爆前端内存。
                # 每个工具调用累积超过阈值后停止继续发射（工具仍会执行，只是不再逐字符展示）。
                _code_arg_bytes: dict[int, int] = {}
                _CODE_ARG_EMIT_CAP = 100_000  # 单次 execute_code 参数最多下发展示 100KB

                def _on_tool_delta(_index: int, name: str, payload: dict) -> None:
                    if name == "execute_code" and payload.get("code") is not None:
                        total = _code_arg_bytes.get(_index, 0) + len(payload["code"])
                        _code_arg_bytes[_index] = total
                        if total <= _CODE_ARG_EMIT_CAP:
                            emit(EventType.CODE_DELTA, {"kind": "code", "text": payload["code"]})

                response = llm_client.chat_completion_stream(
                    messages,
                    tools=tools,
                    on_delta=lambda text: emit(EventType.THOUGHT_DELTA, {"text": text}),
                    on_tool_delta=_on_tool_delta,
                )
            else:
                response = llm_client.chat_completion(messages, tools=tools)
            # 用量落 RunArchive（llm_usage.jsonl，供追溯/成本核算）
            usage = response.get("usage") or {}
            if ctx is not None and ctx.event_emitter is not None:
                archive = getattr(ctx.event_emitter, "archive", None)
                if archive is not None:
                    try:
                        archive.record_llm_usage(usage=usage)
                    except Exception:
                        pass
            message = response["choices"][0]["message"]
            tool_calls = message.get("tool_calls") or []

            if message.get("content"):
                for paragraph in _split_paragraphs(message["content"]):
                    trace.append(TraceEvent(type="text", content=paragraph))

            if not tool_calls:
                # 最终轮：LLM 直接给结论（无工具调用）→ 记录为 final_answer（不含思考过程）
                conclusion = message.get("content") or None
                finished = True
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or None,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["function"]["name"],
                                "arguments": call["function"]["arguments"],
                            },
                        }
                        for call in tool_calls
                    ],
                }
            )

            stop_loop = False
            round_start = len(calls)  # 本轮起点：停滞检测只统计本轮真实执行的调用
            for call in tool_calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"]["arguments"] or "{}")
                except json.JSONDecodeError:
                    args = {}

                # 重复调用治理：同参数已执行过 → 复用上次结果，不真执行、不重复展示。
                # 成功与失败都复用（参数完全一致的重试只会得到同样结果，且副作用工具
                # 重复执行可能产生重复图层）；LLM 想重算必须改参数，否则由连续命中收尾。
                fp = _fingerprint(name, args)
                cached = tool_cache.get(fp)
                if cached is not None:
                    consecutive[fp] = consecutive.get(fp, 0) + 1
                    reused_payload = {
                        "success": cached[1].success,
                        "output": cached[1].output,
                        "message": cached[1].message,
                        "note": "该调用与此前某次调用参数完全一致，已复用上次结果，未重复执行；如确需重新计算请修改参数后再调用。",
                    }
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": json.dumps(reused_payload, ensure_ascii=False),
                        }
                    )
                    if consecutive[fp] >= 3:
                        # 同一调用连续重复 3 次 → LLM 卡死在重复调用上，强制收尾
                        conclusion = _force_conclude(messages, trace)
                        if conclusion:
                            finished = True
                        else:
                            interrupted = True
                        stop_loop = True
                        break
                    continue

                if emit is not None:
                    emit(EventType.TOOL_START, {"tool": name, "args": args})
                result = _call_tool(name, args, ctx=ctx)
                tool_cache[fp] = (result.success, result)
                consecutive[fp] = 1
                calls.append((name, args, result))
                arg_text = ", ".join(f"{key}={value}" for key, value in args.items()) if args else ""
                if emit is not None:
                    emit(
                        EventType.TOOL_RESULT,
                        {
                            "tool": name,
                            "ok": result.success,
                            "message": result.message,
                            "output": result.output,
                        },
                    )
                if result.success:
                    trace.append(
                        TraceEvent(type="ran", content=f"{name}({arg_text})", meta=result.message)
                    )
                    output_layer_id = result.output.get("output_layer_id")
                    if output_layer_id:
                        output_layer_id = str(output_layer_id)
                        if output_layer_id not in [g[0] for g in generated]:
                            display_name, count = _generated_info(output_layer_id, args)
                            generated.append((output_layer_id, display_name, count))
                            trace.append(
                                TraceEvent(type="generated", content=display_name, meta=f"+{count} 要素")
                            )
                            if emit is not None:
                                emit(
                                    EventType.GENERATED_LAYER,
                                    {
                                        "layer_id": output_layer_id,
                                        "name": display_name,
                                        "feature_count": count,
                                    },
                                )
                    # 中间层被 remove_layer 删掉：同步从 generated 累积列表剔除，
                    # 否则收尾 _register_generated(generated) 会把它复活（OSM 原始/裁剪层"删不掉"根因）。
                    if name == "remove_layer":
                        _prune_removed_generated(generated, str(result.output.get("layer_id") or ""))
                else:
                    trace.append(
                        TraceEvent(type="text", content=result.message or f"工具 {name} 执行失败")
                    )
                messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": _tool_result_payload(result)}
                )
            if stop_loop:
                break

            # 按步停滞检测：某步连续 STEP_BUDGET 轮未完成（其工具未产出成功结果），
            # 注入提示让 LLM 跳过该步继续后续，而不是让一个难啃的步骤烧掉整个 run。
            # 指纹去重 + consecutive>=3 收尾先于此处执行（防同参数死循环），这里只兜
            # 「参数在变但同一目标步骤没做成」的横向探索停滞。
            if do_step_budget and tool_calls:
                called = {name for name, _a, r in calls[round_start:] if r.success}
                advanced = False
                for i, s in enumerate(plan_steps):
                    if i not in reached and s.tool in executable and s.tool in called:
                        reached.add(i)
                        advanced = True
                if advanced:
                    rounds_in_step = 0
                else:
                    rounds_in_step += 1
                    next_step = next(
                        (i for i, s in enumerate(plan_steps) if i not in reached and s.tool in executable),
                        None,
                    )
                    if next_step is not None and rounds_in_step >= STEP_BUDGET:
                        target_step = plan_steps[next_step]
                        nudge = (
                            f"注意：步骤 {target_step.step_id}「{target_step.label}」已连续 "
                            f"{STEP_BUDGET} 轮没有完成（其依赖工具未产出成功结果），本步按跳过处理。"
                            "请继续后续步骤；若全部实质步骤已完成，请直接基于已有结果输出最终结论，"
                            "不要再调用工具。"
                        )
                        messages.append({"role": "user", "content": nudge})
                        if emit is not None:
                            emit(EventType.THOUGHT_DELTA, {"text": nudge})
                        reached.add(next_step)  # 按跳过处理，避免对同一目标重复提示
                        rounds_in_step = 0
        else:
            # 到达轮次上限仍未输出结论：强制收尾（禁用工具），让 LLM 基于已有结果直接总结。
            # 避免整轮回退失败话术——LLM 已做真实工具调用，结论应能由已有结果支撑。
            conclusion = _force_conclude(messages, trace)
            if not conclusion:
                raise RuntimeError("收尾调用失败")  # 交由外层抢救已完成的真实结果
            finished = True
            # 无需 break：for...else 的 else 块只在循环耗尽后执行，完成后自然落到循环后检查
    except Exception:
        # LLM 调用异常中断：不整单作废——若已拿到真实工具结果则抢救组装
        interrupted = True

    if interrupted:
        if not calls or not any(_r.success for _n, _a, _r in calls):
            return None  # 没有任何成功的工具结果 → 失败话术
        trace.append(
            TraceEvent(
                type="text",
                content="分析在途中遇到服务异常而中断，以下展示的是中断前已完成的真实计算结果。",
            )
        )
        finished = True
    elif not finished:
        return None  # 主循环非正常收尾且无抢救路径 → 失败话术
    elif not calls and not any(e.type == "text" for e in trace[3:]):
        return None  # 既无工具调用也无文字产出（空响应）→ 失败话术
    if not any(event.type == "text" for event in trace[3:]):
        trace.append(
            TraceEvent(type="text", content="分析完成，结果图层已同步到地图，指标与结论已生成到右侧成果栏。")
        )

    try:
        plots = ctx.meta.get("plots", []) if ctx is not None else []
        charts = ctx.meta.get("charts", []) if ctx is not None else []
        group = _build_group(request, calls, generated, plots=plots, charts=charts)
        if group is None:
            if calls and all(name in _MAP_CONTROL_TOOLS for name, _args, _r in calls):
                # 纯地图控制/样式 run：无指标卡、无生成图层，但操作已生效（事件已发射）。
                # 返回 trace-only result，避免回退规则版把成果吞掉。
                if not any(e.type == "text" for e in trace[3:]):
                    trace.append(
                        TraceEvent(type="text", content="已应用地图控制/样式操作，地图已同步更新（成果栏不新增卡片）。")
                    )
                return ExecutionResult(
                    intent_id=request.intent_id or "map-control",
                    trace=trace,
                    result_group=None,
                    evidence=[],
                    reflection="纯地图控制/样式类 run：工具事件已实时下发到地图，无分析成果卡。",
                    final_answer=conclusion or _last_text(trace),
                )
            if any(e.type == "text" for e in trace[3:]):
                # 组不出成果卡但有 Agent 文字产出（咨询/解释/说明性回应，如「带宽怎么设置」）：
                # 如实返回文字，不落入「服务不可用」失败话术。
                return ExecutionResult(
                    intent_id=request.intent_id or "text",
                    trace=trace,
                    result_group=None,
                    evidence=[],
                    reflection="Agent 已给出文字说明但未形成结果图层/成果卡（咨询、解释或执行未落成图层）。",
                    final_answer=conclusion or _last_text(trace),
                )
            return None
        evidence: list[str] = []
        for widget in group.widgets:
            evidence.extend(widget.evidence)
        _register_generated(generated)
        MEMORY.record(request.intent_id, group, evidence)
        return ExecutionResult(
            intent_id=request.intent_id,
            trace=trace,
            result_group=group,
            evidence=evidence,
            final_answer=conclusion or _last_text(trace),
            stop_reason=group.stop_reason,
        )
    except Exception:
        return None  # 组装异常 → 回退规则版
