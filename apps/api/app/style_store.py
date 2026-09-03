"""样式侧车：图层样式持久化（workspace/.workdir/layer_styles.json）。

LayerMetadata 是运行时对象；demo 图层数据只读 demo_data，改样式不能写回源文件。
样式侧车是唯一写面：`{layer_id: style_patch}`。启动时由 data_registry 合并进注册表，
样式引擎 / PATCH 端点每次改样式都写穿到这里（原子替换），保证跨重启不丢。
"""

import json
import threading

from app.atomicio import atomic_write_json
from app.workspace import LAYER_STYLES_FILE

# 样式侧车是跨请求共享的读-改-写面。uvicorn 单进程多线程下，两个并发的 PATCH
# 若都先 load 再 replace，会互相丢写（lost update）；用进程内锁把「读旧→合并→写新」
# 串成原子段，并交由 atomic_write_json 的换入重试兜底瞬时占用。跨进程（execute_code
# 子进程）写同一文件不在本进程锁覆盖内，遇 WinError 5 由换入重试顶住。
_STYLE_LOCK = threading.Lock()


def load_all() -> dict[str, dict]:
    """全部图层样式 patch（key=layer_id）。"""
    try:
        data = json.loads(LAYER_STYLES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_patch(layer_id: str, patch: dict) -> None:
    """合并写穿单个图层的样式 patch（原子段，防并发半写+并发丢写）。"""
    with _STYLE_LOCK:
        data = load_all()
        data[layer_id] = {**(data.get(layer_id) or {}), **patch}
        atomic_write_json(LAYER_STYLES_FILE, data)


def remove_layer_styles(layer_id: str) -> None:
    """移除图层的样式侧车记录（remove_layer 工具清理用；无记录为空操作）。"""
    with _STYLE_LOCK:
        data = load_all()
        if layer_id not in data:
            return
        data.pop(layer_id, None)
        atomic_write_json(LAYER_STYLES_FILE, data)
