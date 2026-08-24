"""Tool Runtime：工具注册 + 按名分发执行。

Executor 与 LLM Agent 循环都通过 run_tool(name, params) 调用工具，
返回统一的 ToolResult；ToolSpec 是工具的对外契约（GET /tools）。
入参统一为 dict（LLM 工具调用）；coverage 兼容 Pydantic 模型（规则版 executor）。
"""

import json
import sys
from collections.abc import Callable

from app.models.events import EventType
from app.models.tool import ToolResult, ToolSpec
from app.tools.context import (
    ToolContext,
    get_current_context,
    reset_current_context,
    set_current_context,
)
from app.tools.analysis import (
    run_buffer,
    run_density,
    run_get_dataset_schema,
    run_list_datasets,
    run_nearest_distance,
    run_spatial_clip,
    run_spatial_join,
    run_statistics,
    run_supply_demand,
)
from app.tools.coverage import CoverageParams, run_coverage
from app.tools.csv_geojson import run_csv_to_geojson
from app.tools.isochrone import run_isochrone
from app.tools.bivariate_moran import run_bivariate_moran
from app.tools.extract_features import run_extract_features
from app.tools.query_features import run_query_features
from app.tools.registry import TOOLS
from app.tools.register_layer import run_register_layer
from app.tools.register_uploaded_file import run_register_uploaded_file
from app.tools.web import run_webfetch, run_websearch


def _parse_json_arg(value: object) -> dict:
    """解析 tools 的 params 参数（JSON 字符串 或 dict）。"""
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _run_osm_call(params: dict) -> ToolResult:
    """osm_call：经 DataProvider 分发 OSM 命令（get_current_context 读 workspace）。"""
    from app.providers import get_provider

    ctx = get_current_context()
    command = str(params.get("command", ""))
    p = _parse_json_arg(params.get("params"))
    return get_provider("osm").execute(command, p, ctx)


def _run_datasource_call(params: dict) -> ToolResult:
    """datasource_call：经 DataProvider 分发 catalog 命令。"""
    from app.providers import get_provider

    ctx = get_current_context()
    command = str(params.get("command", ""))
    p = _parse_json_arg(params.get("params"))
    return get_provider("datasource").execute(command, p, ctx)


def _run_execute_code(params: dict) -> ToolResult:
    """execute_code：在受控子进程执行任意 Python（L0 隔离 + L1 权限门 + L4 遥测）。

    生命周期：每 run 一个子进程，首次调用懒创建存 ctx.subprocess_executor，同 run 复用
    （namespace 跨 exec 持久），run 结束由 SSE worker finally cleanup。
    """
    from app.execution.auto_install import auto_install_missing
    from app.execution.code_validation import validate_execute_code_payload
    from app.execution.factory import build_run_executor

    ctx = get_current_context()
    code = str(params.get("code", ""))
    if not code.strip():
        return ToolResult(success=False, message="缺少必填参数 code")

    # 防御：LLM 可能把超大 GeoJSON 原样嵌入代码，直接拒绝而不是照跑/流式下发，防前端 OOM
    _MAX_EXEC_CODE_CHARS = 200_000
    if len(code) > _MAX_EXEC_CODE_CHARS:
        return ToolResult(
            success=False,
            message=f"代码过长（{len(code)} 字符，上限 {_MAX_EXEC_CODE_CHARS}）。请勿把数据原样嵌入代码，改用文件路径读取。",
        )

    # L0 前校验：拒绝 think 标签 / 代码围栏 / 长 sleep / 链式思维注释
    err = validate_execute_code_payload(code)
    if err:
        return ToolResult(success=False, message=err)

    # L1 权限门：execute_code 默认 ASK → evaluate 内部阻塞等审批；DENY → 拒绝执行
    if ctx.permission_runtime is None:
        return ToolResult(success=False, message="execute_code 需要权限运行时（当前 run 未配置）")
    decision = ctx.permission_runtime.evaluate("execute_code", {"code": code})
    if not decision.allowed:
        return ToolResult(success=False, message=f"已拒绝执行代码：{decision.reason}")

    if ctx.is_cancelled():
        return ToolResult(success=False, message="run 已取消（客户端断连）")

    # 自动安装缺失依赖。代码已过权限门（用户看过含 pip install 的源码才放行），不二次询问。
    def _pipe_install(line: str) -> None:
        ctx.emit(EventType.CODE_DELTA, {"kind": "stdout", "text": line})

    installed = auto_install_missing(code, python_executable=sys.executable, output_callback=_pipe_install)
    if installed:
        ctx.emit(EventType.CODE_DELTA, {"kind": "stdout", "text": f"ℹ️ {installed}\n"})

    # run 级子进程懒创建 + 同 run 复用
    executor = ctx.subprocess_executor
    if executor is None:
        executor = build_run_executor(ctx)
        ctx.subprocess_executor = executor

    ctx.emit(EventType.CODE_BLOCK_START, {"code": code})
    try:
        result = executor(code)
    except Exception as exc:  # 子进程死亡 / 超时 / 管道断开
        return ToolResult(success=False, message=f"子进程执行异常：{type(exc).__name__}: {exc}")

    # M1：每步自动归档脚本 + 补 steps.jsonl（promote_script_to_skill 的素材来源）
    _archive_executed_script(ctx, code, result)

    output: dict[str, object] = {"output": result.output, "logs": result.logs[-8000:]}
    if installed:
        output["auto_installed"] = installed
    ctx.emit(
        EventType.CODE_RESULT,
        {"ok": result.ok, "logs": result.logs[-8000:], "error": result.error},
    )
    if result.ok:
        return ToolResult(success=True, output=output, message="代码执行完成")
    return ToolResult(success=False, output=output, message=result.error or "代码执行失败")


def _script_slug(code: str) -> str:
    """从代码里抽一个语义名（脚本文件名片段）：函数名 > 赋值变量名 > hash 兜底。

    execute_code 的代码常以注释/docstring 开头且含中文，逐级回退保证总有可用名。
    """
    import hashlib
    import re

    for match in re.finditer(r"^def\s+([A-Za-z_]\w*)\s*\(", code, re.MULTILINE):
        return match.group(1)[:40]
    for match in re.finditer(r"^([A-Za-z_]\w*)\s*=", code, re.MULTILINE):
        return match.group(1)[:40]
    return f"exec_{hashlib.md5(code.encode('utf-8')).hexdigest()[:6]}"


def _script_description(code: str) -> str:
    """抓代码首行注释/docstring 作为一句人话描述，无则留空。"""
    for line in code.splitlines():
        line = line.strip()
        if line.startswith("#") and not line.startswith("#!"):
            text = line.lstrip("#").strip().strip('"""').strip()
            if text and not text.startswith("-*-") and not text.startswith("coding"):
                return text[:80]
        if (line.startswith('"""') or line.startswith("'''")) and len(line) > 6:
            return line.strip('"\'').strip()[:80]
    return ""


def _archive_executed_script(ctx: ToolContext, code: str, result) -> None:
    """execute_code 每步自动归档：脚本 + 元数据 + _scripts_index.jsonl + steps.jsonl。

    - 落盘 .workdir/script/<时间戳>-step<n>-<语义名>.py + 同名 .metadata.json + 追加索引；
    - 补父 run 的 steps.jsonl（该文件此前无人写），script_path 指向归档脚本；
    - 归档是附加能力：任何失败都静默跳过，绝不影响 execute_code 主流程。
    """
    try:
        import hashlib
        import re
        import time
        from pathlib import Path

        from app.workspace import SCRIPT_DIR

        SCRIPT_DIR.mkdir(parents=True, exist_ok=True)
        run_id = ctx.run_id or "anon"
        step = int(ctx.meta.get("_exec_step", 0)) + 1
        ctx.meta["_exec_step"] = step

        slug = _script_slug(code)
        ts = time.strftime("%Y%m%d_%H%M%S")
        filename = f"{ts}-step{step}-{slug}.py"
        path = SCRIPT_DIR / filename
        if path.exists():  # 同秒同名（LLM 连续两步同函数名）：加内容 hash 去重
            digest = hashlib.md5(code.encode("utf-8")).hexdigest()[:6]
            filename = f"{ts}-step{step}-{slug}-{digest}.py"
            path = SCRIPT_DIR / filename

        error_text = None if result.ok else (result.error or "execute_code 执行失败")
        metadata = {
            "filename": filename,
            "run_id": run_id,
            "step": step,
            "semantic_name": slug,
            "description": _script_description(code),
            "has_error": bool(error_text),
            "error": (error_text or "")[:500],
            "archived_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        path.write_text(code, encoding="utf-8")
        (path.with_suffix(".metadata.json")).write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with open(SCRIPT_DIR / "_scripts_index.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(metadata, ensure_ascii=False) + "\n")

        emitter = getattr(ctx, "event_emitter", None)
        archive = getattr(emitter, "archive", None) if emitter is not None else None
        if archive is not None:
            archive.record_step(
                step=step,
                code=code[:2000],
                output=str(result.output or "")[:2000] if result.ok else "",
                error=error_text,
                script_path=f".workdir/script/{filename}",
            )
    except Exception:  # noqa: BLE001
        pass


def _run_map_control(tool: str, params: dict) -> ToolResult:
    """地图控制工具：zoom / visibility / show_only / add_layer（Sprint 2 S2-1）。

    与样式工具不同，这些不发 LAYER_STYLE，而是发 ZOOM_TO_LAYER / LAYER_VISIBILITY
    专用事件让前端定位/显隐。纯地图控制无成果卡，由 llm_executor 的
    _MAP_CONTROL_TOOLS trace-only 分支兜底（不回退规则版）。
    """
    from app.data_registry import DATA_REGISTRY
    from app.models.events import EventType
    from app.tools.layer_style import apply_visibility

    ctx = get_current_context()
    emit = getattr(ctx, "emit", None)
    layer_id = str(params.get("layer_id") or "").strip()

    if tool in ("zoom_to_layer", "show_only_layer", "set_layer_visibility") and not layer_id:
        return ToolResult(success=False, message="缺少必填参数 layer_id")

    if tool == "zoom_to_layer":
        layer = DATA_REGISTRY.get_layer(layer_id)
        if layer is None:
            return ToolResult(success=False, message=f"未找到图层：{layer_id}")
        if emit is not None:
            emit(EventType.ZOOM_TO_LAYER, {"layer_id": layer_id})
        return ToolResult(success=True, output={"layer_id": layer_id}, message=f"已缩放到图层「{layer.name}」。")

    if tool == "set_layer_visibility":
        visible = bool(params.get("visible"))
        if not DATA_REGISTRY.get_layer(layer_id):
            return ToolResult(success=False, message=f"未找到图层：{layer_id}")
        apply_visibility(layer_id, visible, emit=emit)
        return ToolResult(
            success=True,
            output={"layer_id": layer_id, "visible": visible},
            message=("已显示" if visible else "已隐藏") + "该图层。",
        )

    if tool == "show_only_layer":
        layer = DATA_REGISTRY.get_layer(layer_id)
        if layer is None:
            return ToolResult(success=False, message=f"未找到图层：{layer_id}")
        for other in DATA_REGISTRY.list_layers():
            if other.id == layer_id:
                apply_visibility(other.id, True, emit=emit)
            elif other.visible:
                apply_visibility(other.id, False, emit=emit)
        return ToolResult(success=True, output={"layer_id": layer_id}, message=f"已仅显示图层「{layer.name}」。")

    if tool == "add_layer":
        if not layer_id:
            if params.get("file_path"):
                # 未给已存在图层 → 委托 register_layer 发布工作区文件
                from app.tools.register_layer import run_register_layer

                result = run_register_layer(params)
                if not result.success:
                    return result
                layer_id = str(result.output.get("layer_id") or "")
                return ToolResult(
                    success=True,
                    output=result.output,
                    message=f"{result.message}，已添加到地图。",
                )
            return ToolResult(success=False, message="add_layer 需要 layer_id 或 file_path")
        layer = DATA_REGISTRY.get_layer(layer_id)
        if layer is None:
            return ToolResult(success=False, message=f"未找到图层：{layer_id}")
        if not layer.visible:
            apply_visibility(layer_id, True, emit=emit)
        return ToolResult(success=True, output={"layer_id": layer_id}, message=f"图层「{layer.name}」已添加到地图。")

    if tool == "remove_layer":
        layer = DATA_REGISTRY.get_layer(layer_id)
        if layer is None:
            return ToolResult(success=False, message=f"未找到图层：{layer_id}")
        # 平台只读底座不可移除（与前端删除保护一致）：只能隐藏
        if layer.readonly:
            return ToolResult(
                success=False,
                message=f"「{layer.name}」是平台数据（只读底座），不可移除，可以隐藏它。",
            )
        DATA_REGISTRY.remove_layer(layer_id)
        if emit is not None:
            emit(EventType.LAYER_REMOVED, {"layer_id": layer_id})
        return ToolResult(success=True, output={"layer_id": layer_id}, message=f"图层「{layer.name}」已从地图移除。")

    if tool == "set_layer_order":
        layer = DATA_REGISTRY.get_layer(layer_id)
        if layer is None:
            return ToolResult(success=False, message=f"未找到图层：{layer_id}")
        try:
            to_index = int(params.get("to_index", 0))
        except (TypeError, ValueError):
            return ToolResult(success=False, message="to_index 需要是整数（0=最底层）")
        DATA_REGISTRY.move_layer(layer_id, to_index)
        if emit is not None:
            emit(EventType.LAYER_REORDER, {"layer_id": layer_id, "to_index": to_index})
        return ToolResult(
            success=True,
            output={"layer_id": layer_id, "to_index": to_index},
            message=f"图层「{layer.name}」已调整到显示顺序第 {to_index} 位（0=最底）。",
        )

    if tool == "set_basemap":
        _BASEMAPS = {"satellite": "卫星影像", "light": "浅灰街道", "dark": "深色街道", "streets": "街道详图"}
        basemap_id = str(params.get("basemap_id") or "").strip().lower()
        if basemap_id not in _BASEMAPS:
            return ToolResult(
                success=False,
                message=f"未知底图：{basemap_id or '（空）'}。可选：{', '.join(f'{k}（{v}）' for k, v in _BASEMAPS.items())}",
            )
        if emit is not None:
            emit(EventType.BASEMAP_CHANGED, {"basemap_id": basemap_id})
        return ToolResult(
            success=True,
            output={"basemap_id": basemap_id},
            message=f"底图已切换为「{_BASEMAPS[basemap_id]}」。",
        )

    return ToolResult(success=False, message=f"未实现的工具：{tool}")


def _run_layer_style_tool(tool: str, params: dict) -> ToolResult:
    """图层样式工具：update_layer_style / set_graduated_style / set_layer_visual_variables。

    参数面对齐常用 display 语义（fill_color / line_width / point_size / palette 等），
    实现统一走样式引擎（app/tools/layer_style.py），持久化 + emit LAYER_STYLE。
    """
    from app.data_registry import DATA_REGISTRY
    from app.tools import layer_style as ls

    ctx = get_current_context()
    emit = getattr(ctx, "emit", None)
    layer_id = str(params.get("layer_id") or "").strip()
    layer = DATA_REGISTRY.get_layer(layer_id)
    if layer is None:
        return ToolResult(success=False, message=f"未找到图层：{layer_id}")

    if tool == "update_layer_style":
        changes: dict = {}
        if params.get("color") is not None:
            changes["color"] = str(params["color"])
        if params.get("opacity") is not None:
            changes["opacity"] = float(params["opacity"])
        fill = params.get("fill_color") or params.get("fillColor")
        if fill is not None:
            changes["fillColor"] = str(fill)
        line = params.get("line_color") or params.get("strokeColor") or params.get("border_color")
        if line is not None:
            changes["strokeColor"] = str(line)
        lw = params.get("line_width")
        if lw is None:
            lw = params.get("border_width")
        if lw is not None:
            changes["strokeWidth"] = float(lw)
        if params.get("point_size") is not None:
            changes["radius"] = float(params["point_size"])
        if params.get("point_opacity") is not None:
            changes["opacity"] = float(params["point_opacity"])
        if params.get("visible") is not None:
            changes["visible"] = bool(params["visible"])
        if not changes:
            return ToolResult(success=False, message="没有可应用的样式改动（给 color/opacity/fill_color/line_width/point_size/visible 之一）")
        try:
            patch = ls.apply_style_update(layer_id, emit=emit, **changes)
        except ValueError as exc:
            return ToolResult(success=False, message=str(exc))
        return ToolResult(
            success=True,
            output={"layer_id": layer_id, "style": patch},
            message=f"已更新图层「{layer.name}」的样式。",
        )

    if tool == "set_graduated_style":
        field = str(params.get("field") or "").strip()
        if not field:
            return ToolResult(success=False, message="缺少必填参数 field（按哪个字段分层设色）")
        try:
            style = ls.build_graduated_style(
                layer_id,
                field,
                method=str(params.get("method") or "quantile"),
                classes=int(params.get("classes") or 5),
                palette=params.get("palette") or None,
                breaks=params.get("breaks"),
            )
            patch = ls.apply_renderer_style(layer_id, style, emit=emit)
        except ValueError as exc:
            return ToolResult(success=False, message=str(exc))
        return ToolResult(
            success=True,
            output={
                "layer_id": layer_id,
                "field": field,
                "method": style["visualVariables"]["graduated"]["method"],
                "classes": style["visualVariables"]["graduated"]["classes"],
                **patch,
            },
            message=f"已按字段「{field}」分层设色（{style['visualVariables']['graduated']['classes']} 级）。",
        )

    if tool == "set_layer_visual_variables":
        size_field = params.get("size_field") or None
        opacity_field = params.get("opacity_field") or None
        if not size_field and not opacity_field:
            return ToolResult(success=False, message="需要 size_field 或 opacity_field 至少一个")
        try:
            style = ls.build_visual_variables(
                layer_id,
                size_field=size_field,
                size_method=str(params.get("size_method") or "quantile"),
                size_classes=int(params.get("size_classes") or 5),
                size_range=params.get("size_range"),
                opacity_field=opacity_field,
                opacity_method=str(params.get("opacity_method") or "quantile"),
                opacity_classes=int(params.get("opacity_classes") or 5),
                opacity_range=params.get("opacity_range"),
            )
            patch = ls.apply_renderer_style(layer_id, style, emit=emit)
        except ValueError as exc:
            return ToolResult(success=False, message=str(exc))
        return ToolResult(success=True, output={"layer_id": layer_id, **patch}, message="已设置图层视觉变量渐变。")

    if tool == "set_categorized_style":
        field = str(params.get("field") or "").strip()
        if not field:
            return ToolResult(success=False, message="缺少必填参数 field（按哪个分类字段分色）")
        # 分类字段必须是图层真实字段
        field_names = {f.name for f in (layer.fields or [])}
        if field not in field_names:
            return ToolResult(
                success=False,
                message=f"图层「{layer.name}」没有字段：{field}。可用字段：{', '.join(sorted(field_names)) or '（无）'}",
            )
        try:
            style = ls.build_categorized_style(layer_id, field, palette=params.get("palette") or None)
            patch = ls.apply_renderer_style_keep(layer_id, style, emit=emit)
        except ValueError as exc:
            return ToolResult(success=False, message=str(exc))
        n = len(style["visualVariables"]["categorized"]["classes"])
        return ToolResult(
            success=True,
            output={"layer_id": layer_id, "field": field, "categories": n, **patch},
            message=f"已按字段「{field}」分类配色（{n} 类）。",
        )

    if tool == "set_layer_filter":
        conditions = params.get("filter")
        if not isinstance(conditions, dict):
            return ToolResult(success=False, message="filter 需要是属性条件字典（{} 清除过滤）")
        err = ls.validate_filter(layer, conditions)
        if err:
            return ToolResult(success=False, message=err)
        try:
            patch = ls.set_layer_filter(layer_id, conditions, emit=emit)
        except ValueError as exc:
            return ToolResult(success=False, message=str(exc))
        n = "全部要素" if not conditions else f"满足 {len(conditions)} 个条件"
        return ToolResult(
            success=True,
            output={"layer_id": layer_id, "filter": conditions, **patch},
            message=f"已{'清除过滤，显示' if not conditions else '设置图层过滤，只显示满足条件的要素'}（{n}）。",
        )

    if tool == "set_layer_label":
        field = params.get("field")
        if field is None or str(field).strip() == "":
            field = None
        else:
            field = str(field).strip()
            field_names = {f.name for f in (layer.fields or [])}
            if field not in field_names:
                return ToolResult(
                    success=False,
                    message=f"图层「{layer.name}」没有字段：{field}。可用字段：{', '.join(sorted(field_names)) or '（无）'}",
                )
        try:
            patch = ls.set_layer_label(layer_id, field, emit=emit)
        except ValueError as exc:
            return ToolResult(success=False, message=str(exc))
        return ToolResult(
            success=True,
            output={"layer_id": layer_id, "label_field": field, **patch},
            message=(f"已在要素旁标注「{field}」字段。" if field else "已清除该图层标注。"),
        )

    if tool == "highlight_features":
        conditions = params.get("filter")
        if not isinstance(conditions, dict):
            return ToolResult(success=False, message="filter 需要是属性条件字典（{} 清除高亮）")
        err = ls.validate_filter(layer, conditions)
        if err:
            return ToolResult(success=False, message=err)
        color = params.get("color") or None
        try:
            patch = ls.build_highlight_style(layer_id, conditions, color=color, emit=emit)
        except ValueError as exc:
            return ToolResult(success=False, message=str(exc))
        return ToolResult(
            success=True,
            output={"layer_id": layer_id, "filter": conditions, "color": color or "#ffd166", **patch},
            message="已高亮满足条件的要素。" if conditions else "已清除高亮。",
        )

    return ToolResult(success=False, message=f"未实现的工具：{tool}")


def _map_control_factory(tool: str) -> Callable[[dict], ToolResult]:
    return lambda params: _run_map_control(tool, params)


def _layer_style_factory(tool: str) -> Callable[[dict], ToolResult]:
    return lambda params: _run_layer_style_tool(tool, params)


TOOL_IMPLEMENTATIONS: dict[str, Callable[..., ToolResult]] = {
    "list_datasets": run_list_datasets,
    "get_dataset_schema": run_get_dataset_schema,
    "coverage": run_coverage,
    "isochrone": run_isochrone,
    "bivariate_moran": run_bivariate_moran,
    "buffer": run_buffer,
    "spatial_join": run_spatial_join,
    "nearest_distance": run_nearest_distance,
    "density": run_density,
    "supply_demand": run_supply_demand,
    "statistics": run_statistics,
    "spatial_clip": run_spatial_clip,
    # Phase 0 新增：取数 / 联网 / 导入
    "osm_call": _run_osm_call,
    "webfetch": run_webfetch,
    "websearch": run_websearch,
    "datasource_call": _run_datasource_call,
    "csv_to_geojson": run_csv_to_geojson,
    "register_layer": run_register_layer,
    "register_uploaded_file": run_register_uploaded_file,
    "extract_features": run_extract_features,
    "query_features": run_query_features,
    # Phase 1：受控代码执行（L0 子进程 + L1 权限门）
    "execute_code": _run_execute_code,
    # Sprint 2：地图控制 / 图层样式
    "add_layer": _map_control_factory("add_layer"),
    "zoom_to_layer": _map_control_factory("zoom_to_layer"),
    "set_layer_visibility": _map_control_factory("set_layer_visibility"),
    "show_only_layer": _map_control_factory("show_only_layer"),
    "update_layer_style": _layer_style_factory("update_layer_style"),
    "set_graduated_style": _layer_style_factory("set_graduated_style"),
    "set_layer_visual_variables": _layer_style_factory("set_layer_visual_variables"),
    "remove_layer": _map_control_factory("remove_layer"),
    "set_layer_order": _map_control_factory("set_layer_order"),
    "set_basemap": _map_control_factory("set_basemap"),
    "set_categorized_style": _layer_style_factory("set_categorized_style"),
    "set_layer_filter": _layer_style_factory("set_layer_filter"),
    "set_layer_label": _layer_style_factory("set_layer_label"),
    "highlight_features": _layer_style_factory("highlight_features"),
}


# 文件与系统 14 工具：统一走工作区沙箱实现（file_ops.run_file_tool）
def _file_ops_factory(tool: str) -> Callable[[dict], ToolResult]:
    from app.tools.file_ops import run_file_tool

    return lambda params: run_file_tool(tool, params or {})


_FILE_TOOLS = [
    "list_files",
    "read_file",
    "write_file",
    "append_to_file",
    "edit_file",
    "create_folder",
    "delete_file",
    "move_file",
    "copy_file",
    "search_files",
    "get_file_info",
    "run_terminal",
    "run_script",
    "download_url",
]

TOOL_IMPLEMENTATIONS.update({name: _file_ops_factory(name) for name in _FILE_TOOLS})


# 报告生成 3 工具：统一走报告实现（report_ops.run_report_tool）
def _report_factory(tool: str) -> Callable[[dict], ToolResult]:
    from app.tools.report_ops import run_report_tool

    return lambda params: run_report_tool(tool, params or {})


TOOL_IMPLEMENTATIONS.update(
    {name: _report_factory(name) for name in ("write_report", "export_pdf", "page_snapshot")}
)


# Skill 工具 10 件套（M1）：SkillStore 的薄包装
from app.tools.skill_ops import register_skill_tools  # noqa: E402

TOOL_IMPLEMENTATIONS.update(register_skill_tools())


def run_tool(name: str, params: object, *, ctx: ToolContext | None = None) -> ToolResult:
    """按名分发工具；ctx 非空时仅在 handler 调用期间注入 contextvar（try/finally 复位）。

    ctx 为 None 时行为与旧签名完全一致：handler 内部 get_current_context() 返回空上下文。
    """
    handler = TOOL_IMPLEMENTATIONS.get(name)
    if handler is None:
        return ToolResult(success=False, message=f"未实现的工具：{name}")
    token = set_current_context(ctx) if ctx is not None else None
    try:
        return handler(params)
    finally:
        if token is not None:
            reset_current_context(token)


__all__ = [
    "TOOLS",
    "TOOL_IMPLEMENTATIONS",
    "ToolSpec",
    "ToolResult",
    "run_tool",
    "ToolContext",
    "get_current_context",
    "set_current_context",
    "reset_current_context",
    "CoverageParams",
]
