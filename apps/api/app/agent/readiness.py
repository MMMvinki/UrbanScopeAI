"""Readiness Checker：检查分析计划所需数据是否就绪。

返回状态：
- ready   ：所需数据全部存在
- partial ：部分缺失（可执行部分指标）
- blocked ：关键数据缺失（无法执行）
"""

from collections.abc import Sequence

from app.data_registry import DATA_REGISTRY
from app.models.agent import DataCheckItem, DataCheckResult

# 缺数据时给用户的选择：只放真实可用的三项（其余两项是空占位，点按钮无反应，已去掉）。
MISSING_ACTIONS = ["跳过该项", "上传数据", "换个问法"]

# 研究范围语义别名：MODULE_SPECS.required_data 写的是「候选研究范围/研究范围…」，但项目里
# 研究范围图层 id 常叫 study_area、或名字含「研究范围」（如 rail_overlay_研究图层）。字符串
# 精确匹配不上就会误判「研究范围缺失」→ 反复让用户点「跳过/确定」。加一个语义别名兜底：
# 只要 required 项带研究范围语义、且项目里已有研究范围图层，就视为就绪。
_STUDY_AREA_SEMANTICS = ("研究范围", "候选范围", "研究区", "分析范围", "片区", "这个区", "这里")


def _study_area_layer(item: str):
    """返回项目里代表研究范围的图层（study_area / 名字含「研究范围」）；无则 None。"""
    label = (item or "").strip()
    if not any(term in label for term in _STUDY_AREA_SEMANTICS):
        return None
    for layer in DATA_REGISTRY.list_layers():
        if layer.id == "study_area":
            return layer
        if layer.name and "研究范围" in layer.name:
            return layer
    return None


def check_readiness(required: Sequence[str]) -> DataCheckResult:
    ready: list[str] = []
    missing: list[str] = []
    details: list[DataCheckItem] = []

    for item in required:
        layer = DATA_REGISTRY.get_layer(item)
        if layer is not None:
            ready.append(layer.name)
            details.append(DataCheckItem(item=layer.name, ready=True, source=layer.source.value))
            continue
        study_layer = _study_area_layer(item)
        if study_layer is not None:
            # 研究范围已作为图层存在（study_area / 名字含「研究范围」），不因精确匹配不上而判缺失
            ready.append(study_layer.name or item)
            details.append(DataCheckItem(item=study_layer.name or item, ready=True, source=study_layer.source.value))
            continue
        missing.append(item)
        details.append(DataCheckItem(item=item, ready=False, suggestions=list(MISSING_ACTIONS)))

    if not missing:
        state = "ready"
    elif ready:
        state = "partial"
    else:
        state = "blocked"

    return DataCheckResult(state=state, ready=ready, missing=missing, details=details)
