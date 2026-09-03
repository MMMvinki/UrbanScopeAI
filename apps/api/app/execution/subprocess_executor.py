"""父侧子进程 executor：懒启动 / 同 run 复用 / 超时树杀 / cleanup。

协议见 subprocess_runner.py 头部：父子进程经 stdin/stdout 交换换行分隔 JSON。
子进程有每个工具（stub）的调用权——stub 把调用 RPC 回父进程，父进程在
ToolContext 下执行真实工具并回传结果。

生命周期：每个 run 一个子进程（存 ctx.subprocess_executor），首次调用懒创建，
同 run 复用（state 跨 exec 持久，如 Jupyter kernel），run 结束 cleanup()。

（移植来源见 NOTICE.md）
"""

from __future__ import annotations

import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_IS_WINDOWS = sys.platform == "win32"


# ─────────────────────────────────────────────────────────────────────
# 返回模型 / 异常
# ─────────────────────────────────────────────────────────────────────


@dataclass
class CodeExecResult:
    """一次 exec 调用的结果。"""

    output: Any = None
    logs: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


class SubprocessExecutorError(RuntimeError):
    pass


class ChildDiedError(SubprocessExecutorError):
    pass


class ExecTimeout(SubprocessExecutorError):
    pass


# ─────────────────────────────────────────────────────────────────────
# 配置
# ─────────────────────────────────────────────────────────────────────


@dataclass
class SubprocessExecutorConfig:
    python_executable: str = field(default_factory=lambda: sys.executable)
    working_dir: Optional[str] = None
    env: Optional[dict[str, str]] = None
    exec_timeout: Optional[float] = 600.0
    kill_grace: float = 2.0


# ─────────────────────────────────────────────────────────────────────
# Executor
# ─────────────────────────────────────────────────────────────────────


class SubprocessPythonExecutor:
    """子进程执行器。``__call__(code) -> CodeExecResult``。"""

    def __init__(
        self,
        config: Optional[SubprocessExecutorConfig] = None,
        stdout_listener: Optional[Callable[[str], None]] = None,
        risky_op_listener: Optional[Callable[[dict], None]] = None,
        plot_saved_listener: Optional[Callable[[dict], None]] = None,
        chart_saved_listener: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.config = config or SubprocessExecutorConfig()
        self._stdout_listener = stdout_listener
        self._risky_op_listener = risky_op_listener
        self._plot_saved_listener = plot_saved_listener
        self._chart_saved_listener = chart_saved_listener
        self._tools: dict[str, Any] = {}
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._msg_q: "queue.Queue[dict]" = queue.Queue()
        self._shutdown = threading.Event()

    # ── 协议 ─────────────────────────────────────────────────────────

    def send_tools(self, tools: dict[str, Any]) -> None:
        """注册工具并让子进程 init（只传名字，值经 tool_call RPC 回传）。"""
        self._tools = dict(tools)
        self._ensure_child()
        self._send({
            "kind": "init",
            "tool_names": list(self._tools.keys()),
            "authorized_imports": ["*"],
        })
        msg = self._expect({"init_ok"})
        if msg.get("kind") != "init_ok":
            raise SubprocessExecutorError(f"Unexpected init response: {msg!r}")

    def __call__(self, code_action: str) -> CodeExecResult:
        """在子进程执行代码并返回 CodeExecResult。"""
        self._ensure_child()
        self._send({"kind": "exec", "code": code_action})

        deadline = (
            time.monotonic() + self.config.exec_timeout
            if self.config.exec_timeout is not None
            else None
        )
        while True:
            msg = self._recv(deadline=deadline)
            kind = msg.get("kind")
            if kind == "stdout":
                continue  # 已由 reader 线程 fan-out
            if kind == "tool_call":
                self._handle_tool_call(msg)
                continue
            if kind == "done":
                if msg.get("ok"):
                    return CodeExecResult(output=msg.get("output"), logs=msg.get("logs", "") or "")
                err = msg.get("error") or "unknown error"
                logs = msg.get("logs", "") or ""
                return CodeExecResult(error=f"{err}\n--- stdout before error ---\n{logs}", logs=logs)
            self._log_stderr(f"[executor] unexpected msg: {msg!r}\n")

    def cleanup(self) -> None:
        """关闭子进程，可安全重复调用。"""
        if self._proc is None:
            return
        self._shutdown.set()
        proc = self._proc
        try:
            if proc.poll() is None:
                try:
                    self._send({"kind": "shutdown"})
                except Exception:
                    pass
                try:
                    proc.wait(timeout=self.config.kill_grace)
                except subprocess.TimeoutExpired:
                    self._terminate(proc)
        finally:
            self._proc = None
            if self._reader_thread and self._reader_thread.is_alive():
                self._reader_thread.join(timeout=5.0)
            self._reader_thread = None

    def interrupt(self) -> None:
        """让子进程停下；Windows CTRL_BREAK → taskkill /F /T 树杀。"""
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            if _IS_WINDOWS:
                proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                proc.send_signal(signal.SIGINT)
        except Exception as e:
            self._log_stderr(f"[executor] interrupt send failed: {e}\n")
        try:
            proc.wait(timeout=self.config.kill_grace)
            return
        except subprocess.TimeoutExpired:
            pass
        if _IS_WINDOWS:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                    capture_output=True,
                    check=False,
                    timeout=5.0,
                )
            except Exception as e:
                self._log_stderr(f"[executor] taskkill failed: {e}\n")
                self._terminate(proc)
        else:
            self._terminate(proc)

    # ── 内部：生命周期 ────────────────────────────────────────────────

    @staticmethod
    def _ensure_platform_readonly() -> None:
        """平台只读底座：把 platform_data/geojson/*.json 设为只读（幂等）。

        execute_code 子进程里的 Python 钩子（subprocess_runner._install_platform_protection）
        挡 open/os 层写入；但 geopandas/fiona 写 GeoJSON 走 GDAL C 层（不经 Python open），
        只有文件系统只读属性能兜住。只设 geojson/ 子目录文件，不影响 layers.json 注册表。
        平台数据本就是只读依据（产品红线：数据内容不可改）；更新平台数据时需先解除只读。
        """
        try:
            import stat

            platform_dir = Path(__file__).resolve().parents[1] / "platform_data" / "geojson"
            if not platform_dir.is_dir():
                return
            for f in platform_dir.glob("*.json"):
                try:
                    f.chmod(stat.S_IREAD)
                except OSError:
                    pass
        except Exception:
            pass

    def _ensure_child(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self._ensure_platform_readonly()
        env = os.environ.copy()
        if self.config.env:
            env.update(self.config.env)
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        # PYTHONUTF8=1 开启 CPython 的 UTF-8 模式（PEP 540）：让裸 open(path,'w') 的默认编码
        # 也变成 UTF-8，否则 Windows 上默认 GBK 会把带中文的图层文件写成 GBK（Python3.12
        # 默认的 locale 编码仍是 GBK，PYTHONIOENCODING 只管 stdio 不管文件读写）。缺了它，
        # execute_code 子进程里 open('data/layers/xx.json','w') 写出的中文图层重启后读不回来。
        env.setdefault("PYTHONUTF8", "1")
        # 子进程 cwd=workspace（sys.path[0] 是 cwd），app.* 包根目录必须显式注入 PYTHONPATH
        _api_root = str(Path(__file__).resolve().parents[2])
        _existing_pp = env.get("PYTHONPATH")
        env["PYTHONPATH"] = f"{_api_root}{os.pathsep}{_existing_pp}" if _existing_pp else _api_root

        creationflags = 0
        start_new_session = False
        if _IS_WINDOWS:
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            start_new_session = True

        self._proc = subprocess.Popen(
            [self.config.python_executable, "-u", "-m", "app.execution.subprocess_runner"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.config.working_dir,
            env=env,
            creationflags=creationflags,
            start_new_session=start_new_session,
            bufsize=1,
            text=True,
            encoding="utf-8",
        )
        self._shutdown.clear()
        self._reader_thread = threading.Thread(target=self._reader_loop, name="urbanscope-exec-reader", daemon=True)
        self._reader_thread.start()
        threading.Thread(target=self._stderr_loop, name="urbanscope-exec-stderr", daemon=True).start()

        ready = self._expect({"ready"}, timeout=5.0)
        if ready.get("kind") != "ready":
            raise SubprocessExecutorError(f"Child did not report ready: {ready!r}")

    def _terminate(self, proc: subprocess.Popen) -> None:
        try:
            if _IS_WINDOWS:
                proc.terminate()
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=self.config.kill_grace)
            except subprocess.TimeoutExpired:
                if _IS_WINDOWS:
                    proc.kill()
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception as e:
            self._log_stderr(f"[executor] terminate failed: {e}\n")

    # ── 内部：IPC ─────────────────────────────────────────────────────

    def _send(self, obj: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise SubprocessExecutorError("Child not running")
        line = json.dumps(obj, default=str) + "\n"
        try:
            self._proc.stdin.write(line)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            raise ChildDiedError(f"Child pipe closed while writing: {e}") from e

    def _recv(self, deadline: Optional[float] = None) -> dict:
        while True:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._log_stderr("[executor] exec timeout — killing child\n")
                    self.interrupt()
                    raise ExecTimeout("exec call exceeded timeout")
                try:
                    return self._msg_q.get(timeout=remaining)
                except queue.Empty:
                    continue
            try:
                return self._msg_q.get(timeout=0.5)
            except queue.Empty:
                if self._proc is not None and self._proc.poll() is not None:
                    raise ChildDiedError(f"Child exited with code {self._proc.returncode}")

    def _expect(self, kinds: set[str], timeout: Optional[float] = 10.0) -> dict:
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            msg = self._recv(deadline=deadline)
            if msg.get("kind") in kinds:
                return msg
            self._log_stderr(f"[executor] dropping unexpected msg: {msg!r}\n")

    def _reader_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            if self._shutdown.is_set():
                break
            line = line.rstrip("\r\n")
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                self._log_stderr(f"[child non-JSON] {line}\n")
                continue
            kind = msg.get("kind")
            if kind == "stdout":
                if self._stdout_listener is not None:
                    try:
                        self._stdout_listener(msg.get("text", ""))
                    except Exception as e:
                        self._log_stderr(f"[executor] stdout listener raised: {e}\n")
                continue
            if kind == "stderr":
                self._log_stderr(msg.get("text", ""))
                continue
            if kind == "risky_op":
                if self._risky_op_listener is not None:
                    try:
                        self._risky_op_listener(msg)
                    except Exception as e:
                        self._log_stderr(f"[executor] risky_op listener raised: {e}\n")
                continue
            if kind == "plot_saved":
                if self._plot_saved_listener is not None:
                    try:
                        self._plot_saved_listener(msg)
                    except Exception as e:
                        self._log_stderr(f"[executor] plot_saved listener raised: {e}\n")
                continue
            if kind == "chart_saved":
                if self._chart_saved_listener is not None:
                    try:
                        self._chart_saved_listener(msg)
                    except Exception as e:
                        self._log_stderr(f"[executor] chart_saved listener raised: {e}\n")
                continue
            self._msg_q.put(msg)

    def _stderr_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for line in proc.stderr:
            if self._shutdown.is_set():
                break
            self._log_stderr(line)

    def _log_stderr(self, text: str) -> None:
        try:
            sys.stderr.write(text)
            sys.stderr.flush()
        except Exception:
            pass

    # ── 内部：tool RPC 桥 ─────────────────────────────────────────────

    def _handle_tool_call(self, msg: dict) -> None:
        """代替子进程调用已注册工具并回传结果。"""
        call_id = msg.get("call_id") or uuid.uuid4().hex
        name = msg.get("name")
        args = msg.get("args") or []
        kwargs = msg.get("kwargs") or {}

        tool = self._tools.get(name) if isinstance(name, str) else None
        if tool is None:
            self._send({"kind": "tool_result", "call_id": call_id, "ok": False, "error": f"Tool not found: {name!r}"})
            return

        t0 = time.monotonic()
        try:
            value = tool(*args, **kwargs)
        except Exception as e:
            duration_ms = (time.monotonic() - t0) * 1000
            logger.error("TOOL FAIL: %s — %s: %s (%.0fms)", name, type(e).__name__, e, duration_ms)
            self._send({"kind": "tool_result", "call_id": call_id, "ok": False, "error": f"{type(e).__name__}: {e}"})
            return

        duration_ms = (time.monotonic() - t0) * 1000
        try:
            json.dumps(value, default=str)
            safe_value = value
        except (TypeError, ValueError):
            safe_value = repr(value)
        logger.info("TOOL OK: %s — %.0fms", name, duration_ms)
        self._send({"kind": "tool_result", "call_id": call_id, "ok": True, "value": safe_value})
