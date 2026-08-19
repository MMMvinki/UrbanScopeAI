"""EventEmitter：连接"同步 executor 线程"与"异步 SSE 队列"的线程安全桥。

executor 在后台线程里同步执行，emit() 是普通方法调用；内部用
loop.call_soon_threadsafe 把事件送入 asyncio.Queue，SSE 生成器 await 取帧。
同时把事件同步追加到 RunArchive（events.jsonl），并让 tool_start/tool_result
在 archive 里投影成规范化工具调用记录（tool_calls.jsonl），实现"过程可追溯"。

Phase 0：archive 可传 None（仅测试/演示流），emit 静默跳过落盘。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.models.events import AgentEventEnvelope, EventType


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _parse_iso(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


class EventEmitter:
    def __init__(
        self,
        run_id: str,
        queue: asyncio.Queue[AgentEventEnvelope | None] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
        archive: Any | None = None,  # RunArchive | None（Phase 1 挂载）
        conversation_id: str | None = None,  # Sprint 2：结构化记忆按会话落盘
    ):
        self._run_id = run_id
        self._queue = queue
        self._loop = loop
        self._archive = archive
        self._conversation_id = conversation_id
        # tool 调用生命周期：tool_name → [(call_id, start_ts)]，供 tool_result 配对落 archive
        self._tool_seq = 0
        self._pending_starts: dict[str, list[tuple[str, str]]] = {}

    @property
    def archive(self) -> Any | None:
        """挂载的 RunArchive（execute_code 的 listener 用它落 risky_op/artifact）。"""
        return self._archive

    def emit(self, event_type: EventType | str, data: dict[str, object] | None = None) -> AgentEventEnvelope:
        """发出一个事件：入 SSE 队列（若挂载）+ 落 RunArchive（若挂载）。"""
        env = AgentEventEnvelope(
            id=uuid.uuid4().hex,
            type=EventType(event_type) if isinstance(event_type, str) else event_type,
            data=data or {},
            run_id=self._run_id,
            ts=_now_iso(),
        )
        # 同步线程 → 异步队列（线程安全，无需 await）
        if self._queue is not None and self._loop is not None and not self._loop.is_closed():
            try:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, env)
            except RuntimeError:
                pass  # 事件循环已关闭（服务停机/测试场景），仅记录
        # 同步落盘（RunArchive 自持写锁，不阻塞主流程）
        if self._archive is not None:
            try:
                self._archive.record_event(env.type.value, env.data)
                self._maybe_record_tool_call(env)
            except Exception:
                pass  # 归档失败不影响执行流
        # Sprint 2 结构化记忆：图层创建 / 样式操作 → 会话记忆（幂等、失败静默）
        if self._conversation_id is not None:
            try:
                self._maybe_record_memory(env)
            except Exception:
                pass  # 记忆落盘失败不影响执行流
        return env

    def _maybe_record_memory(self, env: AgentEventEnvelope) -> None:
        """把 generated_layer / layer_style / layer_visibility 事件投影成会话记忆。

        - generated_layer → record_layer（会话里创建/发布了哪个图层）
        - layer_style → record_style_action（分层设色/透明度渐变/去边框等）
        - layer_visibility → record_style_action（仅显示/隐藏该图层）
        """
        from app.memory_store import record_layer, record_style_action

        data = env.data
        layer_id = str(data.get("layer_id") or "")
        if env.type == EventType.GENERATED_LAYER:
            record_layer(
                self._conversation_id,
                layer_id,
                name=str(data.get("name") or ""),
                source=str(data.get("source") or "generated"),
            )
        elif env.type == EventType.LAYER_STYLE:
            style = data.get("style") if isinstance(data.get("style"), dict) else {}
            mode = style.get("styleMode")
            vv = style.get("visualVariables") if isinstance(style.get("visualVariables"), dict) else {}
            graduated = vv.get("graduated") if isinstance(vv.get("graduated"), dict) else {}
            if mode == "graduated" and graduated:
                record_style_action(
                    self._conversation_id,
                    layer_id,
                    action="按属性分层设色",
                    detail=(
                        f"字段 {graduated.get('field')}，{graduated.get('method')} "
                        f"分 {graduated.get('classes')} 级（暖色/透明渐变等信息见事件详情）"
                    ),
                )
            elif "opacity_variable" in vv:
                record_style_action(
                    self._conversation_id,
                    layer_id,
                    action="设置透明度渐变",
                    detail=f"按字段 {vv['opacity_variable'].get('field') if isinstance(vv['opacity_variable'], dict) else ''} 渐变",
                )
            else:
                record_style_action(
                    self._conversation_id,
                    layer_id,
                    action="更新图层样式",
                    detail=str(style)[:200],
                )
        elif env.type == EventType.LAYER_VISIBILITY:
            visible = data.get("visible")
            show_only = data.get("show_only")
            if show_only:
                record_style_action(self._conversation_id, layer_id, action="仅显示该图层")
            else:
                record_style_action(
                    self._conversation_id,
                    layer_id,
                    action="显示" if visible else "隐藏",
                )

    def _maybe_record_tool_call(self, env: AgentEventEnvelope) -> None:
        """把 tool_start / tool_result 事件投影成 tool_calls.jsonl 的调用记录。

        同一 run 内 executor 单线程顺序 emit，start/result 一一配对；
        用 tool 名 + 序号生成稳定 call_id，close 时未终结的 start 会被 finalize。
        """
        data = env.data
        tool = str(data.get("tool", ""))
        if env.type == EventType.TOOL_START:
            self._tool_seq += 1
            call_id = f"tc-{self._tool_seq}"
            self._pending_starts.setdefault(tool, []).append((call_id, env.ts))
            self._archive.record_tool_call(
                call_id=call_id,
                name=tool,
                arguments=data.get("args") if isinstance(data.get("args"), dict) else {},
                status="running",
            )
        elif env.type == EventType.TOOL_RESULT:
            pending = self._pending_starts.get(tool)
            call_id = None
            start_ts = None
            if pending:
                call_id, start_ts = pending.pop()
            else:
                # 无对应 start（老路径/异常）：仍记录一条 completed，保证可追溯
                self._tool_seq += 1
                call_id = f"tc-{self._tool_seq}"
            duration_ms = None
            if start_ts:
                duration_ms = (env.ts and _parse_iso(env.ts) - _parse_iso(start_ts)).total_seconds() * 1000
            ok = bool(data.get("ok", True))
            self._archive.record_tool_call(
                call_id=call_id,
                name=tool,
                arguments={},
                output=json.dumps(data.get("output") or {}, ensure_ascii=False, default=str)[:8000],
                error=None if ok else (str(data.get("message") or "工具执行失败")),
                duration_ms=duration_ms,
                status="completed" if ok else "error",
            )

    # ── 便捷方法 ─────────────────────────────────────────────────────
    def emit_tool_start(self, tool: str, args: dict[str, object]) -> None:
        self.emit(EventType.TOOL_START, {"tool": tool, "args": args})

    def emit_tool_result(self, tool: str, *, ok: bool, message: str | None, output: dict[str, object]) -> None:
        self.emit(
            EventType.TOOL_RESULT,
            {"tool": tool, "ok": ok, "message": message, "output": output},
        )

    def emit_generated_layer(self, layer_id: str, name: str, feature_count: int) -> None:
        self.emit(
            EventType.GENERATED_LAYER,
            {"layer_id": layer_id, "name": name, "feature_count": feature_count},
        )

    def emit_error(self, message: str) -> None:
        self.emit(EventType.ERROR, {"message": message})
