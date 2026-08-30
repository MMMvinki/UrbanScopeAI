# -*- coding: utf-8 -*-
"""街景绿视率 / 天空可视因子 —— 公共函数（百度全景取图 + fastseg 语义分割 + 类别占比聚合）。

纯函数、无 argparse / 无 --input/--output I/O，供 scripts/main.py（技能入口）与
examples/fetch_and_segment_demo.py（直跑验证）复用。运行环境为 skill_runner / execute_code
子进程（PYTHONPATH 含 app 根，可 ``from app.agent import llm_client`` 读环境密钥）。

依赖见 ../requirements.txt；torch/fastseg 体积大，首次用 ensure_deps() 自动 pip 安装。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

# ── Cityscapes 19 类（fastseg 官方顺序，被 image/palette.py 的调色板逐条佐证）──
# 0 road,1 sidewalk,2 building,3 wall,4 fence,5 pole,6 traffic_light,7 traffic_sign,
# 8 vegetation,9 terrain,10 sky,11 person,12 rider,13 car,14 truck,15 bus,16 train,
# 17 motorcycle,18 bicycle
CLASS_NAMES = [
    "road", "sidewalk", "building", "wall", "fence", "pole", "traffic_light",
    "traffic_sign", "vegetation", "terrain", "sky", "person", "rider", "car",
    "truck", "bus", "train", "motorcycle", "bicycle",
]
GREEN_CLASSES = (8,)   # vegetation（树+草+灌木）→ 绿视率分子
SKY_CLASSES = (10,)    # sky → 天空可视因子分子

_PANO_URL = "https://api.map.baidu.com/panorama/v2"
_OVERPOST_URL = "https://overpass-api.de/api/interpreter"
_UA = {"User-Agent": "UrbanScopeAI/0.1 (urbanscope-app)"}
_DEPS_TO_ENSURE = ("torch", "fastseg")

# 取图默认参数：百度宽高上限 4096x512；street-view 用 1024x512（略宽，贴近人眼横视场）
_DEFAULT_WIDTH = 1024
_DEFAULT_HEIGHT = 512


# ── 依赖/模型 ──────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def ensure_deps() -> None:
    """缺失 torch/fastseg 时自动 pip 安装（子进程里跑，存进同一 venv）。失败不阻塞。"""
    missing = [p for p in _DEPS_TO_ENSURE if importlib.util.find_spec(p) is None]
    if not missing:
        return
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", *missing],
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        pass  # 让后续 import 自然失败暴露真实错误


@lru_cache(maxsize=1)
def load_model() -> Any:
    """加载 fastseg（MobileNetV3-Large，Cityscapes 19 类预训练）模型。CPU。"""
    ensure_deps()
    from fastseg import MobileV3Large  # 模型类（fastseg 项目名是 fastseg，类即 MobileV3Large）

    return MobileV3Large.from_pretrained()  # 自动下载 Cityscapes 权重到 torch 缓存


def _ensure_min_size(image: Any, min_w: int = 1024, min_h: int = 512) -> Any:
    """fastseg 的 predict 不做 resize，LR-ASPP 全局池化对输入有最小尺寸要求，
    过小会抛 ''output size is too small''。此处把小于安全尺寸的图放大到 ≥1024x512。
    （百度取图默认 1024x512 恰好达标；合成/缩略图小于此才会被放大。）"""
    if image.width >= min_w and image.height >= min_h:
        return image
    return image.resize((max(image.width, min_w), max(image.height, min_h)))


def segment_image(model: Any, image: Any) -> tuple[dict[str, float], Any]:
    """对一张 PIL RGB 图做语义分割。

    返回 (class_ratios, label_map)：
    - class_ratios：{类别名: 像素占比}（合计≈1）
    - label_map：np.ndarray (H, W)，每像素 Cityscapes 19 类 id（用于存配色图/蒙太奇）
    """
    import numpy as np

    image = _ensure_min_size(image)
    labels = model.predict([image], device="cpu")[0]  # (H, W) int 类别 id
    total = labels.size
    counts = np.bincount(labels.ravel(), minlength=len(CLASS_NAMES))
    ratios = {name: float(counts[i]) / total for i, name in enumerate(CLASS_NAMES)}
    return ratios, labels


def green_and_sky(ratios: dict[str, float]) -> dict[str, float]:
    """从全类别占比里抽出常用观测量。"""
    return {
        "green_view_ratio": sum(ratios.get(CLASS_NAMES[i], 0.0) for i in GREEN_CLASSES),
        "sky_view_factor": sum(ratios.get(CLASS_NAMES[i], 0.0) for i in SKY_CLASSES),
        "building_ratio": ratios.get("building", 0.0),
        "road_ratio": ratios.get("road", 0.0),
        "sidewalk_ratio": ratios.get("sidewalk", 0.0),
        "terrain_ratio": ratios.get("terrain", 0.0),
    }


def colorize(labels: Any) -> Any:
    """类别 id 数组 → 配色后的 PIL 分割图（存图/蒙太奇用）。"""
    import numpy as np
    from fastseg.image import colorize as _colorize

    return _colorize(labels.astype(np.uint8), palette="cityscapes")


# ── 百度全景取图 ──────────────────────────────────────────────────────


def _ensure_app_importable() -> None:
    """把 app 根插到 sys.path（get_ak 要从 app.agent 读 .env 密钥）。

    技能子进程由 store.run 注入 app 根所以没问题；但示例脚本从 examples/ 直跑、
    或未来其它入口时，app 根不在 path，需自行定位。定位过程：先插再探测导入，
    失败才继续上溯（从 scripts 上一层到 app 根：parents[4]）。
    """
    try:
        import app.agent  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    script_dir = Path(__file__).resolve().parent  # .../scripts
    for _ in range(6):  # scripts->技能->builtin->skills->app->apps，留余量上溯
        root = script_dir.parent
        if root.joinpath("app", "agent").is_dir():
            sys.path.insert(0, str(root))
            return
        script_dir = root


def get_ak(override: str | None = None) -> str:
    """百度服务端 AK：显式参数 > 进程 env > apps/api/.env。"""
    if override and str(override).strip():
        return str(override).strip()
    _ensure_app_importable()
    from app.agent import llm_client

    return llm_client._env("BAIDU_AK", "").strip()


def fetch_panorama(
    lng: float,
    lat: float,
    *,
    ak: str = "",
    heading: float = 0,
    pitch: float = -5,
    fov: float = 90,
    width: int = _DEFAULT_WIDTH,
    height: int = _DEFAULT_HEIGHT,
    timeout: int = 30,
) -> tuple[Any, dict]:
    """拉取一张百度全景静态图。

    返回 (PIL.Image, meta)：成功时 image 非 None、meta["error"]=None；
    失败时 image=None、meta 含 error 码（402=该点无全景覆盖 / 网络 / 解码）。
    坐标平台 WGS-84 直传（coordtype=wgs84ll），免 GCJ-02/BD-09 转换。
    """
    if not ak:
        return None, {"error": "NO_AK", "message": "未配置 BAIDU_AK"}
    import requests

    params = {
        "ak": ak,
        "coordtype": "wgs84ll",
        "location": f"{lng:.6f},{lat:.6f}",
        "width": width,
        "height": height,
        "heading": int(heading) % 360,
        "pitch": int(pitch),
        "fov": int(round(fov)),
    }
    try:
        resp = requests.get(_PANO_URL, params=params, headers=_UA, timeout=timeout)
    except requests.RequestException as exc:
        return None, {"error": "NETWORK", "message": str(exc)[:200]}
    if resp.status_code != 200:
        return None, {"error": f"HTTP_{resp.status_code}", "message": resp.text[:120]}

    body = resp.content
    ctype = resp.headers.get("content-type", "").lower()
    # 成功返回图片字节；失败返回 JSON 错误（返回码见百度文档：402=该点无 panoid）
    looks_image = body[:8].lstrip().lower().startswith((b"\xff\xd8", b"\x89pn"))
    if (not ctype or "image" in ctype) and looks_image:
        try:
            from io import BytesIO

            from PIL import Image

            img = Image.open(BytesIO(body))
            img.load()
            return img.convert("RGB"), {"error": None}
        except Exception as exc:
            return None, {"error": "DECODE", "message": str(exc)[:200]}

    # 非图片 → 尽力解析 JSON 错误码
    message = ""
    code = None
    try:
        data = json.loads(body.decode("utf-8", errors="replace"))
        if isinstance(data, dict):
            code = data.get("status", data.get("error", code))
            message = str(data.get("message") or data.get("msg") or data.get("error") or data)[:200]
    except Exception:
        message = body.decode("utf-8", errors="replace")[:200]
    return None, {"error": f"PANO_{code or 'ERR'}", "message": message}


# ── 路网采样 ──────────────────────────────────────────────────────────


def _utm_epsg(lon: float, lat: float) -> int:
    zone = int((lon + 180.0) / 6.0) + 1
    return 32600 + zone if lat >= 0 else 32700 + zone


def densify_line(lonlat: list[list[float]], spacing_m: float) -> list[list[float]]:
    """把一条折线（WGS-84 坐标 [lng,lat] 列表）按 spacing_m 均匀布点，回 WGS-84。"""
    from pyproj import Transformer
    from shapely.geometry import LineString

    if len(lonlat) < 2:
        return []
    mean_lon = sum(p[0] for p in lonlat) / len(lonlat)
    mean_lat = sum(p[1] for p in lonlat) / len(lonlat)
    epsg = _utm_epsg(mean_lon, mean_lat)
    to_m = Transformer.from_crs(4326, epsg, always_xy=True)
    to_wgs = Transformer.from_crs(epsg, 4326, always_xy=True)

    line_m = LineString([to_m.transform(lng, lat) for lng, lat in lonlat])
    if line_m.is_empty or line_m.length <= 0:
        return []
    pts: list[list[float]] = []
    d = 0.0
    length = line_m.length
    while d <= length + 1e-6:
        p = line_m.interpolate(d)
        lng, lat = to_wgs.transform(p.x, p.y)
        pts.append([round(lng, 6), round(lat, 6)])
        d += spacing_m
    return pts


def densify_geometry(geom: Any, spacing_m: float) -> list[list[float]]:
    """任意线几何 → 采样点列表。处理 LineString / MultiLineString。"""
    if geom is None:
        return []
    gtype = geom.get("type") if isinstance(geom, dict) else None
    coords = geom.get("coordinates") if isinstance(geom, dict) else None
    if coords is None:
        return []
    out: list[list[float]] = []
    if gtype == "LineString":
        out.extend(densify_line([list(c) for c in coords], spacing_m))
    elif gtype == "MultiLineString":
        for line in coords:
            out.extend(densify_line([list(c) for c in line], spacing_m))
    return out


def fetch_osm_roads(bbox: list[float], timeout: int = 90) -> list[dict]:
    """从 Overpass 抓研究范围内可行驶道路（LineString GeoJSON 未裁剪的原始要素）。

    只抓主干/次干/支路/居住区等典型城市道路，控制体量与时长。
    bbox=[西, 南, 东, 北]（WGS-84）。
    """
    import requests
    from shapely.geometry import LineString, mapping

    west, south, east, north = bbox
    highway_pat = (
        "motorway|trunk|primary|secondary|tertiary|unclassified|residential|"
        "living_street|motorway_link|trunk_link|primary_link|secondary_link|tertiary_link"
    )
    query = (
        "[out:json][timeout:60];"
        f'way["highway"~"^({highway_pat})$"]({south},{west},{north},{east});'
        "out geom;"
    )
    resp = requests.post(_OVERPOST_URL, data={"data": query}, headers=_UA, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    features: list[dict] = []
    for elem in data.get("elements", []):
        geo = elem.get("geometry")
        if not geo:
            continue
        pts = [[g["lon"], g["lat"]] for g in geo if "lon" in g and "lat" in g]
        if len(pts) < 2:
            continue
        try:
            geom = LineString(pts)
        except Exception:
            continue
        props = {"highway": elem.get("tags", {}).get("highway", ""), "name": elem.get("tags", {}).get("name", "")}
        features.append({"type": "Feature", "properties": props, "geometry": mapping(geom)})
    return features
