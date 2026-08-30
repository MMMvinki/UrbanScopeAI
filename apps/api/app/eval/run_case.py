"""e2e 单 case 执行器入口：python -m app.eval.run_case --case <id> --set <set> --out <json>。

实现与 main 都在 app.eval.agent_runner（父进程 score_e2e_case 通过该模块 spawn 子进程）。
本文件是 python -m 的模块入口，避免把 CLI 挂在 agent_runner 的 __main__ 上（不可寻址）。
"""

from __future__ import annotations

import sys

from app.eval.agent_runner import main

if __name__ == "__main__":
    sys.exit(main())
