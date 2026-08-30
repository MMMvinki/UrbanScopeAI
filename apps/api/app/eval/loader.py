"""loader：加载 YAML 评估集 → EvalCase 列表。

评估集根目录 = apps/api/eval_sets/（git 版本化，见设计文档第 8 节）。
"""

from __future__ import annotations

from pathlib import Path

import yaml

from app.eval.models import EvalCase

# 评估集根：apps/api/eval_sets/
EVAL_SETS_DIR = Path(__file__).resolve().parent.parent.parent / "eval_sets"


def _load_yaml_file(path: Path) -> list[EvalCase]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cases_raw = raw.get("cases", [])
    if not isinstance(cases_raw, list):
        raise ValueError(f"评估集 {path.name} 缺少 cases 列表")
    cases: list[EvalCase] = []
    for item in cases_raw:
        case = EvalCase(**item)
        errors = case.validate_consistency()
        if errors:
            raise ValueError(f"{path.name}: " + "; ".join(errors))
        cases.append(case)
    return cases


def load_set(name: str) -> list[EvalCase]:
    """加载单个评估集文件（不带 .yaml 后缀）。"""
    path = EVAL_SETS_DIR / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"评估集不存在：{path}")
    return _load_yaml_file(path)


def load_all() -> list[EvalCase]:
    cases: list[EvalCase] = []
    for path in sorted(EVAL_SETS_DIR.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        cases.extend(_load_yaml_file(path))
    return cases


def list_sets() -> list[str]:
    return sorted(p.stem for p in EVAL_SETS_DIR.glob("*.yaml") if not p.name.startswith("_"))


def get_set_description(name: str) -> str:
    """评估集顶部的 description 字段（供 /eval/sets 展示）。"""
    path = EVAL_SETS_DIR / f"{name}.yaml"
    if not path.is_file():
        return ""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return ""
    return str(raw.get("description", ""))
