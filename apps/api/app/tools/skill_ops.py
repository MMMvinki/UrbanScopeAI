"""Skill 工具派发（10 个 handler）——SkillStore 的薄包装。

对齐 ``_file_ops_factory`` / ``_report_factory`` 模式：skill_ops 导出
``register_skill_tools()``，tools/__init__.py 末尾
``TOOL_IMPLEMENTATIONS.update(register_skill_tools())``。

handler 统一 ``(params: dict) -> ToolResult`` 签名；需要上下文的
（execute_skill_script / promote 溯源）内部用 get_current_context() 读取
（run_tool 已注入 contextvar）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from app.models.tool import ToolResult
from app.skills import get_skill_spec
from app.skills.store import STORE, SkillError
from app.tools.context import get_current_context
from app.workspace import SCRIPT_DIR

# 运行结果里塞给 LLM 看的字段子集（output.json + 注册的图层摘要）
_RUN_OUTPUT_KEYS = ("success", "summary", "layer_id", "stats")


def _err(message: str) -> ToolResult:
    return ToolResult(success=False, message=message)


def _clean(text: str) -> str:
    return str(text or "").strip()


# ── Skill 增删改查运行 ────────────────────────────────────────────


def run_list_skills(params: dict) -> ToolResult:
    scope = _clean(params.get("scope")) or None
    if scope not in (None, "all", "workspace", "builtin"):
        return _err(f"scope 仅支持 workspace / builtin / all，收到：{scope}")
    skills = STORE.list(scope)
    return ToolResult(
        output={"skills": skills, "count": len(skills)},
        message=f"{len(skills)} 个可复用分析能力",
    )


def run_get_skill(params: dict) -> ToolResult:
    skill_id = _clean(params.get("skill_id"))
    if not skill_id:
        return _err("缺少 skill_id")
    # 统一查：SKILL.md 驱动 / 可执行两形态合一；渐进披露下经此工具按需加载方法论正文，不常驻提示。
    skill = get_skill_spec(skill_id)
    if skill is None:
        return _err(f"skill 不存在：{skill_id}")
    # 返回 SKILL.md 方法论正文（body）供 Executor 引导；可执行 skill 的 body 由 store 补读。
    message = (
        f"{skill.name}（已返回 SKILL.md 方法论正文）"
        if getattr(skill, "body", None)
        else f"{skill.name}（{skill.scope}，{skill.status}）"
    )
    return ToolResult(
        output=skill.model_dump(mode="json"),
        message=message,
    )


def run_execute_skill_script(params: dict) -> ToolResult:
    skill_id = _clean(params.get("skill_id"))
    if not skill_id:
        return _err("缺少 skill_id")
    skill_params = params.get("params") or {}
    if not isinstance(skill_params, dict):
        return _err("params 必须是对象（{参数名: 值}）")
    ctx = get_current_context()
    try:
        result = STORE.run(skill_id, skill_params, ctx=ctx)
    except SkillError as exc:
        return _err(str(exc))
    output = result.get("output") or {}
    return ToolResult(
        success=bool(result.get("success")),
        output=result,
        message=(
            str(output.get("summary") or "")
            or ("运行成功" if result.get("success") else "运行失败")
        ),
    )


def run_create_skill(params: dict) -> ToolResult:
    skill_id = _clean(params.get("skill_id"))
    if not skill_id:
        return _err("缺少 skill_id")
    try:
        skill = STORE.create(
            skill_id=skill_id,
            name=_clean(params.get("name")) or skill_id,
            description=_clean(params.get("description")),
            code=params.get("code"),
            requirements=params.get("requirements"),
        )
    except SkillError as exc:
        return _err(str(exc))
    return ToolResult(
        output=skill.model_dump(mode="json"),
        message=f"已创建 skill {skill.id}（{skill.status}）",
    )


def run_edit_skill(params: dict) -> ToolResult:
    skill_id = _clean(params.get("skill_id"))
    if not skill_id:
        return _err("缺少 skill_id")
    patch = {
        key: params[key]
        for key in ("name", "description", "status", "version", "code", "input_schema", "output_schema", "requirements")
        if params.get(key) is not None
    }
    if not patch:
        return _err("没有需要修改的字段")
    try:
        skill = STORE.update(skill_id, patch)
    except SkillError as exc:
        return _err(str(exc))
    return ToolResult(
        output=skill.model_dump(mode="json"),
        message=f"skill {skill.id} 已更新（revision {skill.revision}）",
    )


def run_validate_skill(params: dict) -> ToolResult:
    skill_id = _clean(params.get("skill_id"))
    if not skill_id:
        return _err("缺少 skill_id")
    try:
        result = STORE.validate(skill_id, code=params.get("code"))
    except SkillError as exc:
        return _err(str(exc))
    data = result.model_dump(mode="json")
    return ToolResult(
        success=not data["errors"],
        output=data,
        message="校验通过，可安全运行" if not data["errors"] else f"校验失败：{data['errors'][0]}",
    )


def run_copy_skill_to_workspace(params: dict) -> ToolResult:
    skill_id = _clean(params.get("skill_id"))
    if not skill_id:
        return _err("缺少 skill_id")
    try:
        skill = STORE.copy_to_workspace(skill_id)
    except SkillError as exc:
        return _err(str(exc))
    return ToolResult(
        output=skill.model_dump(mode="json"),
        message=f"已复制到工作区（可 edit_skill 修改）：{skill.id}",
    )


def run_promote_script_to_skill(params: dict) -> ToolResult:
    script_name = _clean(params.get("script_name"))
    if not script_name:
        return _err("缺少 script_name（脚本库里的 .py 文件名）")
    ctx = get_current_context()
    try:
        skill = STORE.promote_script(
            script_name,
            name=_clean(params.get("name")) or None,
            description=_clean(params.get("description")) or None,
            run_id=getattr(ctx, "run_id", None),
        )
    except SkillError as exc:
        return _err(str(exc))
    return ToolResult(
        output=skill.model_dump(mode="json"),
        message=f"已把脚本 {script_name} 提升为 skill {skill.id}（input_schema 为 AST 推断，建议 edit_skill 修正）",
    )


# ── 脚本货架 ───────────────────────────────────────────────────────


def _script_index() -> dict[str, dict]:
    """读 _scripts_index.jsonl → {文件名: 元数据}（Phase C 落盘，缺失则空）。"""
    index_path = SCRIPT_DIR / "_scripts_index.jsonl"
    if not index_path.is_file():
        return {}
    out: dict[str, dict] = {}
    try:
        for line in index_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                import json

                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("filename"):
                out[str(entry["filename"])] = entry
    except OSError:
        pass
    return out


def run_list_scripts(params: dict) -> ToolResult:
    if not SCRIPT_DIR.is_dir():
        return ToolResult(output={"scripts": [], "count": 0}, message="脚本库为空")
    index = _script_index()
    scripts: list[dict] = []
    for path in sorted(SCRIPT_DIR.glob("*.py")):
        meta = index.get(path.name, {})
        scripts.append(
            {
                "name": path.name,
                "path": f".workdir/script/{path.name}",
                "size": path.stat().st_size if path.is_file() else 0,
                "run_id": meta.get("run_id"),
                "step": meta.get("step"),
                "semantic_name": meta.get("semantic_name"),
                "description": meta.get("description"),
                "has_error": meta.get("has_error"),
                "archived_at": meta.get("archived_at"),
            }
        )
    return ToolResult(output={"scripts": scripts, "count": len(scripts)}, message=f"脚本库 {len(scripts)} 个脚本")


def run_read_script(params: dict) -> ToolResult:
    name = Path(_clean(params.get("script_name"))).name
    if not name:
        return _err("缺少 script_name")
    if not name.endswith(".py"):
        name += ".py"
    path = SCRIPT_DIR / name
    if not path.is_file():
        return _err(f"脚本不存在：{name}（应在 .workdir/script/ 下；可先 list_scripts）")
    try:
        code = path.read_text(encoding="utf-8")
    except OSError as exc:
        return _err(f"读取脚本失败：{exc}")
    return ToolResult(output={"name": name, "path": str(path), "content": code[:20000]}, message=f"已读取 {name}")


def register_skill_tools() -> dict[str, Callable[[dict], ToolResult]]:
    return {
        "list_skills": run_list_skills,
        "get_skill": run_get_skill,
        "execute_skill_script": run_execute_skill_script,
        "create_skill": run_create_skill,
        "edit_skill": run_edit_skill,
        "validate_skill": run_validate_skill,
        "copy_skill_to_workspace": run_copy_skill_to_workspace,
        "promote_script_to_skill": run_promote_script_to_skill,
        "list_scripts": run_list_scripts,
        "read_script": run_read_script,
    }
