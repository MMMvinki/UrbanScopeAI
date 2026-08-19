"""Web 工具：webfetch（抓取网页）与 websearch（DuckDuckGo 搜索）。

裁剪自开源实现，纯 stdlib，无第三方依赖（移植来源见 NOTICE.md）。
handler 签名统一为 ``(params: dict) -> ToolResult``，并入声明式注册表。
"""

from __future__ import annotations

import html
import logging
import re
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any

from app.models.tool import ToolResult

logger = logging.getLogger(__name__)

_MAX_FETCH_BYTES = 5 * 1024 * 1024
_MAX_SEARCH_BYTES = 512 * 1024
_DEFAULT_TIMEOUT = 30
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 UrbanScopeAI/0.1"
)


class _MarkdownHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.href_stack: list[str | None] = []
        self.list_depth = 0
        self.in_pre = False
        self._pending_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag in {"script", "style", "noscript", "iframe", "svg"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in {"h1", "h2", "h3"}:
            self.parts.append("\n" + "#" * int(tag[1]) + " ")
        elif tag in {"p", "div", "section", "article", "br"}:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n" + "  " * self.list_depth + "- ")
        elif tag in {"ul", "ol"}:
            self.list_depth += 1
            self.parts.append("\n")
        elif tag == "pre":
            self.in_pre = True
            self.parts.append("\n```\n")
        elif tag == "a":
            self.href_stack.append(attrs_dict.get("href"))
            self._pending_link_text.append("")

    def handle_endtag(self, tag: str) -> None:
        if self.skip_depth:
            if tag in {"script", "style", "noscript", "iframe", "svg"}:
                self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag in {"h1", "h2", "h3", "p", "div", "section", "article"}:
            self.parts.append("\n")
        elif tag in {"ul", "ol"}:
            self.list_depth = max(0, self.list_depth - 1)
            self.parts.append("\n")
        elif tag == "pre":
            self.in_pre = False
            self.parts.append("\n```\n")
        elif tag == "a" and self.href_stack:
            href = self.href_stack.pop()
            text = self._pending_link_text.pop() if self._pending_link_text else ""
            if href and text.strip():
                self.parts.append(f" ({href})")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = data if self.in_pre else re.sub(r"\s+", " ", data)
        if not text.strip() and not self.in_pre:
            return
        if self._pending_link_text:
            self._pending_link_text[-1] += text
        self.parts.append(text)

    def markdown(self) -> str:
        text = "".join(self.parts)
        text = html.unescape(text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _html_to_markdown(value: str) -> str:
    parser = _MarkdownHTMLParser()
    parser.feed(value)
    parser.close()
    return parser.markdown()


def _bounded_get(url: str, *, timeout: int, max_bytes: int) -> tuple[bytes, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/markdown,text/plain,text/html,application/json;q=0.9,*/*;q=0.1",
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        content_type = resp.headers.get("content-type", "")
        body = resp.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"response too large; limit is {max_bytes} bytes")
    return body, content_type


def _assert_http_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("URL 必须是 http:// 或 https://")


def run_webfetch(params: dict) -> ToolResult:
    """抓取一个 HTTP/HTTPS URL，返回 markdown/text/html。"""
    url = str(params.get("url", "")).strip()
    fmt = str(params.get("format", "markdown"))
    timeout = int(params.get("timeout", _DEFAULT_TIMEOUT))
    if not url:
        return ToolResult(success=False, message="缺少必填参数 url")
    try:
        _assert_http_url(url)
        fmt = fmt if fmt in {"markdown", "text", "html"} else "markdown"
        timeout = max(1, min(int(timeout or _DEFAULT_TIMEOUT), 120))
        body, content_type = _bounded_get(url, timeout=timeout, max_bytes=_MAX_FETCH_BYTES)
        mime = content_type.split(";", 1)[0].strip().lower()
        if mime and not (
            mime.startswith("text/")
            or mime in {"application/json", "application/xml", "application/xhtml+xml"}
            or mime.endswith("+json")
            or mime.endswith("+xml")
        ):
            return ToolResult(success=False, message=f"不支持的内容类型: {content_type}")
        raw = body.decode("utf-8", errors="replace")
        is_html = "html" in content_type
        output = raw if fmt == "html" else (_html_to_markdown(raw) if is_html else raw)
        return ToolResult(
            success=True,
            output={"url": url, "content_type": content_type, "format": fmt, "output": output[:8000]},
            message=f"已抓取 {url}（{fmt}）。",
        )
    except Exception as exc:
        logger.warning("[webfetch] failed for %s: %s", url, exc)
        return ToolResult(success=False, message=f"抓取失败: {exc}")


def _parse_duckduckgo(html_text: str, limit: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    pattern = re.compile(
        r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
        r'<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    for match in pattern.finditer(html_text):
        href = html.unescape(match.group("href"))
        parsed_href = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed_href.query)
        if "uddg" in qs:
            href = qs["uddg"][0]
        title = re.sub(r"<[^>]+>", "", match.group("title"))
        snippet = re.sub(r"<[^>]+>", "", match.group("snippet"))
        results.append({
            "title": html.unescape(title).strip(),
            "url": href,
            "snippet": html.unescape(snippet).strip(),
        })
        if len(results) >= limit:
            break
    return results


def run_websearch(params: dict) -> ToolResult:
    """DuckDuckGo 搜索，返回结果列表与 markdown 摘要。"""
    query = str(params.get("query", "")).strip()
    num_results = int(params.get("num_results", 8))
    timeout = int(params.get("timeout", 25))
    if not query:
        return ToolResult(success=False, message="缺少必填参数 query")
    try:
        limit = max(1, min(int(num_results or 8), 20))
        timeout = max(1, min(int(timeout or 25), 60))
        url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
        body, _ = _bounded_get(url, timeout=timeout, max_bytes=_MAX_SEARCH_BYTES)
        html_text = body.decode("utf-8", errors="replace")
        results = _parse_duckduckgo(html_text, limit)
        output = "\n".join(
            f"{idx}. [{item['title']}]({item['url']})\n   {item['snippet']}"
            for idx, item in enumerate(results, 1)
        )
        if not results:
            output = "没有找到搜索结果，请尝试其他关键词。"
        return ToolResult(
            success=True,
            output={"query": query, "provider": "duckduckgo_html", "results": results, "output": output},
            message=f"搜索 '{query}' 返回 {len(results)} 条结果。",
        )
    except Exception as exc:
        logger.warning("[websearch] failed for %s: %s", query, exc)
        return ToolResult(success=False, message=f"搜索失败: {exc}")
