# -*- coding: utf-8 -*-
import json, urllib.request

base = "http://127.0.0.1:8011"
CONV = "skill-creator-progressive-verify"

def plan(question, history=None):
    body = {"question": question, "history": history or [], "conversation_id": CONV}
    req = urllib.request.Request(
        base + "/agent/plan",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read().decode("utf-8"))

print("=" * 60)
print("R1 触发语")
r1 = plan("我想创建一个可复用的新技能，请按 skill-creator 方法论引导我。开始吧——先问我第一个问题。")
print("op:", r1.get("operation"), "| intent:", r1.get("intent_id"))
print("thinking:", (r1.get("thinking") or "")[:120])
print("clarify:", json.dumps(r1.get("clarify"), ensure_ascii=False)[:180])

print("=" * 60)
print("R2 回答澄清问题")
h2 = [
    {"role": "user", "content": "我想创建一个可复用的新技能，请按 skill-creator 方法论引导我。开始吧——先问我第一个问题。"},
    {"role": "assistant", "content": (r1.get("clarify") or {}).get("question", "")},
]
r2 = plan(
    "这个技能用来对幼儿园做服务半径检查，算幼儿园 800 米覆盖率和缺口小区。用户做幼托设施评估时触发，需要幼儿园 POI 和小区人口数据。",
    history=h2,
)
print("op:", r2.get("operation"), "| intent:", r2.get("intent_id"))
print("clarify:", json.dumps(r2.get("clarify"), ensure_ascii=False)[:180])

print("=" * 60)
print("R3 参数确认 → 正式计划")
h3 = h2 + [
    {"role": "user", "content": "这个技能用来对幼儿园做服务半径检查，算幼儿园 800 米覆盖率和缺口小区。用户做幼托设施评估时触发，需要幼儿园 POI 和小区人口数据。"},
    {"role": "assistant", "content": (r2.get("clarify") or {}).get("question", "")},
]
r3 = plan(
    "就用 800 米默认半径，输出覆盖率和缺口小区图层。可以开始了。",
    history=h3,
)
print("op:", r3.get("operation"), "| intent:", r3.get("intent_id"))
print("thinking:", (r3.get("thinking") or "")[:150])
for step in r3.get("plan") or []:
    print("  step:", step.get("step_id"), "|", step.get("label"), "| tool:", step.get("tool"), "| detail:", (step.get("detail") or "")[:60])
