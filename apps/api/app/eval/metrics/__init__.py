"""eval 指标包。Phase 0：rule.py（确定性断言）；judge.py / similarity.py Phase 2。"""

from __future__ import annotations

from app.eval.metrics import rule


def _clip_text(value, limit: int = 3000) -> str:
    """transcript 落盘前裁剪超长文本（execute_code 参数/工具输出可能极大）。"""
    text = str(value)
    return text if len(text) <= limit else text[:limit] + f"…<裁剪 {len(text) - limit} 字符>"


def _sanitize_transcript(transcript: dict | None) -> dict | None:
    """把 e2e transcript 压成可落盘的尺寸：保留诊断/安全扫描所需结构，裁剪超长串。

    不裁剪的话 execute_code 的代码参数、工具返回的大 GeoJSON 会让 result JSON 膨胀
    到几 MB，报告接口和前端弹窗都扛不住。裁剪只影响展示，check_transcript_forbidden
    在评分时已对**完整** transcript 扫描过。
    """
    if not transcript:
        return transcript
    out: dict = {"texts": list(transcript.get("texts") or [])[:40], "final_answer": transcript.get("final_answer") or ""}
    tools = []
    for item in (transcript.get("tools") or [])[:30]:
        tools.append(
            {
                "tool": item.get("tool"),
                "ok": item.get("ok"),
                "message": _clip_text(item.get("message") or ""),
                "args": _clip_text(item.get("args") or ""),
                "output": _clip_text(item.get("output") or ""),
            }
        )
    out["tools"] = tools
    return out


def assemble_result(case, checks: list[dict], record: dict) -> dict:
    """把 checks + record 组装成 CaseResult dict（report.py 输入）。tool/plan/e2e 共用。

    transcript 一并落盘（裁剪版）：e2e 后诊断 LLM 实际调了哪些工具、做了什么思考，
    也是安全审计的物证。tool/plan 层 record 无 transcript → None。
    """
    passed = all(c["ok"] for c in checks)
    return {
        "id": case.id,
        "title": case.title,
        "category": case.category,
        "tier": case.tier,
        "difficulty": case.difficulty,
        "tool": case.tool,
        "depends_external": case.depends_external,
        "operation": getattr(case.expected, "operation", None),
        "tags": list(case.tags),
        "passed": passed,
        "checks": checks,
        "message": record.get("message"),
        "error": record.get("error"),
        "generated_layers": [g["layer_id"] for g in record.get("generated", [])],
        "transcript": _sanitize_transcript(record.get("transcript")),
        "outcome": "success" if record.get("success") else "failed",
    }


def score_tool_case(case, record: dict) -> dict:
    """对 oracle 产出的 tool 级 record 打分，返回 CaseResult dict（report.py 输入）。"""
    checks: list[dict] = []
    for name, kwargs, desc in rule.assertions_for_case(case):
        fn = getattr(rule, name)
        ok, detail = fn(record, **kwargs)
        checks.append({"name": name, "desc": desc, "ok": bool(ok), "detail": detail})
    return assemble_result(case, checks, record)
