"""PydanticAI 迁移：事件桥接（iter 节点 → SSE 事件 + usage 采集）。

- 每轮模型文本：CallToolsNode.model_response.parts 里的 TextPart → trace + THOUGHT_DELTA
  （每轮一块；纯工具轮无 TextPart 不发）。想逐字流式时升级 run_stream_events() 的
  PartDeltaEvent（pydantic_ai.messages，事件名已在 spike 确认，async with 用法）。
- 工具副作用已由 tool_adapter 包装函数在工具执行时同步发出（TOOL_START/TOOL_RESULT/
  GENERATED_LAYER），这里不重复。
- LLM usage：run 结束后遍历 all_messages() 里每个 ModelResponse.usage（RequestUsage:
  input_tokens→prompt_tokens, output_tokens→completion_tokens, details.prompt_cache_hit_tokens
  →cached_tokens）→ 逐条 archive.record_llm_usage（llm_usage.jsonl，供追溯/成本核算）。
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic_ai._agent_graph import CallToolsNode
from pydantic_ai.messages import TextPart

from app.agent.llm_executor import _split_paragraphs
from app.models.agent import TraceEvent
from app.models.events import EventType
from app.tools.context import ToolContext

logger = logging.getLogger(__name__)


def handle_call_tools_node(node: Any, deps: ToolContext, trace: list[TraceEvent]) -> None:
    """把一轮模型的文本 → trace + THOUGHT_DELTA（每段一块，近似旧 loop 的流式）。

    CallToolsNode 持有该轮模型响应；模型返回无工具调用的最终文本也落在
    CallToolsNode（loop 从 model_response 里取 TextPart 作 output），故最终结论
    既走 THOUGHT_DELTA 又成为 conclusion，与旧 loop「最终内容 → trace + final_answer」一致。
    """
    if not isinstance(node, CallToolsNode):
        return
    mresp = node.model_response
    texts = [p.content for p in mresp.parts if isinstance(p, TextPart)]
    if not texts:
        return
    full = "\n".join(texts)
    emitter = getattr(deps, "event_emitter", None)
    for paragraph in _split_paragraphs(full):
        trace.append(TraceEvent(type="text", content=paragraph))
        if emitter is not None:
            emitter.emit(EventType.THOUGHT_DELTA, {"text": paragraph})


def record_llm_usage(deps: ToolContext, run_result: Any) -> None:
    """run 结束后把各轮 ModelResponse.usage 落 RunArchive（llm_usage.jsonl）。

    每个 ModelResponse 对应一次 LLM 调用（一轮）。usage 归一化对齐旧 executor 的
    OpenAI 形状：prompt_tokens/completion_tokens/total_tokens/cached_tokens。
    """
    emitter = getattr(deps, "event_emitter", None)
    archive = getattr(emitter, "archive", None) if emitter is not None else None
    if archive is None:
        return
    try:
        for message in run_result.all_messages():
            usage = getattr(message, "usage", None)
            if usage is None:
                continue
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            details = getattr(usage, "details", None) or {}
            cached = int(details.get("prompt_cache_hit_tokens", 0) or 0)
            archive.record_llm_usage(
                usage={
                    "prompt_tokens": input_tokens,
                    "completion_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    "cached_tokens": cached,
                }
            )
    except Exception:
        logger.exception("record_llm_usage failed (pydai)")
