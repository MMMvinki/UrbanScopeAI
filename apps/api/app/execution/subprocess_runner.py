"""子进程侧 Python runner（``SubprocessPythonExecutor`` 的子进程）。

以 ``python -u -m app.execution.subprocess_runner`` 启动，与父进程通过
stdin/stdout 交换换行分隔的 JSON。设计原则：

1. 父进程持有真实工具函数（闭包了 ToolContext 等），子进程不能直接调用；
   每个工具在子进程 namespace 里成为 **stub**，调用时经管道 RPC 回父进程。
2. 子进程状态跨 ``exec`` 持久：``gdf = gpd.read_file(...)`` 后单独
   ``print(gdf.head())`` 与 Jupyter 行为一致。
3. 无沙箱墙——"Claude Code 风格"而非 Docker。安全来自
   父进程权限门（L1）+ workspace 边界（L2）+ risky-op 遥测（L4）。

协议见 subprocess_executor.py 头部注释。

（移植来源见 NOTICE.md）
"""

from __future__ import annotations

import builtins
import io
import json
import re
import sys
import traceback
import uuid
from typing import Any

# ─────────────────────────────────────────────────────────────────────
# IPC helpers
# ─────────────────────────────────────────────────────────────────────


def _emit(obj: dict) -> None:
    """向父进程发一条 JSON 消息，始终 flush。"""
    sys.__stdout__.write(json.dumps(obj, default=_json_default) + "\n")
    sys.__stdout__.flush()


def _json_default(o: Any) -> Any:
    """无法序列化的值回退为 repr，绝不 crash 管道。"""
    try:
        return repr(o)
    except Exception:
        return f"<unserialisable {type(o).__name__}>"


def _json_safe(value: Any) -> Any:
    """递归把 numpy 标量/数组转成原生 JSON 类型。

    原因：LLM 常用 `gdf['amenity'].value_counts()` 的结果直接传 emit_chart，
    那里面是 numpy.int64。若不做转换，json.dumps 抛
    "Object of type int64 is not JSON serializable"——事件已发出、函数却报错，
    导致同一张图被 LLM 重试一次 → 成果栏出现重复图表。
    """
    try:
        import numpy as np
    except Exception:
        np = None
    if np is not None:
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.bool_):
            return bool(value)
        if isinstance(value, np.ndarray):
            return [_json_safe(v) for v in value.tolist()]
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


def _read_message() -> dict | None:
    """阻塞读一条 stdin 的换行分隔 JSON。"""
    line = sys.__stdin__.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError as e:
        _emit({"kind": "stderr", "text": f"[runner] bad JSON from parent: {e}\n"})
        return {}


# ─────────────────────────────────────────────────────────────────────
# Tool stub：经管道把调用转发给父进程
# ─────────────────────────────────────────────────────────────────────


def _make_tool_stub(name: str):
    def stub(*args, **kwargs):
        call_id = uuid.uuid4().hex
        _emit({
            "kind": "tool_call",
            "call_id": call_id,
            "name": name,
            "args": list(args),
            "kwargs": kwargs,
        })
        while True:
            msg = _read_message()
            if msg is None:
                raise RuntimeError(f"Parent closed pipe while tool '{name}' was in flight")
            if msg.get("kind") == "tool_result" and msg.get("call_id") == call_id:
                if msg.get("ok"):
                    return msg.get("value")
                raise RuntimeError(f"Tool '{name}' failed: {msg.get('error') or 'unknown error'}")
            _emit({
                "kind": "stderr",
                "text": f"[runner] unexpected msg while waiting for tool_result: {msg!r}\n",
            })

    stub.__name__ = name
    stub.__qualname__ = name
    return stub


# ─────────────────────────────────────────────────────────────────────
# Risky-op hooks（L4 遥测）——观察，不拦截
# ─────────────────────────────────────────────────────────────────────
# 打补丁一组写副作用 builtin，让 LLM 的每次破坏性/写调用出现在
# meta.json.risky_ops（父进程 archive.record_risky_op）。安全网是
# workspace 边界 + 权限门，不是这里。


def _install_risky_op_hooks() -> None:
    """Monkey-patch os / shutil / pathlib / builtins.open 以发出 risky_op。"""
    import os as _os
    import pathlib as _pl
    import shutil as _shutil

    def _report(op: str, path: Any, **extra: Any) -> None:
        try:
            payload = {"kind": "risky_op", "op": op, "path": str(path)}
            if extra:
                payload["extra"] = {k: str(v) for k, v in extra.items()}
            _emit(payload)
        except Exception:
            pass

    _os_remove = _os.remove
    _os_unlink = _os.unlink

    def remove(path, *a, **kw):
        _report("os.remove", path)
        return _os_remove(path, *a, **kw)

    def unlink(path, *a, **kw):
        _report("os.unlink", path)
        return _os_unlink(path, *a, **kw)

    _os.remove = remove  # type: ignore[assignment]
    _os.unlink = unlink  # type: ignore[assignment]

    _rmtree = _shutil.rmtree

    def rmtree(path, *a, **kw):
        _report("shutil.rmtree", path)
        return _rmtree(path, *a, **kw)

    _shutil.rmtree = rmtree  # type: ignore[assignment]

    _path_unlink = _pl.Path.unlink
    _path_write_text = _pl.Path.write_text
    _path_write_bytes = _pl.Path.write_bytes

    def path_unlink(self, *a, **kw):
        _report("Path.unlink", self)
        return _path_unlink(self, *a, **kw)

    def path_write_text(self, data, *a, **kw):
        _report("Path.write_text", self, bytes=len(data) if isinstance(data, str) else 0)
        return _path_write_text(self, data, *a, **kw)

    def path_write_bytes(self, data, *a, **kw):
        _report("Path.write_bytes", self, bytes=len(data) if isinstance(data, (bytes, bytearray)) else 0)
        return _path_write_bytes(self, data, *a, **kw)

    _pl.Path.unlink = path_unlink  # type: ignore[assignment]
    _pl.Path.write_text = path_write_text  # type: ignore[assignment]
    _pl.Path.write_bytes = path_write_bytes  # type: ignore[assignment]

    _real_open = builtins.open

    def open_hook(file, mode="r", *a, **kw):  # noqa: D401
        try:
            if isinstance(mode, str) and any(c in mode for c in ("w", "a", "x", "+")):
                _report("open", file, mode=mode)
        except Exception:
            pass
        return _real_open(file, mode, *a, **kw)

    builtins.open = open_hook  # type: ignore[assignment]


def _install_platform_protection() -> None:
    """平台只读底座保护：execute_code 子进程禁止对 platform_data 目录做任何写操作。

    平台数据是**只读依据**（产品红线：数据内容不可改）。Agent 只能读它做二次分析，
    分析产出必须落在工作区（data/layers）再注册为新图层。这里在 Python 层包装所有
    写入口——open('w')/os.open/os.remove/os.rename/os.mkdir/os.chmod 等——目标路径
    resolve 后位于 platform_data 内一律抛 PermissionError。shutil/pathlib 的方法内部
    都走这些 os 层函数，会被自动覆盖。与文件系统只读（_ensure_platform_readonly，
    挡 GDAL/fiona 这类 C 层写入）互为双保险。
    """
    import io as _io
    import os as _os
    from pathlib import Path as _Path

    _PLATFORM_DIR = _os.path.normcase(str(_Path(__file__).resolve().parents[1] / "platform_data"))

    def _in_platform(path: Any) -> bool:
        try:
            candidate = str(path)
        except Exception:
            return False
        if not candidate:
            return False
        norm = _os.path.normcase(_os.path.abspath(_os.path.normpath(candidate)))
        return norm == _PLATFORM_DIR or norm.startswith(_PLATFORM_DIR + _os.sep)

    def _deny(op: str, path: Any) -> None:
        raise PermissionError(
            f"平台数据为只读底座，禁止在 execute_code 里执行「{op}」操作（{path}）。"
            "平台数据只能读取作为分析依据；分析产出请写入工作区 data/layers 并注册为新图层。"
        )

    # ── open 系：builtins.open 与 io.open 指向同一底层，Path.open 走 io.open ──
    _real_open = _io.open

    def open_hook(file, mode="r", *a, **kw):  # noqa: D401
        try:
            if isinstance(mode, str) and any(c in mode for c in ("w", "a", "x", "+")):
                if _in_platform(file):
                    _deny("open", file)
        except PermissionError:
            raise
        except Exception:
            pass
        return _real_open(file, mode, *a, **kw)

    _io.open = open_hook  # type: ignore[assignment]
    builtins.open = open_hook  # type: ignore[assignment]

    # ── os.open：flags 含写位即拒（open() 不经过 os.open，但 GDAL 等可能走）──
    _real_os_open = _os.open

    def os_open(path, flags, *a, **kw):  # noqa: D401
        if flags & (_os.O_WRONLY | _os.O_RDWR | _os.O_CREAT | _os.O_TRUNC | _os.O_APPEND):
            if _in_platform(path):
                _deny("os.open", path)
        return _real_os_open(path, flags, *a, **kw)

    _os.open = os_open  # type: ignore[assignment]

    # ── os 层单路径写操作（shutil/pathlib 内部都走这些，自动被覆盖）──
    for _name, _op in (
        ("remove", "os.remove"),
        ("unlink", "os.unlink"),
        ("rmdir", "os.rmdir"),
        ("mkdir", "os.mkdir"),
        ("makedirs", "os.makedirs"),
        ("chmod", "os.chmod"),
    ):
        _orig = getattr(_os, _name)

        def hooked(path, *a, _orig=_orig, _op=_op, **kw):  # noqa: D401
            if _in_platform(path):
                _deny(_op, path)
            return _orig(path, *a, **kw)

        setattr(_os, _name, hooked)

    # ── os 双路径操作：rename/replace（源或目标在平台内都拒：移走/改盖都算改数据）──
    for _name, _op in (("rename", "os.rename"), ("replace", "os.replace")):
        _orig = getattr(_os, _name)

        def hooked_2(src, dst, *a, _orig=_orig, _op=_op, **kw):  # noqa: D401
            if _in_platform(src) or _in_platform(dst):
                _deny(_op, src if _in_platform(src) else dst)
            return _orig(src, dst, *a, **kw)

        setattr(_os, _name, hooked_2)


# ─────────────────────────────────────────────────────────────────────
# Matplotlib pyplot 补丁 —— plt.savefig/show 自动触发 save_plot()
# ─────────────────────────────────────────────────────────────────────
# 在 namespace 建好后安装（save_plot 必须已就绪）。用 import 深度守卫，
# 防止在 matplotlib.pyplot 自身初始化中途打补丁破坏模块缓存。


def _install_pyplot_patch(namespace: dict) -> None:
    import builtins
    import sys

    save_plot_fn = namespace.get("save_plot")
    if save_plot_fn is None:
        return

    _original_import = builtins.__import__
    _import_depth = [0]

    def _do_patch(plt_module):
        if getattr(plt_module, "_plot_patched", False) or not hasattr(plt_module, "savefig"):
            return
        _original_savefig = plt_module.savefig

        def patched_savefig(*a, **kw):
            try:
                save_plot_fn(auto_close=False)
            except Exception:
                pass
            return _original_savefig(*a, **kw)

        def patched_show(*a, **kw):
            try:
                save_plot_fn(auto_close=False)
            except Exception:
                pass

        plt_module.savefig = patched_savefig
        plt_module.show = patched_show
        plt_module._plot_patched = True

    def patched_import(name, globals=None, locals=None, fromlist=(), level=0):
        _import_depth[0] += 1
        try:
            result = _original_import(name, globals, locals, fromlist, level)
        finally:
            _import_depth[0] -= 1
        if _import_depth[0] == 0 and "matplotlib.pyplot" in sys.modules:
            plt_mod = sys.modules["matplotlib.pyplot"]
            if not getattr(plt_mod, "_plot_patched", False):
                _do_patch(plt_mod)
        return result

    builtins.__import__ = patched_import

    if "matplotlib.pyplot" in sys.modules:
        _do_patch(sys.modules["matplotlib.pyplot"])


# ─────────────────────────────────────────────────────────────────────
# Captured-stdout 包装
# ─────────────────────────────────────────────────────────────────────


class _TeeStdout(io.TextIOBase):
    """捕获用户 print：既缓冲进 logs，也近实时流式发给父进程。

    单次 exec 总输出硬顶 8000 字符，防止大 dump 刷爆 UI / 上下文。
    """

    MAX_OUTPUT_CHARS = 8000
    TAIL_RESERVE = 1500

    def __init__(self) -> None:
        self._buf: list[str] = []
        self._total_chars: int = 0
        self._truncated: bool = False

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        if not s:
            return 0
        if self._truncated:
            self._tail_buf = s
            return len(s)
        self._total_chars += len(s)
        if self._total_chars > self.MAX_OUTPUT_CHARS:
            self._truncated = True
            self._buf.append(
                f"\n\n⚠️ Output truncated ({self._total_chars} chars exceeded "
                f"{self.MAX_OUTPUT_CHARS} limit). Only head is preserved.\n"
            )
            _emit({"kind": "stdout", "text": self._buf[-1]})
            self._tail_buf = ""
            return len(s)
        self._buf.append(s)
        _emit({"kind": "stdout", "text": s})
        return len(s)

    def flush(self) -> None:
        pass

    def getvalue(self) -> str:
        result = "".join(self._buf)
        if self._truncated and hasattr(self, "_tail_buf") and self._tail_buf:
            result += f"\n... (last output line): {self._tail_buf.rstrip()}\n"
        return result


# ─────────────────────────────────────────────────────────────────────
# save_plot —— 子进程本地实现（图在子进程里，父进程够不着）
# ─────────────────────────────────────────────────────────────────────


def _unique_plot_path(target, stem: str):
    """同名文件自动追加 _1/_2/…，避免 LLM 多次 save_plot 互相覆盖。"""
    candidate = target / f"{stem}.png"
    index = 1
    while candidate.exists():
        candidate = target / f"{stem}_{index}.png"
        index += 1
    return candidate


def _make_local_save_plot():
    import os
    import time as _time
    from pathlib import Path as _Path

    def _sanitize_stem(filename: str | None) -> str:
        stem = (filename or f"plot_{int(_time.time() * 1000)}").strip()
        stem = _Path(stem).name or f"plot_{int(_time.time() * 1000)}"
        if stem.lower().endswith(".png"):
            stem = stem[:-4]  # 防止 LLM 传入 "xxx.png" 导致 .png.png
        # 文件名白名单：只保留安全字符，避免路径注入 / URL 编码问题
        stem = re.sub(r"[^A-Za-z0-9_.-]", "_", stem) or f"plot_{int(_time.time() * 1000)}"
        return stem

    def _emit_plot(path, caption: str | None) -> None:
        payload: dict = {"path": str(path)}
        if caption:
            payload["caption"] = caption
        _emit({"kind": "plot_saved", **payload})

    def save_plot(caption: str | None = None, filename: str | None = None, dpi: float | None = None, auto_close: bool = True) -> str:
        try:
            import matplotlib.pyplot as plt
        except ImportError as exc:
            raise ImportError("matplotlib is required for save_plot. Install it with: pip install matplotlib") from exc

        fig = plt.gcf()
        if not fig.get_axes():
            raise RuntimeError("save_plot: no active matplotlib figure to save. Build a chart first then call save_plot().")

        # 落盘到 workspace 的 .workdir/artifacts/plots/
        base = _Path(os.getcwd())
        target = base / ".workdir" / "artifacts" / "plots"
        target.mkdir(parents=True, exist_ok=True)

        stem = _sanitize_stem(filename)
        dpi_int = int(dpi) if dpi else 150
        # 排除 colorbar 轴：fig.colorbar() 会创建第二根 axes（带 _colorbar 属性），
        # 它不是真正的子图——若不排除会被"多子图拆分"误拆成一张纯色条细长图，
        # 导致成果栏出现"重复"图片（实测热力图被拆成 热力图 + 色条 两张）。
        axes_list = [ax for ax in fig.get_axes() if getattr(ax, "_colorbar", None) is None]
        if not axes_list:
            axes_list = list(fig.get_axes())

        # 多子图（subplot）自动拆成多张 PNG → 每个子图一个 widget，而不是四图挤一张。
        # 单子图走原逻辑；多子图逐 axes 用 tight bbox 单独落盘。
        if len(axes_list) > 1:
            fig.canvas.draw()  # 确保 renderer 可用
            renderer = fig.canvas.get_renderer()
            saved: list[str] = []
            for index, ax in enumerate(axes_list):
                fpath = _unique_plot_path(target, f"{stem}_{index + 1}")
                extent = ax.get_tightbbox(renderer).transformed(fig.dpi_scale_trans.inverted())
                fig.savefig(
                    str(fpath), dpi=dpi_int, bbox_inches=extent, facecolor="white", pad_inches=0.15
                )
                ax_caption = caption or ax.get_title() or f"图表 {index + 1}"
                _emit_plot(fpath, ax_caption)
                saved.append(str(fpath.resolve()))
                print(f"[save_plot] ✅ 子图 {index + 1} → {fpath.name}")
            setattr(fig, "_plot_saved", True)
            if auto_close:
                plt.close(fig)
            print(f"[save_plot] ✅ 多子图已拆分，共 {len(saved)} 张")
            return "\n".join(saved)

        fpath = _unique_plot_path(target, stem)
        fig.savefig(str(fpath), dpi=dpi_int, bbox_inches="tight", facecolor=fig.get_facecolor())
        setattr(fig, "_plot_saved", True)
        _emit_plot(fpath, caption)
        if auto_close:
            plt.close(fig)
        abs_path = str(fpath.resolve())
        print(f"[save_plot] ✅ Plot saved → {abs_path}")
        return abs_path

    save_plot.__name__ = "save_plot"
    save_plot.__qualname__ = "save_plot"
    return save_plot


def _make_local_emit_chart():
    """emit_chart —— 把结构化图表数据发给前端，渲染成 ECharts 交互图表。

    与 save_plot 的区别：save_plot 出静态 PNG；emit_chart 出「可悬浮/缩放/动画」
    的交互图表，且一张卡只含一个图表。常规柱/饼/折线/散点优先用 emit_chart。
    图表判据：仅在对比类别/分区/取值点 ≥2 时使用（少于 2 个可比项用指标卡）；
    可比类别合并成一张多系列图表，不拆多个近似单图；图表数量不设硬性上限。
    """

    _CHART_TYPES = {"bar", "pie", "line", "scatter"}

    def emit_chart(
        title: str,
        chart_type: str = "bar",
        x_labels: list | None = None,
        series: list | None = None,
        y_label: str | None = None,
        height: int = 320,
    ) -> str:
        import json as _json

        if not title or not str(title).strip():
            raise RuntimeError("emit_chart: title 不能为空（每个图表一个标题，前端会作为卡片标题展示）")
        chart_type = str(chart_type or "bar").strip().lower()
        if chart_type not in _CHART_TYPES:
            raise RuntimeError(f"emit_chart: 不支持的 chart_type {chart_type!r}，可选 {sorted(_CHART_TYPES)}")
        series = list(series or [])
        if not series:
            raise RuntimeError("emit_chart: series 至少需要一组数据，格式 [{'name': '系列名', 'data': [数值...]}]")
        for item in series:
            if not isinstance(item, dict) or "name" not in item or "data" not in item:
                raise RuntimeError("emit_chart: series 每项需包含 name 与 data，如 {'name': '面积', 'data': [1, 2, 3]}")
        spec = _json_safe({
            "chart_type": chart_type,
            "title": str(title).strip(),
            "x_labels": [str(x) for x in (x_labels or [])],
            "series": [{"name": str(item.get("name")), "data": list(item.get("data", []))} for item in series],
            "y_label": str(y_label) if y_label else None,
            "height": int(height),
        })
        _emit({"kind": "chart_saved", "spec": spec})
        print(f"[emit_chart] ✅ 交互式图表已生成: {title}")
        return _json.dumps(spec, ensure_ascii=False)

    emit_chart.__name__ = "emit_chart"
    emit_chart.__qualname__ = "emit_chart"
    return emit_chart


def _build_namespace(tool_names: list[str]) -> dict[str, Any]:
    ns: dict[str, Any] = {"__name__": "__main__", "__builtins__": builtins}
    for name in tool_names:
        if name == "save_plot":
            ns[name] = _make_local_save_plot()
        elif name == "emit_chart":
            ns[name] = _make_local_emit_chart()
        else:
            ns[name] = _make_tool_stub(name)
    return ns


# ─────────────────────────────────────────────────────────────────────
# 执行
# ─────────────────────────────────────────────────────────────────────


def _run_exec(code: str, namespace: dict[str, Any]) -> None:
    """在 namespace 里执行代码块，出口恰好发一条 done。"""
    captured = _TeeStdout()
    real_stdout = sys.stdout
    sys.stdout = captured
    output: Any = None
    try:
        compiled = None
        try:
            compiled = compile(code, "<agent>", "eval")
            output = eval(compiled, namespace, namespace)
        except SyntaxError:
            exec(compile(code, "<agent>", "exec"), namespace, namespace)
    except BaseException:
        tb = traceback.format_exc()
        try:
            _autosave_open_plots(namespace)
        except Exception:
            traceback.print_exc()
        sys.stdout = real_stdout
        _emit({"kind": "done", "ok": False, "error": tb, "logs": captured.getvalue()})
        return
    finally:
        sys.stdout = real_stdout

    sys.stdout = captured
    try:
        try:
            _autosave_open_plots(namespace)
        except Exception:
            traceback.print_exc()
    finally:
        sys.stdout = real_stdout

    _emit({"kind": "done", "ok": True, "output": output, "logs": captured.getvalue()})


def _autosave_open_plots(namespace: dict[str, Any]) -> None:
    """代码块结束时自动保存残留的 matplotlib 图。"""
    save_plot_fn = namespace.get("save_plot")
    if not callable(save_plot_fn):
        return
    try:
        import matplotlib.pyplot as plt
    except Exception:
        return
    for fig_num in list(plt.get_fignums()):
        try:
            fig = plt.figure(fig_num)
            if not fig.get_axes() or getattr(fig, "_plot_saved", False):
                continue
            save_plot_fn(auto_close=True)
        except Exception:
            traceback.print_exc()


# ─────────────────────────────────────────────────────────────────────
# 主循环
# ─────────────────────────────────────────────────────────────────────


def main() -> None:
    # 内存上限 4GB（仅 Unix 生效）；强制无缓冲 + utf-8。
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (4 * 1024 * 1024 * 1024, 4 * 1024 * 1024 * 1024))
    except (ImportError, ValueError, OSError):
        pass
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except Exception:
        pass

    # matplotlib 中文字体：默认 DejaVu Sans 不含 CJK 字形，Agent 画图的中文标题/坐标轴
    # 会渲染成方块乱码（实测 glyph missing 警告）。进程启动即设中文字体（Windows 有微软雅黑）。
    try:
        import matplotlib

        matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception:
        pass

    _install_risky_op_hooks()
    _install_platform_protection()

    tool_names: list[str] = []
    namespace: dict[str, Any] = {}

    _emit({"kind": "ready"})

    while True:
        msg = _read_message()
        if msg is None:
            return  # stdin EOF —— 父进程消失，干净退出
        if not msg:
            continue

        kind = msg.get("kind")
        if kind == "init":
            tool_names = list(msg.get("tool_names") or [])
            namespace = _build_namespace(tool_names)
            _install_pyplot_patch(namespace)
            _emit({"kind": "init_ok"})
        elif kind == "set_var":
            name = msg.get("name")
            value = msg.get("value")
            if isinstance(name, str):
                namespace[name] = value
                _emit({"kind": "set_var_ok", "name": name})
        elif kind == "exec":
            _run_exec(msg.get("code") or "", namespace)
        elif kind == "shutdown":
            _emit({"kind": "bye"})
            return
        else:
            _emit({"kind": "stderr", "text": f"[runner] unknown kind: {kind!r}\n"})


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
