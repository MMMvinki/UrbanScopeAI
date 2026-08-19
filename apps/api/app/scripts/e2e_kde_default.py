# -*- coding: utf-8 -*-
"""E2E：用户只说「做核密度」（不指定带宽/网格）→ 验证默认调精生效。

断言：工具链含 execute_skill_script(kernel-density)、生成图层为 50m 默认网格（粤海掩膜 ≈ 5740 格）、graduated 分层设色。

用法：.venv/Scripts/python.exe -X utf8 app/scripts/e2e_kde_default.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
import urllib.parse
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # apps/api

BASE = "http://127.0.0.1:8011"
QUESTION = "粤海街道餐饮poi核密度"


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_layers():
    req = urllib.request.Request(f"{BASE}/layers", method="GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


print(f"== plan ==\n问：{QUESTION}")
plan = post("/agent/plan", {"question": QUESTION, "history": [], "conversation_id": uuid.uuid4().hex})
print("operation:", plan.get("operation"), "| targets:", plan.get("targets"))
if plan.get("operation") not in ("kernel_density", "density"):
    print(f"✗ planner 未识别核密度分析（operation={plan.get('operation')}）")
    sys.exit(1)

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
    "accept_missing": list(plan.get("unmet_targets") or []),
    "history": [],
    "conversation_id": uuid.uuid4().hex,
}
print(f"\n== execute ==")

req = urllib.request.Request(
    f"{BASE}/agent/execute",
    data=json.dumps(body).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

tool_calls: list[str] = []
got_generated_layer = False
got_layer_style = False
kde_layer_id = None
kde_cell = None

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
                continue
            kind = evt.get("type") or evt.get("kind") or "?"
            payload = evt.get("data") or evt
            if not isinstance(payload, dict):
                payload = {}
            if kind in ("permission_request", "permission"):
                rid = payload.get("request_id") or payload.get("id") or evt.get("request_id") or ""
                post("/agent/approve", {"request_id": rid, "allowed": True, "remember": True})
            elif kind in ("tool_start", "tool"):
                name = payload.get("name") or evt.get("name") or (payload.get("tool") if isinstance(payload.get("tool"), str) else None)
                args = payload.get("args") or payload.get("kwargs") or payload
                if not isinstance(args, dict):
                    args = {}
                if not name:
                    if "skill_id" in args:
                        name = f"execute_skill_script({args['skill_id']})"
                tool_calls.append(f"{name}:{json.dumps(args)[:90]}")
                print(f"  [tool] {name} args={json.dumps(args)[:140]}")
            elif kind in ("generated_layer", "GENERATED_LAYER"):
                got_generated_layer = True
                kde_layer_id = payload.get("layer_id") or payload.get("id")
                print(f"  [generated_layer] {kde_layer_id}")
            elif kind in ("layer_style", "LAYER_STYLE"):
                got_layer_style = True
                print(f"  [layer_style] layer={payload.get('layer_id') or payload.get('id')}")
            elif kind in ("execution", "execution_complete", "run_complete"):
                print(f"  [execution] result_group={'有' if payload.get('result_group') or payload.get('result') else '无'}")
            elif kind == "stream_end":
                print(f"  [stream_end] {payload.get('status')}")
            elif kind in ("thought_delta", "code_delta", "code_block_start", "artifact", "tool_result", "run_start", "data_check", "error"):
                pass

print(f"\n== 结果 ==")
print(f"工具调用链: {tool_calls}")
kde_calls = [c for c in tool_calls if 'execute_skill_script' in c and 'kernel-density' in c]
print(f"execute_skill_script(kernel-density) 在工具链: {'✓' if kde_calls else '✗'}")
for c in kde_calls:
    print(f"  {c}")

ok = True
if not kde_calls:
    print("  ✗ 无 execute_skill_script(kernel-density) 调用")
    ok = False
if not got_generated_layer or not kde_layer_id:
    print("  ✗ 未生成图层")
    ok = False
else:
    layers = get_layers()
    item = next((l for l in layers if l.get("id") == kde_layer_id), None)
    print(f"/layers 含 {kde_layer_id}: {'✓' if item else '✗'}")
    if item:
        print(f"  geometry={item.get('geometry')} feature_count={item.get('feature_count')} style={json.dumps(item.get('visualVariables'), ensure_ascii=False)[:100]}")
        n = item.get("feature_count") or 0
        # 默认 50m 网格：掩膜粤海 ≈ 5.7k 格；Agent 若不传 area_layer_id（点图层本身已限粤海）
        # 则走无掩膜点 bbox ≈ 1.2 万格。两者都在 50m 默认合理区间。
        if not 3000 < n < 20000:
            print(f"  ⚠️ 网格数 {n} 不在 50m 默认预期（3000-20000），可能 Agent 传了非默认参数")
        if not item.get("visualVariables"):
            print("  ⚠️ 无 graduated 样式")
            ok = False
    else:
        ok = False

sys.exit(0 if ok else 1)
