"""原子文件写入助手：tmp + os.replace（带有限退避重试），专治 Windows 瞬时文件占用。

背景：Windows 上 `os.replace(tmp, dst)` 的目标若在替换瞬间被另一句柄占着
（实时扫描/索引/并发写），会抛 PermissionError(WinError 5)。写法都是
「临时文件写好 → 原子换入」避免前端/读取方读到半截 JSON；但换入本身会偶发被占。
统一在这里做有限指数退避重试，等瞬时锁释放；仍失败则原样上抛（诚实上报，不吞错误）。

调用方负责自己的并发语义（如 read-modify-write 需自行加锁）；本助手只保证换入可靠。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

_REPLACE_RETRIES = 4
_REPLACE_BASE_DELAY = 0.02


def atomic_replace(src: str | os.PathLike, dst: str | os.PathLike) -> None:
    """os.replace 带有限退避重试，专治 Windows 瞬时文件占用（WinError 5）。

    每次 PermissionError 后按 base, base*2, base*4… 退避；最后一次仍失败则上抛。
    """
    delay = _REPLACE_BASE_DELAY
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(delay)
            delay *= 2


def atomic_write_json(path: str | os.PathLike, payload: object) -> None:
    """原子写 JSON：临时文件 + os.replace 换入（带重试），父目录自动创建。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=p.name + ".", suffix=".tmp", dir=str(p.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        atomic_replace(tmp_path, p)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
