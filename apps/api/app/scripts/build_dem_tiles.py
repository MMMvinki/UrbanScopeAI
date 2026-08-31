"""生成深圳 30 米 DEM 的 Web Mercator 瓦片金字塔（真计算，rasterio + numpy）。

输入：平台数据/30米DEM/深圳市_DEM.tif（EPSG:3857，float32，nodata≈-3.4e38）
输出：
  - apps/api/app/workspace/.workdir/dem_tiles/{z}/{x}/{y}.png       → RGBA 高程着色瓦片（2D 平面/贴图）
  - apps/api/app/workspace/.workdir/dem_terrarium/{z}/{x}/{y}.png   → Terrarium 高程编码瓦片（3D 地形网格用，
      高程写入 RGB：R=高字节,G=低字节,B=0；MapLibre/deck 解码 height = R*256 + G + B/256 - 32768）
  - 并把 shenzhen_dem 图层的 bbox（EPSG:4326）写回 platform_data/layers.json

处理链路（全部真计算，无手工数值）：
1. 全图读入内存 → numpy 数组
2. 由高程导数计算 hillshade（坡向光照，立体感）
3. 高程 → 地形色带 LUT（matplotlib terrain，nodata 透明）；同时生成 terrarium 高程编码 RGBA
4. 逐瓦片（z=10..14）按 3857 瓦片边界裁剪窗口 → 缩放到 256×256 → 输出 PNG
5. 瓦片实际覆盖范围外区域保持透明（边缘瓦片）

用法：cd apps/api && ./.venv/Scripts/python.exe app/scripts/build_dem_tiles.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image

_REPO_ROOT = Path(__file__).resolve().parents[4]  # 城市数据分析平台与智能体/
_API_ROOT = Path(__file__).resolve().parents[2]  # apps/api/
DEM_TIF = _REPO_ROOT / "平台数据" / "30米DEM" / "深圳市_DEM.tif"
LAYERS_JSON = _API_ROOT / "app" / "platform_data" / "layers.json"
OUT_DIR = _API_ROOT / "app" / "workspace" / ".workdir" / "dem_tiles"
TERRAIN_DIR = _API_ROOT / "app" / "workspace" / ".workdir" / "dem_terrarium"

# Web Mercator 常数
WEB_MERC_EXTENT = 40075016.6855785  # 地球周长（3857 单位）
HALF_EXTENT = WEB_MERC_EXTENT / 2.0

ZOOM_RANGE = range(10, 15)  # z10..z14（z14 ≈ 9.6m/px，接近 DEM 原生 ~9.7m/px）
TILE_SIZE = 256

_TERRAIN_CMAP = None


# 海面（高程 ≤ 0）固定浅蓝，与陆地绿棕区分
SEA_COLOR = np.array([72, 128, 184], np.uint8)


def _colormap() -> np.ndarray:
    """陆地高程色带 LUT：0..1 → RGBA（256 行）。

    terrain 色带低端是海蓝，但深圳主体是 30-80m 低地；改取 terrain 中高段
    （绿→黄绿→棕→灰白），让低地呈现绿色、高地呈棕白，海面单独浅蓝。
    """
    global _TERRAIN_CMAP
    if _TERRAIN_CMAP is None:
        import matplotlib

        lut = (matplotlib.colormaps["terrain"](np.linspace(0.25, 1.0, 256))[:, :3] * 255).astype(
            np.uint8
        )
        _TERRAIN_CMAP = np.concatenate([lut, np.full((256, 1), 255, np.uint8)], axis=1)
    return _TERRAIN_CMAP


def _hillshade(elev: np.ndarray, az_deg: float = 315.0, alt_deg: float = 45.0) -> np.ndarray:
    """标准 hillshade：高程导数 → 坡向/坡度 → 光照强度 0..255。

    az_deg：太阳方位角（北 0° 顺时针），alt_deg：太阳高度角。
    """
    gy, gx = np.gradient(elev)
    slope = np.arctan(np.sqrt(gx * gx + gy * gy))  # 坡度（弧度）
    aspect = np.arctan2(-gx, gy)  # 坡向（北 0°，东 90°）
    az = math.radians(az_deg)
    alt = math.radians(alt_deg)
    intensity = np.cos(alt) * np.cos(slope) + np.sin(alt) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(intensity * 255, 0, 255).astype(np.uint8)


def _terrarium_rgba(elev: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """float32 高程 → Terrarium 高程编码 RGBA（R=高字节, G=低字节, B=0, A=255）。

    编码：offset = round(e) + 32768（16 位，范围 [-32768, 32767] 米）；R = offset>>8，G = offset&0xFF。
    解码（MapLibre / deck.gl elevationDecoder，terrarium）：
        height = R*256 + G + B/256 - 32768
    无效/缺失区编码为海平面 0（offset=32768 → r=128,g=0,b=0），保证 3D 地形网格跨覆盖边界连续，
    不会在 DEM 边缘出现塌陷/悬空。返回 (H, W, 4) uint8。
    """
    # 有效区取整，缺失区置 0（海平面）；+32768 后夹到 16 位
    off = np.where(valid, np.round(elev), 0.0).astype(np.float64) + 32768.0
    off16 = np.clip(np.round(off), 0, 65535).astype(np.uint16)
    r = (off16 >> 8).astype(np.uint8)
    g = (off16 & 0xFF).astype(np.uint8)
    b = np.zeros(r.shape, dtype=np.uint8)
    a = np.full(r.shape, 255, dtype=np.uint8)
    return np.stack([r, g, b, a], axis=-1)


def _tile_bounds_3857(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    """瓦片 (z,x,y) 的 Web Mercator 边界（xmin, ymin, xmax, ymax）。"""
    n = 2 ** z
    size = WEB_MERC_EXTENT / n
    xmin = -HALF_EXTENT + x * size
    xmax = xmin + size
    ymax = HALF_EXTENT - y * size
    ymin = ymax - size
    return xmin, ymin, xmax, ymax


def _to_4326(x3857: float, y3857: float) -> tuple[float, float]:
    lon = x3857 / HALF_EXTENT * 180.0
    lat = math.degrees(2.0 * math.atan(math.exp(y3857 / HALF_EXTENT * math.pi)) - math.pi / 2.0)
    return lon, lat


def main() -> int:
    if not DEM_TIF.exists():
        print(f"找不到 DEM 源文件：{DEM_TIF}")
        return 1

    with rasterio.open(DEM_TIF) as src:
        left, bottom, right, top = src.bounds
        width, height = src.width, src.height
        nodata = src.nodata
        elev = src.read(1)

    valid = ~np.isclose(elev, nodata) & np.isfinite(elev)
    print(
        f"DEM {width}x{height}，nodata={nodata}，有效高程 {elev[valid].min():.0f}..{elev[valid].max():.0f} m"
    )

    # nodata 先置 0 再算梯度，避免跨无效区溢出；最终用 valid 掩码透明掉
    elev_work = np.where(valid, elev, 0.0).astype(np.float32)
    hs = _hillshade(elev_work)
    lut = _colormap()

    # 陆地高程 → 色带索引（0..255）；海面（≤0）→ 固定浅蓝
    land = valid & (elev > 0)
    sea = valid & (elev <= 0)
    elev_max = float(elev_work[valid].max())  # ~938m
    norm = np.zeros_like(elev, dtype=np.uint8)
    norm[land] = np.clip((elev[land] / elev_max) * 255.0, 0, 255).astype(np.uint8)
    rgb = lut[norm]  # (H, W, 4)
    rgb[sea] = (*SEA_COLOR, 255)
    rgb = (rgb.astype(np.float32) * (0.5 + 0.5 * hs[..., None] / 255.0)).astype(np.uint8)
    rgba = np.concatenate([rgb[..., :3], (valid * 255).astype(np.uint8)[..., None]], axis=2)
    terr_rgba = _terrarium_rgba(elev, valid)  # (H, W, 4) 高程编码 RGBA
    # terrarium 解码下限对应的 offset：把插值下冲焊在源有效下限（≈-72m），不产生塌陷尖刺
    terr_floor = int(round(float(elev_work[valid].min()))) + 32768

    pixel = (right - left) / width  # 3857 米/像素（行同列）

    def _make_tile(
        ar: np.ndarray,
        z: int,
        x: int,
        y: int,
        empty: tuple[int, int, int, int],
        resample: int = Image.LANCZOS,
        clamp_offset: int | None = None,
    ) -> Image.Image:
        """把全图 RGBA 数组 ar 按瓦片 (z,x,y) 裁剪、保持几何对齐贴到 256×256。

        empty 为无数据处（瓦片背景）的 RGBA：彩色瓦片用全透明 (0,0,0,0)；
        terrarium 用海平面编码 (128,0,0,255)，避免地形边缘解码成 -32768 的向下尖刺。
        resample：缩放插值。彩色瓦片用 LANCZOS（视觉更锐）；terrarium 用 BILINEAR
        （振铃更小）。
        clamp_offset：非 None 时（terrarium）把裁剪后解码高程的下限焊死。缩放插值在海岸
        崖边（高山像素 vs 海面像素）会重建出低于真实下限的假高程（源有效下限 -72m → 插值
        可冲低到 ~-170m），把 offset 钳到源有效下限对应的值，保证 3D 网格不在海岸线塌陷。
        """
        size = WEB_MERC_EXTENT / (2 ** z)
        txmin, tymin, txmax, tymax = _tile_bounds_3857(z, x, y)
        if txmax < left or txmin > right or tymax < bottom or tymin > top:
            return Image.new("RGBA", (TILE_SIZE, TILE_SIZE), empty)
        c0 = max(0, int(math.floor((txmin - left) / pixel)))
        c1 = min(width, int(math.ceil((txmax - left) / pixel)))
        r0 = max(0, int(math.floor((top - tymax) / pixel)))
        r1 = min(height, int(math.ceil((top - tymin) / pixel)))
        if c1 <= c0 or r1 <= r0:
            return Image.new("RGBA", (TILE_SIZE, TILE_SIZE), empty)
        tile = Image.new("RGBA", (TILE_SIZE, TILE_SIZE), empty)
        img = Image.fromarray(ar[r0:r1, c0:c1])
        px0 = int(round((txmin - (left + c0 * pixel)) / pixel / (size / pixel) * TILE_SIZE))
        px1 = int(round((txmax - (left + c0 * pixel)) / pixel / (size / pixel) * TILE_SIZE))
        py0 = int(round((top - r0 * pixel - tymax) / pixel / (size / pixel) * TILE_SIZE))
        py1 = int(round((top - r0 * pixel - tymin) / pixel / (size / pixel) * TILE_SIZE))
        px0, px1 = max(0, min(TILE_SIZE, px0)), max(0, min(TILE_SIZE, px1))
        py0, py1 = max(0, min(TILE_SIZE, py0)), max(0, min(TILE_SIZE, py1))
        if px1 > px0 and py1 > py0:
            img = img.resize((px1 - px0, py1 - py0), resample)
            if clamp_offset is not None:
                a = np.asarray(img, dtype=np.uint8).copy()
                off = a[..., 0].astype(np.int32) * 256 + a[..., 1]
                off = np.maximum(off, clamp_offset)  # 焊死解码下限，防海岸插值塌陷
                a[..., 0] = (off >> 8).astype(np.uint8)
                a[..., 1] = (off & 0xFF).astype(np.uint8)
                a[..., 2] = 0  # B 恒 0（terrarium）
                img = Image.fromarray(a)
            tile.paste(img, (px0, py0))
        return tile

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    TERRAIN_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for z in ZOOM_RANGE:
        n = 2 ** z
        size = WEB_MERC_EXTENT / n
        # 该层级下 DEM 覆盖的瓦片范围
        x0 = max(0, math.floor((left - (-HALF_EXTENT)) / size))
        x1 = min(n - 1, math.ceil((right - (-HALF_EXTENT)) / size))
        y0 = max(0, math.floor((HALF_EXTENT - top) / size))
        y1 = min(n - 1, math.ceil((HALF_EXTENT - bottom) / size))
        zcolor = OUT_DIR / str(z)
        zterrain = TERRAIN_DIR / str(z)
        zcolor.mkdir(parents=True, exist_ok=True)
        zterrain.mkdir(parents=True, exist_ok=True)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                _make_tile(rgba, z, x, y, (0, 0, 0, 0)).save(zcolor / f"{x}_{y}.png")
                _make_tile(terr_rgba, z, x, y, (128, 0, 0, 255), Image.BILINEAR, terr_floor).save(
                    zterrain / f"{x}_{y}.png"
                )
                total += 1
        print(f"z={z}: 瓦片 {x1 - x0 + 1}x{y1 - y0 + 1} → {OUT_DIR / str(z)} + {TERRAIN_DIR / str(z)}")

    # 把 DEM 图层 bbox 写回 layers.json（EPSG:4326）
    lon0, lat0 = _to_4326(left, bottom)
    lon1, lat1 = _to_4326(right, top)
    bbox = [round(lon0, 5), round(lat0, 5), round(lon1, 5), round(lat1, 5)]
    raw = json.loads(LAYERS_JSON.read_text(encoding="utf-8"))
    for item in raw:
        if item.get("id") == "shenzhen_dem":
            item["bbox"] = bbox
    LAYERS_JSON.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"共输出 {total} 瓦片；bbox={bbox} 已写入 layers.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
