"""Data Registry：维护当前项目的图层元数据、schema 与真实几何（GeoJSON）。

平台图层元数据来自 demo_data/layers.json；真实几何来自 demo_data/geojson/*.json
（由 app/scripts/generate_demo_geojson.py 确定性生成）。执行器生成的结果图层
通过 register_generated() 注册元数据 + 几何到运行时内存，供 GIS 引擎与前端读取。
"""

import json
from pathlib import Path

from app.models.layer import FieldSpec, LayerMetadata, LayerSchema, LayerSource

_DEMO_DATA_DIR = Path(__file__).parent / "demo_data"
_PLATFORM_DATA_DIR = Path(__file__).parent / "platform_data"

# 按字段类型生成的示例值
_SAMPLE_VALUES: dict[str, object] = {
    "string": "示例记录",
    "number": 0,
    "integer": 0,
}

# ── 生成/上传图层自动配色 ────────────────────────────────────────────────
# 统一定义在注册漏斗 register_generated：任何「未显式配色」的图层（color 仍是
# models/layer.py 里的默认近黑 #0F172A）都被赋一个确定性、可区分、不透明的颜色。
# 按几何类型分三组色板：点 / 线 / 面天然分属不同色系，肉眼一眼区分；
# 组内按 layer_id 哈希取色，同一图层跨重启/跨会话颜色稳定，不同图层大概率不同色。
_DEFAULT_COLOR = "#0F172A"  # LayerMetadata.color 的默认值（认定为「未配色」）

_POINT_COLORS = [
    "#2f6fba", "#e0822b", "#5ba300", "#b23a48", "#7b4397",
    "#0f9b8e", "#c8a512", "#4a6fb0",
]
_LINE_COLORS = [
    "#1f77b4", "#2ca02c", "#9467bd", "#8c564b", "#17becf",
    "#e377c2", "#bcbd22", "#6b8e23",
]
_POLYGON_COLORS = [
    "#7b9fd4", "#f0a35e", "#8fd18f", "#c37fb0", "#ffd92f",
    "#66c7c7", "#f88a82", "#a6cee3",
]
_GEOMETRY_PALETTES: dict[str, list[str]] = {
    "Point": _POINT_COLORS,
    "LineString": _LINE_COLORS,
    "Polygon": _POLYGON_COLORS,
}


def _fnv1a(text: str) -> int:
    """确定性字符串哈希（与前端 mapLayers.hashString 同构：FNV-1a 32 位）。"""
    h = 2166136261
    for ch in text:
        h ^= ord(ch)
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def auto_layer_color(layer_id: str, geometry: str) -> str:
    """给「未显式配色」的图层一个确定性、与几何类型匹配、可区分的颜色。

    点/线/面分属三组色板（保证跨类型一眼区分），组内按 layer_id 哈希取色
    （同图层稳定、不同图层大概率不同色）。geometry 为 'Point'|'LineString'|'Polygon'。
    """
    palette = _GEOMETRY_PALETTES.get(geometry, _POLYGON_COLORS)
    return palette[_fnv1a(f"{layer_id}:{geometry}") % len(palette)]


class DataRegistry:
    def __init__(self) -> None:
        self._layers: dict[str, LayerMetadata] = {}
        self._schemas: dict[str, LayerSchema] = {}
        self._geojson: dict[str, dict] = {}
        # 底座图层 id（demo + platform：全局共享，不属于任何项目）
        self._base_layer_ids: set[str] = set()
        # 当前项目归属的图层 id（upload/generated/OSM：随项目切换换入换出）
        self._project_layer_ids: set[str] = set()
        # 当前项目 id（project-feature-design：换项目 = 换一套用户图层）
        self.current_project_id: str = "default"
        self.load_demo()
        self.load_platform()

    def load_platform(self) -> None:
        """注册平台数据图层（只读底座）：元数据来自 platform_data/layers.json。

        几何惰性加载：lazy 图层（真实深圳 POI/路网/详规等）启动时不读盘、不进 bulk 端点，
        由 get_geojson() 按需读 platform_data/geojson/<id>.json 并缓存。非 lazy 分支兼容保留。
        """
        from app.models.layer import LayerSource

        raw = json.loads((_PLATFORM_DATA_DIR / "layers.json").read_text(encoding="utf-8"))
        for item in raw:
            layer = LayerMetadata(**item)
            if layer.source != LayerSource.platform:
                continue  # 防御：manifest 只允许 platform 来源
            self._layers[layer.id] = layer
            self._schemas[layer.id] = self._build_schema(layer)
            self._base_layer_ids.add(layer.id)
            if not layer.lazy:
                self.get_geojson(layer.id)

    def load_demo(self) -> None:
        raw = json.loads((_DEMO_DATA_DIR / "layers.json").read_text(encoding="utf-8"))
        for item in raw:
            layer = LayerMetadata(**item)
            self._layers[layer.id] = layer
            self._schemas[layer.id] = self._build_schema(layer)
            self._base_layer_ids.add(layer.id)
        # 加载确定性几何（demo_data/geojson/{layer_id}.json）
        geojson_dir = _DEMO_DATA_DIR / "geojson"
        for layer_id in self._layers:
            path = geojson_dir / f"{layer_id}.json"
            if path.exists():
                try:
                    self._geojson[layer_id] = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    pass

    def _build_schema(self, layer: LayerMetadata) -> LayerSchema:
        return LayerSchema(
            layer_id=layer.id,
            name=layer.name,
            geometry=layer.geometry,
            crs=layer.crs,
            feature_count=layer.feature_count,
            fields=layer.fields,
            sample=self._build_sample(layer.fields),
        )

    def _build_sample(self, fields: list[FieldSpec]) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for row_no in range(1, 3):  # 2 条示例记录
            row: dict[str, object] = {}
            for field in fields:
                value = _SAMPLE_VALUES.get(field.type, _SAMPLE_VALUES["string"])
                row[field.name] = value if field.type != "number" else row_no
            rows.append(row)
        return rows

    def list_layers(self) -> list[LayerMetadata]:
        return list(self._layers.values())

    def get_layer(self, layer_id: str) -> LayerMetadata | None:
        return self._layers.get(layer_id)

    def get_layer_by_name(self, name: str) -> LayerMetadata | None:
        """按名称找图层：先精确匹配，再子串包含匹配（如 "人口" → "人口网格"）。"""
        for layer in self._layers.values():
            if layer.name == name:
                return layer
        for layer in self._layers.values():
            if name in layer.name or layer.name in name:
                return layer
        return None

    def get_schema(self, layer_id: str) -> LayerSchema | None:
        return self._schemas.get(layer_id)

    def register_generated(self, layer: LayerMetadata, geojson: dict | None = None) -> None:
        """注册执行器生成的结果图层（覆盖同名 ID），可附带真实几何。

        非底座图层（uploaded/generated/OSM 发布物）归属当前项目，随项目切换换入换出。
        覆盖同名时继承现有图层的样式/可见性字段（「注册数据不冲掉呈现」）：
        工具内部设的分层设色、用户改的颜色/隐藏等不被重注册冲掉（样式只经
        apply_layer_patch 管理；调用方构造的都是无样式 metadata，不存在清样式的合法意图）。
        """
        _STYLE_KEYS = (
            "color", "opacity", "fillOpacity", "strokeOpacity", "strokeColor",
            "strokeWidth", "radius", "fillColor",
            "styleMode", "visualVariables", "labelField", "visible",
        )
        old = self._layers.get(layer.id)
        if old is not None:
            inherit = {k: getattr(old, k) for k in _STYLE_KEYS if getattr(old, k, None) is not None}
            if inherit:
                layer = layer.model_copy(update=inherit)
        # 「未显式配色」的图层（color 仍是默认近黑）→ 自动赋确定性可区分色。
        # 放在 inherit 之后：若旧图层已有真实配色，inherit 已把进来；只有真默认色才触发。
        if (layer.color or "").upper() == _DEFAULT_COLOR.upper():
            layer = layer.model_copy(
                update={"color": auto_layer_color(layer.id, layer.geometry.value)}
            )
        self._layers[layer.id] = layer
        self._schemas[layer.id] = self._build_schema(layer)
        if layer.id not in self._base_layer_ids:
            self._project_layer_ids.add(layer.id)
        if geojson is not None:
            self._geojson[layer.id] = geojson

    def remove_layer(self, layer_id: str) -> LayerMetadata | None:
        """从注册表移除图层（remove_layer 工具路径；委托 delete_layer 一并清磁盘）。"""
        return self.delete_layer(layer_id)

    def delete_layer(self, layer_id: str) -> LayerMetadata | None:
        """彻底删除图层：内存注册表 + 样式侧车 + 磁盘物化文件 + 项目 manifest。

        readonly 平台图层拒绝删除（返回 None，不碰磁盘）；可删图层（上传/生成/
        OSM）从这里清干净，保证「删掉 → 重启 → 不复活」。remove_layer 工具与
        DELETE /layers/{id} 端点共用本方法。
        """
        layer = self._layers.get(layer_id)
        if layer is None:
            return None
        if layer.readonly:
            return None
        self._layers.pop(layer_id, None)
        self._schemas.pop(layer_id, None)
        self._geojson.pop(layer_id, None)
        self._project_layer_ids.discard(layer_id)
        self._style_sidecar_pop(layer_id)
        self._remove_layer_files(layer_id)
        return layer

    def _remove_layer_files(self, layer_id: str) -> None:
        """删除图层的磁盘物化文件与项目 manifest 条目（尽力而为，失败不致命）。

        - workspace/data/layers（上传/生成/OSM 物化点，也是默认项目的 legacy 扫描目录）；
        - 当前项目图层目录 + 项目 manifest（layers.json）里的条目。
        """
        from app.projects import project_layer_dir, project_manifest_path
        from app.workspace import LAYER_DATA_DIR

        # workspace/data/layers + 当前项目图层目录里的 <id>.json
        for directory in (LAYER_DATA_DIR, project_layer_dir(self.current_project_id)):
            path = directory / f"{layer_id}.json"
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
        # 项目 manifest：去掉该图层条目，避免切换/重启后从 manifest 复活
        manifest_path = project_manifest_path(self.current_project_id)
        try:
            if manifest_path.exists():
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                cleaned = [item for item in raw if (item or {}).get("id") != layer_id]
                if len(cleaned) != len(raw):
                    manifest_path.write_text(json.dumps(cleaned, ensure_ascii=False), encoding="utf-8")
        except (json.JSONDecodeError, OSError):
            pass

    def move_layer(self, layer_id: str, to_index: int) -> LayerMetadata | None:
        """调整图层注册顺序（set_layer_order 工具；/layers 返回顺序随之变化）。

        to_index 为可见顺序下标（0 为最底/最后）。越界 clamp 到边界。
        注意：前端以自身 layers 数组序为准渲染，LAYER_REORDER 事件同步其顺序。
        """
        layer = self._layers.pop(layer_id, None)
        if layer is None:
            return None
        items = list(self._layers.values())
        to_index = max(0, min(to_index, len(items)))
        items.insert(to_index, layer)
        self._layers = {item.id: item for item in items}
        return layer

    def _style_sidecar_pop(self, layer_id: str) -> None:
        """移除图层时清理其样式侧车文件（尽力而为，失败不致命）。"""
        from app.style_store import remove_layer_styles

        try:
            remove_layer_styles(layer_id)
        except Exception:  # noqa: BLE001
            pass

    def load_workspace_layer_files(self, directory: Path) -> int:
        """扫描 workspace/data/layers/，把非 demo 的 GeoJSON 文件注册为上传/生成图层。

        轻量混合：文件为持久真，本注册表是文件之上的索引。下载/发布的数据
        （如 register_layer 物化的 OSM 建筑）跨重启后从这里恢复，地图仍可见。
        demo 图层以 demo_data 为准，跳过；解析失败的文件跳过不致命。返回注册数。
        """
        from app.geojson_utils import geometry_type_for, load_feature_collection
        from app.models.layer import LayerSource

        count = 0
        for path in sorted(Path(directory).glob("*.json")):
            layer_id = path.stem
            if layer_id in self._layers:
                continue  # demo 图层以 demo_data 注册表为准，不重复注册
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            features, geometry_types, _bbox = load_feature_collection(data)
            if not features:
                continue
            fc: dict = {"type": "FeatureCollection", "features": features}
            if isinstance(data, dict) and "crs" in data:
                fc["crs"] = data["crs"]
            layer = LayerMetadata(
                id=layer_id,
                name=layer_id,
                source=LayerSource.uploaded,
                geometry=geometry_type_for(geometry_types),
                color="#0F172A",
                visible=True,
                feature_count=len(features),
                crs="EPSG:4326",
                description=f"从 {path.name} 恢复（workspace/data/layers）",
                generated_by="workspace 图层索引",
            )
            self.register_generated(layer, geojson=fc)
            self._project_layer_ids.add(layer.id)  # workspace 恢复的图层归属当前项目
            count += 1
        return count

    def get_geojson(self, layer_id: str) -> dict | None:
        """读取图层真实几何（FeatureCollection）；无几何返回 None。

        平台数据 lazy 图层首次读取时从 disk 加载并缓存（分析工具 / 前端单取共用）。
        """
        cached = self._geojson.get(layer_id)
        if cached is not None:
            return cached
        layer = self._layers.get(layer_id)
        if layer is None or layer.source != LayerSource.platform:
            return None
        path = _PLATFORM_DATA_DIR / "geojson" / f"{layer_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        self._geojson[layer_id] = data
        return data

    def set_geojson(self, layer_id: str, geojson: dict) -> None:
        """写入/更新图层几何（结果图层由工具真计算产出）。"""
        # 防御：旧代码曾把 JSON 字符串存进来，导致 /layers/geojson 响应校验失败
        if isinstance(geojson, str):
            try:
                geojson = json.loads(geojson)
            except (json.JSONDecodeError, TypeError):
                return  # 无法解析的字符串直接丢弃，避免污染响应类型
        self._geojson[layer_id] = geojson

    # readonly 图层禁止修改的数据内容字段（样式/名字/可见性属于视觉呈现，放行）
    _READONLY_DATA_FIELDS = frozenset(
        {"id", "geometry", "feature_count", "crs", "fields", "generated_by", "source", "bbox"}
    )

    def apply_layer_patch(self, layer_id: str, patch: dict) -> LayerMetadata | None:
        """合并写穿图层的名称/可见性/样式 patch → 更新注册表 + 样式侧车。

        样式与可见性变更的唯一写面：样式引擎与 PATCH 端点都走这里。
        只合并 LayerMetadata 认识的字段；返回更新后的图层（不存在返回 None）。
        readonly 平台图层：数据内容字段（id/geometry/feature_count 等）一律拒绝，
        样式/名字/可见性放行——"数据只读、呈现可改"。
        """
        from app.style_store import save_patch

        layer = self._layers.get(layer_id)
        if layer is None:
            return None
        if layer.readonly:
            blocked = sorted(set(patch) & self._READONLY_DATA_FIELDS)
            if blocked:
                raise ValueError(f"平台数据只读，不允许修改字段：{', '.join(blocked)}")
        known = {k: v for k, v in patch.items() if hasattr(layer, k)}
        if known:
            self._layers[layer_id] = layer.model_copy(update=known)
            save_patch(layer_id, known)
        return self._layers[layer_id]

    def load_workspace_styles(self) -> int:
        """启动时把样式侧车合并进注册表（demo 与恢复图层的样式跨重启还原）。"""
        from app.style_store import load_all

        count = 0
        for layer_id, patch in load_all().items():
            layer = self._layers.get(layer_id)
            if layer is None:
                continue
            known = {k: v for k, v in patch.items() if hasattr(layer, k)}
            if known:
                self._layers[layer_id] = layer.model_copy(update=known)
                count += 1
        return count

    def list_geojson(self) -> dict[str, dict]:
        """已载入内存图层的几何（bulk 端点，前端 hydrate 一次拉取）。

        lazy 平台图层不在此列：即使被单取缓存过也不进 bulk —— 否则越用缓存越大，
        /layers/geojson 会膨胀到几百 MB（84 万要素）把前端浏览器撑崩。
        前端对 lazy 图层走 /layers/{id}/geojson 单取（ensureLayerGeojson）。
        """
        return {
            layer_id: gj
            for layer_id, gj in self._geojson.items()
            if self._include_in_bulk(layer_id)
        }

    def _include_in_bulk(self, layer_id: str) -> bool:
        """bulk 端点排除 lazy 平台图层：重图层按需单取，避免首屏全量拉取。"""
        layer = self._layers.get(layer_id)
        return layer is None or not (layer.source == LayerSource.platform and layer.lazy)

    # ── 项目作用域：当前项目图层换入/换出（底座 demo/platform 全局共享） ──

    def project_layer_ids(self) -> set[str]:
        return set(self._project_layer_ids)

    def switch_project(self, project_id: str) -> str:
        """切换当前项目：保存当前项目图层 → 清除 → 换入新项目图层。

        返回切换后的项目 id。底座（demo/platform）不动。
        """
        from app.projects import DEFAULT_PROJECT_ID

        pid = project_id or DEFAULT_PROJECT_ID
        if pid != self.current_project_id:
            self._save_project_layers(self.current_project_id)
            self._remove_project_layers()
            self.current_project_id = pid
            self._load_project_layers(pid)
        return self.current_project_id

    def _save_project_layers(self, project_id: str) -> None:
        """把当前项目图层（meta + geojson）落盘到项目目录，供切换回来恢复。"""
        from app.projects import project_layer_dir, project_manifest_path

        metas = [self._layers[lid] for lid in self._project_layer_ids if lid in self._layers]
        try:
            project_manifest_path(project_id).write_text(
                json.dumps([m.model_dump() for m in metas], ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass
        layer_dir = project_layer_dir(project_id)
        for lid in list(self._project_layer_ids):
            gj = self._geojson.get(lid)
            if gj is None:
                continue
            try:
                (layer_dir / f"{lid}.json").write_text(
                    json.dumps(gj, ensure_ascii=False), encoding="utf-8"
                )
            except OSError:
                continue

    def _remove_project_layers(self) -> None:
        """从注册表移除当前项目图层（内存；已由 _save_project_layers 落盘）。"""
        for lid in list(self._project_layer_ids):
            self._layers.pop(lid, None)
            self._schemas.pop(lid, None)
            self._geojson.pop(lid, None)
        self._project_layer_ids.clear()

    def _load_project_layers(self, project_id: str) -> None:
        """把项目目录里的图层（meta + geojson）注册进注册表。"""
        from app.projects import (
            DEFAULT_PROJECT_ID,
            LEGACY_LAYER_DIR,
            project_layer_dir,
            project_manifest_path,
        )

        manifest = project_manifest_path(project_id)
        if manifest.exists():
            try:
                raw = json.loads(manifest.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                raw = []
            for item in raw:
                try:
                    layer = LayerMetadata(**item)
                except Exception:  # noqa: BLE001
                    continue
                self._layers[layer.id] = layer
                self._schemas[layer.id] = self._build_schema(layer)
                self._project_layer_ids.add(layer.id)
            layer_dir = project_layer_dir(project_id)
            for lid in list(self._project_layer_ids):
                path = layer_dir / f"{lid}.json"
                if not path.exists():
                    continue
                try:
                    self._geojson[lid] = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
        # 默认项目向后兼容：额外扫描 legacy 图层目录（workspace/data/layers）
        if project_id == DEFAULT_PROJECT_ID:
            self.load_workspace_layer_files(LEGACY_LAYER_DIR)


DATA_REGISTRY = DataRegistry()
