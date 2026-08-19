"""Skill 模型：产品级分析技能（流程引导 / 带脚本两形态统一模型）。

Skill 是产品级命名的分析能力，两类形态共用一套模型：

- **流程引导 skill**（``kind="prompt"``）：只有 SKILL.md（Claude Code / Anthropic 风格
  frontmatter + 正文），由 ``app.skills.loader`` 加载，Planner 命中后按提示编排工具；
- **带脚本 skill**（``kind="executable"``）：SKILL.md + ``skill.json``（契约，含
  input_schema / status / revision / provenance）+ ``scripts/main.py``（确定性执行），
  由 ``app.skills.store`` 加载，Executor 用 ``execute_skill_script`` 代码直跑。

两者共享同一套 Planner / Executor / Tool Runtime 执行路径；模型字段全给默认值，
prompt skill 走提示字段（core_question/keywords/indicators/body/plan），
executable skill 走契约字段（input_schema/version/status/revision/provenance）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.tool import ToolStatus


class SkillIndicator(BaseModel):
    """Skill 下的单个指标：所需数据、MVP 实现深度与步骤。"""

    name: str
    data: str
    # MVP 实现深度：真实计算 / 半真实审查 / 缺数据阻断
    mvp_depth: str
    status: ToolStatus = ToolStatus.planned
    steps: list[str] = Field(default_factory=list)


class SkillPlanSpec(BaseModel):
    """Skill 默认分析计划（Planner 命中该 Skill 时按此生成 PlanResult）。"""

    operation: str = "general"
    targets: list[str] = Field(default_factory=list)
    target_distances: dict[str, int] = Field(default_factory=dict)


class SkillRuntime(BaseModel):
    """可执行 skill 的运行环境声明（requirements.txt 为真源）。"""

    language: str = "python"
    python: str | None = None  # 推荐解释器版本；None = 用 sys.executable
    dependencies: list[str] = Field(default_factory=list)


class SkillValidation(BaseModel):
    """可执行 skill 的静态校验结果（validate_skill 纯分析，不落盘执行）。"""

    protocol_ok: bool = True  # scripts/main.py 是否按 --input/--output 契约
    syntax_ok: bool = True
    direct_param_access: list[str] = Field(default_factory=list)  # 代码里 params["x"] 的键
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class SkillSpec(BaseModel):
    """统一 Skill 模型：流程引导 + 带脚本两种形态。

    kind 决定形态；字段按形态分两组，全部有默认值，两个 loader 各自只填自己那组。
    """

    schema_version: str = "1.0"  # skill.json 契约版本
    id: str
    kind: str = "prompt"  # prompt（流程引导，LLM 编排）| executable（带脚本，直跑）
    name: str = ""
    # Anthropic L1 触发词：描述「干什么 + 何时用」，Planner 命中依据。
    # prompt skill 从 SKILL.md frontmatter 读；executable skill 从 skill.json 读。
    description: str = ""

    # ── 流程引导字段（kind=prompt）──────────────────────────────────
    core_question: str = ""
    # 触发该 Skill 的匹配词（Planner 命中即走该 Skill 的流程）
    keywords: list[str] = Field(default_factory=list)
    # 所需数据：图层名或数据标签（如 "乱停乱放" 表示无对应图层，就绪检查会标记缺失）
    required_data: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    # 指标清单：供执行器按指标逐项产出结果
    indicators: list[SkillIndicator] = Field(default_factory=list)
    # SKILL.md 正文（Markdown，供 Agent 读取完整流程 / 前端详情展示）
    body: str = ""
    # 命中该 Skill 时的默认计划（operation / targets / target_distances）
    plan: SkillPlanSpec | None = None

    # ── 可执行字段（kind=executable，skill.json 契约）──────────────
    scope: str = "workspace"  # builtin（只读）| workspace（可变）
    version: str = "0.1.0"
    revision: int = 1  # 每次写操作（edit/create 覆盖/copy）+1
    status: str = "draft"  # draft | validated（工作区首次成功 run 后自动升级）| failed
    entry: str = "scripts/main.py"  # 相对 skill 目录的执行入口
    runtime: SkillRuntime = Field(default_factory=SkillRuntime)
    input_schema: dict = Field(default_factory=dict)  # {"type","properties","required"}
    output_schema: dict = Field(default_factory=dict)
    validation: SkillValidation = Field(default_factory=SkillValidation)
    provenance: dict = Field(
        default_factory=lambda: {
            "promoted_from": None,
            "created_by": None,
            "run_count": 0,
            "last_run_at": None,
        }
    )
    created_at: str = ""
    updated_at: str = ""
