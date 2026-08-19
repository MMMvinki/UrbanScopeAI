"""execute_code 前自动安装缺失依赖。

扫描代码块的 ``import xxx`` / ``from xxx import yyy``，检查顶层包在当前
Python 环境是否可用，缺什么 pip 装什么。消除常见坏模式：
  Step N:   import foo → ImportError
  Step N+1: pip install foo
  Step N+2: import foo（浪费 2 次 LLM 调用）

安全：pip 安装本身要过权限门——execute_code 是 FORCE_ASK，用户已在
L1 门里看过代码（含 pip install 语句）才放行，因此这里不再二次询问。

去掉原实现里对"平台保留 import"的检查（移植来源见 NOTICE.md）。
"""

from __future__ import annotations

import ast
import importlib.util
import logging
import re
import subprocess
import sys
from typing import Callable, Optional, Set

logger = logging.getLogger(__name__)

_STDLIB_MODULES: Set[str] = {
    "abc", "aifc", "argparse", "array", "ast", "asynchat", "asyncio",
    "asyncore", "atexit", "audioop", "base64", "bdb", "binascii",
    "binhex", "bisect", "builtins", "bz2", "calendar", "cgi", "cgitb",
    "chunk", "cmath", "cmd", "code", "codecs", "codeop", "collections",
    "colorsys", "compileall", "concurrent", "configparser", "contextlib",
    "contextvars", "copy", "copyreg", "cProfile", "crypt", "csv",
    "ctypes", "curses", "dataclasses", "datetime", "dbm", "decimal",
    "difflib", "dis", "distutils", "doctest", "email", "encodings",
    "enum", "errno", "faulthandler", "fcntl", "filecmp", "fileinput",
    "fnmatch", "fractions", "ftplib", "functools", "gc", "getopt",
    "getpass", "gettext", "glob", "grp", "gzip", "hashlib", "heapq",
    "hmac", "html", "http", "idlelib", "imaplib", "imghdr", "imp",
    "importlib", "inspect", "io", "ipaddress", "itertools", "json",
    "keyword", "lib2to3", "linecache", "locale", "logging", "lzma",
    "mailbox", "mailcap", "marshal", "math", "mimetypes", "mmap",
    "modulefinder", "multiprocessing", "netrc", "nis", "nntplib",
    "numbers", "operator", "optparse", "os", "ossaudiodev", "pathlib",
    "pdb", "pickle", "pickletools", "pipes", "pkgutil", "platform",
    "plistlib", "poplib", "posix", "posixpath", "pprint", "profile",
    "pstats", "pty", "pwd", "py_compile", "pyclbr", "pydoc",
    "queue", "quopri", "random", "re", "readline", "reprlib",
    "resource", "rlcompleter", "runpy", "sched", "secrets", "select",
    "selectors", "shelve", "shlex", "shutil", "signal", "site",
    "smtpd", "smtplib", "sndhdr", "socket", "socketserver", "spwd",
    "sqlite3", "sre_compile", "sre_constants", "sre_parse", "ssl",
    "stat", "statistics", "string", "stringprep", "struct",
    "subprocess", "sunau", "symtable", "sys", "sysconfig", "syslog",
    "tabnanny", "tarfile", "telnetlib", "tempfile", "termios", "test",
    "textwrap", "threading", "time", "timeit", "tkinter", "token",
    "tokenize", "tomllib", "trace", "traceback", "tracemalloc",
    "tty", "turtle", "turtledemo", "types", "typing", "unicodedata",
    "unittest", "urllib", "uu", "uuid", "venv", "warnings", "wave",
    "weakref", "webbrowser", "winreg", "winsound", "wsgiref",
    "xdrlib", "xml", "xmlrpc", "zipapp", "zipfile", "zipimport",
    "zlib", "_thread", "__future__",
}

_IMPORT_TO_PIP: dict[str, str] = {
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "PIL": "Pillow",
    "bs4": "beautifulsoup4",
    "yaml": "pyyaml",
    "attr": "attrs",
    "dotenv": "python-dotenv",
    "gi": "PyGObject",
    "wx": "wxPython",
    "skimage": "scikit-image",
    "dateutil": "python-dateutil",
    "jose": "python-jose",
    "magic": "python-magic",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "serial": "pyserial",
    "usb": "pyusb",
    "Bio": "biopython",
}


def extract_imports(code: str) -> Set[str]:
    """AST 提取顶层包名；语法错误回退正则。"""
    packages: Set[str] = set()
    try:
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    packages.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    packages.add(node.module.split(".")[0])
    except SyntaxError:
        import_re = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.MULTILINE)
        for m in import_re.finditer(code):
            packages.add(m.group(1))
    return packages


def find_missing_packages(packages: Set[str], python_executable: str | None = None) -> list[str]:
    """检查哪些包当前 import 不到。"""
    exe = python_executable or sys.executable
    missing: list[str] = []
    for pkg in packages:
        if pkg in _STDLIB_MODULES:
            continue
        if exe == sys.executable:
            if importlib.util.find_spec(pkg) is None:
                missing.append(pkg)
        else:
            try:
                result = subprocess.run([exe, "-c", f"import {pkg}"], capture_output=True, timeout=10)
                if result.returncode != 0:
                    missing.append(pkg)
            except Exception:
                missing.append(pkg)
    return missing


def get_pip_package_name(import_name: str) -> str:
    return _IMPORT_TO_PIP.get(import_name, import_name)


def auto_install_missing(
    code: str,
    python_executable: str | None = None,
    progress_callback: Optional[Callable[[str, str], None]] = None,
    output_callback: Optional[Callable[[str], None]] = None,
    timeout: float = 120.0,
) -> Optional[str]:
    """检测并安装缺失包。返回安装摘要，无需安装返回 None。

    失败不阻塞执行——让代码自然以 ImportError 失败（LLM 自愈下一步）。
    """
    packages = extract_imports(code)
    if not packages:
        return None
    missing = find_missing_packages(packages, python_executable)
    if not missing:
        return None

    pip_names = [get_pip_package_name(m) for m in missing]
    logger.info("Auto-installing missing packages: %s (pip names: %s)", missing, pip_names)
    if progress_callback:
        try:
            progress_callback("installing_packages", f"Installing {', '.join(pip_names)}...")
        except Exception:
            pass

    cmd = [python_executable or sys.executable, "-m", "pip", "install"] + pip_names
    try:
        proc = subprocess.Popen(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1)
        lines: list[str] = []
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    lines.append(line)
                    if output_callback:
                        try:
                            output_callback(line)
                        except Exception:
                            pass
            return_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
            raise
        if return_code == 0:
            installed_msg = f"Auto-installed: {', '.join(pip_names)}"
            if output_callback:
                try:
                    output_callback(f"{installed_msg}\n")
                except Exception:
                    pass
            logger.info(installed_msg)
            return installed_msg
        logger.warning("pip install failed (rc=%d): %s", return_code, "".join(lines).strip())
        return None
    except subprocess.TimeoutExpired:
        logger.warning("pip install timed out after %.0fs", timeout)
        return None
    except Exception as e:
        logger.warning("pip install error: %s", e)
        return None


__all__ = ["extract_imports", "find_missing_packages", "get_pip_package_name", "auto_install_missing"]
