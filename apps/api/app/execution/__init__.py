"""execute_code 受控子进程（L0 进程隔离）。

包结构（移植来源见 NOTICE.md）：
- subprocess_runner.py     子进程侧：持久 namespace、JSON-Line IPC、tool stub RPC、
                           risky-op 遥测、matplotlib 自动落盘
- subprocess_executor.py   父侧：懒启动 / 复用 / 超时树杀 / cleanup
- auto_install.py          AST 提取 import → stdlib 过滤 → pip 自动装（失败不阻塞）
- code_validation.py       拒绝 think 标签 / 长 sleep / 链式思维注释
"""

from app.execution.subprocess_executor import (
    ChildDiedError,
    CodeExecResult,
    ExecTimeout,
    SubprocessExecutorConfig,
    SubprocessExecutorError,
    SubprocessPythonExecutor,
)

__all__ = [
    "SubprocessPythonExecutor",
    "SubprocessExecutorConfig",
    "SubprocessExecutorError",
    "ChildDiedError",
    "ExecTimeout",
    "CodeExecResult",
]
