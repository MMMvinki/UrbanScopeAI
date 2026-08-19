"""Skills：产品级分析技能子系统（Anthropic Agent Skills 对齐）。

统一 Skill = 一个目录，两种形态共用 `app/models/skill.py` 的 ``SkillSpec``：
- **流程引导**（kind=prompt）：只有 SKILL.md，Planner 命中后按提示编排工具；
- **带脚本**（kind=executable）：SKILL.md + skill.json（契约）+ scripts/main.py，
  ``execute_skill_script`` 代码直跑（确定性）。

发现机制：
- 流程引导：扫 ``skills/*/SKILL.md``（loader.py → registry.SKILLS）；
- 带脚本：扫 ``*/skill.json`` 两棵树（store.STORE：builtin 只读 / 工作区可变）。

对外统一出口是 ``skill_lines()``（Planner/Executor 提示注入）。
"""

from app.models.skill import SkillIndicator, SkillSpec
from app.skills.registry import SKILLS, STORE, all_skills, get_skill, get_skill_spec, skill_hints


def skill_lines() -> list[str]:
    """动态 skill 清单（流程引导 + 带脚本合并，供 Planner/Executor 系统提示注入）。

    每行给 id / 形态 / 名称 / 描述 / 参数名，让 LLM 知道有哪些技能、什么时候命中。
    """
    lines: list[str] = []
    for item in all_skills():
        kind = item.get("kind")
        tag = "带脚本" if kind == "executable" else "流程引导"
        scope = "内置只读" if item.get("scope") == "builtin" else "工作区"
        props = (item.get("input_schema") or {}).get("properties") or {}
        params = "、".join(props) or "无"
        desc = item.get("description") or item.get("core_question") or ""
        lines.append(f"- {item.get('id')} [{tag}·{scope}] {item.get('name')}：{desc}（参数: {params}）")
    return lines


__all__ = [
    "SKILLS",
    "STORE",
    "SkillIndicator",
    "SkillSpec",
    "all_skills",
    "get_skill",
    "get_skill_spec",
    "skill_hints",
    "skill_lines",
]
