"""eval 运行任务：子进程隔离 + 状态文件轮询（评测后台 POST /eval/run）。

评测必须在子进程跑：execute_tool_case 会把生成图层注册进全局 DATA_REGISTRY，
直接在 8011 服务进程内跑会污染实时图层面板。子进程自带独立注册表，
报告落盘 eval_reports/ 共享；父进程通过状态文件轮询进度。

状态文件：apps/api/eval_runs/<run_id>.json（gitignored，瞬态）
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from app.atomicio import atomic_write_json

# apps/api/eval_runs/（与 eval_reports 同级）
EVAL_RUNS_DIR = Path(__file__).resolve().parent.parent.parent / "eval_runs"
# apps/api/eval_ws/：评测 scratch 工作区。评测子进程把 WORKSPACE_ROOT 覆盖到这里，
# 让 register_layer/file_ops/execute_code 的文件物化完全隔离（Phase 1 e2e 需要）。
EVAL_WS_DIR = Path(__file__).resolve().parent.parent.parent / "eval_ws"

# 当前活跃的 run_id 集合（内存态；进程退出即清）
_ACTIVE: set[str] = set()
_LOCK = threading.Lock()


def _read_status(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_active() -> bool:
    with _LOCK:
        return bool(_ACTIVE)


def start_eval_job(set_name: str = "all", tier: str = "tool", exclude_dep: bool = False) -> str:
    """spawn 子进程跑一轮评测，返回 run_id。调用方需先检查 is_active()。"""
    EVAL_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:8]
    status_path = EVAL_RUNS_DIR / f"{run_id}.json"

    cmd = [sys.executable, "-m", "app.eval.run", "--set", set_name, "--tier", tier]
    if exclude_dep:
        cmd.append("--exclude-dep")
    cmd += ["--status", str(status_path)]

    # cwd = apps/api：子进程能读到 .env（amap 密钥）与评估集/报告目录
    # env：把 WORKSPACE_ROOT 覆盖到 eval_ws → 评测的一切文件物化不落线上工作区
    env = {**os.environ, "URBANSCOPE_WORKSPACE": str(EVAL_WS_DIR)}
    proc = subprocess.Popen(cmd, cwd=str(EVAL_RUNS_DIR.parent), env=env)
    with _LOCK:
        _ACTIVE.add(run_id)

    def watch() -> None:
        proc.wait()
        st = _read_status(status_path)
        # 子进程异常退出且没写 done/error → 补写 error，避免前端永远等 running
        if not st or st.get("state") not in ("done", "error"):
            _write_error(status_path, st, proc.returncode)
        with _LOCK:
            _ACTIVE.discard(run_id)

    threading.Thread(target=watch, name=f"eval-{run_id}", daemon=True).start()
    return run_id


def _write_error(status_path: Path, st: dict | None, returncode: int) -> None:
    msg = (st or {}).get("error") or f"评测子进程异常退出（code {returncode}）"
    atomic_write_json(status_path, {"state": "error", "error": msg})


def get_eval_job(run_id: str) -> dict | None:
    """读运行状态：running 进度 / done（report_id）/ error。无记录返回 None。"""
    status_path = EVAL_RUNS_DIR / f"{run_id}.json"
    st = _read_status(status_path)
    if st is None:
        with _LOCK:
            if run_id in _ACTIVE:
                return {"state": "running", "current": 0, "total": 0, "passed": 0, "current_case": None}
        return None
    st.setdefault("run_id", run_id)
    return st
