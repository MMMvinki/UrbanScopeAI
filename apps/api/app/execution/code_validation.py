"""execute_code 载荷校验（L0 前校验，拒绝非纯代码内容）。

去掉 resident worker 相关检查（本产品无 worker 模型），保留核心三条：
think 标签 / markdown 代码围栏 / 长 sleep + 链式思维注释（移植来源见 NOTICE.md）。
"""

from __future__ import annotations

import re

_THINK_TAG_RE = re.compile(r"</?\s*think\s*>", re.IGNORECASE)
_MARKDOWN_FENCE_RE = re.compile(r"```")
_TOOL_CONFUSION_RE = re.compile(
    r"execute_code\s*中.*?(不能|无法|不行)|"
    r"(不能|无法|不行).*?execute_code\s*中|"
    r"standalone tool calls.*?cannot access|"
    r"无法在\s*execute_code",
    re.IGNORECASE,
)
_SLEEP_CALL_RE = re.compile(r"(?:time|asyncio)\.sleep\(\s*([0-9]+(?:\.[0-9]+)?)")
_REASONING_COMMENT_MARKERS = (
    "我们需要",
    "我们将",
    "我们可以",
    "让我们",
    "相反",
    "因此",
    "由于",
    "但这样",
    "实际上",
    "考虑到",
    "改变策略",
    "更好的方法",
    "需要先",
    "但不行",
)


def validate_execute_code_payload(code: str) -> str | None:
    """载荷不是纯可执行 Python 时返回错误信息，否则 None。"""
    if _THINK_TAG_RE.search(code):
        return (
            "execute_code.code 含 <think> 标签。只发送可执行 Python 代码；"
            "不要把隐藏推理、策略叙述或 XML 式思考标签放进 code。"
        )
    if _MARKDOWN_FENCE_RE.search(code):
        return (
            "execute_code.code 含 Markdown 代码围栏。只发送原始 Python 源码，"
            "不要带 ``` 围栏或周围叙述。"
        )

    sleep_seconds: list[float] = []
    for match in _SLEEP_CALL_RE.finditer(code):
        try:
            sleep_seconds.append(float(match.group(1)))
        except ValueError:
            continue
    if sleep_seconds and max(sleep_seconds) >= 15:
        meaningful_lines = [
            line.strip()
            for line in code.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        wait_only = all(
            line.startswith(("import ", "from ", "print(", "time.sleep(", "asyncio.sleep(", "sleep("))
            for line in meaningful_lines
        )
        if wait_only:
            return (
                "execute_code.code 疑似用长 sleep 阻塞 agent 循环。"
                "不要用 time.sleep 长时间阻塞；做完计算直接返回结果即可。"
            )

    suspicious_total = 0
    suspicious_run = 0
    max_suspicious_run = 0
    comment_total = 0
    scanned = 0
    for raw_line in code.splitlines()[:100]:
        stripped = raw_line.strip()
        if not stripped:
            continue
        scanned += 1
        if not stripped.startswith("#"):
            suspicious_run = 0
            continue
        comment_total += 1
        text = stripped.lstrip("#").strip()
        if _TOOL_CONFUSION_RE.search(text):
            return (
                "execute_code.code 的注释里含工具规划评论。需要别的工具就直接"
                "在下一步调用它；code 参数必须只含可执行 Python。"
            )
        if any(marker in text for marker in _REASONING_COMMENT_MARKERS):
            suspicious_total += 1
            suspicious_run += 1
            max_suspicious_run = max(max_suspicious_run, suspicious_run)
        else:
            suspicious_run = 0

    if scanned >= 12 and comment_total >= 8 and (max_suspicious_run >= 5 or suspicious_total >= 8):
        return (
            "execute_code.code 疑似注释里含链式思维/策略独白。注释保持简短事实性，"
            "只发送执行动作所需 Python。"
        )
    return None


__all__ = ["validate_execute_code_payload"]
