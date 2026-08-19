"""Skill 模型 re-export：统一模型在 ``app/models/skill.py``（SkillSpec 两形态合一）。

保留本文件是为了让 ``skills/store.py`` 的 import 路径稳定（``from app.skills.models import ...``）。
全部符号指向统一模型。
"""

from app.models.skill import (
    SkillIndicator,
    SkillPlanSpec,
    SkillRuntime,
    SkillSpec,
    SkillValidation,
)

__all__ = [
    "SkillIndicator",
    "SkillPlanSpec",
    "SkillRuntime",
    "SkillSpec",
    "SkillValidation",
]
