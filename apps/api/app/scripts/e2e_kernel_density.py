# -*- coding: utf-8 -*-
"""E2E：Agent 命中 kernel-density 可执行技能生成核密度网格图层（真计算 → 地图图层 + 分层设色）。

流程：POST /agent/plan → POST /agent/execute（SSE，自动审批）→
断言：plan.operation == kernel_density|density、工具链含 execute_skill_script(kernel-density)、
SSE 出现 generated_layer + layer_style、/layers 出现 *_kde、geojson 网格带 density 字段。

用法：.venv/Scripts/python.exe -X utf8 app/scripts/e2e_kernel_density.py
"""
from __future__ import annotations

import json
import sys
import urllib.request
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # apps/api

BASE = "http://127.0.0.1:8011"
QUESTION = "我需要带宽是50m的，粤海街道餐饮poi核密度"


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get(path: str) -> dict:
    req = urllib.request.Request(f"{BASE}{path}", method="GET")
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
# planner 是 LLM 输出，operation 可能是 kernel_density 或 density（「核密度」可能归到短词 density），
# 两者都是核密度分析意图，执行链都命中 kernel-density 可执行技能（execute_skill_script）。
if plan.get("operation") not in ("kernel_density", "density"):
    print(f"✗ planner 未识别核密度分析（operation={plan.get('operation')}）")
    sys.exit(1)

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
    "accept_missing": list(plan.get("unmet_targets") or []),
    "history": [],
    "conversation_id": uuid.uuid4().hex,
}
print(f"\n== execute（accept_missing={body['accept_missing']}）==")

req = urllib.request.Request(
    f"{BASE}/agent/execute",
    data=json.dumps(body).encode("utf-8"),
    headers={"Content-Type": "application/json"},
    method="POST",
)

tool_calls: list[str] = []
approved = 0
got_generated_layer = False
got_layer_style = False
kde_layer_id = None
kde_output = None
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
                name = payload.get("name") or evt.get("name") or (payload.get("tool") if isinstance(payload.get("tool"), str) else None)
                args = payload.get("args") or payload.get("kwargs") or payload
                if not isinstance(args, dict):
                    args = {}
                if not name:
                    if "skill_id" in args:
                        name = f"execute_skill_script({args['skill_id']})"
                    elif "layer_id" in args:
                        name = "set_layer_visible"
                tool_calls.append(f"{name}:{json.dumps(args, ensure_ascii=False)}")
                print(f"  [tool] {name} args={json.dumps(args, ensure_ascii=False)[:160]}")
            elif kind == "tool_result":
                ok = payload.get("ok", True)
                msg = str(payload.get("message") or "")
                # execute_skill_script 的 TOOL_RESULT.output 携带完整 skill 输出（含 stats.bandwidth_m）
                if payload.get("tool") == "execute_skill_script" and isinstance(payload.get("output"), dict):
                    kde_output = payload.get("output")
                if not ok:
                    found_error = str(payload.get("error"))[:150]
                    print(f"  [tool结果] ✗ {found_error}")
                else:
                    print(f"  [tool结果] ✓ {msg[:120]}")
            elif kind == "error":
                print(f"  [error] {evt.get('message') or payload}")
            elif kind in ("generated_layer", "GENERATED_LAYER"):
                got_generated_layer = True
                kde_layer_id = payload.get("layer_id") or payload.get("id")
                print(f"  [generated_layer] {kde_layer_id}")
            elif kind in ("layer_style", "LAYER_STYLE"):
                got_layer_style = True
                print(f"  [layer_style] layer={payload.get('layer_id') or payload.get('id')} renderer={payload.get('visualVariables', {}).get('renderer') if isinstance(payload.get('visualVariables'), dict) else payload.get('renderer')}")
            elif kind in ("execution", "execution_complete", "run_complete"):
                result_group = payload.get("result_group") or payload.get("result")
                # 不要覆盖 tool_result 已捕获的 skill 输出（execution 的 output 是 ResultGroup 全量，含巨量 widget）
                if kde_output is None:
                    kde_output = payload.get("output")
                print(f"  [execution] result_group={'有' if result_group else '无'}")
                if kde_output:
                    print(f"    output: {json.dumps(kde_output, ensure_ascii=False)[:300]}")
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

# ── 3. 图层校验 ───────────────────────────────────────────────────────
print(f"\n== 结果 ==")
print(f"审批次数: {approved}")
print(f"工具调用链: {tool_calls}")
print(f"generated_layer 事件: {'✓' if got_generated_layer else '✗'}")
print(f"layer_style 事件: {'✓' if got_layer_style else '✗'}")
kde_calls = [c for c in tool_calls if 'execute_skill_script' in c and 'kernel-density' in c]
print(f"execute_skill_script(kernel-density) 在工具链: {'✓' if kde_calls else '✗'}")
got_bw = any('"bandwidth_m"' in c for c in kde_calls)
print(f"execute_skill_script 收到 bandwidth_m: {'✓' if got_bw else '✗'}")
# skill 输出自报带宽（TOOL_RESULT.output.output.stats.bandwidth_m）——证明带宽真正生效，不只是传参
bw_applied = None
if kde_output and isinstance(kde_output, dict):
    inner = kde_output.get("output") or {}
    if isinstance(inner, dict):
        bw_applied = (inner.get("stats") or {}).get("bandwidth_m")
print(f"skill 实际应用带宽 bandwidth_m: {'✓ ' + str(bw_applied) if bw_applied else '✗ 未应用'}")

ok = True
if not got_bw:
    print("  ✗ 带宽参数未传递——skill 描述/契约未生效，需检查")
    ok = False
if got_bw and not bw_applied:
    print("  ✗ 带宽参数传了但 skill 未应用（stats.bandwidth_m 为空）")
    ok = False
if kde_layer_id:
    layers = get("/layers")
    item = next((l for l in layers if l.get("id") == kde_layer_id), None)
    print(f"/layers 含 {kde_layer_id}: {'✓' if item else '✗'}")
    if item:
        print(f"  name={item.get('name')} geometry={item.get('geometry')} feature_count={item.get('feature_count')} style={json.dumps(item.get('visualVariables'), ensure_ascii=False)[:120]}")
        if not item.get("visualVariables"):
            print("  ⚠️ 无 visualVariables（分层设色可能被冲或未生效）")
            ok = False
    gj = get(f"/layers/{urllib.parse.quote(kde_layer_id, safe='')}/geojson")
    feats = gj.get("features", []) if isinstance(gj, dict) else []
    print(f"geojson 网格要素: {len(feats)} 个")
    if feats:
        props = feats[0].get("properties", {})
        print(f"  首要素字段: {list(props.keys())}")
        if "density" in props:
            vals = [f.get("properties", {}).get("density") for f in feats[:200]]
            print(f"  density 样例（前 200 格范围）: min={min(vals):.1f} max={max(vals):.1f}")
            ok = ok and max(vals) > 0
        else:
            print("  ✗ 要素无 density 字段")
            ok = False
else:
    print("✗ 未捕获到核密度图层 id")
    ok = False

if found_error:
    print(f"工具报错: {found_error}")
    ok = False
sys.exit(0 if ok else 1)
