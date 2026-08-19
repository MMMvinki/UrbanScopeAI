"""样式侧车：图层样式持久化（workspace/.workdir/layer_styles.json）。

LayerMetadata 是运行时对象；demo 图层数据只读 demo_data，改样式不能写回源文件。
样式侧车是唯一写面：`{layer_id: style_patch}`。启动时由 data_registry 合并进注册表，
样式引擎 / PATCH 端点每次改样式都写穿到这里（原子替换），保证跨重启不丢。
"""

import json
import os
import tempfile

from app.workspace import LAYER_STYLES_FILE


def load_all() -> dict[str, dict]:
    """全部图层样式 patch（key=layer_id）。"""
    try:
        data = json.loads(LAYER_STYLES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_patch(layer_id: str, patch: dict) -> None:
    """合并写穿单个图层的样式 patch（原子替换，防并发半写）。"""
    data = load_all()
    data[layer_id] = {**(data.get(layer_id) or {}), **patch}
    LAYER_STYLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix="layer_styles.", suffix=".tmp", dir=str(LAYER_STYLES_FILE.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, LAYER_STYLES_FILE)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def remove_layer_styles(layer_id: str) -> None:
    """移除图层的样式侧车记录（remove_layer 工具清理用；无记录为空操作）。"""
    data = load_all()
    if layer_id not in data:
        return
    data.pop(layer_id, None)
    LAYER_STYLES_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix="layer_styles.", suffix=".tmp", dir=str(LAYER_STYLES_FILE.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_path, LAYER_STYLES_FILE)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
