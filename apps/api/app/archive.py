"""RunArchive — 持久化每次 agent run 到 ``<workspace>/.workdir/runs/<run_id>/``。

布局
----
::

    <workspace>/.workdir/runs/<run_id>/
        meta.json            # run 级元数据，更新时整体重写
        events.jsonl         # AgentEvent 原始事件溯源（与 SSE 同源）
        tool_calls.jsonl     # 规范化工具调用记录（call_id 去重合并）
        artifacts.jsonl      # 产物引用（plot / GeoJSON / CSV ...）
        steps.jsonl          # execute_code 步执行记录
        stdout.log           # 子进程 stdout 镜像（可选）
        final_answer.md      # 终结答案 best-effort

裁剪掉 profile/session/message_parts 维度，保留 tool-call 合并、
stale-running 自愈、事件溯源这三块核心价值（移植来源见 NOTICE.md）。

失败模式：每个公共方法吞掉自身 I/O 错误并打日志——归档是补充品，
必须永不成为 agent run 的致命伤。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.workspace import RUNS_DIR

logger = logging.getLogger(__name__)

STALE_RUNNING_SECONDS = 12 * 60 * 60
OPEN_TOOL_CALL_STATUSES = {"pending", "running", "streaming"}


class RunArchiveError(RuntimeError):
    """仅程序员错误（坏路径）抛出；I/O 失败打日志不抛。"""


def _created_at_is_stale(raw: Any, *, max_age_seconds: float) -> bool:
    if not raw:
        return False
    try:
        dt = datetime.fromisoformat(str(raw))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds() > max_age_seconds
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────
# RunIndex — /runs 列表的轻量摘要行
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RunIndex:
    """一条已归档 run 的摘要。"""

    run_id: str
    status: str
    prompt: str
    created_at: str
    finished_at: Optional[str]
    step_count: int
    tool_call_count: int = 0
    error: Optional[str] = None

    @classmethod
    def from_meta(cls, meta: dict) -> "RunIndex":
        return cls(
            run_id=str(meta.get("run_id", "")),
            status=str(meta.get("status", "unknown")),
            prompt=str(meta.get("prompt", "")),
            created_at=str(meta.get("created_at", "")),
            finished_at=meta.get("finished_at"),
            step_count=int(meta.get("step_count", 0) or 0),
            tool_call_count=int(meta.get("tool_call_count", 0) or 0),
            error=meta.get("error"),
        )


# ─────────────────────────────────────────────────────────────────────
# RunArchive — 主类
# ─────────────────────────────────────────────────────────────────────


@dataclass
class RunArchive:
    """单 run 的磁盘记录。用 :meth:`RunArchive.open` 构造，勿直接调构造函数。"""

    run_id: str
    run_dir: Path
    workspace_path: Optional[Path]
    _meta: dict = field(default_factory=dict)

    # -- 构造 ----------------------------------------------------------

    @classmethod
    def open(
        cls,
        *,
        run_id: str,
        prompt: str,
        workspace_path: Optional[str] = None,
        model: str = "",
    ) -> "RunArchive":
        """创建（或覆盖）归档目录 + 初始 meta.json。agent run 开始时调用一次。"""
        ws = Path(workspace_path).expanduser().resolve() if workspace_path else None
        root = cls.runs_root(ws)
        run_dir = root / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        meta: dict[str, Any] = {
            "run_id": run_id,
            "status": "running",
            "prompt": prompt,
            "workspace_path": str(ws) if ws else None,
            "model": model,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "step_count": 0,
            "tool_call_count": 0,
            "risky_ops": [],
            "error": None,
        }
        archive = cls(run_id=run_id, run_dir=run_dir, workspace_path=ws, _meta=meta)
        archive._flush_meta()
        # 预创建（或截断）jsonl：open() 是"创建或覆盖"语义，run_id 撞名时不得残留旧事件。
        for name in ("events", "tool_calls", "artifacts", "steps", "llm_usage"):
            (run_dir / f"{name}.jsonl").open("w", encoding="utf-8").close()
        logger.info("RunArchive opened at %s", run_dir)
        return archive

    # -- 写接口 ---------------------------------------------------------

    def record_step(
        self,
        *,
        step: int,
        code: str,
        output: str = "",
        error: Optional[str] = None,
        script_path: Optional[str] = None,
    ) -> None:
        """追加一条步记录到 steps.jsonl。绝不抛异常。"""
        entry = {
            "step": step,
            "code": code,
            "output": output,
            "error": error,
            "script_path": script_path,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            self._append_jsonl("steps.jsonl", entry)
            self._meta["step_count"] = int(self._meta.get("step_count", 0)) + 1
            self._flush_meta()
        except Exception:
            logger.exception("record_step failed for run=%s step=%s", self.run_id, step)

    def record_risky_op(self, entry: dict) -> None:
        """追加一条写副作用观察（L4 遥测）。绝不抛异常。"""
        try:
            ops = self._meta.setdefault("risky_ops", [])
            entry.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
            ops.append(entry)
            if len(ops) > 1000:  # 防病态循环撑爆 meta.json
                del ops[:-1000]
            self._flush_meta()
        except Exception:
            logger.exception("record_risky_op failed for run=%s", self.run_id)

    def record_tool_call(
        self,
        *,
        call_id: str,
        name: str,
        arguments: Optional[dict[str, Any]] = None,
        output: str = "",
        error: Optional[str] = None,
        duration_ms: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
        status: str | None = None,
    ) -> None:
        """追加一条规范化工具调用到 tool_calls.jsonl。"""
        entry = {
            "call_id": call_id,
            "name": name,
            "arguments": arguments or {},
            "output": output,
            "error": error,
            "duration_ms": duration_ms,
            "status": status or ("error" if error else "completed"),
            "metadata": metadata or {},
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            self._append_jsonl("tool_calls.jsonl", entry)
            if entry["status"] in {"completed", "error"}:
                self._meta["tool_call_count"] = int(self._meta.get("tool_call_count", 0)) + 1
            self._flush_meta()
        except Exception:
            logger.exception("record_tool_call failed for run=%s call=%s", self.run_id, call_id)

    def record_artifact(self, artifact: dict[str, Any]) -> None:
        """追加一条产物引用到 artifacts.jsonl。"""
        try:
            entry = dict(artifact)
            entry.setdefault("run_id", self.run_id)
            entry.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
            self._append_jsonl("artifacts.jsonl", entry)
            self._meta["artifact_count"] = int(self._meta.get("artifact_count", 0)) + 1
            self._flush_meta()
        except Exception:
            logger.exception("record_artifact failed for run=%s", self.run_id)

    def record_event(self, event_type: str, data: Any = None) -> None:
        """追加一条原始 agent 事件到 events.jsonl（与 SSE 同源）。"""
        entry = {
            "type": event_type,
            "data": data,
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            self._append_jsonl("events.jsonl", entry)
            self._meta["event_count"] = int(self._meta.get("event_count", 0)) + 1
        except Exception:
            logger.exception("record_event failed for run=%s", self.run_id)

    def record_llm_usage(
        self,
        *,
        usage: dict[str, Any] | None = None,
        prompt_cache: dict[str, Any] | None = None,
        request: dict[str, Any] | None = None,
    ) -> None:
        """追加一次 LLM provider 用量（token / DeepSeek 缓存口径）。"""
        entry = {
            "run_id": self.run_id,
            "usage": usage or {},
            "prompt_cache": prompt_cache or {},
            "request": request or {},
            "ts": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            self._append_jsonl("llm_usage.jsonl", entry)
            self._meta["llm_turn_count"] = int(self._meta.get("llm_turn_count", 0)) + 1
            usage_data = entry["usage"]
            if isinstance(usage_data, dict):
                self._meta["llm_total_tokens"] = (
                    int(self._meta.get("llm_total_tokens", 0)) + int(usage_data.get("total_tokens") or 0)
                )
                self._meta["llm_prompt_tokens"] = (
                    int(self._meta.get("llm_prompt_tokens", 0))
                    + int(usage_data.get("prompt_tokens") or usage_data.get("input_tokens") or 0)
                )
                self._meta["llm_cached_tokens"] = (
                    int(self._meta.get("llm_cached_tokens", 0)) + int(usage_data.get("cached_tokens") or 0)
                )
            self._flush_meta()
        except Exception:
            logger.exception("record_llm_usage failed for run=%s", self.run_id)

    def append_stdout(self, text: str) -> None:
        """镜像子进程 stdout 到 stdout.log。"""
        if not text:
            return
        try:
            with (self.run_dir / "stdout.log").open("a", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            logger.exception("append_stdout failed for run=%s", self.run_id)

    def close(
        self,
        *,
        status: str,
        final_answer: Optional[str] = None,
        error: Optional[str] = None,
        termination: Optional[dict[str, Any]] = None,
    ) -> None:
        """终结归档：翻转 status、盖 finished_at、写 final_answer。"""
        self._finalize_open_records(status=status, error=error)
        self._meta["status"] = status
        self._meta["finished_at"] = datetime.now().isoformat(timespec="seconds")
        if termination:
            self._meta["termination"] = dict(termination)
        if error is not None:
            self._meta["error"] = error
        if final_answer:
            try:
                (self.run_dir / "final_answer.md").write_text(final_answer, encoding="utf-8")
            except Exception:
                logger.exception("final_answer write failed for run=%s", self.run_id)
        self._flush_meta()
        logger.info("RunArchive closed run=%s status=%s", self.run_id, status)

    def _finalize_open_records(self, *, status: str, error: Optional[str]) -> None:
        """run 关闭时为遗留未终结的生命周期记录补齐终态，保持 events.jsonl 只追加。"""
        try:
            latest: dict[str, dict[str, Any]] = {}
            for entry in self._read_jsonl("tool_calls.jsonl"):
                call_id = str(entry.get("call_id") or "")
                if call_id:
                    latest[call_id] = entry

            for call_id, entry in latest.items():
                entry_status = str(entry.get("status") or "")
                if entry_status not in OPEN_TOOL_CALL_STATUSES:
                    continue
                terminal_status = "error" if status != "success" else "completed"
                terminal_error = None if terminal_status == "completed" else (
                    error or f"run_closed_before_tool_result: run status={status}"
                )
                self.record_tool_call(
                    call_id=call_id,
                    name=str(entry.get("name") or ""),
                    arguments=entry.get("arguments") if isinstance(entry.get("arguments"), dict) else {},
                    output=str(entry.get("output") or ""),
                    error=terminal_error,
                    duration_ms=None,
                    metadata={
                        **(entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}),
                        "finalized_by_run_close": True,
                        "run_close_status": status,
                    },
                    status=terminal_status,
                )
        except Exception:
            logger.debug("open record finalization failed for run=%s", self.run_id, exc_info=True)

    # -- 读接口 ---------------------------------------------------------

    @property
    def meta(self) -> dict:
        return dict(self._meta)  # 浅拷贝，防调用方改内部状态

    def read_steps(self) -> list[dict]:
        return self._read_jsonl("steps.jsonl")

    def read_tool_calls(self) -> list[dict]:
        """按 call_id 合并（后写覆盖空值），得到每条调用的终态。"""
        latest: dict[str, dict] = {}
        for entry in self._read_jsonl("tool_calls.jsonl"):
            call_id = str(entry.get("call_id") or "")
            if not call_id:
                continue
            previous = latest.get(call_id, {})
            merged = dict(previous)
            for key, value in entry.items():
                if value not in (None, "", {}, []):
                    merged[key] = value
            if "status" not in merged:
                merged["status"] = "error" if merged.get("error") else "completed"
            latest[call_id] = merged
        return list(latest.values())

    def read_artifacts(self) -> list[dict]:
        return self._read_jsonl("artifacts.jsonl")

    def read_events(self) -> list[dict]:
        return self._read_jsonl("events.jsonl")

    def read_llm_usage(self) -> list[dict]:
        return self._read_jsonl("llm_usage.jsonl")

    def _read_jsonl(self, filename: str) -> list[dict]:
        path = self.run_dir / filename
        if not path.exists():
            return []
        out: list[dict] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("malformed %s line in run=%s", filename, self.run_id)
        except Exception:
            logger.exception("read %s failed for run=%s", filename, self.run_id)
        return out

    # -- 列表 / 加载 ----------------------------------------------------

    @staticmethod
    def runs_root(workspace_path: Optional[str | Path] = None) -> Path:
        if workspace_path:
            return Path(workspace_path).expanduser().resolve() / ".workdir" / "runs"
        return RUNS_DIR

    @classmethod
    def list_runs(
        cls,
        workspace_path: Optional[str | Path] = None,
        limit: int = 50,
    ) -> list[RunIndex]:
        """枚举归档 run，按 meta.created_at 新→旧。"""
        root = cls.runs_root(workspace_path)
        if not root.exists():
            return []
        entries: list[RunIndex] = []
        for child in root.iterdir():
            if not child.is_dir():
                continue
            meta_path = child / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("unreadable meta.json in %s", child)
                continue
            cls._reconcile_stale_running_meta(meta_path, meta)
            entries.append(RunIndex.from_meta(meta))
        entries.sort(key=lambda r: r.created_at, reverse=True)
        return entries[: max(1, limit)]

    @staticmethod
    def _reconcile_stale_running_meta(meta_path: Path, meta: dict[str, Any]) -> None:
        """后端重启后，把标记为 running 但早已停滞的 run 自愈为 error。"""
        if meta.get("status") != "running":
            return
        reason = ""
        if meta.get("finished_at"):
            reason = "Recovered stale running run: finished_at was already set."
        elif _created_at_is_stale(meta.get("created_at"), max_age_seconds=STALE_RUNNING_SECONDS):
            reason = "Recovered stale running run: no active backend runner after restart."
        if not reason:
            return
        meta["status"] = "error"
        meta["error"] = str(meta.get("error") or reason)
        meta.setdefault("recovered_from_running", True)
        try:
            archive = RunArchive(
                run_id=str(meta.get("run_id") or meta_path.parent.name),
                run_dir=meta_path.parent,
                workspace_path=None,
                _meta=meta,
            )
            archive._finalize_open_records(status="error", error=str(meta["error"]))
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            logger.debug("failed to reconcile stale run meta %s", meta_path, exc_info=True)

    @classmethod
    def load(cls, workspace_path: Optional[str | Path], run_id: str) -> Optional["RunArchive"]:
        """重开一个已归档 run。不存在返回 None。"""
        root = cls.runs_root(workspace_path)
        run_dir = root / run_id
        meta_path = run_dir / "meta.json"
        if not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("RunArchive.load failed for %s", run_dir)
            return None
        ws = Path(workspace_path).expanduser().resolve() if workspace_path else None
        return cls(run_id=run_id, run_dir=run_dir, workspace_path=ws, _meta=meta)

    # -- 内部 ----------------------------------------------------------

    def _append_jsonl(self, filename: str, entry: dict) -> None:
        with (self.run_dir / filename).open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def _flush_meta(self) -> None:
        try:
            (self.run_dir / "meta.json").write_text(
                json.dumps(self._meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.exception("meta.json flush failed for run=%s", self.run_id)


__all__ = ["RunArchive", "RunArchiveError", "RunIndex"]
