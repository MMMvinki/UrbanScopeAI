"""Skill 静态校验 —— AST 分析 scripts/main.py 契约（语法 / --input--output 协议 / params 键）。

promote_script_to_skill 用 :func:`inspect_params_access` 收集脚本里
``params["x"]`` 直接访问的键，作为 input_schema 的推断起点；
validate_skill 用 :func:`validate_skill_code` 做深度分析（不落盘）。
"""

from __future__ import annotations

import ast

from app.models.skill import SkillValidation


def inspect_params_access(code: str) -> list[str]:
    """收集代码里 ``params["x"]`` / ``params.get("x")`` 访问的键（AST，去重保序）。

    返回值仅作为 input_schema 的**推断起点**（类型保守标 string、不猜必填），
    不视为权威——实际参数校验以运行时报错为准。
    """
    keys: list[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return keys

    def _add(node_key: ast.expr | None) -> None:
        if isinstance(node_key, ast.Constant) and isinstance(node_key.value, str):
            keys.append(node_key.value)
        elif isinstance(node_key, ast.Str):  # py<3.8 兼容
            keys.append(node_key.s)

    for node in ast.walk(tree):
        # params["x"]
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and node.value.id == "params":
            _add(node.slice)
        # params.get("x", ...)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "params"
            and node.func.attr == "get"
            and node.args
        ):
            _add(node.args[0])
    return list(dict.fromkeys(keys))


def validate_skill_code(code: str) -> SkillValidation:
    """对 skill 的 scripts/main.py 源码做静态校验（不执行、不落盘）。

    - 语法：compile() 通过？
    - 协议：代码里是否声明 ``--input`` / ``--output`` 两个参数（契约入口）。
    - 依赖：直接访问的 params 键清单。
    """
    errors: list[str] = []
    warnings: list[str] = []

    syntax_ok = True
    try:
        compile(code, "<skill-script>", "exec")
    except SyntaxError as exc:
        syntax_ok = False
        errors.append(f"main.py 语法错误：{exc.msg}（行 {exc.lineno}）")

    protocol_ok = "--input" in code and "--output" in code
    if not protocol_ok:
        errors.append(
            "main.py 必须按契约解析 --input / --output 两个命令行参数（promote 生成的脚本无需修改，"
            "手动 create_skill 请按 `python main.py --input in.json --output out.json` 编写）"
        )

    params_keys = inspect_params_access(code)
    return SkillValidation(
        protocol_ok=protocol_ok,
        syntax_ok=syntax_ok,
        direct_param_access=params_keys,
        warnings=warnings,
        errors=errors,
    )


__all__ = ["inspect_params_access", "validate_skill_code"]
