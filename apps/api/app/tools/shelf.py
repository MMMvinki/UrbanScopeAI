"""工具货架：/tools 的 7 类分类目录（给人看的货架）。

两个核心原则（tools-module-design.md §1）：
1. 给人看的目录 ≠ AI 实际能用的工具：货架展示全部能力（含 gis-mcp 外部工具），
   AI 实际权限由后端 executor 单独控制；
2. 同一份数据源：前端目录从 GET /tools 读取，加新工具自动上架，不手工维护。

本地工具 category（inspection/analysis/data/system/map/code/processing）映射到 7 类货架；
gis-mcp 桥就绪时把其 90+ 个英文空间工具并到「专业 GIS 分析」货架。
"""

from __future__ import annotations

from app.models.tool import ToolSpec, ToolStatus

# 7 类货架：显示顺序即 /tools 返回顺序（tools-module-design.md §2）
SHELF_ORDER = [
    "文件与系统",
    "地图与可视化",
    "空间分析",
    "数据与栅格",
    "网络与搜索",
    "报告生成",
    "专业GIS分析",
]

# 本地注册表 category → 货架名（未映射的保留原 category 名）
_LOCAL_CATEGORY_TO_SHELF = {
    "file": "文件与系统",  # 文件操作全套（14 个，工作区沙箱）
    "code": "文件与系统",  # execute_code 跑代码
    "map": "地图与可视化",
    "analysis": "空间分析",
    "processing": "空间分析",
    "skill": "空间分析",  # Skill 工具 10 件套（M1）
    "inspection": "数据与栅格",
    "data": "数据与栅格",
    "system": "网络与搜索",
    "report": "报告生成",  # 分节报告 / 导出 PDF / 页面快照
}

_SHELF_RANK = {name: index for index, name in enumerate(SHELF_ORDER)}

# 工具卡「发起对话」预置提示词：白话、可直接发给 Agent（吸收原「通用分析」5 个快捷启动）
TOOL_PROMPT_HINTS: dict[str, str] = {
    "coverage": "帮我分析某类设施的服务覆盖率。",
    "buffer": "帮我对某类设施图层生成服务半径缓冲区。",
    "spatial_join": "帮我叠加分析两个图层的空间关系。",
    "nearest_distance": "帮我计算每个街区到最近设施的距离。",
    "density": "帮我分析人口密度分布情况。",
    "supply_demand": "帮我分析公共服务供需是否匹配。",
    "statistics": "帮我汇总分析多个图层的指标。",
    "extract_features": "帮我从某个数据图层中提取符合条件的数据，做成一个新图层。",
    "osm_call": "帮我从 OpenStreetMap 下载某个地方的路网 / POI / 建筑等真实地图数据。",
    "datasource_call": "帮我获取某个城市的行政区划等公开数据。",
    "query_features": "帮我在某个图层中查询符合条件的要素。",
    "remove_layer": "帮我把某个图层从地图上移除。",
    "set_layer_order": "帮我调整地图图层的显示顺序。",
    "register_layer": "帮我把工作区里下载好的数据文件发布成地图图层。",
    "register_uploaded_file": "帮我把我上传并暂存的文件注册成地图图层。",
    "csv_to_geojson": "帮我把带经纬度的表格数据转换成地图数据。",
    "list_files": "帮我把工作区里有哪些文件列出来。",
    "read_file": "帮我看看工作区里某个文件的内容。",
    "write_file": "帮我把一段内容保存到工作区文件里。",
    "search_files": "帮我在工作区里搜一下包含某个关键字的文件。",
    "download_url": "帮我把某个网址的数据下载到工作区。",
    "run_script": "帮我跑一段 Python 脚本处理数据。",
    "write_report": "帮我生成一份分章节的分析报告。",
    "export_pdf": "帮我把报告导出成 PDF 文件。",
    "page_snapshot": "帮我抓取某个网页存档并提取文字。",
}


def _shelf_of(category: str) -> str:
    return _LOCAL_CATEGORY_TO_SHELF.get(category, category)


def _mcp_parameters(input_schema: dict) -> dict[str, str]:
    """把 gis-mcp 的 JSON Schema properties 压成 {参数名: 说明} 供货架展示。"""
    properties = (input_schema or {}).get("properties") or {}
    out: dict[str, str] = {}
    for name, spec in properties.items():
        if isinstance(spec, dict):
            desc = str(spec.get("description") or "") or ""
            enum = spec.get("enum")
            if isinstance(enum, list) and enum:
                desc = f"{desc}（可选值：{', '.join(str(e) for e in enum[:6])}）".strip()
            out[str(name)] = desc
    return out


def catalog_tools(bridge: object | None = None) -> list[ToolSpec]:
    """合并本地 + gis-mcp 的全部工具，按 7 类货架排序返回（前端 /tools 数据源）。

    bridge：GisMcpBridge（或 None）。就绪时并入其全部外部空间工具；
    未就绪（无 LLM 配置 / 未连上）时专业 GIS 货架为空，由前端展示占位说明。
    """
    from app.tools.registry import TOOLS

    local: list[ToolSpec] = []
    for spec in TOOLS:
        # description 是写给 LLM 的完整配方（含坑/路径约定）；货架给人看用白话版。
        # LLM 侧 _tool_lines/_tool_schemas 直接读 TOOLS 原始 description，不受影响。
        user_desc = spec.display_description or spec.description
        item = spec.model_copy(
            update={"category": _shelf_of(spec.category), "description": user_desc}
        )
        item.prompt_hint = TOOL_PROMPT_HINTS.get(spec.name)
        local.append(item)

    gis: list[ToolSpec] = []
    if bridge is not None and getattr(bridge, "is_ready", lambda: False)():
        for tool in bridge.tools():
            name = str(tool.get("name") or "")
            if not name:
                continue
            desc = str(tool.get("description") or "") or "外部 GIS 空间工具。"
            gis.append(
                ToolSpec(
                    name=f"gis_{name}",
                    category="专业GIS分析",
                    description=desc,
                    status=ToolStatus.ready,
                    parameters=_mcp_parameters(tool.get("input_schema") or {}),
                    json_schema=(tool.get("input_schema") or {}) or None,
                    prompt_hint=f"帮我用 GIS 工具「{name}」做空间分析：{desc[:120]}",
                )
            )

    merged = [*local, *gis]
    merged.sort(key=lambda item: (_SHELF_RANK.get(item.category, 99), item.name))
    return merged
