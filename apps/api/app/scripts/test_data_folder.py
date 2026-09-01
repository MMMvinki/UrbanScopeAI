"""数据目录接入（第一步「只读发现」）确定性验证。

运行：cd apps/api && .venv/Scripts/python.exe app/scripts/test_data_folder.py

验证项（计划文档 §10 要求 6）：
1. 正常目录：WGS84 shapefile + 含 lon/lat 的 CSV → 识别、状态可直接启用；
2. 空目录 → 0 数据集；
3. 不允许的目录（不在白名单）→ link_source 抛错；
4. 重复链接 → 幂等（同 source_id，更新快照不新建）；
5. 大型 CSV（> 阈值）→ 仅可查询、不统计行数（count=None）；
6. Shapefile 配套文件合并 → 1 个数据集（非 4 个）；
7. 坐标信息读取：CSV 坐标列、shapefile .prj CRS；
8. 重启恢复：清内存缓存后从磁盘懒加载恢复数据源。

不复制任何真实大型原始数据；fixture 在临时目录内用 fiona/文本就地构造。
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

# 必须在 import app.* 之前设置：workspace.py / data_folder.py 的模块级常量读取 env。
_WS = tempfile.mkdtemp(prefix="data_folder_test_ws_")
_FIX = tempfile.mkdtemp(prefix="data_folder_test_fix_")
os.environ["URBANSCOPE_WORKSPACE"] = _WS
# 用小阈值模拟「大文件」：>2000 字节 → 仅可查询；>500 字节 → 不统计行数。
os.environ["URBANSCOPE_DATAFOLDER_ROWCOUNT_MAX_BYTES"] = "500"
os.environ["URBANSCOPE_DATAFOLDER_ENABLE_MAX_BYTES"] = "2000"

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app.data_folder as df  # noqa: E402
from app.data_download import resolve_download_output  # noqa: E402
from app.data_folder import DataFolderError  # noqa: E402
from app.data_registry import DATA_REGISTRY  # noqa: E402
from app.models.data_folder import DatasetStatus  # noqa: E402

FAILS: list[str] = []
TOTAL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global TOTAL
    TOTAL += 1
    mark = "✓" if cond else "✗"
    print(f"  {mark} {name}" + (f"  —— {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def make_shp(dir_: Path, stem: str, epsg: int | None = 4326) -> Path:
    import fiona
    from fiona.crs import CRS

    shp = dir_ / f"{stem}.shp"
    schema = {"geometry": "Point", "properties": {"name": "str", "value": "int"}}
    crs = CRS.from_epsg(epsg) if epsg is not None else None
    with fiona.open(str(shp), "w", driver="ESRI Shapefile", crs=crs, schema=schema, encoding="utf-8") as dst:
        dst.write({"geometry": {"type": "Point", "coordinates": (114.0, 22.5)}, "properties": {"name": "A", "value": 1}})
        dst.write({"geometry": {"type": "Point", "coordinates": (114.1, 22.6)}, "properties": {"name": "B", "value": 2}})
    return shp


def build_fixtures() -> dict:
    root = Path(_FIX)
    # 子目录 A：可启用数据
    a = root / "a"
    a.mkdir(parents=True, exist_ok=True)
    make_shp(a, "subway", epsg=4326)
    (a / "vitality.csv").write_text("name,lon,lat\nA,114.0,22.5\nB,114.1,22.6\nC,114.2,22.7\n", encoding="utf-8")
    (a / "nogeo.csv").write_text("name,category\nA,shop\nB,shop\n", encoding="utf-8")
    # 大 CSV：真实表头 + truncate 到 >阈值
    big = a / "big.csv"
    big.write_text("lon,lat,val\n114.0,22.5,1\n", encoding="utf-8")
    with big.open("ab") as f:
        f.truncate(3000)  # 3000 字节 > 2000 → 仅可查询；> 500 → 不统计行数
    # 无 .prj 的 shapefile
    shp_nocrs = make_shp(a, "nocrs", epsg=4326)
    (a / "nocrs.prj").unlink()
    # 空目录
    (root / "empty").mkdir(parents=True, exist_ok=True)
    # 假 gdb 目录（无效 gdb，fiona 读取失败 → 标 unavailable）
    (root / "fake.gdb").mkdir(parents=True, exist_ok=True)
    (root / "fake.gdb" / "dummy.txt").write_text("not a real gdb", encoding="utf-8")
    return {"root": root, "a": a, "shp": a / "subway.shp", "nocrs": shp_nocrs}


def main() -> int:
    print("== 数据目录接入测试 ==")
    fx = build_fixtures()
    root = fx["root"]

    # 1. 正常目录扫描
    datasets = df.scan_folder(root)
    by_id = {d.dataset_id: d for d in datasets}
    check("1a 正常目录识别 subway(可启用)", by_id.get("subway") is not None and by_id["subway"].status == DatasetStatus.enableable)
    check("1b 正常目录识别 vitality(可启用)", by_id.get("vitality") is not None and by_id["vitality"].status == DatasetStatus.enableable)

    # 2. 空目录
    check("2 空目录 0 数据集", len(df.scan_folder(root / "empty")) == 0)

    # 3. 驱动器根拒绝（真安全边界，防止误扫整盘）
    try:
        df.link_source(os.path.splitdrive(str(root))[0] + os.sep)
        check("3 驱动器根链接被拒", False, "未抛错")
    except DataFolderError:
        check("3 驱动器根链接被拒", True)

    # 4. 链接一步完成 + 自动记入白名单
    src = df.link_source(str(root), alias="测试数据")
    check("4a 链接成功", src.source_id and len(src.datasets) > 0)
    check("4b 链接后自动进入白名单", str(root.resolve()) in df.list_allowlist())
    first_source_id = src.source_id
    # 重复链接幂等
    src2 = df.link_source(str(root), alias="测试数据")
    check("4c 重复链接幂等(同 id)", src2.source_id == first_source_id and len(df.list_sources()) == 1)

    # 5. 大 CSV
    big = by_id.get("big")
    check("5 大型 CSV 仅可查询+不统计行数", big is not None and big.status == DatasetStatus.query_only and big.feature_count is None)

    # 6. shapefile 配套合并：subway 应只有 1 个数据集，且不存在 .dbf/.shx/.prj 拆出的数据集
    shp_family = [d for d in datasets if "subway" in d.dataset_id or "subway" in d.rel_path]
    check("6 shapefile 配套合并为 1 个", len(shp_family) == 1)

    # 7. 坐标信息
    vit = by_id.get("vitality")
    sub = by_id.get("subway")
    check("7a CSV 坐标列", vit is not None and vit.coord_columns == ["lon", "lat"])
    check("7b shapefile CRS", sub is not None and sub.crs == "EPSG:4326")
    nocrs = by_id.get("nocrs")
    check("7c 无 .prj 标暂不可用", nocrs is not None and nocrs.status == DatasetStatus.unavailable)
    nogeo = by_id.get("nogeo")
    check("7d 无坐标 CSV 标表格图层", nogeo is not None and nogeo.status == DatasetStatus.enableable and nogeo.geometry == "table")

    # 8. 重启恢复：清缓存后从磁盘懒加载
    df._sources_cache = None
    restored = df.list_sources()
    check("8a 重启后数据源恢复", len(restored) == 1 and restored[0].source_id == first_source_id)
    check("8b 重启后数据集快照保留", len(restored[0].datasets) == len(src.datasets))

    # 9. 小数据启用（第二步）
    r = df.enable_dataset(first_source_id, "subway")
    check("9a 启用成功+注册图层", r.get("layer_id") == "subway" and DATA_REGISTRY.get_layer("subway") is not None)
    check("9b 启用后标记 enabled", next(d for d in df.get_source(first_source_id).datasets if d.dataset_id == "subway").enabled)
    # 大型 CSV 抽稀预览启用（query_only 数据）
    r_big = df.enable_dataset(first_source_id, "big")
    check("9c 大型 CSV 抽稀启用", r_big.get("layer_id") == "big" and r_big["feature_count"] >= 1)
    # 假 gdb 目录标 unavailable（format=gdb 且读取失败）
    fake_gdb = [d for d in df.get_source(first_source_id).datasets if d.format == "gdb"]
    check("9d 假 gdb 标 unavailable", len(fake_gdb) == 1 and fake_gdb[0].status == DatasetStatus.unavailable)
    r_nogeo = df.enable_dataset(first_source_id, "nogeo")
    lyr_nogeo = DATA_REGISTRY.get_layer("nogeo")
    check("9e 无坐标数据启用为表格图层",
          r_nogeo.get("layer_id") == "nogeo" and lyr_nogeo is not None and lyr_nogeo.geometry.value == "Table")

    # 10. 重扫 & 取消链接
    df.rescan_source(first_source_id)
    check("10a 重扫成功", getattr(df.get_source(first_source_id), "datasets", None) is not None)
    df.unlink_source(first_source_id)
    check("10b 取消链接后为空", len(df.list_sources()) == 0)

    # 11. 下载目录（第三阶段）
    dl_dir = Path(tempfile.mkdtemp(prefix="dl_dir_"))
    r_dl = df.set_download_folder(str(dl_dir))
    check("11a 设置下载目录", r_dl.get("source_id") and df.get_download_folder() == str(dl_dir.resolve()))
    dl_src = df.get_source(r_dl["source_id"])
    check("11b 下载目录自动链接为数据源", dl_src is not None and dl_src.alias == "下载数据")
    out = resolve_download_output(None, "poi_polygon", root, "amap")
    check("11c 下载落盘到下载目录", dl_dir.resolve() in out.parents)

    print(f"\n结果：{TOTAL - len(FAILS)}/{TOTAL} 通过" + (f"，失败：{FAILS}" if FAILS else ""))
    return 1 if FAILS else 0


if __name__ == "__main__":
    code = main()
    shutil.rmtree(_WS, ignore_errors=True)
    shutil.rmtree(_FIX, ignore_errors=True)
    sys.exit(code)
