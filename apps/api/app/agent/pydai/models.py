"""PydanticAI 迁移：领域模型（宽松规划产物 + 规划上下文）。

_RawPlanResult 忠实还原旧 planner chat_json_messages 的宽松 JSON 契约：
字段全可选默认——模型漏填不触发 OutputValidationError 反复重试。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class _RawPlanResult(BaseModel):
    """Planner 宽松产物：模型按此 schema 输出，后经 _normalize_output 规整成 PlanResult。

    注意：不用 PlanResult 直接做 output_type——其 intent_id/summary 必填，
    模型漏填会触发 OutputValidationError 反复重试，违背「宽松 JSON 契约」。
    """

    thinking: str | None = None
    intent_id: str | None = None
    summary: str | None = None
    operation: str | None = None
    targets: list[str] | None = None
    distance: int | None = None
    target_distances: dict[str, int] | None = None
    required_data: list[str] | None = None
    plan: list[dict[str, Any]] | None = None
    clarify: dict[str, Any] | None = None
    data_check: dict[str, Any] | None = None


class PlanDeps(BaseModel):
    """Planner 的 RunContext.deps：把 question/history 带给 _normalize_output。"""

    question: str
    history: list[dict] = Field(default_factory=list)
    conversation_id: str | None = None
