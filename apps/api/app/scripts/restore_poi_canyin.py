# -*- coding: utf-8 -*-
"""恢复被误删的平台图层 app/platform_data/geojson/poi_餐饮.json。

从原始 shp（平台数据/POI2023/深圳市餐饮服务poi_2023/深圳市_餐饮服务_WGS84.shp）
经 build_platform_layers.build_one 同款逻辑重建（读 shp → EPSG:4326 → GeoJSON）。
只写 geojson 文件，不动 layers.json（注册记录仍在，feature_count=152789 用于比对）。

用法：.venv/Scripts/python.exe -X utf8 restore_poi_canyin.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # apps/api
from app.scripts.build_platform_layers import GEOJSON_DIR, _POI_COLORS, _poi_entry, build_one  # noqa: E402

entry = _poi_entry(
    "餐饮",
    _POI_COLORS[15],
    "餐饮服务点位（真实 POI 数据）",
    folder="深圳市餐饮服务poi_2023",
    shp="深圳市_餐饮服务_WGS84",
)

out = GEOJSON_DIR / "poi_餐饮.json"
print(f"源：{entry['path']}")
print(f"目标：{out}")

meta = build_one(entry)
if meta is None:
    print("恢复失败", file=sys.stderr)
    raise SystemExit(1)

expected = 152789  # layers.json 注册的 feature_count
size_mb = out.stat().st_size / 1024 / 1024
print(f"恢复：{meta['feature_count']} 要素  {meta['geometry']}  {size_mb:.1f}MB")
if meta["feature_count"] != expected:
    print(f"⚠️ 要素数 {meta['feature_count']} ≠ 注册值 {expected}，需同步更新 layers.json", file=sys.stderr)
else:
    print(f"✓ 要素数与 layers.json 注册一致（{expected}）")
print(f"✓ 文件已写回：{out}")
