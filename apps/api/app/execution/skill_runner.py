"""Skill 子进程执行器 —— ``python -m app.execution.skill_runner <skill_dir> <in> <out>``。

契约（Anthropic skill scripts/ 对齐）：
- scripts/main.py 解析 ``--input in.json --output out.json``；input.json 是 wrapper
  ``{workspace, skill_id, params}``，算完 ``json.dump`` 写 output.json。
- 每次 run 全新解释器（可复现，无上一 run 的 namespace 残留）。
- 安全网不丢：复用 subprocess_runner 的 ``_install_platform_protection``
  （平台只读红线）、``_install_risky_op_hooks``（L4 写副作用遥测）、
  ``_make_local_save_plot`` / ``_make_local_emit_chart``（plot/chart 事件经
  stdout JSON 行回父进程，父进程捕获进成果栏）。

scripts/main.py 可直接 ``from app.gis import engine`` 用真实地理引擎（store.run 注入
API 根到 PYTHONPATH），也可用 ``save_plot`` / ``emit_chart`` 产出图表。

（迁移自 operation_runner.py。）
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from app.execution import subprocess_runner


def _install_chinese_font() -> None:
    """matplotlib 中文字体：默认 DejaVu Sans 不含 CJK 字形，中文标题会渲染成方块。"""
    try:
        import matplotlib

        matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception:
        pass


def _write_error_output(out_path: Path, error_text: str, summary: str) -> None:
    """异常路径也写 output.json，让父进程能读到真实错误而非"无 output.json"。"""
    try:
        out_path.write_text(
            json.dumps(
                {
                    "success": False,
                    "summary": summary,
                    "error": error_text[:4000],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def _resolve_entry(skill_dir: Path) -> Path:
    """入口优先 ``scripts/main.py``（Anthropic 布局）；旧根目录 main.py 兜底兼容。"""
    scripts_entry = skill_dir / "scripts" / "main.py"
    if scripts_entry.is_file():
        return scripts_entry
    return skill_dir / "main.py"


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    if len(argv) < 3:
        print("usage: skill_runner <skill_dir> <input.json> <output.json>")
        return 2
    skill_dir = Path(argv[0])
    in_path = Path(argv[1])
    out_path = Path(argv[2])
    entry = _resolve_entry(skill_dir)

    if not in_path.is_file():
        _write_error_output(out_path, f"input.json 不存在：{in_path}", "skill 输入缺失")
        return 1
    if not entry.is_file():
        _write_error_output(out_path, f"入口 scripts/main.py 不存在：{skill_dir}", "skill 入口缺失")
        return 1

    _install_chinese_font()
    subprocess_runner._install_risky_op_hooks()
    subprocess_runner._install_platform_protection()

    save_plot = subprocess_runner._make_local_save_plot()
    emit_chart = subprocess_runner._make_local_emit_chart()
    ns: dict = {"save_plot": save_plot, "emit_chart": emit_chart}
    subprocess_runner._install_pyplot_patch(ns)

    # 让 main.py 认为自己是被 `python main.py --input ... --output ...` 调用的
    sys.argv = ["main.py", "--input", str(in_path), "--output", str(out_path)]

    try:
        try:
            import runpy

            runpy.run_path(str(entry), run_name="__main__", init_globals=ns)
        except SystemExit as exc:  # argparse 缺参 / main.py 主动 sys.exit()
            code = exc.code if isinstance(exc.code, int) else 1
            summary = f"skill 主动退出（exit code {code}）"
            print(f"[skill_runner] {summary}")
            _write_error_output(out_path, summary, summary)
            return code if code != 0 else 0
        except Exception:
            tb = traceback.format_exc()
            print(tb)
            _write_error_output(
                out_path,
                tb,
                f"skill 执行异常：{tb.splitlines()[-1] if tb.splitlines() else '未知错误'}",
            )
            return 1

        # 自动保存脚本忘调 save_plot 的残留 matplotlib 图（与 execute_code 行为一致）
        try:
            subprocess_runner._autosave_open_plots(ns)
        except Exception:
            traceback.print_exc()

        if not out_path.is_file():
            _write_error_output(out_path, "main.py 未写 output.json", "skill 未产出 output.json")
            return 1
        return 0
    except Exception:  # 上面保护逻辑自身的兜底，绝不让错误静默吞掉
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
