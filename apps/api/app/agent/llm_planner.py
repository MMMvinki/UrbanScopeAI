"""LLM 驱动的 Planner：DeepSeek 把任意规划问题转成 PlanResult。

- 系统提示词**动态注入**：当前项目图层、分析操作、`skills/*/skill.md` 加载的命名 Skill、
  工具注册表（TOOLS）里的可用工具，以及输出 JSON schema；
- 输出用 Pydantic 校验并规范化（非法 operation/targets/plan 自动修正或丢弃）；
- `thinking` 字段记录 Planner 的思考过程（skill 命中依据 / 工具选择理由），前端展示；
- 支持多轮对话历史（history），让 Planner 在上下文中理解追问；
- 任何异常返回 None，由 planner.plan_for 回退到规则版（演示永不挂）。
"""

import re

from app.agent import llm_client
from app.agent.patterns import BASELINE_LAYERS, METHOD_TEMPLATES
from app.agent.readiness import check_readiness
from app.data_registry import DATA_REGISTRY
from app.memory_store import get_memory_lines, get_summary_lines
from app.models.agent import Clarify, PlanResult, PlanStep
from app.skills import skill_lines
from app.skills.registry import SKILLS, get_skill, skill_hints
from app.tools.registry import TOOLS

_OPERATIONS = ("coverage", "overlay", "nearest_distance", "density", "kernel_density", "supply_demand", "general", "chat")

# 已知的非图层数据标签（就绪检查里按缺失项展示，如 乱停乱放）
_KNOWN_DATA_LABELS = {"乱停乱放", "道路网络", "公共服务设施", "街区边界"}


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
    """动态工具清单：来自 tools/registry.py（与执行阶段的 TOOL_IMPLEMENTATIONS 对齐）。"""
    return [
        f"- {tool.name} [{tool.category}] {tool.description}"
        for tool in TOOLS
        if tool.status.value == "ready"
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
        "- 绝大多数规划问题都应落到具体分析操作，chat 仅在明显非分析类对话时使用；",
        "- 【追问】：只有**用户自己没把意图说清楚**（如没指定分析片区 / 关注维度 / 具体对象）时才输出顶层 \"clarify\"（question 一句话追问，choices 给 2~4 个可能的理解，needs 说明缺什么信息）。一旦输出 clarify，其余字段全部填空（targets/plan/required_data 留空数组，operation 留空字符串）；不要一不确定就 clarify，多数问题可直接规划；",
        "- 【数据缺失 ≠ 追问】：目标对象在【当前项目图层】里没有对应图层（如用户要“人群画像”但无人口数据）**不是**意图模糊，此时**不得输出 clarify**——照常输出正式计划，把缺失对象原文写进 targets/required_data（后端会提示缺失，由用户选择跳过或补数据）；绝对不要在 clarify/choices 里给“用其他数据源/POI 间接推断”之类的替代方案；",
        "- 【防重复追问】：澄清最多 2 轮。对话历史里已出现 Agent 追问且用户已给出回答或授权（如说“可以开始了”“开始吧”“按默认”“继续”）时，必须直接产出正式计划，绝不再 clarify；",
        "- 【命中技能】：问题意图与【当前可用技能】里某个 skill 的用途匹配（如“对 XX POI 做核密度”命中 kernel-density）时，plan 步骤 tool 用 execute_skill_script，并在 plan 步骤的 detail 里写明 skill id；参数以用户实际提供的为准，用户没给的参数不要臆造，由执行阶段向用户追问后补齐；不匹配时才拆成通用分析工具步骤；",
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
            if layer_id is None and text not in unmet:
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


# 数据域提示表：问题里出现这些词，说明用户要的是「人口类」数据。
# 若解析到的 targets 里没有任何人口图层，把命中词追加进 unmet —— 确定性拦住
# 「人口画像」被解析成存在但语义错配的图层（如 poi_餐饮）从而绕过缺失闸门。
_POPULATION_HINTS = ("人口", "人群", "常住", "户籍", "普查")


def _domain_check(question: str, targets: list[str], unmet: list[str]) -> list[str]:
    """把「可解析但语义错配」的分析对象也拦进门2（如 用 poi_餐饮替人口画像）。"""
    result = list(unmet)
    if any(k in question for k in _POPULATION_HINTS):
        has_population = any("人口" in t for t in targets)
        if not has_population:
            for word in _POPULATION_HINTS:
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
        if clarify_question and not is_missing_signal:
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
        return PlanResult(
            intent_id="chat",
            summary=str(raw.get("summary") or "").strip()
            or "你好，我是 UrbanScope 的空间分析助手。你可以让我对当前片区做城市体检、设施覆盖、人口密度等空间分析。",
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

    # Skill 一致性：intent_id 命中已注册 Skill 时，operation/targets/距离以 skill.md 的
    # 默认计划为权威（LLM 语义联想可能漂移，skill 流程必须可复现）。
    # 被过滤掉的不可解析 Skill 目标同样进 unmet，不静默丢弃。
    skill = get_skill(intent_id)
    if skill is not None and skill.plan and skill.plan.targets:
        operation = skill.plan.operation
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

    required = _coerce_required(raw.get("required_data"), targets, unmet)

    # SKILL.md 驱动 Skill 命中（渐进披露）：intent_id 直接命中，或 question+history 命中其
    # 关键词（LLM 可能把 intent_id 写成 skill_creator_xx 而非规范 id）时，强制走 skill
    # 流程：plan 第一步固定 get_skill 读取 SKILL.md 方法论正文（body 按需加载），后续由
    # Executor 按方法论引导（澄清/落盘/校验）；不落入通用分析模板。
    prompt_skill = get_skill(intent_id)
    if not (prompt_skill is not None and getattr(prompt_skill, "kind", None) == "prompt"):
        haystack = " ".join(
            [str(h.get("content") or "") for h in (history or [])] + [question, summary]
        ).lower()
        best, best_len = None, 0
        for candidate in SKILLS:
            if getattr(candidate, "kind", None) != "prompt":
                continue
            for kw in candidate.keywords:
                if kw and kw.lower() in haystack and len(kw) > best_len:
                    best, best_len = candidate, len(kw)
        prompt_skill = best
    if prompt_skill is not None:
        plan = [
            PlanStep(
                step_id="s1",
                label=f"读取「{prompt_skill.name}」方法论",
                tool="get_skill",
                detail=f"读取 {prompt_skill.id} 的 SKILL.md 方法论正文，按方法论引导创建/沉淀技能",
            )
        ]
        intent_id = prompt_skill.id
        targets, unmet = [], []
        required = []
        if not thinking:
            thinking = f"命中「{prompt_skill.name}」技能方法论，plan 第一步 get_skill 读取 SKILL.md 正文（渐进披露），由执行器按方法论引导。"

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


def try_plan(
    question: str, history: list[dict] | None = None, conversation_id: str | None = None
) -> PlanResult | None:
    """尝试用 LLM 生成计划；失败或无 key 返回 None（回退规则版）。

    history: 多轮对话记录（[{role, content}]），与当前问题拼接为完整上下文。
    conversation_id: 会话 id，用于注入该会话的多轮记忆摘要。
    """
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
