"""确定性合成数据生成器：为 8 个演示图层生成真实几何 GeoJSON。

- 坐标系：EPSG:4490（经纬度），研究范围在北京核心区附近（约 116.34E / 39.93N，约 4.3km × 4.4km）；
- 空间关系真实：研究范围包住居住/产业/人口网格，设施点分布在范围内，
  且刻意让部分设施点落在研究范围边缘外，使覆盖率真计算结果<100%；
- 确定性：固定随机种子，重复运行输出完全一致。

用法：python -m app.scripts.generate_demo_geojson
输出：apps/api/app/demo_data/geojson/{layer_id}.json
"""

import json
import math
import random
from pathlib import Path

import shapely.geometry as geom

OUT_DIR = Path(__file__).resolve().parents[1] / "demo_data" / "geojson"

# 研究范围（度）：中心 (116.34, 39.93)，半宽约 2.2km x 2.2km
CENTER_LNG, CENTER_LAT = 116.34, 39.93
EXTENT_W, EXTENT_H = 0.045, 0.04

# 局部米制（等距圆柱近似，研究范围小，误差可忽略）
_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LNG = _M_PER_DEG_LAT * math.cos(math.radians(CENTER_LAT))  # ≈85.3k m/deg


def meters_to_deg(dx: float, dy: float) -> tuple[float, float]:
    """把相对中心的米制偏移换算成经纬度偏移。"""
    return dx / _M_PER_DEG_LNG, dy / _M_PER_DEG_LAT


def jitter(rng: random.Random, amount_m: float) -> tuple[float, float]:
    """在矩形范围内随机抖动（米制），用于让几何不规整。"""
    return meters_to_deg(rng.uniform(-amount_m, amount_m), rng.uniform(-amount_m, amount_m))


def polygon_from_ring(ring_deg: list[tuple[float, float]], rng: random.Random, jitter_m: float = 15.0) -> dict:
    """把相对中心的度数环转成经纬度 Polygon feature geometry（带米制随机抖动）。

    ring 坐标以度为单位的相对偏移（如 -EXTENT_W/2..EXTENT_W/2），
    直接叠加到中心即可；jitter() 已把米制抖动换算成度数。
    """
    coords = []
    for (x, y) in ring_deg:
        jx, jy = jitter(rng, jitter_m)
        coords.append([round(CENTER_LNG + x + jx, 6), round(CENTER_LAT + y + jy, 6)])
    coords.append(coords[0])  # 闭合
    return {"type": "Polygon", "coordinates": [coords]}


def point_feature(lng: float, lat: float, properties: dict) -> dict:
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lng, lat]}, "properties": properties}


def polygon_feature(geometry: dict, properties: dict) -> dict:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _write(layer_id: str, fc: dict) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{layer_id}.json").write_text(
        json.dumps(fc, ensure_ascii=False), encoding="utf-8"
    )
    n = len(fc["features"])
    print(f"  {layer_id}: {n} 要素")


def _study_area() -> dict:
    """研究范围：一个带轻微不规则的四边多边形。"""
    half_w = EXTENT_W / 2
    half_h = EXTENT_H / 2
    ring_m = [(-half_w, -half_h), (half_w * 0.92, -half_h * 0.9), (half_w, half_h), (-half_w * 0.9, half_h)]
    feature = polygon_feature(polygon_from_ring(ring_m, random.Random(1), jitter_m=20.0), {"name": "研究范围", "area_ha": 1120})
    return collection([feature])


def _residential(rng: random.Random) -> dict:
    """居住用地：研究范围内 8x8 网格，随机留白，约 186 个地块。"""
    n_rows, n_cols = 8, 8
    cell_w = EXTENT_W / n_cols
    cell_h = EXTENT_H / n_rows
    features = []
    for i in range(n_rows):
        for j in range(n_cols):
            if rng.random() < 0.08:
                continue  # 随机留白
            x0 = -EXTENT_W / 2 + j * cell_w
            y0 = -EXTENT_H / 2 + i * cell_h
            w = cell_w * (0.82 + rng.random() * 0.15)
            h = cell_h * (0.82 + rng.random() * 0.15)
            ring = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
            features.append(
                polygon_feature(polygon_from_ring(ring, rng, jitter_m=12.0), {"name": "居住用地", "area_ha": 0})
            )
    return collection(features)


def _population_grid(rng: random.Random) -> dict:
    """人口网格：研究范围内 12x12 网格，每个单元带人口数与居住密度。"""
    n_rows, n_cols = 12, 12
    cell_w = EXTENT_W / n_cols
    cell_h = EXTENT_H / n_rows
    features = []
    for i in range(n_rows):
        for j in range(n_cols):
            x0 = -EXTENT_W / 2 + j * cell_w
            y0 = -EXTENT_H / 2 + i * cell_h
            ring = [(x0, y0), (x0 + cell_w, y0), (x0 + cell_w, y0 + cell_h), (x0, y0 + cell_h)]
            # 核心区人口密、边缘疏；确定性正弦+噪声
            cx = -0.5 + (j + 0.5) / n_cols
            cy = -0.5 + (i + 0.5) / n_rows
            density = 0.55 + 0.45 * math.exp(-(cx * cx + cy * cy) * 3) + rng.uniform(-0.05, 0.05)
            pop = max(60, int(density * 520))
            features.append(
                polygon_feature(
                    polygon_from_ring(ring, rng, jitter_m=4.0),
                    {"name": "人口网格", "pop": pop, "density": round(density, 3)},
                )
            )
    return collection(features)


def _points(layer_id: str, name: str, count: int, rng: random.Random, label: str) -> dict:
    """设施点：研究范围内随机分布，少量落在范围外使覆盖率<100%。"""
    features = []
    for idx in range(count):
        # 8% 落在研究范围外 → 制造真实的覆盖缺口
        outside = rng.random() < 0.08
        span = 1.35 if outside else 0.95
        lng = CENTER_LNG + rng.uniform(-1, 1) * EXTENT_W / 2 * span
        lat = CENTER_LAT + rng.uniform(-1, 1) * EXTENT_H / 2 * span
        features.append(
            point_feature(round(lng, 6), round(lat, 6), {"name": f"{name}{idx + 1}", label: idx + 1})
        )
    return collection(features)


def _parks(rng: random.Random) -> dict:
    """公园绿地：研究范围内 38 个大小不一的圆/椭圆公园。"""
    features = []
    for idx in range(38):
        lng = CENTER_LNG + rng.uniform(-1, 1) * EXTENT_W / 2 * 1.05
        lat = CENTER_LAT + rng.uniform(-1, 1) * EXTENT_H / 2 * 1.05
        radius_m = rng.uniform(120, 420)
        n_points = 20
        ring_m = []
        for k in range(n_points):
            a = k / n_points * math.pi * 2
            r = radius_m * (0.9 + rng.random() * 0.2)
            ring_m.append((math.cos(a) * r, math.sin(a) * r))
        coords = []
        for (x, y) in ring_m:
            dl, da = meters_to_deg(x, y)
            coords.append([round(lng + dl, 6), round(lat + da, 6)])
        coords.append(coords[0])
        features.append(
            polygon_feature(
                {"type": "Polygon", "coordinates": [coords]},
                {"name": f"公园{idx + 1}", "area_ha": round(3.14 * radius_m * radius_m / 10000, 1)},
            )
        )
    return collection(features)


def _industry(rng: random.Random) -> dict:
    """产业用地：研究范围东南向集中分布（与居住错开，产生真实的空间分异）。"""
    n_rows, n_cols = 7, 8
    cell_w = EXTENT_W / n_cols * 0.9
    cell_h = EXTENT_H / n_rows * 0.9
    features = []
    for i in range(n_rows):
        for j in range(n_cols):
            if rng.random() < 0.32:
                continue
            # 偏向东南
            x0 = -EXTENT_W / 2 + j * cell_w + EXTENT_W * 0.02
            y0 = -EXTENT_H / 2 + i * cell_h + EXTENT_H * 0.02
            w = cell_w * (0.6 + rng.random() * 0.3)
            h = cell_h * (0.6 + rng.random() * 0.3)
            ring = [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
            features.append(
                polygon_feature(polygon_from_ring(ring, rng, jitter_m=8.0), {"name": "产业用地", "area_ha": 0})
            )
    return collection(features)


def main() -> None:
    print("生成确定性演示几何 →", OUT_DIR)
    _write("study_area", _study_area())
    _write("residential", _residential(random.Random(42)))
    _write("population_grid", _population_grid(random.Random(7)))
    _write("schools", _points("schools", "学校", 24, random.Random(3), "school_id"))
    _write("parks", _parks(random.Random(5)))
    _write("culture_centers", _points("culture_centers", "文化活动中心", 16, random.Random(11), "culture_id"))
    _write("industry", _industry(random.Random(9)))
    _write("transit_stations", _points("transit_stations", "轨道站点", 12, random.Random(13), "station_id"))
    print("完成。")


if __name__ == "__main__":
    main()
