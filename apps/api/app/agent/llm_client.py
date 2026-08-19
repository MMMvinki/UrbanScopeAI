"""LLM 客户端 —— 基于 LiteLLM 的多 Provider 客户端。

公共 API 与旧实现（urllib 直连 DeepSeek）完全一致，planner/executor 零改动：
- is_available()
- chat_completion(messages, *, tools, json_mode, temperature, max_tokens) -> OpenAI 形状 dict
- chat_json(system, user, ...) / chat_json_messages(messages, ...) -> 解析后的 dict
- chat_completion_stream(messages, *, tools, on_delta, on_tool_delta, ...) -> OpenAI 形状 dict（流式）

配置（优先级：环境变量 > apps/api/.env > 默认值）：
- LLM_PROVIDER  / LLM_MODEL / LLM_API_KEY / LLM_BASE_URL
- 兼容旧名：DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
模型名自动补 provider 前缀：MODEL=deepseek-chat → "deepseek/deepseek-chat"（litellm 按前缀选协议）。
返回形状保持 OpenAI：response["choices"][0]["message"]{content, tool_calls}。

chunk 归一化 / usage / 流式装配裁剪为直接返回 dict 的薄客户端（移植来源见 NOTICE.md）。
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

import litellm

logger = logging.getLogger(__name__)


def _system_proxy_alive() -> bool:
    """探测 Windows 系统代理是否真的在监听。

    系统代理可能指向一台已停止的本地代理（如 127.0.0.1:7078），会让 httpx/litellm
    的所有外网请求被拒（WinError 10061），导致 LLM / websearch 全部静默失败回退规则版。
    返回 True 表示代理存活（应继续走代理，OSM 等外部服务依赖它）。
    """
    try:
        import socket
        import urllib.request

        for scheme in ("https", "http"):
            proxy = (urllib.request.getproxies() or {}).get(scheme)
            if not proxy:
                continue
            host, _, port = proxy.replace("http://", "").replace("https://", "").partition(":")
            if not host:
                continue
            try:
                int_port = int(port) if port else (443 if scheme == "https" else 80)
            except ValueError:
                continue
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.4)
            try:
                if sock.connect_ex((host, int_port)) == 0:
                    return True
            finally:
                sock.close()
        return False
    except Exception:
        return False


# 仅在「用户未显式配置环境代理 && 系统代理已死」时绕过系统代理（DeepSeek 等可直连）；
# 若代理存活则保留，OSM 地理编码 / Overpass 等服务需要走它。进程级生效。
if (
    not any(os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"))
    and not _system_proxy_alive()
):
    os.environ.setdefault("NO_PROXY", "*")

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"  # apps/api/.env

# 默认超时（秒）：防止 provider 慢/无响应时无限挂起
DEFAULT_TIMEOUT_SEC: float = 300.0


def _load_env_file() -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


_FILE_ENV = _load_env_file()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name) or _FILE_ENV.get(name) or default


LLM_PROVIDER = _env("LLM_PROVIDER", "deepseek")
LLM_MODEL = _env("LLM_MODEL", _env("DEEPSEEK_MODEL", "deepseek-chat"))
LLM_API_KEY = _env("LLM_API_KEY", _env("DEEPSEEK_API_KEY", ""))
LLM_BASE_URL = _env("LLM_BASE_URL", _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com")).rstrip("/")


def _model() -> str:
    """litellm 模型名：自动补 provider 前缀（如 deepseek-chat → deepseek/deepseek-chat）。"""
    return LLM_MODEL if "/" in LLM_MODEL else f"{LLM_PROVIDER}/{LLM_MODEL}"


# 兼容别名：/agent/status 等仍读 MODEL / BASE_URL / API_KEY
MODEL = _model()
BASE_URL = LLM_BASE_URL
API_KEY = LLM_API_KEY


def is_available() -> bool:
    """是否已配置 key（未配置时整个 LLM 路径自动回退规则版）。"""
    return bool(API_KEY)


# ─────────────────────────────────────────────────────────────────────
# 流式 chunk 归一化
# ─────────────────────────────────────────────────────────────────────


def _extract_content_delta(chunk: Any) -> str:
    """从 litellm chunk 里尽力提取文本增量。"""
    try:
        choices = getattr(chunk, "choices", None) or chunk.get("choices")  # type: ignore[union-attr]
        if not choices:
            return ""
        delta = getattr(choices[0], "delta", None) or choices[0].get("delta")  # type: ignore[union-attr]
        if delta is None:
            return ""
        content = getattr(delta, "content", None)
        if content is None and isinstance(delta, dict):
            content = delta.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):  # Anthropic 风格：content 是 block 列表
            out: list[str] = []
            for block in content:
                t = getattr(block, "text", None) if not isinstance(block, dict) else block.get("text")
                if isinstance(t, str):
                    out.append(t)
            return "".join(out)
    except Exception:
        logger.debug("_extract_content_delta: chunk shape not recognised", exc_info=True)
    return ""


def _extract_tool_calls(message: Any) -> list[dict] | None:
    """从 litellm message 提取 OpenAI 形状 tool_calls（无则 None）。"""
    raw = getattr(message, "tool_calls", None)
    if not raw:
        return None
    result: list[dict] = []
    for tc in raw:
        func = getattr(tc, "function", None)
        if func is None:
            continue
        result.append(
            {
                "id": getattr(tc, "id", "") or "",
                "type": "function",
                "function": {
                    "name": getattr(func, "name", "") or "",
                    "arguments": getattr(func, "arguments", "{}") or "{}",
                },
            }
        )
    return result if result else None


def _extract_partial_json_string_field(raw: str, field: str) -> str | None:
    """从部分 JSON 里尽力提取字符串字段（execute_code 流式 code 用）。"""
    if not raw:
        return None
    marker = json.dumps(field)
    idx = raw.find(marker)
    if idx < 0:
        return None
    colon = raw.find(":", idx + len(marker))
    if colon < 0:
        return None
    pos = colon + 1
    while pos < len(raw) and raw[pos].isspace():
        pos += 1
    if pos >= len(raw) or raw[pos] != '"':
        return None
    pos += 1
    escaped: list[str] = []
    i = pos
    while i < len(raw):
        ch = raw[i]
        if ch == "\\":
            if i + 1 >= len(raw):
                break
            escaped.append(ch)
            escaped.append(raw[i + 1])
            i += 2
            continue
        if ch == '"':
            break
        escaped.append(ch)
        i += 1
    try:
        return json.loads('"' + "".join(escaped) + '"')
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# usage 归一化（含 DeepSeek 缓存口径）
# ─────────────────────────────────────────────────────────────────────


def _usage_field(obj: Any, name: str) -> Any:
    value = getattr(obj, name, None)
    if value is None and isinstance(obj, dict):
        value = obj.get(name)
    return value


def _to_plain_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _to_plain_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        try:
            return _to_plain_jsonable(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return _to_plain_jsonable(value.dict())
        except Exception:
            pass
    try:
        return {
            key: _to_plain_jsonable(getattr(value, key))
            for key in dir(value)
            if not key.startswith("_") and not callable(getattr(value, key, None))
        }
    except Exception:
        return str(value)


def _extract_usage(response: Any) -> dict[str, Any]:
    """从响应/块提取 usage，归一化 cached_tokens（DeepSeek 缓存口径）。"""
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return {}

    plain = _to_plain_jsonable(usage)
    out: dict[str, Any] = dict(plain) if isinstance(plain, dict) else {}
    prompt = int(_usage_field(usage, "prompt_tokens") or _usage_field(usage, "input_tokens") or 0)
    completion = int(_usage_field(usage, "completion_tokens") or _usage_field(usage, "output_tokens") or 0)
    total = int(_usage_field(usage, "total_tokens") or 0)

    cached = 0
    details = _usage_field(usage, "prompt_tokens_details")
    if details is not None:
        cached = int(_usage_field(details, "cached_tokens") or 0)
    input_details = _usage_field(usage, "input_tokens_details") or _usage_field(usage, "input_token_details")
    if input_details is not None:
        cached = max(cached, int(_usage_field(input_details, "cached_tokens") or _usage_field(input_details, "cache_read") or 0))
    cache_read = int(_usage_field(usage, "cache_read_input_tokens") or 0)
    cache_creation = int(_usage_field(usage, "cache_creation_input_tokens") or 0)
    if cache_read:
        cached = max(cached, cache_read)
    deepseek_hit = int(_usage_field(usage, "prompt_cache_hit_tokens") or 0)
    if deepseek_hit:
        cached = max(cached, deepseek_hit)

    out["prompt_tokens"] = prompt
    out["completion_tokens"] = completion
    out["total_tokens"] = total
    out["cached_tokens"] = cached
    out["cache_read_input_tokens"] = cache_read
    out["cache_creation_input_tokens"] = cache_creation
    out["non_cached_input_tokens"] = max(0, prompt - cached)
    reports_cache = (
        cached > 0
        or cache_read > 0
        or cache_creation > 0
        or _usage_field(usage, "prompt_tokens_details") is not None
        or deepseek_hit > 0
    )
    if not reports_cache:
        out["cache_status"] = "provider_not_reported"
        out["cache_hit_ratio"] = None
    else:
        out["cache_status"] = "reported"
        out["cache_hit_ratio"] = round(cached / prompt, 4) if prompt > 0 else 0.0
    return out


# ─────────────────────────────────────────────────────────────────────
# OpenAI 形状归一化
# ─────────────────────────────────────────────────────────────────────


def _to_openai_dict(response: Any) -> dict:
    """把 litellm 响应归一化为 OpenAI 形状 dict（executor 现有访问方式不变）。"""
    choice = response.choices[0]
    msg = choice.message
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": getattr(msg, "content", None) or None,
                    "tool_calls": _extract_tool_calls(msg),
                },
                "finish_reason": str(getattr(choice, "finish_reason", "stop") or "stop"),
            }
        ],
        "usage": _extract_usage(response),
        "model": getattr(response, "model", "") or _model(),
    }


def _build_kwargs(
    messages: list[dict],
    *,
    tools: list[dict] | None,
    json_mode: bool,
    temperature: float,
    max_tokens: int,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": _model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": DEFAULT_TIMEOUT_SEC,
        "api_key": API_KEY,
    }
    if LLM_BASE_URL:
        kwargs["api_base"] = LLM_BASE_URL
    if tools:
        kwargs["tools"] = tools
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    return kwargs


# ─────────────────────────────────────────────────────────────────────
# 公共 API
# ─────────────────────────────────────────────────────────────────────


def chat_completion(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    json_mode: bool = False,
    temperature: float = 0.2,
    max_tokens: int = 3000,
) -> dict:
    """底层 chat completion（非流式），返回 OpenAI 形状 dict。"""
    if not API_KEY:
        raise RuntimeError("LLM_API_KEY / DEEPSEEK_API_KEY 未配置")
    kwargs = _build_kwargs(messages, tools=tools, json_mode=json_mode, temperature=temperature, max_tokens=max_tokens)
    try:
        response = litellm.completion(**kwargs)
    except Exception as exc:
        raise RuntimeError(f"LLM 调用失败：{type(exc).__name__}: {exc}") from exc
    return _to_openai_dict(response)


def chat_completion_stream(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    json_mode: bool = False,
    temperature: float = 0.2,
    max_tokens: int = 3000,
    on_delta: Callable[[str], None] | None = None,
    on_tool_delta: Callable[[int, str, dict[str, Any]], None] | None = None,
) -> dict:
    """流式 chat completion，返回与 chat_completion 同形状的 dict。

    - on_delta(text)：每段内容增量回调（前端逐字渲染）。
    - on_tool_delta(index, name, payload)：工具调用参数增量（execute_code 时 payload 带部分 code）。
    首次调用"流式失败 → 非流式兜底"（常见的流式鲁棒性设计）。
    """
    if not API_KEY:
        raise RuntimeError("LLM_API_KEY / DEEPSEEK_API_KEY 未配置")
    kwargs = _build_kwargs(messages, tools=tools, json_mode=json_mode, temperature=temperature, max_tokens=max_tokens)

    try:
        stream = litellm.completion(**{**kwargs, "stream": True, "stream_options": {"include_usage": True}})
    except Exception:
        logger.warning("[LLM] streaming completion failed, retrying without usage metadata", exc_info=True)
        try:
            stream = litellm.completion(**{**kwargs, "stream": True})
        except Exception:
            logger.warning("[LLM] streaming completion failed again, falling back to non-streaming", exc_info=True)
            try:
                response = litellm.completion(**kwargs)
            except Exception as exc:
                raise RuntimeError(f"LLM 调用失败：{type(exc).__name__}: {exc}") from exc
            out = _to_openai_dict(response)
            content = out["choices"][0]["message"].get("content")
            if content and on_delta is not None:
                try:
                    on_delta(content)
                except Exception:
                    logger.exception("on_delta callback raised on fallback path")
            return out

    parts: list[str] = []
    streaming_tool_calls: dict[int, dict] = {}
    finish_reason = "stop"
    stream_usage: dict[str, Any] = {}

    for chunk in stream:
        chunk_usage = _extract_usage(chunk)
        if chunk_usage.get("prompt_tokens") or chunk_usage.get("total_tokens"):
            stream_usage = chunk_usage
        choices = getattr(chunk, "choices", None)
        if not choices:
            continue
        choice = choices[0]
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue

        piece = _extract_content_delta(chunk)
        if piece:
            parts.append(piece)
            if on_delta is not None:
                try:
                    on_delta(piece)
                except Exception:
                    logger.exception("on_delta callback raised — continuing")

        tc_delta = getattr(delta, "tool_calls", None)
        if tc_delta:
            for tc in tc_delta:
                idx = getattr(tc, "index", 0) or 0
                if idx not in streaming_tool_calls:
                    streaming_tool_calls[idx] = {
                        "id": "",
                        "type": "function",
                        "function": {"name": "", "arguments": ""},
                    }
                entry = streaming_tool_calls[idx]
                tc_id = getattr(tc, "id", None)
                if tc_id:
                    entry["id"] = tc_id
                func = getattr(tc, "function", None)
                if func:
                    name = getattr(func, "name", None)
                    if name:
                        entry["function"]["name"] = name
                    args = getattr(func, "arguments", None)
                    if args:
                        entry["function"]["arguments"] += args
                    if on_tool_delta is not None:
                        payload: dict[str, Any] = {"arguments": entry["function"]["arguments"]}
                        tool_name = entry["function"]["name"]
                        if tool_name == "execute_code":
                            code = _extract_partial_json_string_field(entry["function"]["arguments"], "code")
                            if code is not None:
                                payload["code"] = code
                        try:
                            on_tool_delta(idx, tool_name, payload)
                        except Exception:
                            logger.exception("on_tool_delta callback raised — continuing")

        fr = getattr(choice, "finish_reason", None)
        if fr:
            finish_reason = str(fr)

    content = "".join(parts) or None
    tool_calls = list(streaming_tool_calls.values()) or None
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content, "tool_calls": tool_calls},
                "finish_reason": finish_reason,
            }
        ],
        "usage": stream_usage,
        "model": _model(),
    }


def chat_json(system: str, user: str, *, temperature: float = 0.2, max_tokens: int = 3000) -> dict:
    """JSON 模式单轮对话，返回解析后的 dict。"""
    return chat_json_messages(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )


def chat_json_messages(
    messages: list[dict],
    *,
    temperature: float = 0.2,
    max_tokens: int = 3000,
) -> dict:
    """JSON 模式多轮对话（history 由调用方注入 messages），返回解析后的 dict。

    messages 必须以 system 开头（供 Planner 注入动态 skill/tool 清单）。
    """
    response = chat_completion(
        messages,
        json_mode=True,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    content = response["choices"][0]["message"]["content"]
    return json.loads(content)
