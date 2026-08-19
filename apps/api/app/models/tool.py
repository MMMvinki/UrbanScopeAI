from enum import Enum

from pydantic import BaseModel, Field


class ToolStatus(str, Enum):
    planned = "planned"
    ready = "ready"
    blocked = "blocked"


class ToolSpec(BaseModel):
    name: str
    category: str
    description: str
    # 用户版白话描述：/tools 货架展示用（描述里写给 LLM 的配方/坑不暴露给用户）。
    # 缺省时前端回退到 description。
    display_description: str | None = None
    status: ToolStatus = ToolStatus.planned
    # 工具入参说明（展示）
    parameters: dict[str, object] = Field(default_factory=dict)
    # 工具卡「发起对话」预置提示词（无则前端由 name/description 兜底生成）
    prompt_hint: str | None = None
    # 完整 JSON Schema（OpenAI function calling 格式）：LLM Agent 工具调用契约。
    # 注册表是唯一事实源，llm_executor 据此动态生成 tool schemas，避免手写漂移。
    json_schema: dict[str, object] | None = None


class ToolCall(BaseModel):
    name: str
    arguments: dict[str, object] = Field(default_factory=dict)
    call_id: str | None = None


class ToolResult(BaseModel):
    success: bool = True
    output: dict[str, object] = Field(default_factory=dict)
    message: str | None = None
