"""数据目录接入 · 第三轮回归测试（子文件夹级批量启用/停用）。

覆盖（对应本轮增强）：
26. 多层级目录：对子 folder_path（rel_path 前缀）批量启用只命中该前缀，含嵌套子目录；
27. 防误匹配：同源同时放「城市」与「城市化」两个文件夹，前缀过滤不互相混淆（须 '/' 拼接）；
28. 兄弟文件夹/根目录数据集不被误动；
29. 20 硬上限在子文件夹作用域内仍生效（capped_by_limit）；
30. 512MB 累计预算在子文件夹作用域内仍生效（capped_by_bytes）；
31. disable_all_datasets 带 rel_path 只清该子目录，其他子目录已启用数据集保留；
32. rel_path 缺省（None）保持原全量行为不变。

运行：cd apps/api && .venv/Scripts/python.exe app/scripts/test_data_folder_round3.py
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

# 必须在 import app.* 之前设置 env（模块级常量读取）。
_WS = tempfile.mkdtemp(prefix="data_folder_r3_ws_")
_FIX = tempfile.mkdtemp(prefix="data_folder_r3_fix_")
os.environ["URBANSCOPE_WORKSPACE"] = _WS
# 字节阈值放宽：本轮聚焦「子目录作用域过滤」，不按字节模拟大文件，所有小 CSV 均 enableable
os.environ["URBANSCOPE_DATAFOLDER_ROWCOUNT_MAX_BYTES"] = "100000000"
os.environ["URBANSCOPE_DATAFOLDER_ENABLE_MAX_BYTES"] = "100000000"
os.environ["URBANSCOPE_DATAFOLDER_ENABLE_MAX_FEATURES"] = "100000"
os.environ["URBANSCOPE_DATAFOLDER_ENABLE_MAX_FEATURES_VECTOR"] = "100000"
os.environ["URBANSCOPE_DATAFOLDER_PREVIEW_MAX_FEATURES"] = "50000"

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.data_folder as df  # noqa: E402

FAILS: list[str] = []
TOTAL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global TOTAL
    TOTAL += 1
    mark = "✓" if cond else "✗"
    print(f"  {mark} {name}" + (f"  —— {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def make_csv(dir_: Path, stem: str) -> Path:
    """小 CSV（lon/lat → enableable），写入 dir_ 下并返回路径。"""
    p = dir_ / f"{stem}.csv"
    p.write_text("name,lon,lat\np,114.0,22.5\n", encoding="utf-8")
    return p


def status_by_rel(src_id: str) -> dict[str, bool]:
    """返回 {rel_path: enabled}，便于断言某前缀下的启用状态。"""
    return {d.rel_path: d.enabled for d in df.get_source(src_id).datasets}


def main() -> int:
    print("== 数据目录接入 · 第三轮回归（子文件夹级批量启用/停用）==")

    # ── 26/27/28. 多层级 + 防误匹配 + 兄弟文件夹隔离 ────────────
    tree = Path(_FIX) / "tree"
    (tree / "城市" / "nested").mkdir(parents=True, exist_ok=True)
    (tree / "城市化").mkdir(parents=True, exist_ok=True)
    (tree / "交通").mkdir(parents=True, exist_ok=True)
    make_csv(tree / "城市", "a")
    make_csv(tree / "城市", "b")
    make_csv(tree / "城市" / "nested", "c")
    make_csv(tree / "城市化", "d")
    make_csv(tree / "交通", "e")
    make_csv(tree, "top")
    src = df.link_source(str(tree), alias="树源")
    st0 = status_by_rel(src.source_id)
    check("26a 初始全部未启用", all(not v for v in st0.values()) and len(st0) == 6, f"{sorted(st0)}")

    # 嵌套子目录：只命中 城市/nested/c.csv
    r_nested = df.enable_all_datasets(src.source_id, rel_path="城市/nested")
    st_nested = status_by_rel(src.source_id)
    check("26b 嵌套前缀只命中其下数据集",
          st_nested.get("城市/nested/c.csv") is True
          and not st_nested.get("城市/a.csv") and not st_nested.get("城市/b.csv")
          and not st_nested.get("城市化/d.csv") and not st_nested.get("交通/e.csv")
          and not st_nested.get("top.csv"),
          f"enabled={[k for k, v in st_nested.items() if v]}")
    check("26c 嵌套前缀候选数 = 1", r_nested.get("total_candidates") == 1 and r_nested.get("enabled") == 1,
          f"total={r_nested.get('total_candidates')} enabled={r_nested.get('enabled')}")

    # 防误匹配：rel_path="城市" 必须不含「城市化」，也不含「交通」「top」
    df.disable_all_datasets(src.source_id, rel_path="城市/nested")
    r_city = df.enable_all_datasets(src.source_id, rel_path="城市")
    st_city = status_by_rel(src.source_id)
    check("27a 「城市」前缀命中 城市/a,b,c 三件",
          st_city.get("城市/a.csv") is True and st_city.get("城市/b.csv") is True
          and st_city.get("城市/nested/c.csv") is True,
          f"enabled={[k for k, v in st_city.items() if v]}")
    check("27b 不误匹配「城市化」前缀（须 '/' 拼接）",
          st_city.get("城市化/d.csv") is False,
          f"城市化={st_city.get('城市化/d.csv')}")
    check("27c 兄弟文件夹「交通」与根目录不被误动",
          not st_city.get("交通/e.csv") and not st_city.get("top.csv"))
    check("27d 「城市」前缀候选数 = 3（不把城市化算进去）",
          r_city.get("total_candidates") == 3 and r_city.get("enabled") == 3,
          f"total={r_city.get('total_candidates')} enabled={r_city.get('enabled')}")

    # 兄弟文件夹仍可独立启用：enable 交通 后 城市 状态不受影响
    r_tr = df.enable_all_datasets(src.source_id, rel_path="交通")
    st_tr = status_by_rel(src.source_id)
    check("28a 兄弟文件夹独立启用", st_tr.get("交通/e.csv") is True and r_tr.get("enabled") == 1)
    check("28b 启用交通后城市前缀未被改变",
          st_tr.get("城市/a.csv") is True and st_tr.get("城市化/d.csv") is False)

    # ── 31. disable 带 rel_path 只清该子目录 ─────────────────
    r_d = df.disable_all_datasets(src.source_id, rel_path="城市")
    st_d = status_by_rel(src.source_id)
    check("31a disable 只清「城市」前缀（含嵌套 c）",
          not st_d.get("城市/a.csv") and not st_d.get("城市/b.csv")
          and not st_d.get("城市/nested/c.csv"),
          f"enabled={[k for k, v in st_d.items() if v]}")
    check("31b 其他子目录已启用数据集保留",
          st_d.get("交通/e.csv") is True and st_d.get("城市化/d.csv") is False)
    check("31c disable 返回数 = 3", r_d.get("disabled") == 3 and r_d.get("count") == 3,
          f"disabled={r_d.get('disabled')}")
    df.disable_all_datasets(src.source_id, rel_path="交通")

    # ── 29. 20 硬上限在子文件夹作用域内仍生效 ────────────────
    bulk = Path(_FIX) / "bulk"
    (bulk / "批量").mkdir(parents=True, exist_ok=True)
    for i in range(25):
        make_csv(bulk / "批量", f"d{i}")
    bulk_src = df.link_source(str(bulk), alias="批量源")
    r_bulk = df.enable_all_datasets(bulk_src.source_id, rel_path="批量")
    check("29a 子文件夹内 25 候选被 clamp 到 20",
          r_bulk.get("total_candidates") == 25 and r_bulk.get("planned_count") == 20
          and r_bulk.get("enabled") == 20 and r_bulk.get("capped") is True
          and r_bulk.get("capped_by_limit") is True,
          f"planned={r_bulk.get('planned_count')} enabled={r_bulk.get('enabled')}")
    enabled_ids = [d.dataset_id for d in df.get_source(bulk_src.source_id).datasets if d.enabled]
    check("29b 恰好启用 20 个（都在 批量/ 前缀下）",
          len(enabled_ids) == 20 and all(d.rel_path.startswith("批量/")
                                          for d in df.get_source(bulk_src.source_id).datasets if d.enabled))
    r_bulk_d = df.disable_all_datasets(bulk_src.source_id, rel_path="批量")
    check("29c 子文件夹 disable-all 停用 20 个", r_bulk_d.get("count") == 20)

    # ── 30. 512MB 累计预算在子文件夹作用域内仍生效 ────────────
    old_budget = df._ENABLE_ALL_MAX_BYTES
    df._ENABLE_ALL_MAX_BYTES = 1  # 收紧预算：第 1 个候选也受预算约束（首项不再豁免）
    try:
        r_budget = df.enable_all_datasets(bulk_src.source_id, rel_path="批量")
    finally:
        df._ENABLE_ALL_MAX_BYTES = old_budget
    check("30a 子文件夹内预算截断仍生效（首项也受限）",
          r_budget.get("capped") is True and r_budget.get("capped_by_bytes") is True
          and r_budget.get("planned_count") == 0 and r_budget.get("enabled") == 0,
          f"planned={r_budget.get('planned_count')} capped_by_bytes={r_budget.get('capped_by_bytes')}")
    df.disable_all_datasets(bulk_src.source_id, rel_path="批量")

    # ── 32. rel_path 缺省 = 全量行为不变 ─────────────────────
    r_full = df.enable_all_datasets(bulk_src.source_id)
    check("32a 不带 rel_path 仍全量启用（含批量前缀外）",
          r_full.get("total_candidates") == 25 and r_full.get("enabled") == 20,
          f"total={r_full.get('total_candidates')} enabled={r_full.get('enabled')}")
    df.disable_all_datasets(bulk_src.source_id)

    print(f"\n结果：{TOTAL - len(FAILS)}/{TOTAL} 通过" + (f"，失败：{FAILS}" if FAILS else ""))
    return 1 if FAILS else 0


if __name__ == "__main__":
    code = main()
    shutil.rmtree(_WS, ignore_errors=True)
    shutil.rmtree(_FIX, ignore_errors=True)
    sys.exit(code)
