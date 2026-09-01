"""下载数据落盘路径决策：下载目录（用户配置的本地文件夹）优先，否则 workspace 默认。

供 datasource / OSM / amap 三个联网取数 provider 共用，避免落盘逻辑三处重复。
下载目录由 data_folder.set_download_folder 配置，落在白名单内，可越出 workspace。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path


def resolve_download_output(raw_path: str | None, command: str, workspace: Path, subdir: str) -> Path:
    """决定下载数据的落盘路径。

    - 显式 raw_path：解析（绝对 / 相对 workspace）
    - 否则：下载目录（若配置）> workspace/.workdir/{subdir}/
    允许落盘到 workspace 内或下载目录内（两者均已白名单）；其余越界拒绝。
    """
    from app.data_folder import get_download_folder

    if raw_path:
        raw = Path(str(raw_path)).expanduser()
        path = raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()
    else:
        safe = re.sub(r"[^A-Za-z0-9_.\-一-鿿]+", "_", command).strip("_") or "download"
        fname = f"{safe}_{uuid.uuid4().hex[:8]}.geojson"
        dl = get_download_folder()
        if dl:
            path = (Path(dl) / fname).resolve()
        else:
            path = (workspace / ".workdir" / subdir / fname).resolve()

    # 越界校验：允许 workspace 内 + 下载目录内
    in_workspace = workspace == path or workspace in path.parents
    dl = get_download_folder()
    in_download = bool(dl) and (Path(dl).resolve() == path or Path(dl).resolve() in path.parents)
    if not in_workspace and not in_download:
        raise ValueError(f"output_path 必须位于 workspace 或下载目录内: {path}")

    if path.suffix.lower() not in {".geojson", ".json"}:
        path = path.with_suffix(".geojson")
    return path


def notify_download_saved() -> None:
    """下载落盘后触发重扫下载目录，让新数据即时进数据面板。"""
    try:
        from app.data_folder import rescan_download_folder

        rescan_download_folder()
    except Exception:  # noqa: BLE001
        pass  # 重扫失败不致命，不影响下载本身
