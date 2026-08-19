# -*- coding: utf-8 -*-
"""execute_code 审批路径自测：FORCE_ASK → BROKER.wait 阻塞 → resolve → 子进程执行，
且 CODE_BLOCK_START/CODE_DELTA/CODE_RESULT 事件与 tool_calls 落入 RunArchive。
"""
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.agent.emitter import EventEmitter
from app.archive import RunArchive
from app.permissions import PermissionAction, PermissionPolicy, PermissionRuntime
from app.permissions.broker import BROKER
from app.tools import run_tool
from app.tools.context import ToolContext
from app.workspace import WORKSPACE_ROOT, ensure_workspace

ensure_workspace()
ws = WORKSPACE_ROOT

# 清掉上一轮自测种下的 execute_code allow 持久化规则 → 这次必须走 FORCE_ASK 审批路径
permissions_file = ws / ".workdir" / "permissions.json"
if permissions_file.exists():
    os.remove(permissions_file)

run_id = "test-exec-approve"
archive = RunArchive.open(run_id=run_id, prompt="test approve", workspace_path=str(ws))
emitter = EventEmitter(run_id, None, None, archive)  # queue/loop 传 None：仅落盘，验证 archive 事件
cancel_event = threading.Event()

pr = PermissionRuntime(
    policy=PermissionPolicy(workspace_path=str(ws), tool_overrides={"execute_code": PermissionAction.ASK}),
    approval_callback=lambda tool, args, decision: BROKER.wait(
        tool, args, decision, emit=emitter.emit, cancel_event=cancel_event
    ),
)
ctx = ToolContext(workspace_path=ws, run_id=run_id, permission_runtime=pr,
                  event_emitter=emitter, cancel_event=cancel_event)

# 审批线程：轮询 pending 请求并允许
stop = threading.Event()
def approver() -> None:
    while not stop.is_set():
        pend = BROKER.pending_requests()
        if pend:
            rid = pend[0]["id"]  # PermissionRequest.to_dict 键名是 id
            print(f"[approver] resolving {rid} → allow")
            BROKER.resolve(rid, allowed=True, remember=False)
            return
        time.sleep(0.05)

# 主线程先发 permission_request（BROKER.wait 会阻塞）；approver 随后解阻塞
t = threading.Thread(target=approver, daemon=True)
t.start()

# 用真实 run 的工具调用形状（llm_executor 会在 execute_code 前后发 TOOL_START/TOOL_RESULT）
ctx.emit("tool_start", {"tool": "execute_code", "args": {"code": "print('approved-and-ran')"}})
r = run_tool("execute_code", {"code": "print('approved-and-ran')"}, ctx=ctx)
ctx.emit("tool_result", {"tool": "execute_code", "ok": r.success,
                         "message": r.message, "output": r.output or {}})
stop.set()
t.join(timeout=5)

print("result ok:", r.success)
print("logs:", (r.output or {}).get("logs") if r.success else r.message)

if ctx.subprocess_executor is not None:
    ctx.subprocess_executor.cleanup()
archive.close(status="success" if r.success else "error")

evs = archive.read_events()
print("event types:", [e.get("type") for e in evs])
print("tool_calls:", [(tc.get("name"), tc.get("status"), tc.get("output")) for tc in archive.read_tool_calls()])
print("done")
