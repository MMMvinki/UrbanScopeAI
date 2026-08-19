# -*- coding: utf-8 -*-
"""execute_code 接线自测（.venv python 运行，工作目录 = apps/api）。

测：子进程启动、geopandas 可用、namespace 跨 exec 持久、buffer→to_file、
auto_install 不误触发、code_validation 拒绝、cleanup 防泄漏。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.permissions import PermissionAction, PermissionPolicy, PermissionRuntime
from app.tools import run_tool
from app.tools.context import ToolContext
from app.workspace import WORKSPACE_ROOT, ensure_workspace

ensure_workspace()

# FORCE_ASK 在策略表之前短路（设计如此）；用"记住允许"持久化规则来放行，
# 复刻真实 run 里用户审批后的路径。
from app.permissions.store import PermissionRuleStore

PermissionRuleStore(str(WORKSPACE_ROOT)).add_rule(tool="execute_code", action=PermissionAction.ALLOW)

policy = PermissionPolicy(workspace_path=str(WORKSPACE_ROOT))
pr = PermissionRuntime(policy=policy)
ctx = ToolContext(workspace_path=WORKSPACE_ROOT, permission_runtime=pr, run_id="test-exec-1")


def show(tag: str, r) -> None:
    ok = "OK " if r.success else "ERR"
    print(f"[{ok}] {tag}")
    if not r.success:
        print("      message:", r.message)
    else:
        out = r.output or {}
        print("      output:", str(out.get("output"))[:300])
        logs = str(out.get("logs") or "")[:500]
        if logs.strip():
            print("      logs:", logs.replace("\n", " | ")[:500])


# 1) import geopandas + 建 GeoDataFrame（state 持久）
show("import geopandas + pts", run_tool("execute_code", {
    "code": "import geopandas as gpd\n"
            "pts = gpd.GeoDataFrame({'name': ['a', 'b'], 'geometry': gpd.points_from_xy([121.0, 121.5], [31.0, 31.2])}, crs='EPSG:4326')\n"
            "print('gpd', gpd.__version__)"
}, ctx=ctx))

# 2) namespace 跨 exec 持久
show("namespace persistent", run_tool("execute_code", {
    "code": "print('points:', len(pts), 'crs:', pts.crs)"
}, ctx=ctx))

# 3) buffer + to_file（L2 落盘到 workspace/.workdir/artifacts/）
show("buffer + to_file", run_tool("execute_code", {
    "code": "import pathlib\n"
            "pathlib.Path('.workdir/artifacts').mkdir(parents=True, exist_ok=True)\n"
            "buf = pts.to_crs('EPSG:3857').buffer(1000)\n"
            "out = buf.to_crs('EPSG:4326')\n"
            "out.to_file('.workdir/artifacts/test_buffer.geojson', driver='GeoJSON')\n"
            "print('saved features:', len(out))"
}, ctx=ctx))

# 4) code_validation：超长 sleep 拒绝
show("reject long sleep", run_tool("execute_code", {
    "code": "import time\ntime.sleep(120)"
}, ctx=ctx))

# 5) code_validation：think 标签拒绝
show("reject think tag", run_tool("execute_code", {
    "code": "<think>先分析一下</think>\nprint(1)"
}, ctx=ctx))

# 6) cleanup（防泄漏）
if ctx.subprocess_executor is not None:
    ctx.subprocess_executor.cleanup()
    print("[OK ] cleanup() 调用成功")
else:
    print("[WARN] subprocess_executor 未创建")

print("done")
