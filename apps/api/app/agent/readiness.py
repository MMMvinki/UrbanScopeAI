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


def check_readiness(required: Sequence[str]) -> DataCheckResult:
    ready: list[str] = []
    missing: list[str] = []
    details: list[DataCheckItem] = []

    for item in required:
        layer = DATA_REGISTRY.get_layer(item)
        if layer is not None:
            ready.append(layer.name)
            details.append(DataCheckItem(item=layer.name, ready=True, source=layer.source.value))
        else:
            missing.append(item)
            details.append(DataCheckItem(item=item, ready=False, suggestions=list(MISSING_ACTIONS)))

    if not missing:
        state = "ready"
    elif ready:
        state = "partial"
    else:
        state = "blocked"

    return DataCheckResult(state=state, ready=ready, missing=missing, details=details)
