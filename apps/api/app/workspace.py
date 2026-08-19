"""Workspace：服务器端工作目录 + .workdir 内部结构。

WORKSPACE_ROOT 是代理产物（下载的 OSM/datasource 数据、execute_code 脚本、RunArchive）
的统一根目录。所有落盘必须 resolve 后位于 workspace 内（L2 工作区边界）。

布局：
  workspace/
    data/layers/          # 平台内置图层 GeoJSON 物化副本（execute_code 可直接读取）
    .workdir/
      runs/<run_id>/      # RunArchive（meta.json / events.jsonl / ...）
      osm/  data/  script/ artifacts/plots/
      permissions.json    # 权限规则持久化（Phase 1）
      memory/  skills/       # Phase 2 结构化记忆 / 工作区可执行 Skill 仓库
"""

from __future__ import annotations

from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent / "workspace"

# execute_code 可读的图层数据目录（内置图层 GeoJSON 的物化副本）
LAYER_DATA_DIR = WORKSPACE_ROOT / "data" / "layers"

# .workdir 子目录
WORKDIR_DIR = WORKSPACE_ROOT / ".workdir"
RUNS_DIR = WORKDIR_DIR / "runs"
OSM_DIR = WORKDIR_DIR / "osm"
DATA_DIR = WORKDIR_DIR / "data"
SCRIPT_DIR = WORKDIR_DIR / "script"
PLOTS_DIR = WORKDIR_DIR / "artifacts" / "plots"
PERMISSIONS_FILE = WORKDIR_DIR / "permissions.json"
LAYER_STYLES_FILE = WORKDIR_DIR / "layer_styles.json"  # 样式侧车（Sprint 2）：{layer_id: style_patch}
DEM_TILES_DIR = WORKDIR_DIR / "dem_tiles"  # DEM 高程瓦片金字塔（build_dem_tiles.py 生成）
SKILLS_DIR = WORKDIR_DIR / "skills"  # 工作区可执行 Skill（kind=executable）仓库根

_SUBDIRS = [
    LAYER_DATA_DIR,
    WORKDIR_DIR,
    RUNS_DIR,
    OSM_DIR,
    DATA_DIR,
    SCRIPT_DIR,
    PLOTS_DIR,
    WORKDIR_DIR / "memory",
    WORKDIR_DIR / "skills",
]


def ensure_workspace() -> Path:
    """确保 workspace 目录结构存在，返回 WORKSPACE_ROOT。"""
    for path in _SUBDIRS:
        path.mkdir(parents=True, exist_ok=True)
    return WORKSPACE_ROOT


def ensure_layer_data_in_workspace() -> Path:
    """把内置 demo 图层 GeoJSON 物化到 workspace/data/layers/，供 execute_code 直接读取。

    数据落在 workspace，子进程代码用相对路径即可 gpd.read_file。
    幂等：目标已存在且 mtime 不比源旧则跳过。返回 LAYER_DATA_DIR。
    """
    ensure_workspace()
    source_dir = Path(__file__).resolve().parent / "demo_data" / "geojson"
    if not source_dir.is_dir():
        return LAYER_DATA_DIR
    for source in source_dir.glob("*.json"):
        target = LAYER_DATA_DIR / source.name
        try:
            if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
                continue
            target.write_bytes(source.read_bytes())
        except OSError:
            continue  # 数据物化失败不致命，仅影响 execute_code 的文件读取便利
    return LAYER_DATA_DIR


def resolve_in_workspace(raw_path: str | Path, subdir: str | None = None) -> Path:
    """把（相对/绝对）路径解析到 workspace 内；越界抛 ValueError（L2 边界）。"""
    raw = Path(str(raw_path)).expanduser()
    resolved = raw.resolve() if raw.is_absolute() else (WORKSPACE_ROOT / raw).resolve()
    if WORKSPACE_ROOT != resolved and WORKSPACE_ROOT not in resolved.parents:
        raise ValueError(f"路径必须位于 workspace 内: {resolved}")
    if subdir and resolved.suffix.lower() not in {".geojson", ".json"}:
        resolved = resolved.with_suffix(".geojson")
    return resolved
