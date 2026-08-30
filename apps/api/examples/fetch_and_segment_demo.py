# -*- coding: utf-8 -*-
"""街景绿视率/天空率 —— 独立验证脚本（不依赖 skill_runner、不写图层注册表）。

用途：给定一串 WGS-84 坐标点，逐个拉百度全景静态图 → fastseg(Cityscapes 19 类)分割
      → 算植被/天空/建筑/道路占比，逐点打印 + 存一张分割着色蒙太奇 + 点 GeoJSON。

和技能 main.py 的区别：
  - 这里坐标/采样与 road 无关，直接给点清单（便于人工抽查某几条街道）；
  - 不产出 output.json / 不注册图层，仅打印核对 + 存图存文件。

用法（先在 apps/api/.env 设 BAIDU_AK，或 --ak 传）：
    ./.venv/Scripts/python.exe examples/fetch_and_segment_demo.py \
        --lnglat "116.397428,39.90923" "116.400,39.911" ... --hdg 0,90,180,270 --out demo_out
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 直连技能里的公共模块（需要脚本目录在 sys.path 上）
SKILL_DIR = Path(__file__).resolve().parent.parent / "app" / "skills" / "builtin" / "street-view-metrics" / "scripts"
sys.path.insert(0, str(SKILL_DIR))

import numpy as np  # noqa: E402

from panorama import (  # noqa: E402
    colorize,
    fetch_panorama,
    get_ak,
    green_and_sky,
    load_model,
    segment_image,
)


def main() -> None:
    ap = argparse.ArgumentParser(description="街景绿视率/天空率独立验证")
    ap.add_argument("--lnglat", nargs="+", required=True, help="WGS-84 点：\"lng,lat\" 可多个")
    ap.add_argument("--hdg", default="0,90,180,270", help="朝向角度列表（逗号分隔），默认 4 朝向")
    ap.add_argument("--fov", type=float, default=90, help="水平视角，默认 90")
    ap.add_argument("--pitch", type=float, default=-5, help="垂直视角，默认 -5")
    ap.add_argument("--width", type=int, default=1024, help="取图宽度，默认 1024")
    ap.add_argument("--height", type=int, default=512, help="取图高度，默认 512")
    ap.add_argument("--ak", default=None, help="百度服务端 AK；缺省读 BAIDU_AK 环境变量/.env")
    ap.add_argument("--out", default="demo_out", help="输出目录，默认 demo_out/")
    args = ap.parse_args()

    ak = get_ak(args.ak)
    if not ak:
        print("[FAIL] 未配置百度 AK：在 apps/api/.env 设 BAIDU_AK，或 --ak 传。")
        sys.exit(1)

    headings = [int(h) for h in args.hdg.split(",") if h.strip() != ""]
    points = []
    for s in args.lnglat:
        lng, lat = s.split(",")[:2]
        points.append((float(lng), float(lat)))
    print(f"模型加载中（首次会下载权重，之后走缓存）...")
    model = load_model()
    print(f"共 {len(points)} 点 × {len(headings)} 朝向，AK 已配置，开始取图分割。\n")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for i, (lng, lat) in enumerate(points):
        agg: dict[str, float] = {}
        n_ok = 0
        tiles = []
        for hd in headings:
            img, meta = fetch_panorama(lng, lat, ak=ak, heading=hd, pitch=args.pitch,
                                       fov=args.fov, width=args.width, height=args.height)
            if img is None:
                print(f"  [{i}] ({lng:.5f},{lat:.5f}) hdg={hd} 取图失败 err={meta.get('error')} {str(meta.get('message'))[:60]}")
                continue
            ratios, labels = segment_image(model, img)
            for name, r in ratios.items():
                agg[name] = agg.get(name, 0.0) + r
            n_ok += 1
            tiles.append(colorize(labels))
        if n_ok == 0:
            print(f"  [{i}] ({lng:.5f},{lat:.5f}) 全部朝向取图失败，跳过")
            continue
        agg = {name: v / n_ok for name, v in agg.items()}
        m = green_and_sky(agg)
        rows.append({
            "lng": lng, "lat": lat, "n_views": n_ok,
            "green_view_ratio": round(m["green_view_ratio"], 4),
            "sky_view_factor": round(m["sky_view_factor"], 4),
            "building_ratio": round(m["building_ratio"], 4),
            "road_ratio": round(m["road_ratio"], 4),
            "sidewalk_ratio": round(m["sidewalk_ratio"], 4),
            "terrain_ratio": round(m["terrain_ratio"], 4),
        })
        print(f"  [{i}] ({lng:.5f},{lat:.5f}) n={n_ok} 绿={m['green_view_ratio']:.3f} "
              f"天空={m['sky_view_factor']:.3f} 建={m['building_ratio']:.3f} 路={m['road_ratio']:.3f}")
        # 蒙太奇拼接本点各朝向的分割着色图
        if tiles:
            _save_montage(tiles, out_dir / f"seg_{i}_{lng:.5f}_{lat:.5f}.png")

    if not rows:
        print("\n[FAIL] 没有任何点取到图（覆盖不全/坐标无全景），请换点或核对 AK/权益。")
        sys.exit(2)

    # 汇总 + 写 GeoJSON
    g = np.mean([r["green_view_ratio"] for r in rows])
    s = np.mean([r["sky_view_factor"] for r in rows])
    print(f"\n==== 汇总 ====")
    print(f"平均绿视率 = {g:.3f}；平均天空可视因子 = {s:.3f}；样本 {len(rows)} 点")
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [r["lng"], r["lat"]]},
            "properties": {k: v for k, v in r.items() if k not in ("lng", "lat")},
        }
        for r in rows
    ]
    gj = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": "EPSG:4490"}},
          "features": features}
    gj_path = out_dir / "points.geojson"
    gj_path.write_text(json.dumps(gj, ensure_ascii=False), encoding="utf-8")
    print(f"点 GeoJSON 已写：{gj_path}")


def _save_montage(tiles, path) -> None:
    """多张分割着色图拼接为一张（点各朝向横向拼），便于人眼核对。"""
    try:
        from PIL import Image
        h = tiles[0].height
        w = sum(t.width for t in tiles)
        canvas = Image.new("RGB", (w, h))
        x = 0
        for t in tiles:
            canvas.paste(t, (x, 0))
            x += t.width
        canvas.save(path)
        print(f"    蒙太奇已存：{path.name}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
