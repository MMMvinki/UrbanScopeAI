"""数据目录操作工具：Agent 直接列出/启用/停用本地数据目录里的数据集。

对应「Agent 分析时能否直接启用/停用数据目录里的数据」需求。handler 内懒 import
app.data_folder（防 import 环），包装 list_sources/enable_dataset/disable_dataset。

注意：folder_enable_dataset 绝不返回 output_layer_id（会触发 llm_executor._register_generated
二次注册，把 folder/Table 图层改成 generated/Polygon），只返回 layer_id；即时 UI 刷新通过
ToolContext 发 GENERATED_LAYER / LAYER_REMOVED 事件，全量刷新作兜底。
"""

from __future__ import annotations

from app.models.events import EventType
from app.models.tool import ToolResult
from app.tools.context import get_current_context


def run_folder_list(params: dict) -> ToolResult:
    """列出数据源/数据集/状态/几何（含 table），支持按 source 过滤并限制输出规模。

    指定不存在的 source_id 返回明确失败（不得成功返回空目录，误导 Agent）；
    缺省 source_id 且所有来源为空仍正常成功。
    """
    from app import data_folder

    source_id = (params.get("source_id") or "").strip() if isinstance(params, dict) else ""
    sources = data_folder.list_sources()
    if source_id:
        matched = [s for s in sources if s.source_id == source_id]
        if not matched:
            return ToolResult(success=False, message=f"数据源不存在：{source_id}")
        sources = matched

    rows: list[dict] = []
    for src in sources:
        for d in src.datasets:
            rows.append({
                "source_id": src.source_id,
                "source_alias": src.alias,
                "dataset_id": d.dataset_id,
                "name": d.name,
                "status": d.status.value,
                "enabled": d.enabled,
                "geometry": d.geometry,
                "feature_count": d.feature_count,
            })

    total = len(rows)
    rows = rows[:200]  # 限制输出规模，避免大目录把响应撑爆
    return ToolResult(
        success=True,
        output={"datasets": rows, "total": total, "truncated": total > 200},
        message=f"共 {total} 个数据集" + ("（已截断到前 200 条）" if total > 200 else ""),
    )


def run_folder_enable_dataset(params: dict) -> ToolResult:
    """启用数据目录里的一个数据集为图层（只返回 layer_id，绝不返回 output_layer_id）。"""
    from app import data_folder

    source_id = str((params or {}).get("source_id", "")).strip()
    dataset_id = str((params or {}).get("dataset_id", "")).strip()
    if not source_id or not dataset_id:
        return ToolResult(success=False, message="需要 source_id 和 dataset_id")
    try:
        result = data_folder.enable_dataset(source_id, dataset_id)
    except data_folder.DataFolderError as exc:
        return ToolResult(success=False, message=str(exc))

    layer_id = result.get("layer_id")
    get_current_context().emit(EventType.GENERATED_LAYER, {"layer_id": layer_id})
    return ToolResult(
        success=True,
        output={"layer_id": layer_id, "dataset_id": dataset_id, "feature_count": result.get("feature_count")},
        message=f"已启用数据集「{dataset_id}」为图层 {layer_id}",
    )


def run_folder_disable_dataset(params: dict) -> ToolResult:
    """停用数据目录里的一个已启用数据集（移除图层，幂等）。

    仅在实际删除图层后发送 LAYER_REMOVED，事件 payload 用实际 removed_layer_id
    （目录图层与 uploaded 同名冲突时，实际 id 是 dup_986d29 之类后缀，不是 dataset_id）；
    幂等停用（未实际删除）不发送伪删除事件。
    """
    from app import data_folder

    source_id = str((params or {}).get("source_id", "")).strip()
    dataset_id = str((params or {}).get("dataset_id", "")).strip()
    if not source_id or not dataset_id:
        return ToolResult(success=False, message="需要 source_id 和 dataset_id")
    try:
        result = data_folder.disable_dataset(source_id, dataset_id)
    except data_folder.DataFolderError as exc:
        return ToolResult(success=False, message=str(exc))

    removed_layer_id = result.get("removed_layer_id")
    if removed_layer_id:
        get_current_context().emit(EventType.LAYER_REMOVED, {"layer_id": removed_layer_id})
    return ToolResult(
        success=True,
        output={
            "dataset_id": dataset_id,
            "already_disabled": result.get("already_disabled", False),
            "removed_layer_id": removed_layer_id,
        },
        message=result.get("message", "已停用"),
    )
