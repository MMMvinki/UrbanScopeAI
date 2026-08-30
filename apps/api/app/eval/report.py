"""report：把 CaseResult 列表汇总成 Markdown 报告（设计文档第 7 节）。

覆盖矩阵 + 失败归类 + 维度统计；Phase 2 扩展趋势 diff。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

# 评估集根：apps/api/eval_reports/
EVAL_REPORTS_DIR = Path(__file__).resolve().parent.parent.parent / "eval_reports"


def summarize(results: list[dict]) -> dict:
    """公开汇总（runner / /eval/reports 端点复用；与 _summarize 同实现）。"""
    return _summarize(results)


def _summarize(results: list[dict]) -> dict:
    total = len(results)
    passed = sum(1 for r in results if r["passed"])
    by_category: dict[str, list[bool]] = {}
    by_tool: dict[str, list[bool]] = {}
    by_tier: dict[str, list[bool]] = {}
    # 覆盖矩阵：行 = tier·意图/工具，列 = 分类；空格 = 缺口（驱动出题闭环）
    matrix: dict[str, dict[str, list[bool]]] = {}
    fail_reasons: dict[str, int] = {}
    for r in results:
        by_category.setdefault(r["category"], []).append(r["passed"])
        by_tier.setdefault(r["tier"], []).append(r["passed"])
        if r.get("tool"):
            by_tool.setdefault(r["tool"], []).append(r["passed"])
        tier = r.get("tier", "")
        dim = f"tool:{r.get('tool')}" if tier == "tool" else f"{tier}:{r.get('operation') or r['category']}"
        matrix.setdefault(dim, {}).setdefault(r["category"], []).append(r["passed"])
        if not r["passed"]:
            for c in r.get("checks", []):
                if not c["ok"]:
                    name = c["name"]
                    fail_reasons[name] = fail_reasons.get(name, 0) + 1
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": (passed / total) if total else 0.0,
        "by_category": {k: f"{v.count(True)}/{len(v)}" for k, v in sorted(by_category.items())},
        "by_tool": {k: f"{v.count(True)}/{len(v)}" for k, v in sorted(by_tool.items())},
        "by_tier": {k: f"{v.count(True)}/{len(v)}" for k, v in sorted(by_tier.items())},
        "matrix": {
            dim: {cat: f"{vals.count(True)}/{len(vals)}" for cat, vals in sorted(cells.items())}
            for dim, cells in sorted(matrix.items())
        },
        "fail_reasons": dict(sorted(fail_reasons.items(), key=lambda x: -x[1])),
    }


def render_markdown(results: list[dict], meta: dict) -> str:
    s = _summarize(results)
    lines: list[str] = []
    lines.append("# UrbanScope AI · Agent 评估报告")
    lines.append("")
    lines.append(f"- 生成时间：{datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"- 评估集：{meta.get('set', '')} ｜ tier：{meta.get('tier', '')}")
    lines.append(f"- 通过率：**{s['pass_rate']:.1%}**（{s['passed']}/{s['total']}，失败 {s['failed']}）")
    lines.append("")

    lines.append("## 一、总览")
    lines.append("")
    lines.append("| 维度 | 通过/总数 |")
    lines.append("|---|---|")
    lines.append(f"| 全部 | {s['passed']}/{s['total']} |")
    for tier, val in s["by_tier"].items():
        lines.append(f"| tier·{tier} | {val} |")
    for cat, val in s["by_category"].items():
        lines.append(f"| 分类·{cat} | {val} |")
    for tool, val in s["by_tool"].items():
        lines.append(f"| 工具·{tool} | {val} |")
    lines.append("")

    lines.append("## 二、覆盖矩阵")
    lines.append("")
    lines.append("行 = tier·意图/工具，列 = 分类；**空格 = 缺口**（驱动出题闭环）")
    lines.append("")
    matrix = s["matrix"]
    cats = sorted({cat for cells in matrix.values() for cat in cells})
    lines.append("| 意图/工具 | " + " | ".join(cats) + " |")
    lines.append("|" + "---|" * (len(cats) + 1))
    for dim, cells in matrix.items():
        lines.append("| " + dim + " | " + " | ".join(cells.get(c, "") for c in cats) + " |")
    lines.append("")

    if s["fail_reasons"]:
        lines.append("## 三、失败归类")
        lines.append("")
        lines.append("| 断言类型 | 失败数 |")
        lines.append("|---|---|")
        for name, count in s["fail_reasons"].items():
            lines.append(f"| {name} | {count} |")
        lines.append("")

    lines.append("## 四、逐条明细")
    lines.append("")
    lines.append("| id | 分类 | 工具 | 难度 | 结果 | 失败断言 |")
    lines.append("|---|---|---|---|---|---|")
    for r in sorted(results, key=lambda x: x["id"]):
        failed_checks = ", ".join(c["name"] for c in r.get("checks", []) if not c["ok"])
        lines.append(
            f"| {r['id']} | {r['category']} | {r.get('tool') or '-'} | {r.get('difficulty', '')} "
            f"| {'✅' if r['passed'] else '❌'} | {failed_checks} |"
        )
    lines.append("")
    return "\n".join(lines)


def write_report(results: list[dict], meta: dict, path: str | None = None) -> Path:
    EVAL_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if not path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = str(EVAL_REPORTS_DIR / f"{meta.get('set', 'eval')}_{stamp}.md")
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(results, meta), encoding="utf-8")
    # 同名 JSON：结构化结果 + 汇总（/eval/reports 列表/详情直接读，免重算）
    json_path = out.with_suffix(".json")
    json_path.write_text(
        json.dumps(
            {"meta": meta, "summary": _summarize(results), "results": results},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out
