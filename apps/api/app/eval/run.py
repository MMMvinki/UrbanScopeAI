"""eval CLI：python -m app.eval.run --set <集> --tier tool [--exclude-dep] [--status <状态文件>]。

计算核心在 app.eval.runner.run_eval()；本文件只做参数解析 + 输出。
--status 供评测后台子进程用：逐条进度/最终状态写入 JSON 文件（前端轮询）。
退出码：全过 0，有失败 1（可进 CI）。
"""

from __future__ import annotations

import argparse
import json
import sys

from app.atomicio import atomic_write_json
from app.eval import runner


def _write_status(path: str, payload: dict) -> None:
    """原子写状态文件（temp + os.replace），避免前端轮询读到半截 JSON。"""
    atomic_write_json(path, payload)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.eval.run", description="UrbanScope Agent 评测 CLI")
    p.add_argument("--set", default="all", help="评估集名（缺省 all=全部）")
    p.add_argument("--tier", default="tool", help="tier 逗号分隔：tool|plan|e2e")
    p.add_argument("--exclude-dep", action="store_true", help="跳过外部依赖 case（amap/mapbox/network）")
    p.add_argument("--report", default=None, help="报告输出路径（缺省 eval_reports/<set>_<ts>.md）")
    p.add_argument("--status", default=None, help="子进程模式：进度写入该状态文件（评测后台用）")
    p.add_argument("--verbose", action="store_true", help="逐条打印失败详情")
    args = p.parse_args(argv)

    status_path = args.status

    def on_progress(i: int, total: int, case_id: str, passed_so_far: int, res: dict) -> None:
        if status_path:
            _write_status(
                status_path,
                {
                    "state": "running",
                    "current": i,
                    "total": total,
                    "passed": passed_so_far,
                    "current_case": case_id,
                },
            )
            return
        mark = "PASS" if res["passed"] else "FAIL"
        print(f"[{i}/{total}] {mark} {case_id}")
        if args.verbose and not res["passed"]:
            for c in res["checks"]:
                if not c["ok"]:
                    print(f"      - {c['name']} {c['desc']}: {c['detail']}")
        if args.verbose and res.get("message"):
            print(f"      - message: {res['message']}")

    if not status_path:
        print(f"开始评估（{args.set} / {args.tier}）")

    try:
        result = runner.run_eval(args.set, args.tier, args.exclude_dep, on_progress=on_progress, report_path=args.report)
    except Exception as exc:
        if status_path:
            _write_status(status_path, {"state": "error", "error": f"{type(exc).__name__}: {exc}"})
        raise

    failed = result["total"] - result["passed"]
    if status_path:
        _write_status(
            status_path,
            {
                "state": "done",
                "report_id": result["report_id"],
                "passed": result["passed"],
                "total": result["total"],
            },
        )
    else:
        print(f"\n通过率 {result['passed']}/{result['total']}（失败 {failed}）")
        print(f"报告：{result['report_path']}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
