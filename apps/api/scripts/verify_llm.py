"""全 LLM Agent 集成检查：验证真实平台数据路径由 DeepSeek 规划 + agentic 工具循环执行。

前置：后端已启动（uvicorn app.main:app --port 8011）且 apps/api/.env 配好 DEEPSEEK_API_KEY。
运行：PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verify_llm.py
约定：数值断言全部来自确定性工具；operation/目标由 LLM 判定，只做语义级断言
（kernel_density|buffer|clip 等均合法）。三条路径全部用**平台真实图层**
（poi_* / shenzhen_*）——demo 合成图层（schools/population_grid 等）已随平台化下线，
见 llm_planner._layer_lines 注释（planner 动态注入运行态图层，targets 只能从中选）。

/agent/execute 已改为 SSE 流式：本脚本消费事件流，从 execution 事件取终结载荷，
并断言 thought_delta（真流式）确实逐字到达。plan 可能回 clarify（问法有歧义），
此时追加片区信息重试直到拿到可执行计划。
"""
import json
import threading
import time
import urllib.request

BASE = "http://127.0.0.1:8011"

ok = True


def auto_approve_loop(stop_event: threading.Event) -> None:
    """SSE 消费期间轮询 /agent/permissions/pending，自动允许 execute_code 等 ASK 请求。
    模拟用户在 ApprovalModal 点击「允许」（remember=False，逐次询问）。"""
    while not stop_event.is_set():
        try:
            with urllib.request.urlopen(BASE + "/agent/permissions/pending", timeout=3) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception:
            time.sleep(0.3)
            continue
        for rq in data.get("requests") or []:
            if rq.get("status") == "pending":
                body = json.dumps({"request_id": rq["id"], "allowed": True, "remember": False}).encode("utf-8")
                req = urllib.request.Request(
                    BASE + "/agent/approve",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req, timeout=5):
                        pass
                except Exception:
                    pass
        time.sleep(0.3)


def check(label: str, cond: bool, detail: str = ""):
    global ok
    status = "PASS" if cond else "FAIL"
    if not cond:
        ok = False
    print(f"[{status}] {label}" + (f" — {detail}" if detail else ""))


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def consume_sse(payload: dict) -> list[tuple[str, dict]]:
    """POST /agent/execute 并消费 SSE 帧，返回 [(event, data_dict), ...]。"""
    req = urllib.request.Request(
        f"{BASE}/agent/execute",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    # 超时对齐 LLM 上限（litellm timeout=300s）+ 余量；慢 LLM 回合期间无帧是正常现象
    resp = urllib.request.urlopen(req, timeout=600)
    stop_approver = threading.Event()
    threading.Thread(target=auto_approve_loop, args=(stop_approver,), daemon=True).start()
    frames: list[tuple[str, dict]] = []
    cur_event = None
    data_buf: list[str] = []
    try:
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\r\n")
            if not line:
                if cur_event:
                    frames.append((cur_event, json.loads("".join(data_buf))))
                cur_event = None
                data_buf = []
                continue
            if line.startswith("event: "):
                cur_event = line[len("event: "):].strip()
            elif line.startswith("data: "):
                data_buf.append(line[len("data: "):])
        if cur_event:
            frames.append((cur_event, json.loads("".join(data_buf))))
    finally:
        stop_approver.set()
    return frames


def plan_until_executable(question: str, tries: int = 3) -> tuple[dict, str]:
    """/agent/plan 可能回 clarify（LLM 觉得问法有歧义，先要用户澄清）。
    此时 data_check 为 None、plan 无实质步骤；追加片区信息重新 plan，直到拿到可执行计划。
    返回 (plan, 最终使用的 question)。"""
    for _ in range(tries):
        plan = post("/agent/plan", {"question": question})
        print(f"[Plan] intent={plan['intent_id']} op={plan['operation']} targets={plan['targets']}")
        if plan["intent_id"] != "clarify":
            return plan, question
        print(f"  -> clarify（需澄清），追加片区范围重试：{plan['summary']}")
        question = question + "（范围用深圳）"
    return plan, question


def run(label: str, question: str, want: dict):
    print(f"\n{'=' * 64}\n{label}\n问：{question}\n{'=' * 64}")
    plan, question = plan_until_executable(question)
    dc = plan.get("data_check") or {}
    print(f"       summary={plan['summary']}")
    print(f"       data_check.state={dc.get('state')}  missing={dc.get('missing')}")
    for key, cond in want.get("plan", {}).items():
        check(f"plan.{key}", cond(plan))
    check("plan 步骤非空", len(plan["plan"]) >= 2, f"{len(plan['plan'])} 步")

    frames = consume_sse({**plan, "question": question})
    types = [e for e, _ in frames]
    exec_frames = [d for e, d in frames if e == "execution"]
    result = (exec_frames[0] or {}).get("data", {}) if exec_frames else {}
    status = [d.get("data", {}).get("status") for e, d in frames if e == "stream_end"]
    print(f"\n[SSE] {len(frames)} 帧 · status={status}")
    check("流式结束 status=success", status == ["success"], str(status))

    td = [d for e, d in frames if e == "thought_delta"]
    check("真流式：thought_delta 逐字到达", len(td) >= 5, f"{len(td)} 个增量帧")
    if not result:
        print("  !! 无 execution 事件（LLM 回退或失败）")
        check("execution 事件存在", False)
        return

    print(f"\n[Trace] {len(result.get('trace', []))} 条")
    for t in result.get("trace", []):
        meta = f"  <- {t['meta']}" if t.get("meta") else ""
        print(f"  {t['type']:<12} {t['content']}{meta}")
    group = result.get("result_group")
    print(f"\n[ResultGroup] title={group['title'] if group else None} widgets={len(group['widgets']) if group else 0}")
    for w in (group.get("widgets") or []) if group else []:
        extra = f" data={json.dumps(w['data'], ensure_ascii=False)}" if w.get("data") else ""
        print(f"  - {w['widget_type']:<7} {w['title']} = {w.get('value')} ({w.get('subtitle')}){extra}")
    print(f"[Evidence] {len(result.get('evidence', []))} 条")
    for e in result.get("evidence", [])[:4]:
        print(f"  • {e}")
    check("used_fallback=False（真实 LLM 路径）", result.get("used_fallback") is False)
    trace = result.get("trace", [])
    check("trace 含 LLM 叙事文本", any(t["type"] == "text" and len(t["content"]) > 10 for t in trace))
    check("trace 含工具 ran 事件", any(t["type"] == "ran" for t in trace))
    check("result_group 有结果卡", group is not None and len(group["widgets"]) >= 1, group["title"] if group else "")
    check("evidence 非空", len(result.get("evidence", [])) > 0)


run(
    "路径 1 · 空间裁剪（平台真实数据 + 生成图层）",
    "把深圳的餐饮POI裁剪到深圳的街道边界范围内，统计保留了多少餐饮点",
    {
        "plan": {
            "targets": lambda p: any("餐饮" in t or t == "poi_餐饮" for t in p["targets"]),
            "operation": lambda p: p["operation"] in ("general", "overlay", "coverage"),
        }
    },
)
run(
    "路径 2 · 服务范围缓冲区（平台真实数据 + 生成图层）",
    "对深圳的公共设施POI做300米服务范围缓冲区",
    {
        "plan": {
            "targets": lambda p: any("公共设施" in t or t == "poi_公共设施" for t in p["targets"]),
            "operation": lambda p: p["operation"] in ("general", "coverage", "buffer"),
        }
    },
)
run(
    "路径 3 · 空间密度（平台真实数据）",
    "计算深圳餐饮POI的空间密度",
    {
        "plan": {
            "targets": lambda p: any("餐饮" in t or t == "poi_餐饮" for t in p["targets"]),
            "operation": lambda p: p["operation"] in ("density", "general", "statistics"),
        }
    },
)

print("\n=== 结论:", "全部通过" if ok else "存在失败项", "===")
