# -*- coding: utf-8 -*-
"""临时探针：对比 kernel_density 候选默认参数组合，决定「默认调精」改哪些值。

对真实粤海街道餐饮 POI（engine.load_gdf 已是 UTM 米制）：
- 网格：100m（现状）/ 50m
- 带宽：scott（自动）/ 固定 50 / 100 / 150 米
对比：格数、scott 有效带宽估计、density max/mean。

用法：cd apps/api && .venv/Scripts/python.exe -X utf8 app/scripts/probe_kde_defaults.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
from scipy.stats import gaussian_kde  # noqa: E402
from app.gis import engine  # noqa: E402

p = engine.load_gdf("poi_餐饮")
a = engine.load_gdf("shenzhen_streets")
a = a[a["JDNAME"] == "粤海"].copy()

# 按 engine 相同裁剪逻辑取掩膜内点（UTM 米制）
cents = p.geometry.centroid
inside = p[cents.within(a.geometry.union_all())].copy()
print(f"全量点 {len(p)} → 粤海街道内 {len(inside)} 点\n")

coords = np.vstack([inside.geometry.centroid.x.values, inside.geometry.centroid.y.values])
kde_scott = gaussian_kde(coords)
std_data = float(np.sqrt(np.mean(np.var(coords, axis=1))))
bw_m = kde_scott.factor * std_data
print(f"scott 有效带宽 ≈ {bw_m:.0f} 米（factor={kde_scott.factor:.3f}，坐标 std_data={std_data:.0f} 米）")
print(f"固定带宽换算对照：X 米 → 缩放因子 = X / {std_data:.0f}\n")

print(f"{'网格':>5} {'带宽':>8} {'格数':>7} {'density_max':>12} {'density_mean':>12}  说明")
combos = [
    (100, None, "scott（现状默认）"),
    (50, None, "scott + 50m 网格"),
    (50, 50, "固定50m + 50m 网格"),
    (50, 100, "固定100m + 50m 网格"),
    (100, 100, "固定100m + 100m 网格"),
    (100, 150, "固定150m + 100m 网格"),
]
for cell, bw, label in combos:
    g, _gj, s = engine.kernel_density(p, a, cell_size_m=cell, bandwidth_m=bw)
    if g is None:
        print(f"{cell:>5} {str(bw):>8}  {'拒绝':>7}  {label}")
        continue
    # 非零格占比：直观反映热点是否铺满
    nz = (g["density"] > 0).sum()
    print(f"{cell:>5} {str(bw):>8} {len(g):>7} {s['max']:>12.1f} {s['mean']:>12.2f}  {label}（非零格 {nz}）")
