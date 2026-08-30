"""Skill 工具注册表（10 个 ToolSpec）——可执行 Skill / 可复用分析能力的增删改查运行。

注册模式（对齐 registry.py「事实源 + 派发表分离」）：
- 本文件提供 ``SKILL_TOOL_SPECS``，registry.py 末尾 ``TOOLS += SKILL_TOOL_SPECS``；
- 派发实现在 skill_ops.py，tools/__init__.py 末尾挂载。

Category：``skill``（8 个，映射到「空间分析」货架）+ ``code``（脚本货架 2 个）。
全部 ``status="ready"``：planner/executor 直接读 TOOLS，自动进 LLM。
"""

from __future__ import annotations

from app.models.tool import ToolSpec

SKILL_TOOL_SPECS: list[ToolSpec] = [
    ToolSpec(
        name="list_skills",
        category="skill",
        description=(
            "列出所有可复用分析能力（Skill）。Skill = 一个可重跑的目录："
            "SKILL.md（触发词 + 步骤）+ skill.json（契约）+ scripts/main.py（可执行逻辑），"
            "由 execute_code 脚本提升或内置。用 execute_skill_script 直接复用；"
            "内置（scope=builtin）只读，改需先 copy_skill_to_workspace。"
        ),
        display_description="列出所有可复用分析能力（Skill）。",
        status="ready",
        parameters={"scope": "可选：workspace / builtin / all（默认全部，workspace 优先）"},
        json_schema={
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["all", "workspace", "builtin"],
                    "description": "过滤范围，默认 all",
                }
            },
        },
    ),
    ToolSpec(
        name="get_skill",
        category="skill",
        description=(
            "查看某个 Skill 的详情：input_schema（参数契约）、output_schema、validation、"
            "provenance（run_count/来源）。execute_skill_script 前先看它的参数要求。"
        ),
        display_description="查看某个 Skill 的参数契约与详情。",
        status="ready",
        parameters={"skill_id": "skill id（如 kernel-density）"},
        json_schema={
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "skill id（必填）"}
            },
            "required": ["skill_id"],
        },
    ),
    ToolSpec(
        name="execute_skill_script",
        category="skill",
        description=(
            "运行一个可执行 Skill（可复用分析能力）：全新子进程执行其 scripts/main.py，真计算。"
            "产物图层自动注册上图（按 output.json 声明的 style 分层/分类设色），图表进成果栏。"
            "内置 skill（scope=builtin）只读不能改，但可以直接运行；改逻辑需先 "
            "copy_skill_to_workspace 再 edit_skill。params 里不传的参数用 Skill 默认值。"
            "已用过的参数组合会命中缓存（tool_cache）；要强制重算请改参数（如换网格/带宽）。"
        ),
        display_description="运行一个可执行 Skill，产物图层上图、图表进成果栏。",
        status="ready",
        parameters={
            "skill_id": "skill id（必填）",
            "params": "按该 skill 的 input_schema 传参（对象）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "skill id（必填）"},
                "params": {
                    "type": "object",
                    "description": "运行参数（遵循该 skill 的 input_schema；可选字段用默认值）",
                },
            },
            "required": ["skill_id"],
        },
    ),
    ToolSpec(
        name="create_skill",
        category="skill",
        description=(
            "新建一个工作区可执行 Skill：写 skill.json（status=draft）+ scripts/main.py + "
            "requirements.txt + README。main.py 必须按契约解析 --input / --output 两个命令行参数："
            "input.json 是 wrapper {workspace, skill_id, params}，算完 json.dump 写 output.json"
            "（output 的 layers 数组声明产物图层，layer_id/name/path/feature_count/fields/style）。"
            "code 为空时给协议合规的占位实现，之后用 edit_skill 替换逻辑。"
            "id 必须小写字母开头、仅含小写字母/数字/连字符（kebab-case）、2-50 字符。"
        ),
        display_description="新建一个可复用分析能力（可执行 Skill）。",
        status="ready",
        user_visible=False,
        parameters={
            "skill_id": "skill id（小写字母开头，kebab-case，2-50 字符）",
            "name": "展示名（中文）",
            "description": "功能说明（触发词）",
            "code": "可选：scripts/main.py 源码（符合 --input/--output 契约）",
            "requirements": "可选：依赖包列表",
        },
        json_schema={
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "id：小写字母开头，仅小写字母/数字/连字符，2-50 字符"},
                "name": {"type": "string", "description": "展示名"},
                "description": {"type": "string", "description": "功能说明"},
                "code": {"type": "string", "description": "scripts/main.py 源码（符合 --input/--output 契约）"},
                "requirements": {"type": "array", "items": {"type": "string"}, "description": "依赖包（pip 包名）"},
            },
            "required": ["skill_id"],
        },
    ),
    ToolSpec(
        name="edit_skill",
        category="skill",
        description=(
            "修改工作区可执行 Skill（内置只读，需先 copy_skill_to_workspace）。"
            "深合并非空字段，revision+1 并重新静态校验。可改 name/description/status/version/"
            "code/input_schema/output_schema/requirements。改 code 后建议 validate_skill 复查。"
        ),
        display_description="修改工作区 Skill 的代码或元信息。",
        status="ready",
        user_visible=False,
        parameters={
            "skill_id": "skill id（必填）",
            "name": "可选：新展示名",
            "description": "可选：新说明",
            "code": "可选：新 scripts/main.py 源码",
            "input_schema": "可选：新参数契约（JSON Schema）",
        },
        json_schema={
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "skill id（必填）"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "code": {"type": "string", "description": "新 scripts/main.py 源码"},
                "input_schema": {"type": "object", "description": "新参数契约（JSON Schema）"},
                "requirements": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["skill_id"],
        },
    ),
    ToolSpec(
        name="validate_skill",
        category="skill",
        description=(
            "纯分析不落盘：静态校验可执行 Skill 的 scripts/main.py——语法、--input/--output 协议、"
            "直接访问的 params 键清单。validate_skill 是深度分析唯一权威（promote 的 "
            "input_schema 只是 AST 推断起点）。errors 为空即可安全运行。"
        ),
        display_description="校验 Skill 的 scripts/main.py 契约（不执行）。",
        status="ready",
        user_visible=False,
        parameters={"skill_id": "skill id（必填）", "code": "可选：用这段代码校验（不读磁盘）"},
        json_schema={
            "type": "object",
            "properties": {
                "skill_id": {"type": "string", "description": "skill id（必填）"},
                "code": {"type": "string", "description": "可选：待校验的代码（覆盖磁盘 scripts/main.py）"},
            },
            "required": ["skill_id"],
        },
    ),
    ToolSpec(
        name="copy_skill_to_workspace",
        category="skill",
        description=(
            "把内置可执行 Skill（scope=builtin，只读）整目录复制到工作区，获得可修改副本"
            "（status 继承，revision+1）。之后 edit_skill 可改。copy 到已存在同名则拒绝。"
        ),
        display_description="把内置 Skill 复制到工作区获得可修改副本。",
        status="ready",
        user_visible=False,
        parameters={"skill_id": "内置 skill id（必填）"},
        json_schema={
            "type": "object",
            "properties": {"skill_id": {"type": "string", "description": "内置 skill id（必填）"}},
            "required": ["skill_id"],
        },
    ),
    ToolSpec(
        name="promote_script_to_skill",
        category="skill",
        description=(
            "把脚本库（list_scripts）里的 execute_code 脚本升级为可复用 Skill："
            "生成协议包装 scripts/main.py（原脚本另存 original_<name>.py，**不重写脚本逻辑**），"
            "input_schema 用 AST 从脚本里 params[\"x\"] 推断（类型保守标 string、不猜必填，"
            "见 validation.warnings，建议再 edit_skill 修正）。升级后新会话可用一句话复用该分析。"
        ),
        display_description="把脚本库里的脚本升级为可复用 Skill。",
        status="ready",
        user_visible=False,
        parameters={
            "script_name": "脚本库中的文件名（必填，如 20260818-step2-kde.py）",
            "name": "可选：skill 展示名",
            "description": "可选：功能说明",
        },
        json_schema={
            "type": "object",
            "properties": {
                "script_name": {"type": "string", "description": "脚本库中的 .py 文件名（必填）"},
                "name": {"type": "string", "description": "skill 展示名"},
                "description": {"type": "string", "description": "功能说明"},
            },
            "required": ["script_name"],
        },
    ),
    ToolSpec(
        name="list_scripts",
        category="code",
        description=(
            "列出 execute_code 执行过的脚本库（.workdir/script/）：每次执行归档为 "
            "<时间戳>-step<n>-<用途>.py + 同名 .metadata.json + _scripts_index.jsonl。"
            "配合 read_script / promote_script_to_skill 复用历史分析。"
        ),
        display_description="列出 execute_code 执行过的历史脚本。",
        status="ready",
        user_visible=False,
        parameters={},
        json_schema={"type": "object", "properties": {}},
    ),
    ToolSpec(
        name="read_script",
        category="code",
        description=(
            "读取脚本库里的一个脚本内容（promote_script_to_skill 前的预览）。"
            "也接受不带 .py 后缀的名字。文件不存在返回明确错误。"
        ),
        display_description="读取脚本库里的某个脚本内容。",
        status="ready",
        user_visible=False,
        parameters={"script_name": "脚本文件名（必填，如 20260818-step2-kde.py）"},
        json_schema={
            "type": "object",
            "properties": {"script_name": {"type": "string", "description": "脚本文件名（必填）"}},
            "required": ["script_name"],
        },
    ),
]
