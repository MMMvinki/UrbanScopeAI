"""Conversation Memory：按会话持久化 run 摘要，供多轮上下文注入。

最小版（Sprint 1，对应 PRD 补充 P6 conversation memory）：
- 每次 run 结束后追加一条摘要（问题 → 结论/证据）；
- 后续 run 的 system prompt 注入最近几条摘要，让 Agent"记得"之前做过什么；
- 与"最近 6 轮原始消息"互补：原始消息保上下文细节，摘要兜长距离记忆。

存储：workspace/.workdir/memory/<conversation_id>.json（JSON 数组，新→旧截断）。
完整版（Sprint 2）再扩展 project / layer / run / style-action 记忆与检索。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from app.workspace import WORKSPACE_ROOT

_MEMORY_DIR = WORKSPACE_ROOT / ".workdir" / "memory"
_MAX_ENTRIES = 20


def _safe_conv_id(conversation_id: str | None) -> str:
    """规整会话 id，防路径穿越；空值回落 "default"。"""
    safe = re.sub(r"[^0-9a-zA-Z_-]+", "", conversation_id or "")
    return safe or "default"


def _conv_path(conversation_id: str | None) -> Path:
    return _MEMORY_DIR / f"{_safe_conv_id(conversation_id)}.json"


def _load(conversation_id: str | None) -> list[dict]:
    try:
        data = json.loads(_conv_path(conversation_id).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def get_summary_lines(conversation_id: str | None, limit: int = 6) -> list[str]:
    """最近几条会话摘要（新→旧），返回可直接注入 system prompt 的行。"""
    entries = _load(conversation_id)[-_MAX_ENTRIES:]
    lines: list[str] = []
    for entry in reversed(entries[-limit:]):
        question = str(entry.get("question") or "").strip()
        summary = str(entry.get("summary") or "").strip()
        if question and summary:
            lines.append(f"- 之前问题：{question[:60]} → {summary[:200]}")
    return lines


def append_summary(conversation_id: str | None, question: str, summary: str) -> None:
    """追加一条摘要；保留最近 _MAX_ENTRIES 条；写入失败不致命。"""
    question = (question or "").strip()
    summary = (summary or "").strip()
    if not question and not summary:
        return
    entries = _load(conversation_id)
    entries.append(
        {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "question": question,
            "summary": summary,
        }
    )
    entries = entries[-_MAX_ENTRIES:]
    try:
        path = _conv_path(conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def build_run_summary(result) -> str:
    """从 ExecutionResult 提炼一句话摘要（结论 + 证据），供记忆存储。"""
    lines: list[str] = []
    group = getattr(result, "result_group", None)
    if group is not None:
        title = getattr(group, "title", None)
        if title:
            lines.append(f"结论：{title}")
    evidence = list(dict.fromkeys(result.evidence or []))[:3]
    for item in evidence:
        lines.append(f"- {item}")
    return "；".join(lines) if lines else "已完成一轮分析（无结论文本）"


# ── Sprint 2 结构化记忆：layer / style-action（run 记忆复用上面的摘要列表） ──
#
# 每个会话一个结构化文件 <conv_id>.structured.json：
#   {"layers": [{layer_id, name, source, ts}],            # 已创建/发布的图层，按 layer_id 去重
#    "style_actions": [{layer_id, action, detail, ts}]}   # 样式操作（分层设色/透明度渐变/去边框/可见性）
# 与摘要列表（<conv_id>.json）互补：摘要存"做了什么分析"，结构化记忆存"有哪些图层、被改成什么样"。

_MAX_STYLE_ACTIONS = 20


def _struct_path(conversation_id: str | None) -> Path:
    return _MEMORY_DIR / f"{_safe_conv_id(conversation_id)}.structured.json"


def _load_struct(conversation_id: str | None) -> dict:
    try:
        data = json.loads(_struct_path(conversation_id).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                "layers": data.get("layers") if isinstance(data.get("layers"), list) else [],
                "style_actions": (
                    data.get("style_actions") if isinstance(data.get("style_actions"), list) else []
                ),
            }
    except (OSError, json.JSONDecodeError):
        pass
    return {"layers": [], "style_actions": []}


def _save_struct(conversation_id: str | None, data: dict) -> None:
    try:
        path = _struct_path(conversation_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def record_layer(conversation_id: str | None, layer_id: str, name: str = "", source: str = "") -> None:
    """记录会话中创建/发布的图层（按 layer_id 去重置顶）。"""
    layer_id = (layer_id or "").strip()
    if not layer_id:
        return
    data = _load_struct(conversation_id)
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "layer_id": layer_id,
        "name": (name or "").strip(),
        "source": (source or "").strip(),
    }
    data["layers"] = [e for e in data["layers"] if e.get("layer_id") != layer_id]
    data["layers"].append(entry)
    data["layers"] = data["layers"][-_MAX_ENTRIES:]
    _save_struct(conversation_id, data)


def record_style_action(conversation_id: str | None, layer_id: str, action: str, detail: str = "") -> None:
    """记录一次样式操作（分层设色/透明度渐变/去边框/可见性切换等）。"""
    layer_id = (layer_id or "").strip()
    action = (action or "").strip()
    if not layer_id or not action:
        return
    data = _load_struct(conversation_id)
    data["style_actions"].append(
        {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "layer_id": layer_id,
            "action": action,
            "detail": (detail or "").strip(),
        }
    )
    data["style_actions"] = data["style_actions"][-_MAX_STYLE_ACTIONS:]
    _save_struct(conversation_id, data)


def get_memory_lines(conversation_id: str | None, limit: int = 4) -> list[str]:
    """结构化记忆注入行（图层现状 + 最近样式操作），供 system prompt 参考。"""
    data = _load_struct(conversation_id)
    lines: list[str] = []
    for layer in reversed(data["layers"][-limit:]):
        name = layer.get("name") or ""
        source = layer.get("source") or ""
        lines.append(
            f"- 已创建图层：{layer.get('layer_id')}"
            + (f" {name}" if name else "")
            + (f"（来源：{source}）" if source else "")
        )
    for action in reversed(data["style_actions"][-limit:]):
        detail = action.get("detail") or ""
        lines.append(
            f"- 样式操作：{action.get('layer_id')} 已{action.get('action')}"
            + (f"，{detail}" if detail else "")
        )
    return lines
