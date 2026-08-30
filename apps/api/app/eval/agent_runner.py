"""agent 级评测执行（Phase 1）：plan-only + e2e 两层。

plan-only：headless 直调 plan_for()（单次 LLM 调用、零注册表/工作区污染），
          在 run_eval 里由 ThreadPoolExecutor 并发 + 每 case 超时。
e2e：    每条 case 在独立子进程（python -m app.eval.run_case）里跑真实
          plan_for → execute() 全链路，URBANSCOPE_WORKSPACE 覆盖到 eval_ws
          隔离文件物化；emit 回调收集事件作 transcript；硬超时由父进程
          subprocess.run(timeout=…) 保证。

设计文档 §6.3：隔离靠进程边界 + scratch 工作区，不污染 8011 实时图层与文件。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from app.agent.executor import execute
from app.agent.planner import plan_for
from app.data_registry import DATA_REGISTRY
from app.eval import loader
from app.eval.metrics import assemble_result
from app.eval.metrics import rule
from app.eval.oracle import _geojson_stats
from app.models.agent import ExecuteRequest
from app.tools.context import ToolContext
from app.workspace import (
    WORKSPACE_ROOT,
    ensure_layer_data_in_workspace,
    ensure_workspace,
)

# apps/api/eval_ws：agent 级评测 scratch 工作区（URBANSCOPE_WORKSPACE 覆盖目标）
EVAL_WS_DIR = Path(__file__).resolve().parent.parent.parent / "eval_ws"
# apps/api/eval_runs：瞬态（子进程模式状态文件 / 单 case 结果 JSON）
EVAL_RUNS_DIR = Path(__file__).resolve().parent.parent.parent / "eval_runs"

PLAN_TIMEOUT_S = 60  # plan-only 单条硬超时（DeepSeek 名义 max_tokens，防御性）
E2E_TIMEOUT_S = 300  # e2e 单条硬超时（父进程 subprocess.run 硬杀）


def _failed_result(case, detail: str, error: str | None = None) -> dict:
    """执行层失败（超时/崩溃）的占位 CaseResult。"""
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
        "passed": False,
        "checks": [{"name": "exec", "desc": "执行", "ok": False, "detail": detail}],
        "message": None,
        "error": error,
        "generated_layers": [],
        "outcome": "failed",
    }


def score_plan_case(case) -> dict:
    """plan-only：真实调用 plan_for()，组装 record 后按 expected 逐条断言。"""
    record: dict = {
        "success": True,
        "message": None,
        "output": {},
        "error": None,
        "generated": [],
        "transcript": None,
        "operation": None,
        "intent_id": "",
        "plan": [],
        "params": {},
        "clarify": False,
        "missing": [],
        "unmet_targets": [],
    }
    try:
        plan = plan_for(case.question, history=case.history or None)
        record.update(
            {
                "operation": plan.operation,
                "intent_id": plan.intent_id,
                "plan": [{"tool": s.tool, "label": s.label} for s in plan.plan],
                "params": {
                    "targets": list(plan.targets),
                    "distance": plan.distance,
                    "target_distances": dict(plan.target_distances),
                    "required_data": list(plan.required_data),
                },
                "clarify": plan.clarify is not None,
                "missing": list(plan.data_check.missing) if plan.data_check else [],
                "unmet_targets": list(plan.unmet_targets),
                "message": plan.summary,
            }
        )
    except Exception as exc:  # plan 崩溃：任何断言都不该过
        record["success"] = False
        record["error"] = f"{type(exc).__name__}: {exc}"

    checks: list[dict] = []
    for name, kwargs, desc in rule.assertions_for_plan_case(case):
        fn = getattr(rule, name)
        ok, detail = fn(record, **kwargs)
        checks.append({"name": name, "desc": desc, "ok": bool(ok), "detail": detail})
    return assemble_result(case, checks, record)


def score_e2e_case(case, set_name: str) -> dict:
    """e2e：在独立子进程跑真实 plan→execute，父进程硬超时 + 读结果 JSON。

    子进程本身在 eval_ws 里（env 覆盖 WORKSPACE_ROOT），文件/图层隔离。
    """
    out_path = EVAL_RUNS_DIR / f"e2e_{case.id}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()
    cmd = [
        sys.executable,
        "-m",
        "app.eval.run_case",
        "--case",
        case.id,
        "--set",
        set_name,
        "--out",
        str(out_path),
    ]
    env = {**os.environ, "URBANSCOPE_WORKSPACE": str(EVAL_WS_DIR)}
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(EVAL_RUNS_DIR.parent),
            env=env,
            capture_output=True,
            timeout=E2E_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return _failed_result(case, f"超时（>{E2E_TIMEOUT_S}s，子进程已杀）", "timeout")
    if out_path.exists():
        try:
            return json.loads(out_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return _failed_result(case, f"结果 JSON 读取失败: {exc}", str(exc))
    tail = (proc.stderr or b"").decode("utf-8", errors="replace")[-400:]
    return _failed_result(case, f"子进程异常退出（exit {proc.returncode}）: {tail}", tail or f"exit {proc.returncode}")


class _EvalEmitter:
    """把 ToolContext.event_emitter 桥接到 eval emit 回调。

    PydanticAI executor 路径（pa_try_execute）的工具/思考事件走 deps.event_emitter
    （tool_adapter:48 `emit = deps.event_emitter`），不是 execute() 的 emit 参数。
    不接这个桥，eval transcript 就永远捕获不到 pydai 路径的工具调用与思考增量——
    既没法诊断 LLM 实际调了哪些工具，transcript 安全扫描（check_transcript_forbidden）
    也扫不到工具参数/输出里的敏感串。真实 EventEmitter 的签名是 emit(event_type, data)。
    """

    def __init__(self, emit) -> None:
        self._emit = emit

    def emit(self, event_type, data=None) -> None:
        self._emit(event_type, data or {})


def run_e2e_case(case) -> dict:
    """单条 e2e case 真跑：plan_for → ExecuteRequest → execute() → 产物断言。"""
    ensure_workspace()
    ensure_layer_data_in_workspace()  # demo 图层物化到 eval_ws/data/layers（execute_code 可读）
    before = {l.id for l in DATA_REGISTRY.list_layers()}
    transcript: dict = {"texts": [], "tools": [], "final_answer": ""}
    record: dict = {
        "success": True,
        "message": None,
        "output": {},
        "error": None,
        "generated": [],
        "transcript": transcript,
    }

    def emit(event_type, data: dict) -> None:
        et = getattr(event_type, "value", event_type)
        et = str(et)
        if et in ("thought_delta",):
            text = data.get("text") or ""
            if text:
                transcript["texts"].append(text)
        elif et in ("tool_start", "tool_result"):
            transcript["tools"].append(
                {
                    "tool": data.get("tool"),
                    "args": data.get("args"),
                    "ok": data.get("ok"),
                    "output": data.get("output"),
                    "message": data.get("message"),
                }
            )
        elif et in ("execution",):
            fa = data.get("final_answer")
            if fa:
                transcript["final_answer"] = fa

    try:
        plan = plan_for(case.question, history=case.history or None)
        request = ExecuteRequest(
            intent_id=plan.intent_id,
            question=case.question,
            summary=plan.summary,
            operation=plan.operation,
            targets=list(plan.targets),
            distance=plan.distance,
            target_distances=dict(plan.target_distances),
            required_data=list(plan.required_data),
            plan=list(plan.plan),
            data_check=plan.data_check,
            unmet_targets=list(plan.unmet_targets),
            accept_missing=[],
            history=list(case.history or []),
        )
        ctx = ToolContext(workspace_path=WORKSPACE_ROOT, event_emitter=_EvalEmitter(emit))
        result = execute(request, ctx=ctx, emit=emit)
        record["message"] = result.final_answer
        record["output"] = result.model_dump(mode="json")
        if result.final_answer:
            transcript["final_answer"] = result.final_answer
        record["success"] = bool(result.final_answer) and not getattr(result, "used_fallback", False)

        for layer in DATA_REGISTRY.list_layers():
            if layer.id in before:
                continue
            try:
                geojson = DATA_REGISTRY.get_geojson(layer.id)
            except Exception:
                geojson = None
            stats = _geojson_stats(geojson) if geojson else {"feature_count": 0, "bbox": None, "fields": []}
            record["generated"].append(
                {
                    "layer_id": layer.id,
                    "name": layer.name,
                    "geometry": layer.geometry,
                    **stats,
                }
            )
    except Exception as exc:  # 崩溃
        record["success"] = False
        record["error"] = f"{type(exc).__name__}: {exc}"
        if record["output"]:
            record["output"] = {}

    checks: list[dict] = []
    for name, kwargs, desc in rule.assertions_for_e2e_case(case):
        fn = getattr(rule, name)
        ok, detail = fn(record, **kwargs)
        checks.append({"name": name, "desc": desc, "ok": bool(ok), "detail": detail})
    return assemble_result(case, checks, record)


def _load_case(case_id: str) -> object:
    """跨评估集按 id 找 case（run_case 子进程用；set 维度由父进程过滤）。"""
    for case in loader.load_all():
        if case.id == case_id:
            return case
    return None


def main() -> None:
    """python -m app.eval.run_case --case <id> --set <set> --out <json>"""
    import argparse

    parser = argparse.ArgumentParser(description="e2e 单 case 执行器（子进程模式）")
    parser.add_argument("--case", required=True, help="case id")
    parser.add_argument("--set", default="", help="所属评估集（信息性）")
    parser.add_argument("--out", required=True, help="结果 JSON 路径")
    args = parser.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    case = _load_case(args.case)
    if case is None:
        res = {
            "id": args.case,
            "title": args.case,
            "category": "unknown",
            "tier": "e2e",
            "difficulty": "normal",
            "tool": None,
            "depends_external": "",
            "tags": [],
            "passed": False,
            "checks": [{"name": "load", "desc": "加载", "ok": False, "detail": f"未找到 case: {args.case}"}],
            "message": None,
            "error": "case not found",
            "generated_layers": [],
            "outcome": "failed",
        }
    else:
        res = run_e2e_case(case)
    out.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
