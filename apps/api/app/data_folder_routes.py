"""本地数据目录接入（第一步「只读发现」）的 HTTP 路由。

仅负责发现与数据目录管理：链接/重扫/取消链接/白名单。不涉及启用为图层
（第二步）、大型数据查询（第三步）。错误统一转中文 400/403，供产品人员理解。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.data_folder import (
    DataFolderError,
    add_allowlist_root,
    browse_folder_dialog,
    disable_all_datasets,
    disable_dataset,
    enable_all_datasets,
    enable_dataset,
    get_download_folder,
    get_source,
    link_source,
    list_allowlist,
    list_sources,
    rescan_source,
    set_download_folder,
    unlink_source,
)
from app.models.data_folder import AllowlistAddRequest, LinkFolderRequest, FolderSource

router = APIRouter(prefix="/api/data-sources", tags=["data-sources"])


def _handle(exc: DataFolderError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("")
def api_list_sources() -> dict:
    """列出已链接数据源 + 当前白名单根目录。"""
    return {"sources": [s.model_dump() for s in list_sources()], "allowlist": list_allowlist()}


@router.post("/link", response_model=FolderSource)
def api_link_source(req: LinkFolderRequest) -> FolderSource:
    try:
        return link_source(req.root_path, req.alias)
    except DataFolderError as exc:
        raise _handle(exc) from exc


@router.post("/browse-folder")
def api_browse_folder() -> dict:
    """弹出系统原生文件夹选择器，返回用户选中的目录路径。"""
    path = browse_folder_dialog()
    return {"path": path, "cancelled": path is None}


@router.get("/download-folder")
def api_get_download_folder() -> dict:
    return {"path": get_download_folder()}


@router.post("/download-folder")
def api_set_download_folder(req: AllowlistAddRequest) -> dict:
    """设置下载目录：联网取数默认落盘到该本地文件夹（自动白名单 + 链接为数据源）。"""
    try:
        return set_download_folder(req.root_path)
    except DataFolderError as exc:
        raise _handle(exc) from exc


@router.post("/{source_id}/datasets/{dataset_id}/enable")
def api_enable_dataset(source_id: str, dataset_id: str) -> dict:
    """把「可直接启用」的数据集启用为项目图层（第二步「小数据启用」）。"""
    if get_source(source_id) is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    try:
        return enable_dataset(source_id, dataset_id)
    except DataFolderError as exc:
        raise _handle(exc) from exc


@router.post("/{source_id}/datasets/{dataset_id}/disable")
def api_disable_dataset(source_id: str, dataset_id: str) -> dict:
    """停用一个已启用数据集（移除其图层，幂等）。"""
    if get_source(source_id) is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    try:
        return disable_dataset(source_id, dataset_id)
    except DataFolderError as exc:
        raise _handle(exc) from exc


@router.post("/{source_id}/enable-all")
def api_enable_all_datasets(
    source_id: str,
    limit: int | None = None,
    folder_path: str | None = None,
    dry_run: bool = False,
) -> dict:
    """文件夹级全部启用（硬上限 20 + 512MB 软预算）；folder_path 非空时仅作用于该子目录前缀。

    dry_run=true 只返回后端算好的计划（planned_count/estimated_features/capped 等），不真正启用，
    供前端确认框展示与后端一致的数字（不臆断一定计划 20 个）。
    """
    if get_source(source_id) is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    try:
        return enable_all_datasets(source_id, limit=limit, rel_path=folder_path, dry_run=dry_run)
    except DataFolderError as exc:
        raise _handle(exc) from exc


@router.post("/{source_id}/disable-all")
def api_disable_all_datasets(source_id: str, folder_path: str | None = None) -> dict:
    """文件夹级全部停用；folder_path 非空时仅作用于该子目录前缀。"""
    if get_source(source_id) is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    try:
        return disable_all_datasets(source_id, rel_path=folder_path)
    except DataFolderError as exc:
        raise _handle(exc) from exc


@router.post("/{source_id}/rescan", response_model=FolderSource)
def api_rescan_source(source_id: str) -> FolderSource:
    if get_source(source_id) is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    try:
        return rescan_source(source_id)
    except DataFolderError as exc:
        raise _handle(exc) from exc


@router.delete("/{source_id}")
def api_unlink_source(source_id: str) -> dict:
    if get_source(source_id) is None:
        raise HTTPException(status_code=404, detail="数据源不存在")
    unlink_source(source_id)
    return {"ok": True}


@router.get("/allowlist")
def api_get_allowlist() -> dict:
    return {"roots": list_allowlist()}


@router.post("/allowlist")
def api_add_allowlist(req: AllowlistAddRequest) -> dict:
    try:
        roots = add_allowlist_root(req.root_path)
    except DataFolderError as exc:
        raise _handle(exc) from exc
    return {"roots": roots}
