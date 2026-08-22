"""PydanticAI 迁移：Planner（两阶段第一步）。

函数型 output_type：模型按 _RawPlanResult（宽松 schema）产出 JSON → 框架调用
_normalize_output → 现有 _normalize 规整 → result.output 直接是 PlanResult。
消息顺序与旧实现 [system, *history, question] 完全一致（SystemPromptPart 显式前置，
规避「非空 message_history 不注入 system_prompt」的坑——v2 实测行为）。

try_plan 签名不变；PA_AGENT=0 时走旧 loop（见 llm_planner.try_plan）。
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider

from app.agent import llm_client, llm_planner
from app.models.agent import PlanResult

from .models import _RawPlanResult, PlanDeps

logger = logging.getLogger(__name__)


def _build_model() -> OpenAIChatModel:
    """DeepSeek 原生 provider（v2 唯一路径；env 与 llm_client 同源）。

    LiteLLMModel 已从 v2 移除，无兜底；is_available() 继续作「无 key 就不跑」的守卫。
    """
    return OpenAIChatModel(
        llm_client.LLM_MODEL,
        provider=DeepSeekProvider(api_key=llm_client.API_KEY),
    )


def _history_messages(system_prompt: str, history: list[dict] | None) -> list[Any]:
    """把 system + 历史最后 6 轮 → PydanticAI 消息列表。

    顺序与旧实现 [system, *history[-6:], question] 一致；message_history 非空时
    PydanticAI 不注入 agent system_prompt，故 SystemPromptPart 显式前置。
    """
    messages: list[Any] = [ModelRequest(parts=[SystemPromptPart(content=system_prompt)])]
    for turn in (history or [])[-6:]:
        role = turn.get("role")
        content = turn.get("content")
        if role not in ("user", "assistant") or not content:
            continue
        if role == "user":
            # 用户轮必须用 UserPromptPart：OpenAI 模型映射 _map_user_message 只认
            # UserPromptPart，裸 TextPart 会撞 assert_never（assert_never 崩溃→规划失败）。
            # assistant 轮的 TextPart 没问题（_map_model_response 认）。
            messages.append(ModelRequest(parts=[UserPromptPart(content=str(content))]))
        else:
            messages.append(ModelResponse(parts=[TextPart(content=str(content))]))
    return messages


def _normalize_output(ctx: RunContext[PlanDeps], raw: _RawPlanResult) -> PlanResult:
    """模型产物 → 现有 _normalize（一字不改）；返回 PlanResult 即 agent output。

    raw 已被框架按 _RawPlanResult 校验成 dict 形状，_normalize 的 None 分支不会触发；
    万一触发则 raise，交由 pa_try_plan 捕获回退规则版。
    """
    result = llm_planner._normalize(ctx.deps.question, raw.model_dump(), ctx.deps.history)
    if result is None:
        raise ValueError("规划结果无法规范化，回退规则版")
    return result


def pa_try_plan(
    question: str,
    history: list[dict] | None = None,
    conversation_id: str | None = None,
) -> PlanResult | None:
    """PydanticAI 版 try_plan；失败/无 key 返回 None（回退规则版）。

    PydanticAI 结构化输出在模型产出不合法时会自动重试 1 次（比旧 json_mode 宽松，
    json_mode 出 list/string 直接 None），重试仍失败才抛异常 → None。

    异常绝不静默：logger.exception 落真实原因（瞬时 API 错误/限流/超时/校验失败），
    否则前端只会看到 planner.py 的「AI 规划服务暂时不可用或未配置」兜底文案，
    把偶发 LLM 失败误读成配置缺失。max_tokens 取 8000（=deepseek-chat 文档化输出
    硬上限 8192，即「有效无上限」）：实测复杂问题（多商圈多指标）规划输出 2188~
    2547 tokens，8000 覆盖任意现实规划且不触发 API 超限拒绝；注意 DeepSeek 路径
    下该字段目前实际不生效（PydanticAI 发 max_completion_tokens，DeepSeek 忽略，
    实测 max_tokens=100 仍输出 720 tokens），此设置是防御性的——换执行该字段的
    provider 时不至于截断/拒绝。
    """
    if not llm_client.is_available():
        logger.warning("pa_try_plan skipped: LLM 未配置（is_available=False），question=%.80s", question)
        return None
    try:
        agent = Agent(
            _build_model(),
            output_type=_normalize_output,
            deps_type=PlanDeps,
            model_settings={"max_tokens": 8000, "temperature": 0.2},
        )
        messages = _history_messages(llm_planner._system_prompt(conversation_id), history)
        result = agent.run_sync(
            question,
            message_history=messages,
            deps=PlanDeps(
                question=question,
                history=history or [],
                conversation_id=conversation_id,
            ),
        )
        return result.output
    except Exception:
        logger.exception(
            "pa_try_plan 失败（LLM 规划调用异常，回退规则版）。question=%.80s conversation_id=%s",
            question,
            conversation_id,
        )
        return None
