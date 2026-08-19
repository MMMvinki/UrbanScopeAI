"""报告生成工具：分节报告 / 导出 PDF / 页面快照（真实现，不编造）。

「报告生成」货架的 3 个工具：
- write_report：把分节内容组装成 Markdown 报告，落盘工作区 .workdir/reports/；
- export_pdf：把工作区里的文本/Markdown（或直接给的 content）渲染成 PDF
  （reportlab + 系统中文字体，中英文均可显示）；
- page_snapshot：抓取网页 HTML 保存到工作区，并提取纯文本预览。

内容一律来自调用方（Agent/用户），本模块只负责组织、渲染、落盘，绝不虚构数值。
"""

from __future__ import annotations

import re
import urllib.request
from pathlib import Path
from typing import Any

from app.models.tool import ToolResult
from app.workspace import WORKSPACE_ROOT

REPORTS_DIR = WORKSPACE_ROOT / ".workdir" / "reports"
MAX_PREVIEW = 500  # 报告预览截断


def _ensure_reports() -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTS_DIR


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(p)


def _safe_name(name: str, suffix: str) -> str:
    stem = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(name or "report").strip()).strip("_")
    if not stem:
        stem = "report"
    return f"{stem[:60]}{suffix}"


def _clip(text: str, limit: int = MAX_PREVIEW) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def run_write_report(params: dict) -> ToolResult:
    title = str(params.get("title") or "未命名报告")
    filename = str(params.get("filename") or "")
    sections = params.get("sections") or []

    lines = [f"# {title}", ""]
    count = 0
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or "章节")
        content = str(section.get("content") or "")
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(content)
        lines.append("")
        count += 1

    md = "\n".join(lines).strip() + "\n"
    if not filename:
        filename = _safe_name(title, ".md")
    else:
        filename = filename if filename.lower().endswith(".md") else f"{filename}.md"
    try:
        target = _ensure_reports() / filename
        target.write_text(md, encoding="utf-8")
    except OSError as exc:
        return ToolResult(success=False, message=f"写入报告失败：{exc}")

    return ToolResult(
        success=True,
        output={
            "path": _rel(target),
            "title": title,
            "sections": count,
            "bytes": len(md.encode("utf-8")),
            "preview": _clip(md),
        },
        message=f"报告「{title}」已生成（{count} 个章节，{len(md.encode('utf-8'))} 字节）。",
    )


def _find_cn_font() -> Path | None:
    """找系统中文字体（黑体/仿宋/等线），用于中文 PDF 渲染。"""
    candidates = ["simhei.ttf", "simfang.ttf", "simsun.ttc", "Deng.ttf", "msyh.ttc"]
    for name in candidates:
        p = Path("C:/Windows/Fonts") / name
        if p.exists():
            return p
    return None


def run_export_pdf(params: dict) -> ToolResult:
    """把工作区文本/Markdown 文件或直接给的 content 渲染成 PDF。"""
    path = str(params.get("path") or "").strip()
    content = params.get("content")
    filename = str(params.get("filename") or "").strip()

    text: str | None = None
    if content is not None:
        text = str(content)
    elif path:
        source = _resolve_report_path(path)
        if source is None or not source.is_file():
            return ToolResult(success=False, message=f"报告文件不存在：{path}")
        try:
            text = source.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return ToolResult(success=False, message=f"读取失败：{exc}")
    else:
        return ToolResult(success=False, message="需要 path（已有报告文件）或 content（直接文本）之一")

    if not filename:
        stem = Path(path or "report").stem
        filename = _safe_name(stem, ".pdf")
    else:
        filename = filename if filename.lower().endswith(".pdf") else f"{filename}.pdf"

    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    font_path = _find_cn_font()
    try:
        target = _ensure_reports() / filename
        c = canvas.Canvas(str(target), pagesize=A4)
        width, height = A4
        if font_path is not None:
            pdfmetrics.registerFont(TTFont("CJK", str(font_path)))
            c.setFont("CJK", 11)
        else:
            c.setFont("Helvetica", 11)
        # 无字体回退时中文会显示为方块，仍生成（文件不损坏）
        margin, line_h = 50, 17
        y = height - margin
        for raw in text.splitlines():
            # 简单折行：每行约 45 个字符宽（中文/英文混排粗略处理）
            for chunk in [raw[i : i + 42] for i in range(0, len(raw), 42)]:
                if y < margin:
                    c.showPage()
                    c.setFont("CJK" if font_path is not None else "Helvetica", 11)
                    y = height - margin
                c.drawString(margin, y, chunk)
                y -= line_h
            y -= 4  # 段落间距
        c.save()
    except OSError as exc:
        return ToolResult(success=False, message=f"生成 PDF 失败：{exc}")

    return ToolResult(
        success=True,
        output={
            "path": _rel(target),
            "bytes": target.stat().st_size,
            "pages": None,
        },
        message=f"PDF 已导出：{_rel(target)}（{target.stat().st_size} 字节）。",
    )


def run_page_snapshot(params: dict) -> ToolResult:
    url = str(params.get("url") or "").strip()
    filename = str(params.get("filename") or "").strip()
    if not url:
        return ToolResult(success=False, message="需要 url（要抓取的网页地址）")
    if not filename:
        import time

        filename = f"snapshot_{int(time.time())}.html"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "UrbanScope/1.0"})
        data = urllib.request.urlopen(req, timeout=30).read()
    except Exception as exc:
        return ToolResult(success=False, message=f"抓取失败：{exc}")
    html = data.decode("utf-8", errors="replace")
    target = _ensure_reports() / filename
    try:
        target.write_text(html, encoding="utf-8")
    except OSError as exc:
        return ToolResult(success=False, message=f"保存失败：{exc}")

    # 纯文本预览：去掉标签
    text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return ToolResult(
        success=True,
        output={
            "url": url,
            "path": _rel(target),
            "bytes": len(data),
            "text_preview": _clip(text),
        },
        message=f"网页快照已保存：{_rel(target)}（{len(data)} 字节）。",
    )


def _resolve_report_path(path: str) -> Path | None:
    """解析报告文件路径：支持相对工作区的完整路径（.workdir/reports/x.md）
    与相对 reports 目录的短路径（x.md）两种写法。"""
    raw = Path(str(path or "")).expanduser()
    candidates = []
    if raw.is_absolute():
        candidates.append(raw)
    else:
        candidates.append(WORKSPACE_ROOT / raw)  # 完整相对路径
        candidates.append(REPORTS_DIR / raw)  # 相对 reports 目录
    for p in candidates:
        try:
            resolved = p.resolve()
        except OSError:
            continue
        if resolved != REPORTS_DIR and REPORTS_DIR not in resolved.parents:
            continue
        if resolved.exists():
            return resolved
    return None


_HANDLERS: dict[str, Any] = {
    "write_report": run_write_report,
    "export_pdf": run_export_pdf,
    "page_snapshot": run_page_snapshot,
}


def run_report_tool(tool: str, params: dict) -> ToolResult:
    handler = _HANDLERS.get(tool)
    if handler is None:
        return ToolResult(success=False, message=f"未实现的报告工具：{tool}")
    try:
        return handler(params or {})
    except Exception as exc:
        return ToolResult(success=False, message=f"报告操作失败：{exc}")
