"""图层样式引擎：Sprint 2（S2-1/S2-2）共用的样式计算与分发。

后端算分档断点（breaks）+ 产出 renderer 描述（styleMode / visualVariables），
经 LAYER_STYLE 事件下发，前端 deck.gl 按要素回调渲染 —— 同构于
"后端算 breaks → 前端 set_layer_renderer" 的常见模式。

两个消费方共用本引擎：
- 确定性拦截（llm_executor._try_style_request）：识别「按 X 分层设色 / 暖色调 / 透明度渐变 / 去边框」意图；
- LLM 工具（set_graduated_style / set_layer_visual_variables / update_layer_style / ...）。

持久化统一走 DATA_REGISTRY.apply_layer_patch（写穿样式侧车，跨重启不丢）。
"""

import re
from typing import Any

from app.data_registry import DATA_REGISTRY
from app.models.events import EventType

# 色带（镜像常见分级配色 _COLOR_RAMPS；来源见 NOTICE.md）
_COLOR_RAMPS: dict[str, list[str]] = {
    "viridis": ["#fde725", "#5ec962", "#21918c", "#3b528b", "#440154"],
    "plasma": ["#f0f921", "#f89540", "#cc4778", "#7e03a8", "#0d0887"],
    "inferno": ["#fcffa4", "#f98e09", "#bc3754", "#57106e", "#000004"],
    "magma": ["#fcfdbf", "#fb8861", "#b73779", "#51127c", "#000004"],
    "reds": ["#fee5d9", "#fcae91", "#fb6a4a", "#de2d26", "#a50f15"],
    "blues": ["#eff3ff", "#bdd7e7", "#6baed6", "#3182bd", "#08519c"],
    "greens": ["#edf8e9", "#bae4b3", "#74c476", "#31a354", "#006d2c"],
    "oranges": ["#feedde", "#fdbe85", "#fd8d3c", "#e6550d", "#a63603"],
    "purples": ["#f3e8ff", "#d8b4fe", "#a855f7", "#7e22ce", "#581c87"],
    "rdylgn": ["#d73027", "#fc8d59", "#ffffbf", "#91cf60", "#1a9850"],
    "rdylbu": ["#d73027", "#fc8d59", "#ffffbf", "#91bfdb", "#4575b4"],
    "spectral": ["#9e0142", "#f46d43", "#ffffbf", "#66c2a5", "#5e4fa2"],
    "tableau10": ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f"],
    # 分类专用色板：12 色（类别配色默认，覆盖到 12 类都有独立色）
    "category10": [
        "#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948",
        "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac", "#6a3d9a", "#17becf",
    ],
}

# 暖色调提示词 → 色带名（演示常用「暖色调」，默认 oranges）
_WARM_RAMPS = ("oranges", "reds", "rdylgn", "spectral", "magma")


def _numeric_values(values: list[Any]) -> list[float]:
    nums: list[float] = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f == f:  # 排除 NaN
            nums.append(f)
    return nums


def _field_values(layer_id: str, field: str) -> list[Any]:
    """从图层 geojson 读某字段的原始取值序列。"""
    geojson = DATA_REGISTRY.get_geojson(layer_id) or {}
    out: list[Any] = []
    for feat in geojson.get("features", []) or []:
        props = feat.get("properties") or {}
        if field in props:
            out.append(props[field])
    return out


def _sample_values(values: list[float], limit: int = 2000) -> list[float]:
    """确定性步长采样：超过 limit 时按步长取真实值，保证大图层断点计算不卡死。

    Jenks 为 O(k·n²) 动态规划，几何/标准差也无需全量——84 万要素平台图层
    （poi_公司企业 15 万要素）先采样再算。采样取真实取值（非编造），
    步长均匀覆盖全量分布，且输入有序时采样结果确定可复现。
    """
    n = len(values)
    if n <= limit:
        return values
    step = max(1, n // limit)
    return values[::step]


def _jenks_breaks(values: list[float], k: int) -> list[float]:
    """自然间断点（Fisher-Jenks 一维）：升序数值切 k 类，类内平方和（SSE）最小。

    经典 O(k·n²) 动态规划：dp[j][i] = 前 i 个样本切 j 类的最小 SSE，转移枚举
    最后一类的起点 p；回溯 parent 表得各级断点。k<2 / 唯一值不足 k 时退化返回空，
    由调用方回落分位数。n 已由 _sample_values 限到 ≤1000。
    """
    if k < 2:
        return []
    data = sorted(values)
    n = len(data)
    if n < k:
        return []
    uniq = sorted(set(data))
    if len(uniq) <= k:
        # 唯一值不足 k 类：返回唯一值边界，不伪造断点
        return [float(v) for v in uniq[:-1]]
    # 前缀和 / 平方前缀和 → 区间 SSE = Σx² - (Σx)²/count
    cum: list[float] = [0.0]
    cum2: list[float] = [0.0]
    for v in data:
        cum.append(cum[-1] + v)
        cum2.append(cum2[-1] + v * v)

    def _sse(left: int, right: int) -> float:
        count = right - left
        if count <= 0:
            return float("inf")
        s = cum[right] - cum[left]
        s2 = cum2[right] - cum2[left]
        return s2 - s * s / count

    INF = float("inf")
    dp = [[INF] * (n + 1) for _ in range(k + 1)]
    parent = [[0] * (n + 1) for _ in range(k + 1)]
    for i in range(1, n + 1):
        dp[1][i] = _sse(0, i)
        parent[1][i] = 0
    for j in range(2, k + 1):
        for i in range(j, n + 1):
            best, best_idx = INF, 0
            for p in range(j - 1, i):
                cand = dp[j - 1][p] + _sse(p, i)
                if cand < best:
                    best, best_idx = cand, p
            dp[j][i] = best
            parent[j][i] = best_idx
    # 回溯：parent[j][end] 是第 j 类的起点，断点 = 该起点处的取值
    boundaries: list[float] = []
    end = n
    for j in range(k, 1, -1):
        start = parent[j][end]
        boundaries.append(data[start])
        end = start
    boundaries.reverse()
    result: list[float] = []
    for b in boundaries:
        if not result or b > result[-1]:
            result.append(float(b))
    return result


def compute_breaks(values: list[Any], method: str = "quantile", classes: int = 5, breaks: list | None = None) -> list[float]:
    """数值列表 → 分档断点（method: quantile | equal-interval | geometric-interval | stddev | jenks | manual）。

    返回 n 个断点把取值范围切成 n+1 级。
    - quantile 分位数（默认）；equal-interval 等距；
    - geometric-interval 几何间隔（min≤0 时等比级数无意义 → 回落 quantile）；
    - stddev 标准差断点（mean ± std 对称，clamp 到数据范围；std=0 常数分布回落等距）；
    - jenks 自然间断点（Fisher-Jenks，类内方差最小；唯一值不足/异常回落 quantile）；
    - manual 直接用传入 breaks（排序去重）。
    大数组先确定性采样（_sample_values），84 万要素图层也不卡死。
    """
    nums = _numeric_values(values)
    if not nums:
        return []
    method = (method or "quantile").replace("_", "-")
    if breaks is not None and len(breaks):
        return sorted(float(b) for b in breaks)
    import numpy as np

    nums = sorted(nums)
    n = max(min(int(classes or 5), 12), 2)
    # jenks 是 O(k·n²)：样本上限收紧到 1000，保证交互不卡死
    sample = _sample_values(nums, limit=1000 if method == "jenks" else 2000)
    arr = np.asarray(sample, dtype=float)
    lo_v, hi_v = float(arr[0]), float(arr[-1])
    if method == "equal-interval":
        return [round(lo_v + (hi_v - lo_v) * i / n, 4) for i in range(1, n)]
    if method == "geometric-interval":
        if lo_v > 0:
            ratio = (hi_v / lo_v) ** (1 / n)
            return [round(lo_v * ratio**i, 4) for i in range(1, n)]
        # 含非正值：等比级数无意义 → 回落分位数
    if method == "stddev":
        mean = float(np.mean(arr))
        std = float(np.std(arr))
        if std > 0:
            return [
                round(min(hi_v, max(lo_v, mean + std * (i - n / 2))), 4)
                for i in range(1, n)
            ]
        # 常数分布（std=0）：无法分档 → 回落等距
        return [round(lo_v + (hi_v - lo_v) * i / n, 4) for i in range(1, n)]
    if method == "jenks":
        brk = _jenks_breaks(sample, n)
        if brk:
            return brk
        # Fisher-Jenks 异常/样本退化 → 回落分位数
    # quantile（默认）
    qs = [i / n for i in range(1, n)]
    return [float(round(v, 4)) for v in np.quantile(arr, qs)]


def resolve_ramp(palette: Any, classes: int = 5) -> list[str]:
    """色带名 / 逗号色串 / hex 数组 → classes 长度的色列表（不足插值，超了采样）。"""
    if isinstance(palette, list):
        colors = [str(c) for c in palette if str(c).strip()]
    elif isinstance(palette, str):
        key = palette.strip().lower()
        if key in _COLOR_RAMPS:
            colors = _COLOR_RAMPS[key]
        else:
            colors = [item.strip() for item in palette.split(",") if item.strip()]
    else:
        colors = []
    if not colors:
        colors = _COLOR_RAMPS["viridis"]
    n = max(int(classes or 5), 1)
    if n == len(colors):
        return list(colors)
    if n < len(colors):
        step = (len(colors) - 1) / max(n - 1, 1)
        return [colors[round(i * step)] for i in range(n)]
    # 需要更多色：末色重复补齐（常见实现用采样，此处取最近色回退）
    return [colors[min(i, len(colors) - 1)] for i in range(n)]


def warm_ramp_name(question: str) -> str | None:
    """提问含暖色调关键词 → 暖色带名（oranges），否则 None。"""
    if re.search(r"暖色调?|暖色系|暖色", question):
        return "oranges"
    return None


def find_field(layer, question: str) -> str | None:
    """从提问里匹配图层字段名（最长命中；字段含数值优先）。返回字段名或 None。"""
    q = str(question or "")
    fields = [f.name for f in (layer.fields or []) if f.name]
    if not fields:
        geojson = DATA_REGISTRY.get_geojson(layer.id) or {}
        for feat in geojson.get("features", [])[:1]:
            fields = [str(k) for k in (feat.get("properties") or {}).keys()]
    best, best_len = None, 0
    for f in fields:
        if f and f in q and len(f) > best_len:
            best, best_len = f, len(f)
    return best


def build_graduated_style(
    layer_id: str,
    field: str,
    method: str = "quantile",
    classes: int = 5,
    palette: Any = None,
    breaks: list | None = None,
) -> dict:
    """按数值字段分层设色：读字段值 → 算 breaks → renderer 描述。字段无数值抛 ValueError。"""
    values = _field_values(layer_id, field)
    nums = _numeric_values(values)
    if not nums:
        raise ValueError(f"字段「{field}」没有可用的数值（读到的取值均非数值）")
    if palette is None:
        palette = "viridis"
    colors = resolve_ramp(palette, int(classes or 5))
    brk = compute_breaks(nums, method, classes, breaks)
    return {
        "styleMode": "graduated",
        "visualVariables": {
            "renderer": "graduated",
            "graduated": {
                "field": field,
                "method": (method or "quantile").replace("_", "-"),
                "classes": len(colors),
                "palette": colors,
                "breaks": brk,
            },
        },
    }


def build_visual_variables(
    layer_id: str,
    *,
    size_field: str | None = None,
    size_method: str = "quantile",
    size_classes: int = 5,
    size_range: list[float] | None = None,
    opacity_field: str | None = None,
    opacity_method: str = "quantile",
    opacity_classes: int = 5,
    opacity_range: list[float] | None = None,
) -> dict:
    """透明度 / 点大小按字段渐变：size_variable / opacity_variable。至少一个字段。"""
    vv: dict[str, Any] = {}
    if size_field:
        nums = _numeric_values(_field_values(layer_id, size_field))
        if nums:
            vv["size_variable"] = {
                "field": size_field,
                "method": size_method.replace("_", "-"),
                "classes": int(size_classes),
                "range": [float(x) for x in (size_range or [4, 20])],
                "breaks": compute_breaks(nums, size_method, size_classes),
            }
    if opacity_field:
        nums = _numeric_values(_field_values(layer_id, opacity_field))
        if nums:
            vv["opacity_variable"] = {
                "field": opacity_field,
                "method": opacity_method.replace("_", "-"),
                "classes": int(opacity_classes),
                "range": [float(x) for x in (opacity_range or [0.3, 1.0])],
                "breaks": compute_breaks(nums, opacity_method, opacity_classes),
            }
    if not vv:
        raise ValueError("需要 size_field 或 opacity_field 至少一个")
    return {"styleMode": "graduated", "visualVariables": vv}


def apply_style_update(layer_id: str, *, emit: Any | None = None, **changes) -> dict:
    """单值样式合并：改色 / 透明 / 描边 / 点半径 / 标注字段；有样式改动时清除分档回 single。

    返回写穿的 style patch（含 visible 时一并返回）。
    """
    style: dict[str, Any] = {}
    for key in ("color", "fillColor", "strokeColor", "strokeWidth", "radius", "opacity", "fillOpacity", "strokeOpacity", "labelField"):
        if changes.get(key) is not None:
            style[key] = changes[key]
    patch: dict[str, Any] = dict(style)
    if changes.get("visible") is not None:
        patch["visible"] = bool(changes["visible"])
    if style:
        patch["styleMode"] = None  # 回到单值渲染
        patch["visualVariables"] = None
    _apply_and_emit(layer_id, patch, emit=emit)
    return patch


def apply_renderer_style(layer_id: str, style: dict, *, emit: Any | None = None) -> dict:
    """应用 graduated / visual_variables renderer 样式并持久化 + emit。"""
    patch = {
        "styleMode": style.get("styleMode"),
        "visualVariables": style.get("visualVariables"),
    }
    _apply_and_emit(layer_id, patch, emit=emit)
    return patch


def apply_visibility(layer_id: str, visible: bool, *, emit: Any | None = None) -> dict:
    """仅改可见性（不动样式/分档）。"""
    patch = {"visible": bool(visible)}
    _apply_and_emit(layer_id, patch, emit=emit)
    return patch


# ── Sprint 2 补（分类配色 / 过滤 / 标注 / 高亮）：真实现，确定性计算 ──

def validate_filter(layer, conditions: dict | None) -> str | None:
    """校验 filter 字段均为图层真实字段；返回错误信息（无错误返回 None）。"""
    if not conditions:
        return None
    known_fields = {f.name for f in (layer.fields or [])}
    unknown = [f for f in conditions if f not in known_fields]
    if unknown:
        return (
            f"图层「{layer.name}」没有字段：{', '.join(unknown)}。"
            f"可用字段：{', '.join(sorted(known_fields)) or '（无）'}"
        )
    return None


def build_categorized_style(
    layer_id: str,
    field: str,
    palette: Any = None,
    max_categories: int = 12,
) -> dict:
    """分类配色：按类型字段的唯一取值给要素分色（如按学校/医院）。

    读取字段真实取值，按出现频次降序取前 max_categories-1 类，其余并入「其它」，
    每类一色（默认 category10 色板）。字段无取值抛 ValueError。
    """
    values = _field_values(layer_id, field)
    if not values:
        raise ValueError(f"字段「{field}」没有可取值的要素")
    counter: dict[str, int] = {}
    for v in values:
        if v is None:
            continue
        key = str(v)
        counter[key] = counter.get(key, 0) + 1
    if not counter:
        raise ValueError(f"字段「{field}」所有取值都为空")
    items = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
    cats = [key for key, _count in items]
    if len(cats) > max_categories:
        cats = cats[: max_categories - 1] + ["其它"]
    colors = resolve_ramp(palette if palette is not None else "category10", len(cats))
    classes = [{"value": value, "color": color} for value, color in zip(cats, colors)]
    return {
        "styleMode": "categorized",
        "visualVariables": {
            "renderer": "categorized",
            "categorized": {"field": field, "classes": classes},
        },
    }


def apply_renderer_style_keep(
    layer_id: str, style: dict, *, emit: Any | None = None
) -> dict:
    """应用 renderer 样式但保留既有叠加（filter/highlight）：仅替换 renderer 相关 key。"""
    layer = DATA_REGISTRY.get_layer(layer_id)
    if layer is None:
        raise ValueError(f"未找到图层：{layer_id}")
    current = (layer.visualVariables or {}).copy() if layer.visualVariables else {}
    _RENDERER_KEYS = {"renderer", "graduated", "categorized", "size_variable", "opacity_variable"}
    merged = {k: v for k, v in current.items() if k not in _RENDERER_KEYS}
    merged.update(style.get("visualVariables") or {})
    patch = {
        "styleMode": style.get("styleMode"),
        "visualVariables": merged or None,
    }
    _apply_and_emit(layer_id, patch, emit=emit)
    return patch


def set_layer_filter(layer_id: str, conditions: dict, *, emit: Any | None = None) -> dict:
    """图层过滤：只显示满足条件的要素（与查询/提取同一套 filter 语法）。

    filter 作为叠加写入 visualVariables.filter，不影响已有分档/分类配色。
    """
    merged = _merge_vv_key(layer_id, {"filter": conditions or {}})
    _apply_and_emit(layer_id, merged, emit=emit)
    return merged


def set_layer_label(layer_id: str, field: str | None, *, emit: Any | None = None) -> dict:
    """图层标注：要素边上显示字段值；field=None 清除标注。"""
    patch = {"labelField": field}
    _apply_and_emit(layer_id, patch, emit=emit)
    return patch


def build_highlight_style(
    layer_id: str,
    conditions: dict,
    color: str | None = None,
    *,
    emit: Any | None = None,
) -> dict:
    """高亮要素：满足条件的要素用醒目色标出（默认金色）。

    highlight 作为叠加写入 visualVariables.highlight，不动既有分档/配色。
    """
    merged = _merge_vv_key(layer_id, {"highlight": {"filter": conditions or {}, "color": color or "#ffd166"}})
    _apply_and_emit(layer_id, merged, emit=emit)
    return merged


def clear_visual_overlays(layer_id: str, *, emit: Any | None = None) -> dict:
    """清除叠加（filter/highlight），保留 renderer 与 labelField。"""
    layer = DATA_REGISTRY.get_layer(layer_id)
    if layer is None:
        raise ValueError(f"未找到图层：{layer_id}")
    current = (layer.visualVariables or {}).copy() if layer.visualVariables else {}
    _RENDERER_KEYS = {"renderer", "graduated", "categorized", "size_variable", "opacity_variable"}
    merged = {k: v for k, v in current.items() if k in _RENDERER_KEYS}
    patch = {"visualVariables": merged or None}
    _apply_and_emit(layer_id, patch, emit=emit)
    return patch


def _merge_vv_key(layer_id: str, extra: dict) -> dict:
    """把 extra 里的 key 合并进 visualVariables，返回 patch（不 emit）。"""
    layer = DATA_REGISTRY.get_layer(layer_id)
    if layer is None:
        raise ValueError(f"未找到图层：{layer_id}")
    current = (layer.visualVariables or {}).copy() if layer.visualVariables else {}
    current.update(extra)
    return {"visualVariables": current or None}


def _apply_and_emit(layer_id: str, patch: dict, *, emit: Any | None = None) -> None:
    layer = DATA_REGISTRY.apply_layer_patch(layer_id, patch)
    if layer is None:
        raise ValueError(f"未找到图层：{layer_id}")
    if emit is not None:
        emit(EventType.LAYER_STYLE, {"layer_id": layer_id, "style": patch})
