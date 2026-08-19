"""文件与系统工具：工作区沙箱内的文件操作全套 + 受控终端 + 跑脚本 + 下载。

「文件与系统」货架的 14 个真实现。所有路径 resolve 后必须位于 WORKSPACE_ROOT 内
（L2 工作区边界，与 execute_code 同一套安全口径），越界直接拒绝。

确定性操作：全部直接读写磁盘，绝无模拟/编造。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from app.models.tool import ToolResult
from app.workspace import WORKSPACE_ROOT

MAX_READ_BYTES = 200 * 1024  # 读文件截断上限（避免把超大产物灌进对话）
MAX_WRITE_BYTES = 5 * 1024 * 1024  # 单文件写入上限
MAX_SEARCH_RESULTS = 50  # 搜索结果条数上限
TERMINAL_TIMEOUT = 20  # 终端命令超时（秒）
SCRIPT_TIMEOUT = 120  # 跑脚本超时（秒）
MAX_TERMINAL_OUTPUT = 20_000  # 终端/脚本输出截断字节


def _resolve(path: str | None) -> Path | None:
    """把相对/绝对路径解析为工作区内绝对路径；越界返回 None。"""
    try:
        raw = Path(str(path or "")).expanduser()
    except Exception:
        return None
    p = raw if raw.is_absolute() else WORKSPACE_ROOT / raw
    try:
        resolved = p.resolve()
    except OSError:
        return None
    if resolved != WORKSPACE_ROOT and WORKSPACE_ROOT not in resolved.parents:
        return None
    return resolved


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(WORKSPACE_ROOT))
    except ValueError:
        return str(p)


def _clip(text: str, limit: int = MAX_TERMINAL_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（输出过长，已截断，共 {len(text)} 字符）"


def _bad_path(p: str | None) -> ToolResult:
    return ToolResult(
        success=False,
        message=f"路径越界或无效：{p or '（空）'}。文件工具只能操作工作区 workspace/ 内的文件。",
    )


def _list_files(params: dict) -> ToolResult:
    path = str(params.get("path") or ".")
    target = _resolve(path)
    if target is None:
        return _bad_path(path)
    if target.is_file():
        p = target
        return ToolResult(
            success=True,
            output={
                "path": _rel(p),
                "type": "file",
                "size": p.stat().st_size,
                "name": p.name,
            },
            message=f"「{_rel(p)}」是文件（{p.stat().st_size} 字节）。",
        )
    if not target.is_dir():
        return ToolResult(success=False, message=f"目录不存在：{path}")
    entries: list[dict[str, Any]] = []
    for child in sorted(target.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower())):
        try:
            st = child.stat()
            entries.append(
                {
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": st.st_size if child.is_file() else None,
                    "path": _rel(child),
                }
            )
        except OSError:
            continue
    return ToolResult(
        success=True,
        output={"path": _rel(target), "entries": entries, "count": len(entries)},
        message=f"目录「{_rel(target)}」共 {len(entries)} 项。",
    )


def _read_file(params: dict) -> ToolResult:
    path = str(params.get("path") or "")
    target = _resolve(path)
    if target is None:
        return _bad_path(path)
    if not target.is_file():
        return ToolResult(success=False, message=f"文件不存在：{path}")
    try:
        raw = target.read_bytes()
        truncated = len(raw) > MAX_READ_BYTES
        text = raw[:MAX_READ_BYTES].decode("utf-8", errors="replace")
    except OSError as exc:
        return ToolResult(success=False, message=f"读取失败：{exc}")
    return ToolResult(
        success=True,
        output={
            "path": _rel(target),
            "content": text,
            "bytes": len(raw),
            "truncated": truncated,
        },
        message=f"已读取「{_rel(target)}」（{len(raw)} 字节{'，超长已截断' if truncated else ''}）。",
    )


def _write_file(params: dict) -> ToolResult:
    path = str(params.get("path") or "")
    target = _resolve(path)
    if target is None:
        return _bad_path(path)
    content = params.get("content") or ""
    if not isinstance(content, str):
        return ToolResult(success=False, message="content 需要是字符串")
    if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
        return ToolResult(success=False, message=f"内容超过 {MAX_WRITE_BYTES // 1024} KB 写入上限")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        return ToolResult(success=False, message=f"写入失败：{exc}")
    return ToolResult(
        success=True,
        output={"path": _rel(target), "bytes": len(content.encode("utf-8"))},
        message=f"已写入「{_rel(target)}」（{len(content.encode('utf-8'))} 字节）。",
    )


def _append_file(params: dict) -> ToolResult:
    path = str(params.get("path") or "")
    target = _resolve(path)
    if target is None:
        return _bad_path(path)
    content = str(params.get("content") or "")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(content)
    except OSError as exc:
        return ToolResult(success=False, message=f"追加失败：{exc}")
    return ToolResult(
        success=True,
        output={"path": _rel(target)},
        message=f"已向「{_rel(target)}」追加 {len(content)} 字符。",
    )


def _edit_file(params: dict) -> ToolResult:
    """行级编辑：按行替换（old → new），可重复 n 次或全部（replace_all）。"""
    path = str(params.get("path") or "")
    target = _resolve(path)
    if target is None:
        return _bad_path(path)
    if not target.is_file():
        return ToolResult(success=False, message=f"文件不存在：{path}")
    old = str(params.get("old_text") or "")
    new = str(params.get("new_text") or "")
    if not old:
        return ToolResult(success=False, message="需要 old_text（要替换的文本）")
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        return ToolResult(success=False, message=f"读取失败：{exc}")
    count = text.count(old)
    if count == 0:
        return ToolResult(success=False, message=f"「{_rel(target)}」里没找到要替换的文本。")
    replace_all = bool(params.get("replace_all", False))
    if replace_all:
        new_text = text.replace(old, new)
    else:
        new_text = text.replace(old, new, 1)
    try:
        target.write_text(new_text, encoding="utf-8")
    except OSError as exc:
        return ToolResult(success=False, message=f"写入失败：{exc}")
    replaced = count if replace_all else 1
    return ToolResult(
        success=True,
        output={"path": _rel(target), "replaced": replaced, "total_matches": count},
        message=f"已替换「{_rel(target)}」中 {replaced} 处（共匹配 {count} 处）。",
    )


def _create_folder(params: dict) -> ToolResult:
    path = str(params.get("path") or "")
    target = _resolve(path)
    if target is None:
        return _bad_path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return ToolResult(success=False, message=f"创建目录失败：{exc}")
    return ToolResult(
        success=True,
        output={"path": _rel(target)},
        message=f"目录「{_rel(target)}」已就绪。",
    )


def _delete_file(params: dict) -> ToolResult:
    path = str(params.get("path") or "")
    target = _resolve(path)
    if target is None:
        return _bad_path(path)
    if target == WORKSPACE_ROOT or target == WORKSPACE_ROOT / ".workdir":
        return ToolResult(success=False, message="不允许删除工作区根目录或 .workdir 内部结构。")
    if not target.exists():
        return ToolResult(success=False, message=f"不存在：{path}")
    try:
        if target.is_dir():
            import shutil

            shutil.rmtree(target)
            kind = "目录"
        else:
            target.unlink()
            kind = "文件"
    except OSError as exc:
        return ToolResult(success=False, message=f"删除失败：{exc}")
    return ToolResult(success=True, output={"path": _rel(target)}, message=f"已删除{kind}「{_rel(target)}」。")


def _move_file(params: dict) -> ToolResult:
    src = str(params.get("src") or params.get("source") or "")
    dst = str(params.get("dst") or params.get("destination") or "")
    source, dest = _resolve(src), _resolve(dst)
    if source is None or dest is None:
        return _bad_path(src if source is None else dst)
    if not source.exists():
        return ToolResult(success=False, message=f"源不存在：{src}")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        source.rename(dest)
    except OSError as exc:
        return ToolResult(success=False, message=f"移动失败：{exc}")
    return ToolResult(
        success=True,
        output={"from": _rel(source), "to": _rel(dest)},
        message=f"已移动/重命名到「{_rel(dest)}」。",
    )


def _copy_file(params: dict) -> ToolResult:
    src = str(params.get("src") or params.get("source") or "")
    dst = str(params.get("dst") or params.get("destination") or "")
    source, dest = _resolve(src), _resolve(dst)
    if source is None or dest is None:
        return _bad_path(src if source is None else dst)
    if not source.exists():
        return ToolResult(success=False, message=f"源不存在：{src}")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            import shutil

            shutil.copytree(source, dest)
        else:
            import shutil

            shutil.copy2(source, dest)
    except OSError as exc:
        return ToolResult(success=False, message=f"复制失败：{exc}")
    return ToolResult(
        success=True,
        output={"from": _rel(source), "to": _rel(dest)},
        message=f"已复制到「{_rel(dest)}」。",
    )


def _search_files(params: dict) -> ToolResult:
    query = str(params.get("query") or "").strip()
    folder = str(params.get("path") or ".")
    content = bool(params.get("content", False))
    if not query:
        return ToolResult(success=False, message="需要 query（要搜索的关键字）")
    base = _resolve(folder)
    if base is None:
        return _bad_path(folder)
    if not base.is_dir():
        return ToolResult(success=False, message=f"目录不存在：{folder}")
    results: list[dict[str, Any]] = []
    for child in base.rglob("*"):
        try:
            if not child.is_file():
                continue
        except OSError:
            continue
        rel = _rel(child)
        if not content and query.lower() in child.name.lower():
            results.append({"path": rel, "match": "name", "size": child.stat().st_size})
        elif content:
            try:
                text = child.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if query in text:
                results.append({"path": rel, "match": "content", "size": len(text)})
        if len(results) >= MAX_SEARCH_RESULTS:
            break
    return ToolResult(
        success=True,
        output={"query": query, "results": results, "count": len(results)},
        message=f"找到 {len(results)} 个匹配" + ("（已达上限）" if len(results) >= MAX_SEARCH_RESULTS else "") + "。",
    )


def _get_file_info(params: dict) -> ToolResult:
    path = str(params.get("path") or "")
    target = _resolve(path)
    if target is None:
        return _bad_path(path)
    if not target.exists():
        return ToolResult(success=False, message=f"不存在：{path}")
    st = target.stat()
    info: dict[str, Any] = {
        "path": _rel(target),
        "type": "dir" if target.is_dir() else "file",
        "size": st.st_size,
        "modified": st.st_mtime,
    }
    if target.is_dir():
        children = list(target.iterdir())
        info["children"] = len(children)
    else:
        info["extension"] = target.suffix
    return ToolResult(success=True, output=info, message=f"「{_rel(target)}」的信息已返回。")


def _run_terminal(params: dict) -> ToolResult:
    command = str(params.get("command") or "").strip()
    if not command:
        return ToolResult(success=False, message="需要 command（要执行的终端命令）")
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            timeout=TERMINAL_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, message=f"命令超时（>{TERMINAL_TIMEOUT}s），已终止。")
    except OSError as exc:
        return ToolResult(success=False, message=f"执行失败：{exc}")
    out = (proc.stdout or "") + (proc.stderr or "")
    return ToolResult(
        success=proc.returncode == 0,
        output={
            "command": command,
            "return_code": proc.returncode,
            "output": _clip(out),
        },
        message=f"命令退出码 {proc.returncode}，输出 {len(out)} 字符。",
    )


def _run_script(params: dict) -> ToolResult:
    """在工作区里跑一段 Python 脚本（一次性子进程，复用后端 venv + PYTHONPATH）。"""
    code = str(params.get("code") or "").strip()
    if not code:
        return ToolResult(success=False, message="需要 code（要执行的 Python 代码）")
    # 脚本落盘到 .workdir/script/ 再执行，便于事后追溯
    import tempfile

    script = _resolve(".workdir/script/agent_file_tool.py")
    if script is None:
        return _bad_path(".workdir/script/")
    try:
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(code, encoding="utf-8")
    except OSError as exc:
        return ToolResult(success=False, message=f"写脚本失败：{exc}")
    python = sys.executable
    env = dict(subprocess_env())
    try:
        proc = subprocess.run(
            [python, str(script)],
            cwd=str(WORKSPACE_ROOT),
            capture_output=True,
            text=True,
            timeout=SCRIPT_TIMEOUT,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(success=False, message=f"脚本超时（>{SCRIPT_TIMEOUT}s），已终止。")
    except OSError as exc:
        return ToolResult(success=False, message=f"执行失败：{exc}")
    out = (proc.stdout or "") + (proc.stderr or "")
    return ToolResult(
        success=proc.returncode == 0,
        output={"path": _rel(script), "return_code": proc.returncode, "output": _clip(out)},
        message=f"脚本退出码 {proc.returncode}，输出 {len(out)} 字符。",
    )


def subprocess_env() -> dict[str, str]:
    """子进程环境：注入后端包根路径（app.* 可导入）与 UTF-8 输出。

    与 execute_code 同一套口径：app.* 包根目录 = app/tools/ 上溯两级（apps/api）。
    """
    import os

    _api_root = str(Path(__file__).resolve().parents[2])
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _api_root + (os.pathsep + existing if existing else "")
    return env


def _download_url(params: dict) -> ToolResult:
    url = str(params.get("url") or "").strip()
    filename = str(params.get("filename") or "")
    if not url:
        return ToolResult(success=False, message="需要 url（要下载的地址）")
    target = _resolve(filename) if filename else None
    if target is None:
        return _bad_path(filename or url)
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "UrbanScope/1.0"})
        data = urllib.request.urlopen(req, timeout=30).read()
    except Exception as exc:
        return ToolResult(success=False, message=f"下载失败：{exc}")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    except OSError as exc:
        return ToolResult(success=False, message=f"保存失败：{exc}")
    return ToolResult(
        success=True,
        output={"url": url, "path": _rel(target), "bytes": len(data)},
        message=f"已下载 {len(data)} 字节到「{_rel(target)}」。",
    )


_HANDLERS: dict[str, Any] = {
    "list_files": _list_files,
    "read_file": _read_file,
    "write_file": _write_file,
    "append_to_file": _append_file,
    "edit_file": _edit_file,
    "create_folder": _create_folder,
    "delete_file": _delete_file,
    "move_file": _move_file,
    "copy_file": _copy_file,
    "search_files": _search_files,
    "get_file_info": _get_file_info,
    "run_terminal": _run_terminal,
    "run_script": _run_script,
    "download_url": _download_url,
}


def run_file_tool(tool: str, params: dict) -> ToolResult:
    handler = _HANDLERS.get(tool)
    if handler is None:
        return ToolResult(success=False, message=f"未实现的文件工具：{tool}")
    try:
        return handler(params or {})
    except Exception as exc:  # 防御：文件系统意外错误也以消息返回，不抛栈
        return ToolResult(success=False, message=f"文件操作失败：{exc}")
