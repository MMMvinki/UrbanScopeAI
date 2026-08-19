# -*- coding: utf-8 -*-
"""端到端验证层1：Agent 用 get_dataset_schema 的 data_path 读平台 POI 数据。

流程：POST /agent/plan → 构造 ExecuteRequest（unmet 授权跳过）→ POST /agent/execute
读 SSE，收到 permission_request 自动批 allowed=True，统计工具调用链，确认
execute_code 成功读 poi_餐饮（而非找数据死循环），最后看 result_group 是否产出。

用法：.venv/Scripts/python.exe -X utf8 e2e_poi_mix.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
import uuid
import threading
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # apps/api（import app.*）

BASE = "http://127.0.0.1:8011"
QUESTION = "分析深圳市餐饮 POI 的空间分布，统计每个行政区内的餐饮 POI 数量，找出餐饮最密集的行政区"


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── 1. plan ──────────────────────────────────────────────────────────
print(f"== plan ==\n问：{QUESTION}")
plan = post("/agent/plan", {"question": QUESTION, "history": [], "conversation_id": uuid.uuid4().hex})
print("operation:", plan.get("operation"))
print("targets:", plan.get("targets"))
print("unmet_targets:", plan.get("unmet_targets"))
if plan.get("clarify"):
    print("⚠️ clarify 出现：", plan.get("clarify"))
print("data_check:", plan.get("data_check"))

accept_missing = list(plan.get("unmet_targets") or [])

# ── 2. execute（SSE + 自动审批）───────────────────────────────────────
body = {
    "intent_id": uuid.uuid4().hex,
    "question": QUESTION,
    "summary": plan.get("summary", ""),
    "operation": plan.get("operation", "general"),
    "targets": plan.get("targets", []),
    "distance": plan.get("distance", 800),
    "target_distances": plan.get("target_distances", {}),
    "required_data": plan.get("required_data", []),
    "plan": plan.get("plan", []),
    "data_check": plan.get("data_check"),
    "unmet_targets": plan.get("unmet_targets", []),
    "accept_missing": accept_missing,
    "history": [],
    "conversation_id": uuid.uuid4().hex,
}
print(f"\n== execute（accept_missing={accept_missing}）==")

req = urllib.request.Request(
    f"{BASE}/agent/execute",
    data=json.dumps(body).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

tool_calls: list[str] = []
approved = 0
result_group = None
reflection = None
found_error = None

with urllib.request.urlopen(req, timeout=300) as resp:
    buf = b""
    for raw in resp:
        buf += raw
        while b"\n\n" in buf:
            block, buf = buf.split(b"\n\n", 1)
            text = block.decode("utf-8", errors="replace")
            data_line = None
            for line in text.splitlines():
                if line.startswith("data:"):
                    data_line = line[5:].strip()
            if not data_line:
                continue
            try:
                evt = json.loads(data_line)
            except json.JSONDecodeError:
                print("  [非JSON帧]", data_line[:80])
                continue
            kind = evt.get("type") or evt.get("kind") or "?"
            payload = evt.get("data") or evt
            if not isinstance(payload, dict):
                payload = {}
            if kind in ("permission_request", "permission"):
                rid = payload.get("request_id") or payload.get("id") or evt.get("request_id") or ""
                post("/agent/approve", {"request_id": rid, "allowed": True, "remember": True})
                approved += 1
                print(f"  [审批] {rid} tool={payload.get('tool') or evt.get('tool')}")
            elif kind in ("tool_start", "tool"):
                name = payload.get("name") or evt.get("name")
                args = payload.get("args") or payload.get("kwargs") or {}
                tool_calls.append(f"{name}:{json.dumps(args)[:60]}")
                code = ""
                if isinstance(args, dict):
                    code = str(args.get("code") or args.get("code_action") or "")
                if code:
                    # 只打代码首行 + 是否含平台路径读取
                    first = code.splitlines()[0][:80] if code.splitlines() else ""
                    uses_platform = "../platform_data" in code
                    print(f"  [tool] execute_code 首行={first}  读平台数据={uses_platform}  代码长度={len(code)}")
                else:
                    print(f"  [tool] {name} args={json.dumps(args)[:120]}")
            elif kind == "tool_result":
                ok = payload.get("ok", True)
                note = ""
                msg = str(payload.get("message") or "")
                if "重复调用" in msg or "复用" in msg:
                    note = f"  ⚠️ 命中去重复用: {msg[:80]}"
                if not ok:
                    found_error = str(payload.get("error"))[:150]
                    print(f"  [tool结果] ✗ {found_error}")
                else:
                    print(f"  [tool结果] ✓{note}")
            elif kind == "error":
                print(f"  [error] {evt.get('message') or payload}")
            elif kind in ("execution", "execution_complete", "run_complete"):
                result_group = payload.get("result_group") or payload.get("result")
                reflection = payload.get("reflection")
                evidence = payload.get("evidence")
                print(f"  [execution] result_group={'有' if result_group else '无'}")
                if result_group and isinstance(result_group, dict):
                    print(f"    title: {result_group.get('title')}")
                    print(f"    metrics: {json.dumps(result_group.get('metrics'), ensure_ascii=False)[:300]}")
                if evidence:
                    print(f"    evidence: {json.dumps(evidence, ensure_ascii=False)[:200]}")
            elif kind == "run_start":
                print(f"  [run_start] {payload.get('run_id') or evt.get('run_id')}")
            elif kind == "stream_end":
                print(f"  [stream_end] {payload.get('status')}")
            elif kind == "data_check":
                print("  [data_check]", payload)
            elif kind in ("thought_delta", "code_delta", "code_block_start", "artifact"):
                pass  # 忽略流式/图表噪音
            else:
                print(f"  [{kind}] {json.dumps(evt)[:150]}")

print(f"\n== 结果 ==")
print(f"审批次数: {approved}")
print(f"工具调用链: {tool_calls}")
print(f"result_group 产出: {'✓ 有' if result_group else '✗ 无'}")
if reflection:
    print(f"reflection: {str(reflection)[:200]}")
if found_error:
    print(f"工具报错: {found_error}")
sys.exit(0 if result_group is not None and not found_error else 1)
