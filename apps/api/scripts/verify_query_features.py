"""query_features 全链路验证：真实 LLM 规划 → Agent 调 query_features → 成果卡。

问的是平台数据（深圳 POI·医疗保健）里"人民医院"的数量，全部真计算。
前置：后端已启动（uvicorn app.main:app --port 8011）且已配 DEEPSEEK_API_KEY。
运行：PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/verify_query_features.py
"""

import json
import threading
import urllib.request

BASE = "http://127.0.0.1:8011"


def auto_approve_loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            with urllib.request.urlopen(BASE + "/agent/permissions/pending", timeout=3) as r:
                data = json.loads(r.read().decode("utf-8"))
        except Exception:
            import time
            time.sleep(0.3)
            continue
        for rq in data.get("requests") or []:
            if rq.get("status") == "pending":
                body = json.dumps({"request_id": rq["id"], "allowed": True, "remember": False}).encode("utf-8")
                req = urllib.request.Request(BASE + "/agent/approve", data=body,
                                             headers={"Content-Type": "application/json"}, method="POST")
                try:
                    with urllib.request.urlopen(req, timeout=5):
                        pass
                except Exception:
                    pass
        import time
        time.sleep(0.3)


def post(path: str, payload: dict) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def consume_sse(payload: dict) -> list[tuple[str, dict]]:
    req = urllib.request.Request(BASE + "/agent/execute", data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    resp = urllib.request.urlopen(req, timeout=600)
    stop = threading.Event()
    threading.Thread(target=auto_approve_loop, args=(stop,), daemon=True).start()
    frames, cur, buf = [], None, []
    try:
        for raw in resp:
            line = raw.decode("utf-8").rstrip("\r\n")
            if not line:
                if cur:
                    frames.append((cur, json.loads("".join(buf))))
                cur, buf = None, []
                continue
            if line.startswith("event: "):
                cur = line[7:].strip()
            elif line.startswith("data: "):
                buf.append(line[6:])
        if cur:
            frames.append((cur, json.loads("".join(buf))))
    finally:
        stop.set()
    return frames


question = "深圳医疗类POI数据里，名字含「人民医院」的有多少家？帮我查出来，列出几家的名字和地址"
print("问：", question)
plan = post("/agent/plan", {"question": question})
print("[Plan]", plan["intent_id"], "| op=", plan["operation"], "| targets=", plan["targets"])
print("       summary=", plan["summary"])

frames = consume_sse({**plan, "question": question})
exec_frames = [d for e, d in frames if e == "execution"]
result = (exec_frames[0] or {}).get("data", {}) if exec_frames else {}
group = result.get("result_group")

print(f"\n[SSE] {len(frames)} 帧")
ran_tools = [t["content"] for t in result.get("trace", []) if t["type"] == "ran"]
print("[工具调用]", ran_tools)
print("[成果组]", group["title"] if group else None, "| widgets:", len(group["widgets"]) if group else 0)
for w in (group.get("widgets") or []) if group else []:
    print("  -", w["widget_type"], "|", w["title"], "=", w.get("value"), "|", json.dumps(w.get("data", {}), ensure_ascii=False)[:120])

used_query = any("query_features" in t for t in ran_tools)
matched = None
for w in (group.get("widgets") or []) if group else []:
    if "要素查询" in w["title"]:
        matched = w.get("value")
print("\n结论: query_features 被调用:", used_query, "| 匹配数:", matched)
ok = used_query and matched and "712" in str(matched)
print("=== 全部通过 ===" if ok else "=== 有失败 ===")
