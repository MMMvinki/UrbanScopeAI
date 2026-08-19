"""ProjectStore：项目元数据 + 每项目独立图层空间的持久化。

每个「项目」是一间独立厨房（project-feature-design.md）：项目自己的数据文件、
自己的地图图层、自己的运行历史。本模块管理项目列表与每项目的图层落盘，
DataRegistry 负责把「当前项目图层」换入/换出注册表（底座 demo/platform 全局共享）。

布局：
  workspace/
    projects/
      <project_id>/
        project.json          # 项目元数据（名称/描述/创建时间）
        data/layers/*.json    # 项目自己的图层 GeoJSON（upload/generated/OSM 物化）
        layers.json           # 项目图层元数据清单（LayerMetadata 列表）
      default/                # 默认项目（现有行为：工作区即默认项目）
    .workdir/runs/            # 运行历史（全局，按 run_id）
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.workspace import WORKSPACE_ROOT

PROJECTS_ROOT = WORKSPACE_ROOT / "projects"
DEFAULT_PROJECT_ID = "default"

# 现有「无项目」时的图层目录：默认项目的图层空间（保持向后兼容）
LEGACY_LAYER_DIR = WORKSPACE_ROOT / "data" / "layers"


def ensure_projects() -> Path:
    PROJECTS_ROOT.mkdir(parents=True, exist_ok=True)
    (PROJECTS_ROOT / DEFAULT_PROJECT_ID).mkdir(parents=True, exist_ok=True)
    return PROJECTS_ROOT


def project_dir(project_id: str) -> Path:
    ensure_projects()
    return PROJECTS_ROOT / (project_id or DEFAULT_PROJECT_ID)


def assert_project_id(project_id: str) -> str:
    """校验项目 id：非空、无路径穿越（禁止 `..` 逃出 projects 根）、目录必须存在。"""
    pid = project_id or DEFAULT_PROJECT_ID
    root = PROJECTS_ROOT.resolve()
    candidate = (PROJECTS_ROOT / pid).resolve()
    if candidate.parent != root or candidate == root or not candidate.is_dir():
        raise ValueError(f"非法项目 id：{project_id}")
    return pid


def project_layer_dir(project_id: str) -> Path:
    d = project_dir(project_id) / "data" / "layers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def project_meta_path(project_id: str) -> Path:
    return project_dir(project_id) / "project.json"


def project_manifest_path(project_id: str) -> Path:
    return project_dir(project_id) / "layers.json"


def _default_meta() -> dict:
    return {
        "id": DEFAULT_PROJECT_ID,
        "name": "默认项目",
        "description": "平台初始项目（含演示研究范围与内置示例图层）。",
        "created_at": "2026-08-15T00:00:00",
    }


def list_projects() -> list[dict]:
    ensure_projects()
    out: list[dict] = []
    for child in sorted(PROJECTS_ROOT.iterdir()):
        if not child.is_dir():
            continue
        pid = child.name
        meta = _read_meta(pid)
        out.append(_with_counts(pid, meta))
    # 置顶项目在前（pinned_at 较新者优先），其余按创建时间倒序
    out.sort(key=lambda m: (m.get("pinned_at") or "", m.get("created_at") or ""), reverse=True)
    return out


def get_project(project_id: str) -> dict | None:
    pid = project_id or DEFAULT_PROJECT_ID
    if not (project_dir(pid)).is_dir():
        return None
    return _with_counts(pid, _read_meta(pid))


def create_project(name: str, description: str | None = None) -> dict:
    ensure_projects()
    import time

    from app.models.project import new_project_id

    pid = new_project_id(name)
    if (PROJECTS_ROOT / pid).exists():
        pid = f"{pid}_{int(time.time()) % 10000}"
    d = project_dir(pid)
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": pid,
        "name": (name or "未命名项目").strip() or "未命名项目",
        "description": (description or "").strip(),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    project_meta_path(pid).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "layers.json").write_text("[]", encoding="utf-8")
    return _with_counts(pid, meta)


def delete_project(project_id: str) -> bool:
    """删除项目目录（拒绝删除默认项目）。返回是否删除成功。"""
    pid = project_id or DEFAULT_PROJECT_ID
    if pid == DEFAULT_PROJECT_ID:
        return False
    d = project_dir(pid)
    if not d.exists():
        return False
    shutil.rmtree(d, ignore_errors=True)
    return True


def update_project(
    project_id: str,
    name: str | None = None,
    description: str | None = None,
    pinned: bool | None = None,
) -> dict | None:
    """改名 / 改描述 / 置顶：只更新传了的字段，目录名（=id）不动。不存在返回 None。"""
    pid = assert_project_id(project_id)
    path = project_meta_path(pid)
    if not path.exists():
        return None
    meta = _read_meta(pid)
    if name is not None:
        cleaned = (name or "").strip()
        if cleaned:
            meta["name"] = cleaned
    if description is not None:
        meta["description"] = (description or "").strip()
    if pinned is not None:
        import time

        if pinned:
            meta["pinned_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        else:
            meta.pop("pinned_at", None)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return _with_counts(pid, meta)


def _read_meta(project_id: str) -> dict:
    path = project_meta_path(project_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    if project_id == DEFAULT_PROJECT_ID:
        meta = _default_meta()
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return meta
    return {"id": project_id, "name": project_id, "description": "", "created_at": ""}


def _with_counts(project_id: str, meta: dict) -> dict:
    from app.archive import RunArchive

    layers_path = project_manifest_path(project_id)
    layer_count = 0
    try:
        layer_count = len(json.loads(layers_path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError):
        layer_count = 0
    try:
        run_count = len(list(RunArchive.runs_root().glob("run_*.json")))
    except OSError:
        run_count = 0
    out = dict(meta)
    out["layer_count"] = layer_count
    out["run_count"] = run_count
    return out
