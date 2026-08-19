# -*- coding: utf-8 -*-
"""验证层1/层2：execute_code 能读平台数据（通读）、写平台数据被拒（禁写）。

顺序说明：先层2B（文件系统只读，父进程侧无 Python 钩子，chmod 不被拦），
再层2A（子进程侧 Python 钩子）。层2B 把平台文件设为只读后，层2A 的写测试
同时被「钩子 + 文件系统只读」双保险拦截，符合生产形态。

用法（cwd=app/workspace，PYTHONPATH=apps/api）：
    .venv/Scripts/python.exe -X utf8 ../../verify_platform_guard.py
"""
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # apps/api（import app.*）

PLATFORM_FILE = "../platform_data/geojson/poi_餐饮.json"
TMP_TARGET = "../platform_data/geojson/_guard_test_tmp.json"

pass_count = 0
fail_count = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global pass_count, fail_count
    if cond:
        pass_count += 1
        print(f"  PASS  {name}  {detail}")
    else:
        fail_count += 1
        print(f"  FAIL  {name}  {detail}")


# ── 层2B：文件系统只读（挡 GDAL/fiona C 层写，父进程侧无钩子）──
from app.execution.subprocess_executor import SubprocessPythonExecutor

SubprocessPythonExecutor._ensure_platform_readonly()
print("== 层2B 文件系统只读（geopandas/fiona 覆盖已有平台文件应失败）==")
import stat

is_readonly = not (os.stat(PLATFORM_FILE).st_mode & stat.S_IWRITE)
check("平台文件已设只读属性", is_readonly, f"st_mode={oct(os.stat(PLATFORM_FILE).st_mode)}")

import geopandas as gpd

try:
    gdf = gpd.read_file(PLATFORM_FILE)
    check("gpd 读平台数据", len(gdf) > 0, f"读 {len(gdf)} 要素")
except Exception as e:
    check("gpd 读平台数据", False, f"读失败: {e}")

try:
    gdf.head(5).to_file(PLATFORM_FILE, driver="GeoJSON")
    check("gpd 覆盖平台文件被拒", False, "竟然覆盖成功！")
except PermissionError:
    check("gpd 覆盖平台文件被拒", True, "PermissionError")
except OSError as e:
    check("gpd 覆盖平台文件被拒", True, f"OSError({getattr(e,'winerror',None)}): {str(e)[:60]}")
except Exception as e:
    check("gpd 覆盖平台文件被拒", True, f"{type(e).__name__}: {str(e)[:60]}")

# ── 层2A：Python 钩子（_install_platform_protection，子进程侧）──
from app.execution.subprocess_runner import _install_platform_protection

_install_platform_protection()
print("== 层2A Python 钩子 ==")
try:
    open(PLATFORM_FILE, "w").close()
    check("open('w') 拒写", False, "竟然能写！")
except PermissionError as e:
    check("open('w') 拒写", True, str(e)[:40])
except Exception as e:
    check("open('w') 拒写", False, f"错误类型不对: {type(e).__name__}: {str(e)[:40]}")

try:
    os.remove(PLATFORM_FILE)
    check("os.remove 拒删", False, "竟然能删！")
except PermissionError:
    check("os.remove 拒删", True)
except Exception as e:
    check("os.remove 拒删", False, f"错误类型不对: {type(e).__name__}: {str(e)[:40]}")

try:
    os.rename(PLATFORM_FILE, TMP_TARGET)
    check("os.rename 拒移", False, "竟然能移！")
except PermissionError:
    check("os.rename 拒移", True)
except Exception as e:
    check("os.rename 拒移", False, f"错误类型不对: {type(e).__name__}: {str(e)[:40]}")

# 读不受影响
try:
    data = open(PLATFORM_FILE, "r", encoding="utf-8").read()
    check("open('r') 可读", len(data) > 100, f"读 {len(data)} 字符")
except Exception as e:
    check("open('r') 可读", False, f"读失败: {e}")

# 写 workspace 不受影响（读平台 → 算 → 写工作区是合法二次编辑）
ws_file = "data/layers/_guard_test_tmp.json"
try:
    open(ws_file, "w").close()
    check("写 workspace 放行", os.path.exists(ws_file))
    os.remove(ws_file)
except Exception as e:
    check("写 workspace 放行", False, f"错误: {e}")

print(f"\n结果: {pass_count} PASS / {fail_count} FAIL")
sys.exit(0 if fail_count == 0 else 1)
