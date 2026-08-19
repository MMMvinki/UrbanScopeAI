"""Skills 注册表：SKILL.md 驱动 skill（``skills/*/SKILL.md``）+ 可执行 skill（``*/skill.json``）。

- ``SKILLS``：SKILL.md 驱动 skill 列表（启动时加载一次，变更需重启）；
- ``all_skills()``：合并可执行 skill（SkillStore.list()），供统一出口（skill_lines / REST）展示。
"""

from app.skills.loader import load_skills
from app.skills.store import STORE

# 启动时加载一次（SKILL.md 变更需重启服务生效）
SKILLS: list = load_skills()


def get_skill(skill_id: str):
    """按 id 查 SKILL.md 驱动的 Skill（kind=prompt）；未命中返回 None。

    可执行 skill 请用 ``STORE.get(skill_id)``（workspace 优先回退 builtin）。
    """
    for skill in SKILLS:
        if skill.id == skill_id:
            return skill
    return None


def get_skill_spec(skill_id: str):
    """统一查 skill：先 SKILL.md 驱动，后可执行（可执行 workspace 优先回退 builtin）。"""
    prompt = get_skill(skill_id)
    if prompt is not None:
        return prompt
    return STORE.get(skill_id)


def skill_hints() -> list[dict]:
    """给 Planner 的动态 skill 清单（命中匹配 + 描述），不带完整指标细节。"""
    return [
        {
            "id": skill.id,
            "name": skill.name,
            "core_question": skill.core_question,
            "keywords": skill.keywords,
        }
        for skill in SKILLS
    ]


def all_skills(scope: str | None = None) -> list[dict]:
    """统一 skill 列表（SKILL.md 驱动 + 可执行），每项带 kind，供前端 / LLM 提示注入。

    scope 只作用于可执行部分（SKILL.md 驱动的都内置）：workspace 时 prompt 不进结果，
    builtin / all / None 时全进。
    """
    prompt_ok = scope in (None, "all", "builtin")
    out: list[dict] = [s.model_dump(mode="json") for s in SKILLS] if prompt_ok else []
    seen = {s["id"] for s in out}
    for item in STORE.list(scope):
        if item["id"] in seen:
            continue  # 带脚本与流程引导撞 id 时保留流程引导（提示优先命中）
        seen.add(item["id"])
        out.append(item)
    out.sort(key=lambda e: e["id"])
    return out
