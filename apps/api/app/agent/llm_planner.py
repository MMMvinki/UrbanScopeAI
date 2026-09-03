"""LLM 驱动的 Planner：DeepSeek 把任意规划问题转成 PlanResult。

- 系统提示词**动态注入**：当前项目图层、分析操作、`skills/*/skill.md` 加载的命名 Skill、
  工具注册表（TOOLS）里的可用工具，以及输出 JSON schema；
- 输出用 Pydantic 校验并规范化（非法 operation/targets/plan 自动修正或丢弃）；
- `thinking` 字段记录 Planner 的思考过程（skill 命中依据 / 工具选择理由），前端展示；
- 支持多轮对话历史（history），让 Planner 在上下文中理解追问；
- 任何异常返回 None，由 planner.plan_for 回退到规则版（演示永不挂）。
"""

import json
import os
import re

from app.agent import llm_client
from app.agent.patterns import BASELINE_LAYERS, METHOD_TEMPLATES
from app.agent.readiness import check_readiness
from app.data_registry import DATA_REGISTRY
from app.memory_store import get_memory_lines, get_summary_lines
from app.models.agent import Clarify, PlanResult, PlanStep
from app.skills import skill_lines
from app.skills.registry import SKILLS, get_skill, get_skill_spec, skill_hints
from app.tools.registry import TOOLS

_OPERATIONS = ("coverage", "overlay", "nearest_distance", "density", "kernel_density", "supply_demand", "general", "chat")

# 已知的非图层数据标签（就绪检查里按缺失项展示，如 乱停乱放）
_KNOWN_DATA_LABELS = {"乱停乱放", "道路网络", "公共服务设施", "街区边界"}


def _question_hits_skill(question: str, skill) -> bool:
    """问题原文是否命中 skill 的某个关键词（「关键词合同」）。

    方法论技能只有问题确实包含其（已收窄的）关键词才驱动，防止 LLM 把工具级请求
    （等时圈/isochrone 等）误归为 skill、在计划头部挂无关 get_skill。
    """
    q = question.lower()
    return any(kw and kw.lower() in q for kw in getattr(skill, "keywords", ()))


# 技能创建任务检测：与 llm_planner 系统提示【技能创建任务】的触发语对齐。
# 用正则而非关键词子串——skill-creator 的 frontmatter 关键词（创建技能/沉淀技能/
# 把方法存成技能/制作技能/保存方法）覆盖不了「把刚才这套方法做成一个技能」这类
# 口径，而系统提示明确要求它们路由到 skill-creator。命中即强制走 skill-creator 兜底，
# 即使 LLM 把这类请求误判成 clarify（无 history 时「刚才这套」无指代，DeepSeek 合理
# 追问，但按设计应路由到 skill-creator 由执行器按方法论引导澄清，而不是门1 短路）。
_SKILL_CREATE_RE = re.compile(
    r"(创建|沉淀|生成|制作|新建|保存|升级)[^\s，。]{0,12}(技能|skill|Skill)"
    r"|(把|将).{0,20}(方法|流程|步骤|分析|脚本).{0,8}(做成|存成|沉淀成|升级为|保存为).{0,3}(技能|skill|Skill)"
    r"|skill[\s-]?creator"
)


def _is_skill_creation_request(question: str) -> bool:
    return bool(_SKILL_CREATE_RE.search(question))


# 人口热力语义检测：问题命中「人口/人群/常住 + 热力/热度/画像/密度」或「热力图/哪里人多」等，
# 即人口热力（population-heatmap）技能的口径。与 planner 系统提示【人口热力路由】同款口径。
# 用于**确定性兜底**：LLM 常把 intent_id 生成成非平凡 id（napshot 见 population_heatmap_planning_area），
# 使 get_skill(intent_id) 不命中、executable skill 路由落空 → plan 退回通用模板、人口被误判缺数据。
# 不赌 LLM 发对 id，命中即强制走人口热力技能。
_POPULATION_HEAT_RE = re.compile(
    r"(人口|人群|常住|户籍).{0,10}(热力|热度|画像|密度|分布|结构|构成)"
    r"|(热力图|热度地图|热度分布|哪里人多|哪里人最多|人口热力)"
)


def _is_population_heat_request(question: str) -> bool:
    return bool(_POPULATION_HEAT_RE.search(question or ""))


# 人口语义词：被 population-heatmap 技能的 huoyan_resident/huoyan_worker 数据源覆盖，
# 命中人口热力路由时把这些从「缺数据」列表剔除（人口数据在盘上，不是真缺失）。
_POP_TERMS_RE = re.compile(r"(人口|人群|常住|户籍|热力图|热度)")


def _population_heat_plan_detail(targets: list[str], required: list[str] | None = None) -> str:
    """人口热力确定性 plan 步 detail：写明 skill_id 与人口源，area 图层从已解析 targets 挑。

    area 优先选**非热力图层的范围多边形**（用户指定/上传的研究范围，如 规划范围_568b13），
    而不是 *_pop_heat 结果热力图层——后者是 skill 自己的产物，不能当裁剪范围（会自引用）。
    targets 可能不含范围图层（LLM 只把它放 required_data），故回退从 required 挑已注册的
    非热力图层。
    """
    candidates = list(targets) + list(required or [])
    area = next(
        (
            t
            for t in candidates
            if t
            and t != "population"
            and not t.endswith("_pop_heat")
            and DATA_REGISTRY.get_layer(t) is not None
            and getattr(DATA_REGISTRY.get_layer(t), "kind", None) != "heatmap"
        ),
        None,
    )
    clause = f", area_layer_id={area}" if area else ""
    return (
        f"execute_skill_script(skill_id=population-heatmap, population_source=huoyan_resident{clause})"
        " —— 读慧眼居住人口点（人数）物化 kind=heatmap 连续渐变热力面 + 附人群画像饼图；"
        "不是核密度/点位密度，勿路由到 kernel-density。"
    )


# ── 解释工具原理：确定性兜底（LLM 偶发把问题复述当 chat summary，见 _explain_for_question）──

# 原理/用法类问题关键词：命中才可能触发确定性解释
_EXPLAIN_KEYWORDS = (
    "原理", "怎么实现", "如何实现", "怎么工作", "如何工作", "是怎么算的", "怎么算的",
    "是什么", "怎么用", "如何用", "有什么用", "作用", "干嘛", "干吗", "咋用",
)

# 工具中文别名：问题里出现即视为提及该工具（tool.name 本身也参与匹配）
_TOOL_ALIASES: dict[str, list[str]] = {
    "coverage": ["覆盖率", "覆盖范围", "服务范围"],
    "spatial_join": ["空间叠加", "叠加"],
    "nearest_distance": ["最近距离"],
    "density": ["密度分析", "密度指数"],
    "kernel_density": ["核密度"],
    "supply_demand": ["供需匹配", "供需"],
    "isochrone": ["等时圈"],
    "statistics": ["统计指标", "指标汇总"],
    "spatial_clip": ["裁剪", "clip"],
    "buffer": ["缓冲区", "缓冲"],
}

# 常见分析工具的「原理」扩充说明（比 registry 一行描述更能回答"实现原理"）
_TOOL_PRINCIPLE: dict[str, str] = {
    "spatial_join": "通过空间拓扑关系（相交/包含/相邻）把两个图层的要素关联起来，统计目标图层要素落在叠加图层各要素内/上的数量与占比，得到两个图层在空间上的重叠分布。",
    "supply_demand": "用设施供给量（设施点数量、服务半径覆盖面积）与人口需求量（片区人口数/密度）的比值计算供需匹配满足度，值越高代表设施相对人口越充足，越低代表越紧缺。",
    "coverage": "以设施点为圆心按服务半径做真实缓冲区（米制 buffer），统计该缓冲区内覆盖到的目标要素数量与占比，衡量设施的几何可达覆盖。",
    "kernel_density": "对点要素做核密度估计，在每个空间位置上按带宽加权累计邻近点的贡献，生成连续的密度面（网格），反映点的聚集强度分布。",
    "nearest_distance": "计算目标图层每个要素到最近参考设施（POI/站点等）的直线距离，汇总平均距离/最近距离，衡量目标与设施的邻近程度。",
    "density": "计算图层要素的空间密度指数（单位面积内要素数量），是一个汇总数值而非密度分布面。",
    "isochrone": "按真实路网（Mapbox Isochrone API）计算从中心点步行/驾车/骑行 N 分钟内可到达的区域，比直线缓冲更真实，输出按分钟分色的可达多边形。",
}


def _mentioned_tools(text: str) -> list:
    """文本里提到的工具：英文名或中文别名命中即算（保持 TOOLS 顺序去重）。"""
    q = text.lower()
    hits: list = []
    for tool in TOOLS:
        if tool.status.value != "ready":
            continue
        aliases = _TOOL_ALIASES.get(tool.name, []) + [tool.name]
        if any(name and name.lower() in q for name in aliases):
            if tool.name not in {h.name for h in hits}:
                hits.append(tool)
    return hits


def _build_tool_explain(tools: list) -> str:
    """从工具注册表组装确定性解释：作用（原理优先）+ 参数。"""
    lines: list[str] = []
    for tool in tools:
        principle = _TOOL_PRINCIPLE.get(tool.name) or (tool.description or "")
        lines.append(f"【{tool.name}】{principle}")
        params = tool.parameters or {}
        if params:
            lines.append("参数：" + "、".join(f"{k}（{v}）" for k, v in params.items()))
    return "\n".join(lines)


_RESTATEMENT_RE = re.compile(r"^(解释|说明|讲|请解释|请说明|帮我解释|帮我说明|帮我讲|可以解释|能否解释|介绍一下)")


def _is_degenerate_summary(question: str, summary: str) -> bool:
    """chat summary 是否退化（只是复述问题/太短）：退化才用确定性解释覆盖。"""
    s = summary.strip()
    if not s:
        return True
    if _RESTATEMENT_RE.match(s) and len(s) < 60:
        return True

    def _norm(t: str) -> str:
        return re.sub(r"[^\w一-鿿]", "", t).lower()

    ns, nq = _norm(s), _norm(question)
    if not ns:
        return True
    # summary 与问题几乎一样（去掉"解释/说明"前缀后相同）
    stripped = _RESTATEMENT_RE.sub("", s)
    return _norm(stripped) == nq or nq and (nq in ns or ns in nq)


def _explain_for_question(question: str, history: list[dict] | None) -> str | None:
    """用户问某工具/操作原理时，从注册表确定性生成解释；命中不了返回 None。

    扫 question + 最近 2 条 user 轮（覆盖「解释呀」这类不带工具名的追问），
    不扫全部历史防跨轮 bleed。返回的解释只用于覆盖「退化」的 chat summary。
    """
    haystack = question
    for turn in (history or [])[-4:]:
        if turn.get("role") == "user" and turn.get("content"):
            haystack += "\n" + str(turn["content"])
    if not any(k in haystack for k in _EXPLAIN_KEYWORDS):
        return None
    tools = _mentioned_tools(haystack)
    if not tools:
        return None
    return _build_tool_explain(tools)


def _skill_lines() -> list[str]:
    """动态 skill 清单：只注入触发词（name + description），不注入方法论全文。

    对齐官方渐进披露：SKILL.md 的 frontmatter（name/description）是唯一常驻触发词，
    正文（body 方法论）只在命中后经 get_skill 工具按需加载，避免方法论常驻每次请求。
    """
    lines = []
    for skill in skill_hints():
        keywords = "、".join(skill["keywords"][:5])
        spec = get_skill(skill["id"])
        trigger = (spec.description if spec and spec.description else skill["core_question"]).strip() or skill["id"]
        plan_text = ""
        if spec and spec.plan and spec.plan.targets:
            tds = ", ".join(f"{k}={v}m" for k, v in spec.plan.target_distances.items()) or "默认 800m"
            plan_text = f"，默认计划 operation={spec.plan.operation}，targets={spec.plan.targets}，各目标距离 {tds}"
        lines.append(
            f"- {skill['id']} {skill['name']}：{trigger}（关键词 {keywords}{plan_text}）"
        )
    return lines


def _tool_lines() -> list[str]:
    """动态工具清单：来自 tools/registry.py（与执行阶段的 TOOL_IMPLEMENTATIONS 对齐）。

    过滤 user_visible=False 的技能 CRUD/脚本库工具——Planner 只需出「读取方法论」等
    plan 步骤，真正的创建落盘交给 Executor 按 skill-creator 方法论调 create_skill。
    隐藏工具仍在 Executor 工具面（_tool_schemas）保留，供落盘调用。
    """
    return [
        f"- {tool.name} [{tool.category}] {tool.description}"
        for tool in TOOLS
        if tool.status.value == "ready" and tool.user_visible
    ]


def _layer_lines() -> list[str]:
    """动态图层清单：来自当前项目 DATA_REGISTRY 的真实图层。

    以前硬编码 8 个图层（study_area/schools/population_grid…），与运行态真实数据
    （深圳行政区划/道路/POI）完全脱节，LLM 按过期清单选目标 → _resolve_id 全失败
    被静默丢弃 → 数据检查误报 ready → 执行时只能拿无关 POI 凑数（人群画像→餐饮 POI）。
    """
    lines = []
    for layer in DATA_REGISTRY.list_layers():
        note = "（空间上下文，永远只是分析背景，绝不选为 targets）" if layer.id == "study_area" else ""
        lines.append(f"- {layer.id} {layer.name} {layer.geometry} {layer.feature_count} 要素{note}")
    return lines


def _system_prompt(conversation_id: str | None = None) -> str:
    memory_lines = get_summary_lines(conversation_id)
    memory_section = (
        ["", "【多轮记忆】此前几轮的分析摘要（对话上下文，规划时可参考）"]
        + memory_lines
        + [""]
        if memory_lines
        else [""]
    )
    # Sprint 2：结构化记忆（会话已创建的图层 / 已应用的样式操作）
    struct_lines = get_memory_lines(conversation_id)
    struct_section = (
        ["【图层与样式记忆】本会话已创建的图层及样式操作（规划/追问时可沿用）"]
        + struct_lines
        + [""]
        if struct_lines
        else [""]
    )
    lines = [
        "你是 UrbanScope 空间分析平台的 Planner：把城市规划师的提问转成结构化分析计划。",
        "这是面向规划师的空间分析工作台，执行阶段会调用确定性 GIS 工具计算真实数值；你不要编造任何覆盖率/距离/占比。",
        *memory_section,
        *struct_section,
        "【当前项目图层】id / 名称 / 几何 / 要素数（真实数据，targets 只能从中选）",
        *_layer_lines(),
        "",
        "【分析操作】coverage 覆盖率 / overlay 空间叠加 / nearest_distance 最近距离 / density 密度 / kernel_density 核密度 / supply_demand 供需匹配 / general 通用 / chat 闲聊（非分析类）",
        "",
        "【命名 Skill】（当前项目已加载，问题命中其语义时按定义规划，否则走通用引擎）",
        *_skill_lines(),
        "",
        "【当前可用技能】（可复用分析能力，问题语义命中其用途时，plan 步骤 tool 直接用 execute_skill_script）",
        *skill_lines(),
        "",
        "【可用工具】（执行阶段可调用，规划时据此挑选每一步的 tool）",
        *_tool_lines(),
        "",
        "【输出】只输出一个 JSON 对象（不要 markdown、不要多余文字）：",
        "{",
        '  "thinking": "你的思考过程：为什么命中这个 Skill / 选择这些操作与工具",',
        '  "intent_id": "稳定的英文 id，如 coverage_schools 或 skill id"',
        '  "summary": "一句话中文说明要做的分析"',
        '  "operation": "coverage|overlay|nearest_distance|density|supply_demand|general|chat"',
        '  "targets": ["问题涉及的分析对象图层 id（可多个；不含 study_area）"]',
        '  "distance": 800,',
        '  "target_distances": {"图层id": 距离},',
        '  "required_data": ["完成分析所需图层 id"]',
        '  "plan": [{"label":"步骤中文名","tool":"工具名或null","detail":"说明"}]',
        '  "clarify": {"needs":"缺什么信息","question":"一句话追问","choices":["选项1","选项2"]}',  # 可选，见规则
        "}",
        "",
        "规则：",
        "- targets 必须使用上面列出的真实图层 id；空间上下文图层绝不进 targets；",
        "- 问题提到多个图层（如 产业+轨道）就把它们都放进 targets；",
        "- 问题里出现具体距离（如 800米）就提取到 distance；未提及用 800；",
        "- operation 按问题的核心分析意图选最合适的一个；",
        "- plan 的每一步尽量关联【可用工具】里的一个 tool，无法映射的步骤 tool 用 null；",
        "- 只有问题是纯闲聊 / 问候 / 询问平台能力（如“你好”“你能做什么”）而非空间分析任务时，operation 才用 chat：",
        '  targets=[], required_data=[], plan=[], intent_id="chat"，summary 写一句自然的中文回复（如自我介绍 + 能做的分析）；',
        "- 【解释工具原理】：用户询问某个具体分析工具/操作的原理、实现方式或用法（如“空间叠加怎么实现”“核密度是什么”）时仍用 chat，但 summary 必须直接给出对该工具的完整解释——它做什么、输入什么参数、大致如何计算——可引用【可用工具】里该工具的 description 与 parameters。绝不允许把问题复述一遍当作 summary；",
        "- 绝大多数规划问题都应落到具体分析操作，chat 仅在明显非分析类对话时使用；",
        "- 【追问】：只有**用户自己没把意图说清楚**（如没指定分析片区 / 关注维度 / 具体对象）时才输出顶层 \"clarify\"（question 一句话追问，choices 给 2~4 个可能的理解，needs 说明缺什么信息）。一旦输出 clarify，其余字段全部填空（targets/plan/required_data 留空数组，operation 留空字符串）；不要一不确定就 clarify，多数问题可直接规划；",
        "- 【数据缺失 ≠ 追问】：目标对象在【当前项目图层】里没有对应图层（如用户要“人群画像”但无人口数据）**不是**意图模糊，此时**不得输出 clarify**——照常输出正式计划，把缺失对象原文写进 targets/required_data（后端会提示缺失，由用户选择跳过或补数据）；绝对不要在 clarify/choices 里给“用其他数据源/POI 间接推断”之类的替代方案；",
        "- 【防重复追问】：澄清最多 2 轮。对话历史里已出现 Agent 追问且用户已给出回答或授权（如说“可以开始了”“开始吧”“按默认”“继续”）时，必须直接产出正式计划，绝不再 clarify；",
        "- 【命中技能】：问题意图与【当前可用技能】里某个 skill 的用途匹配（如“对 XX POI 做核密度”命中 kernel-density）时，plan 步骤 tool 用 execute_skill_script，并在 plan 步骤的 detail 里写明 skill id；参数以用户实际提供的为准，用户没给的参数不要臆造，由执行阶段向用户追问后补齐；不匹配时才拆成通用分析工具步骤；",
        "- 【人口热力路由】：问题意图是「人口热力/热力图/热度分布/哪里人多」，或「人口/人群/常住 + 密度热力」组合时，直接命中 population-heatmap 可执行技能——plan 步骤 tool 用 execute_skill_script，detail 写明 skill_id=population-heatmap；不要路由到 kernel-density 或 density 操作（那是点位密度，每个点权重 1，丢人口本值）；人口数据源用户没说就从 huoyan_resident（居住人口）里选；population_source 必须是原始数据源 id（huoyan_resident/huoyan_worker），绝不能选已生成的热力图图层（*_pop_heat）当源；",
        "- 【技能创建任务】：当用户说要「创建/沉淀/生成/制作一个（新）技能」「把刚才的方法做成技能」「新建分析技能」「升级脚本为技能」时——即使用户顺带描述了技能的具体用途（如幼儿园覆盖率检查），这也是**技能创建任务**而非空间分析任务：intent_id 必须是该 skill 的 id（skill-creator），plan 第一步 tool 用 get_skill（skill_id=skill-creator，detail 写“读取 SKILL.md 方法论正文”），后续由执行器按方法论引导（澄清/落盘/校验）；不要把它规划成直接的空间分析。若用户明确要求先提问（如说“先问我第一个问题”）且关键信息未提供，允许先输出一次 clarify 追问；但用户随后给出回答或授权时，必须直接产出正式计划，不得再次 clarify；",
        "- 【数据缺失】：目标对象在【当前项目图层】里没有对应图层（如用户要“人群画像”但无人口数据）时，不要自行改换数据源、也不要用别的图层顶替，把该对象原文如实写进 targets/required_data（即使清单里没有该 id 也照写原文，后端会标记为缺失并在执行前让用户选择跳过或补数据）；",
        "- 只输出一个 JSON 对象。",
    ]
    return "\n".join(lines)


def _resolve_id(text: str) -> str | None:
    """图层 id 或中文名 → 规范化 id。"""
    if DATA_REGISTRY.get_layer(text) is not None:
        return text
    layer = DATA_REGISTRY.get_layer_by_name(text)
    return layer.id if layer is not None else None


def _is_known_data_source(text: str) -> bool:
    """判断 text 是否为可解析的数据源 id（get_source_meta 能命中 → 该数据源始终可读）。

    区分「数据的源」与「分析图层」：源（如 huoyan_resident）不是缺图层的对象，
    不该进门2 缺失面板——否则每个人口热力请求都要多跳过一次「huoyan_resident 缺失」。
    真实图库基线与人口/人群等语义标签（_POPULATION_HINTS）照常照旧拦截。
    """
    try:
        from app.data_sources import get_source_meta

        return get_source_meta(text) is not None
    except Exception:
        return False


def _coerce_targets(raw: object) -> tuple[list[str], list[str]]:
    """规范化 targets，返回 (resolved, unmet)。

    resolved：解析到真实图层的分析对象 id；
    unmet：解析不到图层的分析对象原文（如「人群画像」）——**不静默丢弃**，
    必须保留给上游 → data_check 会报缺失 → 前端让用户选择跳过/补数据/换问法。
    旧实现静默丢目标正是「人群画像 → 餐饮 POI 凑数」的源头。
    """
    targets: list[str] = []
    unmet: list[str] = []
    if not isinstance(raw, list):
        return targets, unmet
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text:
            continue
        layer_id = _resolve_id(text)
        if layer_id is None or layer_id == "study_area":
            # 可解析的数据源 id（如 huoyan_resident）不是缺图层对象：它始终能读，
            # 不该进门2 缺失列表，正常作为「源」参与分析。
            if layer_id is None and not _is_known_data_source(text) and text not in unmet:
                unmet.append(text)
            continue
        if layer_id not in targets:
            targets.append(layer_id)
    return targets, unmet


def _coerce_required(raw: object, targets: list[str], unmet: list[str]) -> list[str]:
    # 基线只保留运行态真实存在的图层（study_area/residential 可能不存在，
    # 无条件塞入会让数据检查永远报缺失、触发「缺数据就停」误伤每条请求）。
    baseline = [layer for layer in BASELINE_LAYERS if DATA_REGISTRY.get_layer(layer) is not None]
    items: list[str] = list(baseline) + list(targets) + list(unmet)
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, str):
                continue
            label = item.strip()
            if not label or label in items:
                continue
            layer_id = _resolve_id(label)
            if layer_id is not None and layer_id not in items:
                items.append(layer_id)
            elif layer_id is None and label in _KNOWN_DATA_LABELS:
                items.append(label)
    return items


# 数据域提示表：问题里出现这些词，说明用户要的是某类「平台没有的数据」。
# 若解析到的 targets 里没有语义覆盖该域的图层，把命中词追加进 unmet —— 确定性拦住
# 「可解析但语义错配」的替换（如 用 poi_汽车服务相关替 乱停乱放 / poi_餐饮替 人口画像），
# 让这些对象照常进 data_check.missing → 门2 由用户选择，而不是静默换数据源。
_POPULATION_HINTS = ("人口", "人群", "常住", "户籍", "普查")

_DOMAIN_HINTS: dict[str, tuple[str, ...]] = {
    "人口": _POPULATION_HINTS,
    "乱停乱放": ("乱停乱放", "停车乱象", "乱停车"),
    "外卖": ("外卖", "骑手", "外送"),
    "共享单车": ("共享单车", "共享单车投放点", "单车"),
}


def _domain_check(question: str, targets: list[str], unmet: list[str]) -> list[str]:
    """把「可解析但语义错配」的分析对象也拦进门2（如 用 poi_餐饮替人口画像）。"""
    result = list(unmet)
    for label, hints in _DOMAIN_HINTS.items():
        if not any(h in question for h in hints):
            continue
        covered = any(label in t or any(h in t for h in hints) for t in targets)
        if covered:
            continue
        for word in hints:
            if word in question and word not in result:
                result.append(word)
                break
    return result


def _coerce_plan(raw: object, operation: str) -> list[PlanStep]:
    if isinstance(raw, list):
        steps: list[PlanStep] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            if not label:
                continue
            steps.append(
                PlanStep(
                    step_id=f"s{len(steps) + 1}",
                    label=label[:80],
                    tool=str(item["tool"]) if item.get("tool") else None,
                    detail=str(item["detail"]) if item.get("detail") else None,
                )
            )
        # 步骤数下限：太简短的分析计划显示效果差，不足 2 步回退模板（仍保留 LLM 标签）。
        # 不能设太高——LLM 针对具体任务给出的 3~5 步真实计划比通用模板更有信息量。
        if len(steps) >= 2:
            return steps
    template = METHOD_TEMPLATES.get(operation, METHOD_TEMPLATES["general"])
    return [
        PlanStep(step_id=f"s{index + 1}", label=spec.label, tool=spec.tool, detail=spec.detail)
        for index, spec in enumerate(template)
    ]


def _safe_intent_id(raw: object, operation: str, targets: list[str]) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]", "_", str(raw or "").strip())[:60]
    return value or f"{operation}_{'_'.join(targets) or 'generic'}"


# clarify 文本里的「缺数据」特征：LLM 有时把数据缺失误报成意图澄清（如
# needs="缺少人口数据图层" / question="当前项目中没有人口数据图层…"）。
# 命中即视为门2（数据缺失），强制走正常计划 + unmet_targets，不渲染追问气泡。
_MISSING_CLARIFY_RE = re.compile(
    r"(缺|没有|缺少|无|未提供|暂未).{0,10}(数据|图层)|(数据|图层).{0,10}(缺|没有|缺少|无)"
)


def _normalize(question: str, raw: dict, history: list[dict] | None = None) -> PlanResult | None:
    thinking = str(raw.get("thinking") or "").strip()[:500]

    # 门1：意图澄清。顶层 clarify 存在且 question 非空 → 停下追问、不产出计划。
    # 触发后无视其余所有字段（防 LLM 半澄清半执行，把追问硬当作 operation）。
    raw_clarify = raw.get("clarify")
    if isinstance(raw_clarify, dict):
        clarify_question = str(raw_clarify.get("question") or "").strip()
        clarify_needs = str(raw_clarify.get("needs") or "").strip()
        # 数据缺失 ≠ 意图模糊：clarify 语义是「缺数据/没数据」时，不算门1，
        # 落到下方正常路径（_domain_check 会把缺失词追加进 unmet → 走门2 缺失面板）。
        is_missing_signal = bool(
            _MISSING_CLARIFY_RE.search(clarify_question) or _MISSING_CLARIFY_RE.search(clarify_needs)
        )
        if clarify_question and not is_missing_signal and not _is_skill_creation_request(question):
            choices = [
                c for c in raw_clarify.get("choices", []) if isinstance(c, str) and c.strip()
            ][:5]
            needs = clarify_needs or None
            return PlanResult(
                intent_id="clarify",
                summary=clarify_question,
                thinking=thinking or "用户意图不明确，需要澄清后再规划。",
                operation="clarify",
                targets=[],
                distance=800,
                target_distances={},
                required_data=[],
                plan=[],
                data_check=None,
                clarify=Clarify(needs=needs, question=clarify_question, choices=choices),
                unmet_targets=[],
            )

    operation = raw.get("operation") if isinstance(raw.get("operation"), str) else "general"

    # 闲聊：不抽取图层/操作，直接返回一段回复文本
    if operation == "chat":
        summary = str(raw.get("summary") or "").strip() or (
            "你好，我是 UrbanScope 的空间分析助手。你可以让我对当前片区做城市体检、设施覆盖、人口密度等空间分析。"
        )
        # 「问工具原理」兜底：LLM 偶发把问题复述当 summary（用户看到"不解释"），
        # 命中工具且 summary 退化时用注册表确定性解释覆盖。
        explain = _explain_for_question(question, history)
        if explain and _is_degenerate_summary(question, summary):
            summary = explain
        return PlanResult(
            intent_id="chat",
            summary=summary,
            thinking=thinking or "用户问题属于闲聊/询问能力，不触发空间分析。",
            operation="chat",
            targets=[],
            distance=800,
            target_distances={},
            required_data=[],
            plan=[],
            data_check=None,
        )

    if operation not in _OPERATIONS:
        operation = "general"

    targets, unmet = _coerce_targets(raw.get("targets"))
    unmet = _domain_check(question, targets, unmet)

    try:
        distance = max(100, min(10000, int(raw.get("distance", 800))))
    except (TypeError, ValueError):
        distance = 800

    target_distances: dict[str, int] = {}
    tds = raw.get("target_distances")
    if isinstance(tds, dict):
        for key, value in tds.items():
            if not isinstance(key, str):
                continue
            layer_id = _resolve_id(key)
            if layer_id is None or layer_id == "study_area" or layer_id in target_distances:
                continue
            try:
                target_distances[layer_id] = max(100, min(10000, int(value)))
            except (TypeError, ValueError):
                pass

    plan = _coerce_plan(raw.get("plan"), operation)
    summary = str(raw.get("summary") or "").strip() or f"围绕问题涉及的数据执行分析。"
    intent_id = _safe_intent_id(raw.get("intent_id"), operation, targets)

    # Skill 一致性：intent_id 命中已注册 Skill 时，operation/距离以 skill.md 的默认计划
    # 为权威（LLM 语义联想可能漂移，skill 流程必须可复现）。
    # 方法论 skill 的 targets 通常留空（由 Planner 按当前项目真实图层动态选取，
    # 防硬编码顶掉真实 POI 图层），故 operation 对齐不依赖 targets 非空；
    # 只有 skill.plan.targets 非空时才强制替换 targets/距离。
    skill = get_skill(intent_id)
    if skill is not None and skill.plan:
        operation = skill.plan.operation
        if skill.plan.targets:
            resolved_targets: list[str] = []
            for target in skill.plan.targets:
                if DATA_REGISTRY.get_layer(target) is not None:
                    if target not in resolved_targets:
                        resolved_targets.append(target)
                elif target not in unmet:
                    unmet.append(target)
            targets = resolved_targets
            target_distances = dict(skill.plan.target_distances)
            if not thinking:
                thinking = f"命中 Skill「{skill.name}」，按 frontmatter 定义的 {operation} 流程对齐计划。"
        elif not thinking:
            thinking = f"命中 Skill「{skill.name}」，按 frontmatter 的 {operation} 流程对齐（targets 留空由 Planner 动态选取）。"

    required = _coerce_required(raw.get("required_data"), targets, unmet)

    # SKILL.md 驱动 Skill 命中（渐进披露），按「关键词合同」：
    # 方法论技能只有问题原文真的包含其（已收窄的）关键词才驱动。等时圈/isochrone 等工具级
    # 请求不含 生活圈/体检/评估 框架词 → 绝不挂方法论技能，直接走通用计划调工具；LLM 偶发把
    # 这类请求误归为 skill（如「地铁站等时圈」→ 公共服务设施评估）时，关键词不中即降级。
    # 两种分支：
    # 1) skill-creator（技能创建任务）：plan 第一步固定 get_skill 读取 SKILL.md 方法论正文，
    #    清空 targets/required，后续由 Executor 按方法论引导（澄清/落盘/校验）；
    # 2) 分析方法论技能（15 分钟生活圈 / 城市体检 / 公共服务评估等）：保留 LLM 计划与
    #    targets/required（缺数据闸门不能丢），只在计划头部补 get_skill 步骤让 Executor
    #    加载方法论正文——计划卡展示完整分析路径，而非单步「读取方法论」。
    prompt_skill = get_skill(intent_id)
    if prompt_skill is not None and getattr(prompt_skill, "kind", None) == "prompt":
        if prompt_skill.id != "skill-creator" and not _question_hits_skill(question, prompt_skill):
            prompt_skill = None
    if prompt_skill is None:
        # 关键词兜底：只在当前问题原文匹配（不扫 history/summary，防跨轮 bleed 与 LLM 复述
        # 误命中），最长关键词胜出。skill-creator 的创建词（创建/沉淀/新技能…）同样参与。
        best, best_len = None, 0
        q = question.lower()
        for candidate in SKILLS:
            if getattr(candidate, "kind", None) != "prompt":
                continue
            for kw in candidate.keywords:
                if kw and kw.lower() in q and len(kw) > best_len:
                    best, best_len = candidate, len(kw)
        # 技能创建请求兜底：问题明显是「做成/创建/沉淀…技能」时，即使关键词没进
        # frontmatter 列表（如「把刚才这套方法做成一个技能」的「做成…技能」口径），
        # 也强制落到 skill-creator——系统提示已明确这类请求路由到 skill-creator。
        if best is None and _is_skill_creation_request(question):
            best = get_skill("skill-creator")
        prompt_skill = best
    if prompt_skill is not None:
        intent_id = prompt_skill.id
        if prompt_skill.id == "skill-creator":
            # 创建任务：方法论引导，不产出通用分析计划
            plan = [
                PlanStep(
                    step_id="s1",
                    label=f"读取「{prompt_skill.name}」方法论",
                    tool="get_skill",
                    detail=f"读取 {prompt_skill.id} 的 SKILL.md 方法论正文，按方法论引导创建/沉淀技能",
                )
            ]
            targets, unmet = [], []
            required = []
            if not thinking:
                thinking = f"命中「{prompt_skill.name}」技能方法论，plan 第一步 get_skill 读取 SKILL.md 正文（渐进披露），由执行器按方法论引导。"
        else:
            # 分析方法论技能：保留 LLM 计划与 targets/required，头部补 get_skill 步骤。
            if not any(step.tool == "get_skill" for step in plan):
                plan = [
                    PlanStep(
                        step_id="s1",
                        label=f"读取「{prompt_skill.name}」方法论",
                        tool="get_skill",
                        detail=f"读取 {prompt_skill.id} 的 SKILL.md 方法论正文，按方法论引导执行",
                    )
                ] + [
                    PlanStep(
                        step_id=f"s{i + 2}", label=step.label, tool=step.tool, detail=step.detail
                    )
                    for i, step in enumerate(plan)
                ]
            if not thinking:
                thinking = f"命中「{prompt_skill.name}」分析方法论，plan 头部 get_skill 加载方法论正文，按方法论执行。"

    # ── 人口热力确定性兜底 ──
    # 用户问题命中「人口热力/人群画像」语义时，强制路由到 population-heatmap 可执行技能。
    # LLM 常把 intent_id 生成成非平凡 id（如 population_heatmap_planning_area），使
    # get_skill(intent_id) 不命中、executable skill 路由落空 → plan 退回通用模板、人口域
    # 被 _domain_check 误判缺数据（前端弹「跳过/上传/换问法」= 问用户要数据）。这里不赌 LLM
    # 发对 id，命中即强制 intent_id=population-heatmap、plan 单步 execute_skill_script 并
    # 免掉人口域缺数据（huoyan_resident 在盘上，人口已覆盖）。
    if _is_population_heat_request(question):
        pop_skill = get_skill_spec("population-heatmap")
        if pop_skill is not None:
            # 人口由 huoyan_resident 覆盖：把人口/人群等词从缺数据列表剔除，避免误弹缺失面板。
            unmet = [u for u in unmet if not _POP_TERMS_RE.search(u)]
            required = [r for r in required if not _POP_TERMS_RE.search(r)]
            # 剔掉热力结果图层（*_pop_heat）自引用：它不是分析目标，也不该当 targets——
            # 否则会被误选为 area_layer_id 或当作人口源。原始源由 skill 内部还原/读取。
            targets = [t for t in targets if not t.endswith("_pop_heat")]
            required = [r for r in required if not r.endswith("_pop_heat")]
            # required 里可能是真实范围图层（LLM 只放 required 不放 targets）：补回 targets，
            # 前端据此呈现分析范围、area 传递到 execute_skill_script。
            for r in list(required):
                if r not in targets and DATA_REGISTRY.get_layer(r) is not None and not r.endswith("_pop_heat"):
                    targets.append(r)
            intent_id = pop_skill.id
            # 人口热力是单步技能执行，不套通用模板；area 图层（几何范围）作为 area_layer_id 传参。
            plan = [
                PlanStep(
                    step_id="s1",
                    label=f"执行「{pop_skill.name}」",
                    tool="execute_skill_script",
                    detail=_population_heat_plan_detail(targets, required),
                )
            ]
            if not thinking:
                thinking = (
                    f"命中 Skill「{pop_skill.name}」（人口热力确定性路由）：plan 用 execute_skill_script，"
                    f"population_source=huoyan_resident（居住），area 图层取已解析 targets；"
                    "人口数据在盘上，不属缺失，已从缺数据列表剔除。"
                )

    return PlanResult(
        intent_id=intent_id,
        summary=summary,
        thinking=thinking or f"按 {operation} 操作规划，目标图层 {'、'.join(targets) or '（未确定）'}。",
        operation=operation,
        targets=targets,
        distance=distance,
        target_distances=target_distances,
        required_data=required,
        plan=plan,
        data_check=check_readiness(required),
        unmet_targets=unmet,
    )


# PydanticAI 迁移开关：默认走新 loop（pydai/planner_agent.py）；PA_AGENT=0 回旧 loop。
PA_AGENT = os.environ.get("PA_AGENT", "1") != "0"


def try_plan(
    question: str, history: list[dict] | None = None, conversation_id: str | None = None
) -> PlanResult | None:
    """尝试用 LLM 生成计划；失败或无 key 返回 None（回退规则版）。

    history: 多轮对话记录（[{role, content}]），与当前问题拼接为完整上下文。
    conversation_id: 会话 id，用于注入该会话的多轮记忆摘要。
    """
    if PA_AGENT:
        from app.agent.pydai.planner_agent import pa_try_plan

        return pa_try_plan(question, history, conversation_id)
    if not llm_client.is_available():
        return None
    messages: list[dict] = [{"role": "system", "content": _system_prompt(conversation_id)}]
    for turn in (history or [])[-6:]:
        role = turn.get("role")
        content = turn.get("content")
        if role not in ("user", "assistant") or not content:
            continue
        messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})
    try:
        raw = llm_client.chat_json_messages(messages, max_tokens=2500)
    except Exception:
        return None
    try:
        return _normalize(question, raw, history)
    except Exception:
        return None
