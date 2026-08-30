"""eval 可编程核心：run_eval() 供 CLI 与子进程复用（设计文档 §6/§8）。

CLI 的循环体集中在这里：load → 过滤 → 逐条 _score_case → write_report。
on_progress 回调对接 CLI 打印或子进程状态文件写入。

分层（Phase 0/1）：
- tool：oracle 确定性真跑（execute_tool_case + score_tool_case），串行；
- plan：headless 直调 plan_for()（单次 LLM），ThreadPoolExecutor 并发 + 每 case 超时；
- e2e：单 case 独立子进程（run_case），父进程硬超时（agent_runner.score_e2e_case）。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Callable

from app.eval import agent_runner, loader, report
from app.eval.metrics import score_tool_case
from app.eval.oracle import execute_tool_case


def _score_case(case, set_name: str = "all") -> dict:
    """单条 case 打分：按 tier 分发到 tool / plan / e2e。"""
    if case.tier == "tool":
        record = execute_tool_case(case)
        return score_tool_case(case, record)
    if case.tier == "plan":
        return agent_runner.score_plan_case(case)
    if case.tier == "e2e":
        return agent_runner.score_e2e_case(case, set_name)
    return {
        "id": case.id,
        "title": case.title,
        "category": case.category,
        "tier": case.tier,
        "difficulty": case.difficulty,
        "tool": case.tool,
        "depends_external": case.depends_external,
        "passed": False,
        "checks": [{"name": "tier", "desc": "分层", "ok": False, "detail": f"未知 tier: {case.tier}"}],
        "message": None,
        "error": None,
        "generated_layers": [],
        "outcome": "skipped",
    }


def run_eval(
    set_name: str = "all",
    tier: str = "tool",
    exclude_dep: bool = False,
    on_progress: Callable[[int, int, str, int, dict], None] | None = None,
    report_path: str | None = None,
) -> dict:
    """跑一轮评估，返回结构化结果 + 已落盘报告。

    on_progress(i, total, case_id, passed_so_far, res) 每条 case 后回调。
    plan 级并发（纯 LLM 无状态变更）；tool/e2e 串行（e2e 每条自带子进程 + 硬超时）。
    """
    cases = loader.load_all() if set_name == "all" else loader.load_set(set_name)
    tiers = [t.strip() for t in str(tier).split(",") if t.strip()]
    cases = [c for c in cases if c.tier in tiers]
    if exclude_dep:
        cases = [c for c in cases if not c.depends_external]

    total = len(cases)
    index = {c.id: i for i, c in enumerate(cases, 1)}
    results: dict[str, dict] = {}
    passed_so_far = 0

    def _report(res: dict) -> None:
        nonlocal passed_so_far
        results[res["id"]] = res
        if res["passed"]:
            passed_so_far += 1
        if on_progress:
            on_progress(index.get(res["id"], 0), total, res["id"], passed_so_far, res)

    # plan 级并发：每 case 超时用 future.result(timeout) 保障；
    # 超时线程是 daemon（py3.9+），shutdown(wait=False) 后不阻塞 run 收尾。
    plan_cases = [c for c in cases if c.tier == "plan"]
    if plan_cases:
        pool = ThreadPoolExecutor(max_workers=min(4, len(plan_cases)), thread_name_prefix="eval-plan")
        futures = {pool.submit(agent_runner.score_plan_case, c): c for c in plan_cases}
        for fut, c in futures.items():
            try:
                _report(fut.result(timeout=agent_runner.PLAN_TIMEOUT_S))
            except FutureTimeout:
                _report(agent_runner._failed_result(c, f"超时（>{agent_runner.PLAN_TIMEOUT_S}s）", "timeout"))
            except Exception as exc:
                _report(agent_runner._failed_result(c, f"异常: {type(exc).__name__}: {exc}", str(exc)))
        pool.shutdown(wait=False)

    # tool / e2e 串行（顺序与评估集一致）
    for case in cases:
        if case.tier == "plan":
            continue
        _report(_score_case(case, set_name))

    ordered = [results[c.id] for c in cases]
    meta = {"set": set_name, "tier": tier}
    path = report.write_report(ordered, meta, path=report_path)
    return {
        "results": ordered,
        "meta": meta,
        "summary": report.summarize(ordered),
        "report_path": str(path),
        "report_id": path.stem,
        "passed": passed_so_far,
        "total": len(ordered),
    }
