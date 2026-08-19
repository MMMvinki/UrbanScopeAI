"""SKILL.md 加载器：读取流程引导 skill（`app/skills/<id>/SKILL.md` 或旧 `skill.md`）。

格式采用 Anthropic / Claude Code 风格 agent skill：
- 文件开头两个 `---` 之间是 YAML frontmatter，字段映射到 SkillSpec；
- frontmatter 之后是 Markdown 正文，作为完整流程说明存入 SkillSpec.body，
  供 Agent（Planner / Executor）读取执行细节，也供前端详情展示。

**只负责 kind=prompt（SKILL.md 驱动）skill**；kind=executable（可执行）skill 由
``app.skills.store`` 按 ``*/skill.json`` 发现并加载。
"""

from pathlib import Path

import yaml

from app.models.skill import SkillIndicator, SkillPlanSpec, SkillSpec

_SKILLS_ROOT = Path(__file__).resolve().parent  # app/skills/


def _coerce_status(value) -> str:
    """status 字段允许 yaml 里的字符串值，做一次白名单归一。"""
    return value if value in {"planned", "ready", "blocked"} else "planned"


def _parse_indicator(raw: dict) -> SkillIndicator:
    data = dict(raw)
    status = _coerce_status(data.pop("status", "planned"))
    return SkillIndicator(**data, status=status)


def _parse_skill(path: Path) -> SkillSpec | None:
    text = path.read_text(encoding="utf-8")
    # 只接受带 frontmatter 的 SKILL.md / skill.md
    if not text.startswith("---"):
        return None
    _, raw_front, *rest = text.split("---", 2)
    front: dict = yaml.safe_load(raw_front) or {}
    body = "---".join(rest).strip()
    if not isinstance(front, dict):
        return None

    # frontmatter → SkillSpec（kind=prompt）
    skill_id = str(front.get("id") or path.parent.name)
    indicators = [
        _parse_indicator(item)
        for item in front.get("indicators", [])
        if isinstance(item, dict)
    ]
    plan_raw = front.get("plan")
    plan = SkillPlanSpec(**plan_raw) if isinstance(plan_raw, dict) else None
    description = str(front.get("description") or "")
    return SkillSpec(
        id=skill_id,
        kind="prompt",
        entry="",  # SKILL.md 驱动 skill 无脚本入口（区别于可执行 skill 的 scripts/main.py）
        name=str(front.get("name", skill_id)),
        description=description,
        core_question=str(front.get("core_question", "")),
        keywords=[str(k) for k in front.get("keywords", [])],
        required_data=[str(k) for k in front.get("required_data", [])],
        outputs=[str(k) for k in front.get("outputs", [])],
        indicators=indicators,
        body=body,
        plan=plan,
    )


def load_skills() -> list[SkillSpec]:
    """扫描 skills 目录下所有 `*/SKILL.md`（兼容旧 `*/skill.md`），按 id 稳定排序返回。

    只加载流程引导 skill；同目录若另有 skill.json（带脚本），id 冲突时以 prompt 为准
    （registry 里 prompt 在前，executable 由 store 独立管理）。
    """
    skills: list[SkillSpec] = []
    for skill_dir in sorted(p for p in _SKILLS_ROOT.iterdir() if p.is_dir()):
        md = skill_dir / "SKILL.md"
        if not md.is_file():
            legacy = skill_dir / "skill.md"
            if legacy.is_file():
                md = legacy
            else:
                continue
        skill = _parse_skill(md)
        if skill:
            skills.append(skill)
    return skills
