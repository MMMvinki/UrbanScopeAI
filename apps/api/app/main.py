import asyncio
import json
import os
import re
import sys
import threading
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from app.agent import llm_client
from app.agent.emitter import EventEmitter
from app.agent.executor import execute
from app.agent.memory import MEMORY
from app.agent.planner import plan_for
from app.archive import RunArchive, RunIndex
from app.data_registry import DATA_REGISTRY
from app.geojson_utils import clean_layer_id
from app.mcp import get_gis_mcp_bridge
from app.memory_store import append_summary, build_run_summary
from app.skills.store import STORE, SkillError
from app.models.agent import ExecuteRequest, PlanRequest, PlanResult
from app.models.events import AgentEventEnvelope, EventType
from app.models.layer import LayerMetadata, LayerPatch, LayerSchema
from app.models.result import ExecutionResult, ResultGroup
from app.permissions import PermissionAction, PermissionPolicy, PermissionRuntime
from app.permissions.broker import BROKER
from app.models.project import ProjectCreate, ProjectDetail, ProjectInfo, ProjectUpdate
from app.projects import (
    DEFAULT_PROJECT_ID,
    assert_project_id,
    create_project,
    delete_project,
    get_project,
    list_projects,
    project_dir,
    project_layer_dir,
    update_project,
)
from app.skills import SKILLS, SkillSpec, all_skills
from app.tools import TOOLS, ToolSpec
from app.tools.context import ToolContext
from app.tools.shelf import catalog_tools
from app.upload_service import UploadError, ingest_upload, stage_upload as stage_upload_service
from app.workspace import (
    DEM_TILES_DIR,
    LAYER_DATA_DIR,
    PLOTS_DIR,
    WORKSPACE_ROOT,
    ensure_layer_data_in_workspace,
    ensure_workspace,
)

# 审批类目（/agent/approve 请求体）
from pydantic import BaseModel


class ApproveRequest(BaseModel):
    request_id: str
    allowed: bool
    remember: bool = False


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 启动即预热 MCP 桥（后台线程连 gis-mcp，不阻塞启动；未就绪则首请求前自动补上）
    get_gis_mcp_bridge()
    ensure_workspace()
    # 内置图层 GeoJSON 物化到 workspace/data/layers/（execute_code 可直接读取）
    ensure_layer_data_in_workspace()
    # 轻量混合：扫描 workspace/data/layers/，把非 demo 的已发布图层（如 OSM 建筑）恢复进注册表，
    # 使下载/发布的数据跨重启仍在地图上
    DATA_REGISTRY.load_workspace_layer_files(LAYER_DATA_DIR)
    # Sprint 2：样式侧车合并回注册表，改过的样式跨重启还原
    DATA_REGISTRY.load_workspace_styles()
    yield


app = FastAPI(title="UrbanScope AI API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    # 本地开发：允许任意 localhost/127.0.0.1/[::1] 端口（vite 端口可能因占用漂移到 5174/5175…）
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1|\[::1\]):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "urbanscope-api"}


@app.get("/dem-tiles/{z}/{x}/{y}.png")
def dem_tile(z: int, x: int, y: int) -> FileResponse:
    """DEM 高程瓦片（Web Mercator 金字塔，build_dem_tiles.py 生成）。"""
    if z < 0 or x < 0 or y < 0 or x >= 2**z or y >= 2**z:
        raise HTTPException(status_code=404, detail="瓦片越界")
    path = DEM_TILES_DIR / str(z) / f"{x}_{y}.png"
    if not path.exists():
        raise HTTPException(status_code=404, detail="瓦片不存在")
    return FileResponse(path, media_type="image/png")


@app.get("/artifacts/plots/{filename}")
def get_plot(filename: str) -> FileResponse:
    """返回 agent 保存的 matplotlib 图表 PNG（成果栏 image widget 加载）。

    文件名白名单：仅单个安全文件名，禁止路径穿越/子路径。
    """
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
        raise HTTPException(status_code=404, detail="无效的图表文件名")
    path = PLOTS_DIR / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="图表不存在")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-cache"})


@app.get("/tools", response_model=list[ToolSpec])
def list_tools() -> list[ToolSpec]:
    """7 类货架目录：本地全部工具 + gis-mcp 外部空间工具（桥就绪时并入）。

    给人看的货架 ≠ AI 权限：这里返回全部能力，AI 实际可用性由 executor 单独控制。
    """
    return catalog_tools(get_gis_mcp_bridge())


@app.get("/skills", response_model=list[SkillSpec])
def list_skills(scope: str | None = None) -> list[SkillSpec]:
    """列出全部 Skill（流程引导 + 带脚本合并，每项带 kind）。scope=workspace/builtin 过滤带脚本部分。"""
    return [SkillSpec(**item) for item in all_skills(scope)]


@app.get("/layers", response_model=list[LayerMetadata])
def list_layers() -> list[LayerMetadata]:
    return DATA_REGISTRY.list_layers()


@app.get("/layers/{layer_id}/schema", response_model=LayerSchema)
def get_layer_schema(layer_id: str) -> LayerSchema:
    schema = DATA_REGISTRY.get_schema(layer_id)
    if schema is None:
        raise HTTPException(status_code=404, detail=f"未找到图层：{layer_id}")
    return schema


@app.get("/layers/geojson")
def all_layer_geojson() -> dict[str, dict]:
    """全部图层真实几何（FeatureCollection 索引，前端一次拉取渲染）。"""
    return DATA_REGISTRY.list_geojson()


@app.get("/layers/{layer_id}/geojson")
def layer_geojson(layer_id: str) -> dict:
    """单个图层的真实几何（GeoJSON FeatureCollection）。"""
    geojson = DATA_REGISTRY.get_geojson(layer_id)
    if geojson is None:
        raise HTTPException(status_code=404, detail=f"图层无几何数据：{layer_id}")
    return geojson


@app.patch("/layers/{layer_id}", response_model=LayerMetadata)
def patch_layer(layer_id: str, body: LayerPatch) -> LayerMetadata:
    """PATCH /layers/{id}：改名称/可见性/样式（单值 + graduated 描述）并持久化到样式侧车。

    exclude_unset 区分「未传」与「显式置 null」（styleMode: null 清除分档样式）。
    """
    patch = body.model_dump(exclude_unset=True)
    try:
        layer = DATA_REGISTRY.apply_layer_patch(layer_id, patch)
    except ValueError as exc:
        # readonly 平台图层：试图改数据内容字段 → 400（样式/名字/可见性放行）
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if layer is None:
        raise HTTPException(status_code=404, detail=f"未找到图层：{layer_id}")
    return layer


@app.delete("/layers/{layer_id}")
def delete_layer(layer_id: str) -> dict[str, str]:
    """删除图层：注册表 + 样式侧车 + 磁盘物化文件 + 项目 manifest。

    彻底删除保证跨重启不复活。平台数据（readonly）不可删，只能隐藏。
    """
    layer = DATA_REGISTRY.get_layer(layer_id)
    if layer is None:
        raise HTTPException(status_code=404, detail=f"未找到图层：{layer_id}")
    if layer.readonly:
        raise HTTPException(status_code=400, detail=f"平台数据只读，不可删除：{layer_id}")
    DATA_REGISTRY.delete_layer(layer_id)
    return {"deleted": layer_id}


@app.post("/layers/upload")
async def upload_layer(
    file: UploadFile = File(...), layer_name: str | None = Form(default=None)
) -> dict:
    """上传 CSV/GeoJSON → 注册为地图图层（自动识别经纬度列）。"""
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    try:
        return ingest_upload(file.filename or "upload", content, layer_name=layer_name)
    except UploadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/uploads/stage")
async def stage_upload(
    file: UploadFile = File(...),
) -> dict:
    """暂存上传文件：只落盘，不解析/不注册图层。

    Agent 对话框上传走这里：文件先挂到附件 chip，等 Agent 真正要用时再通过
    register_uploaded_file 工具按需创建图层（避免上传即上地图）。
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传文件为空")
    return stage_upload_service(file.filename or "upload", content)


@app.post("/agent/plan", response_model=PlanResult)
def plan(body: PlanRequest) -> PlanResult:
    return plan_for(body.question, history=body.history, conversation_id=body.conversation_id)


def _final_answer_from_result(result: ExecutionResult | None) -> str | None:
    """从 ExecutionResult 提炼 final_answer.md 内容（best-effort）。"""
    if result is None:
        return None
    lines: list[str] = []
    if result.result_group is not None:
        lines.append(f"# {result.result_group.title}")
        evidence = list(dict.fromkeys(result.evidence or []))
        lines.extend(f"- {e}" for e in evidence[:12])
    if result.reflection:
        lines.append("")
        lines.append("## 反思")
        lines.append(result.reflection)
    return "\n".join(lines) if lines else None


@app.post("/agent/execute")
async def execute_plan_sse(body: ExecuteRequest, request: Request) -> StreamingResponse:
    """SSE 真流式：同步 executor 在后台线程跑，事件经 asyncio.Queue 桥接产流。

    事件帧：run_start → (data_check / thought_delta / tool_start / tool_result /
    generated_layer / ...)* → execution(终结载荷) → stream_end。
    客户端断连 → cancel_event 置位，worker 关键循环尽早停，archive 仍正常关闭。
    """
    run_id = uuid.uuid4().hex
    queue: asyncio.Queue[AgentEventEnvelope | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    cancel_event = threading.Event()
    ctx = ToolContext(
        workspace_path=WORKSPACE_ROOT,
        run_id=run_id,
        cancel_event=cancel_event,
    )
    archive = RunArchive.open(run_id=run_id, prompt=body.question, workspace_path=str(WORKSPACE_ROOT))
    emitter = EventEmitter(run_id, queue, loop, archive, conversation_id=body.conversation_id)
    ctx.event_emitter = emitter
    # L1 权限门：execute_code 等写操作默认 ASK；审批经 BROKER（SSE 发 permission_request + POST /agent/approve 解阻塞）
    permission_runtime = PermissionRuntime(
        policy=PermissionPolicy(
            workspace_path=str(WORKSPACE_ROOT),
            tool_overrides={"execute_code": PermissionAction.ASK},
        ),
        approval_callback=lambda tool, args, decision: BROKER.wait(
            tool, args, decision, emit=emitter.emit, cancel_event=cancel_event
        ),
    )
    ctx.permission_runtime = permission_runtime

    def worker() -> None:
        result: ExecutionResult | None = None
        status = "error"
        try:
            emitter.emit(EventType.RUN_START, {"prompt": body.question, "run_id": run_id})
            result = execute(body, ctx=ctx, emit=emitter.emit)
            status = "success" if result is not None else "error"
        except Exception as exc:
            emitter.emit(EventType.ERROR, {"message": str(exc)})
        finally:
            # 会话记忆：run 结束后把本轮摘要写入 conversation memory（供后续轮次注入）
            if result is not None:
                if not result.final_answer:
                    # 兜底：final_answer 未显式设置时取 trace 最后一条 text 事件作最终回复
                    for event in reversed(result.trace):
                        if event.type == "text" and event.content and event.content.strip():
                            result.final_answer = event.content.strip()
                            break
                append_summary(
                    body.conversation_id,
                    body.question,
                    build_run_summary(result),
                )
            if result is not None:
                emitter.emit(EventType.EXECUTION, result.model_dump(mode="json"))
            archive.close(status=status, final_answer=_final_answer_from_result(result))
            emitter.emit(EventType.STREAM_END, {"run_id": run_id, "status": status})
            # 哨兵经同一线程安全通道入队，保证排在 EXECUTION/STREAM_END 之后（FIFO），
            # 避免 worker 线程直接 put_nowait(None) 抢先导致 stream_end 帧被跳过。
            loop.call_soon_threadsafe(queue.put_nowait, None)
            # 关闭 run 级子进程执行器（防泄漏；未创建时为空操作）
            if ctx.subprocess_executor is not None:
                try:
                    ctx.subprocess_executor.cleanup()
                except Exception:
                    pass

    threading.Thread(target=worker, name=f"exec-{run_id}", daemon=True).start()

    async def gen():
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                payload = json.dumps(item.model_dump(mode="json"), ensure_ascii=False)
                yield f"event: {item.type.value}\ndata: {payload}\n\n"
        except asyncio.CancelledError:
            # 客户端断连：通知 worker 尽早停（同步阻塞工具查 cancel_event）
            cancel_event.set()
            raise

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/agent/results", response_model=ResultGroup | None)
def latest_results() -> ResultGroup | None:
    return MEMORY.latest_result_group()


@app.get("/agent/status")
def agent_status() -> dict[str, object]:
    """Agent 运行模式诊断：LLM 是否已配置、当前模型、gis-mcp 就绪状态。"""
    bridge = get_gis_mcp_bridge()
    return {
        "llm": llm_client.is_available(),
        "model": llm_client.MODEL if llm_client.is_available() else None,
        "base_url": llm_client.BASE_URL,
        "mcp": {
            "ready": bridge.is_ready() if bridge is not None else False,
            "tools": len(bridge.tools()) if bridge is not None and bridge.is_ready() else 0,
            "error": bridge._error if bridge is not None and bridge._error else None,
        },
    }


# ── Run Archive / Trace Archive（P1.3） ────────────────────────────────


@app.post("/agent/approve")
def approve(body: ApproveRequest) -> dict[str, object]:
    """审批一个 ASK 权限请求：解阻塞等审批的 worker 线程；remember 时持久化规则。"""
    ok = BROKER.resolve(body.request_id, allowed=body.allowed, remember=body.remember)
    if not ok:
        raise HTTPException(status_code=404, detail=f"审批请求不存在或已处理：{body.request_id}")
    return {"request_id": body.request_id, "allowed": body.allowed, "remember": body.remember}


@app.get("/agent/permissions/pending")
def pending_permissions() -> dict[str, object]:
    """当前未决的权限审批请求（前端恢复 / 调试用）。"""
    return {"requests": BROKER.pending_requests()}


@app.get("/runs", response_model=list[RunIndex])
def list_runs(limit: int = 50) -> list[RunIndex]:
    """已归档 agent run 列表（新→旧）。"""
    return RunArchive.list_runs(WORKSPACE_ROOT, limit=limit)


@app.get("/runs/{run_id}")
def get_run(run_id: str) -> dict[str, object]:
    """单个 run 的完整追溯：meta + events + tool_calls + artifacts + llm_usage。"""
    archive = RunArchive.load(WORKSPACE_ROOT, run_id)
    if archive is None:
        raise HTTPException(status_code=404, detail=f"未找到 run：{run_id}")
    return {
        "meta": archive.meta,
        "events": archive.read_events(),
        "tool_calls": archive.read_tool_calls(),
        "artifacts": archive.read_artifacts(),
        "llm_usage": archive.read_llm_usage(),
        "steps": archive.read_steps(),
    }


# ── 项目（每间独立厨房）：列表 / 创建 / 详情 / 切换 / 删除 ──


@app.get("/projects", response_model=list[ProjectInfo])
def api_list_projects() -> list[ProjectInfo]:
    """全部项目（含默认项目），layer_count / run_count 已统计。"""
    return [ProjectInfo(**meta) for meta in list_projects()]


@app.post("/projects", response_model=ProjectInfo, status_code=201)
def api_create_project(body: ProjectCreate) -> ProjectInfo:
    """新建项目：独立目录 + 独立图层空间，不碰当前图层。"""
    meta = create_project(body.name, body.description)
    return ProjectInfo(**meta)


@app.get("/projects/active")
def api_active_project() -> dict[str, object]:
    """当前激活的项目（信息 + current 标记，供前端恢复选中）。"""
    pid = DATA_REGISTRY.current_project_id
    info = get_project(pid) or {"id": pid, "name": pid, "description": "", "created_at": ""}
    return {**info, "current": True}


@app.get("/projects/{project_id}", response_model=ProjectDetail)
def api_project_detail(project_id: str) -> ProjectDetail:
    """项目详情：信息 + 数据文件清单（项目目录 layers） + 最近运行历史。"""
    info = get_project(project_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"未找到项目：{project_id}")

    # 数据文件清单：项目图层目录里的文件 + 是否已上地图 + 真实要素数（从文件实读）
    files: list[dict] = []
    on_map = DATA_REGISTRY.project_layer_ids() if DATA_REGISTRY.current_project_id == project_id else set()
    for path in sorted(project_layer_dir(project_id).glob("*.json")):
        feature_count = 0
        try:
            feature_count = len(json.loads(path.read_text(encoding="utf-8")).get("features", []))
        except (json.JSONDecodeError, OSError):
            pass
        layer_id = clean_layer_id(path.stem)
        files.append(
            {
                "name": path.name,
                "type": "geojson",
                "size": path.stat().st_size,
                "feature_count": feature_count,
                "layer_id": layer_id,
                "on_map": layer_id in on_map,
            }
        )

    # 运行历史：全局 archive 最近 10 条摘要
    runs: list[dict] = []
    for run in RunArchive.list_runs(WORKSPACE_ROOT, limit=10):
        runs.append(
            {
                "run_id": run.run_id,
                "question": run.prompt[:80],
                "status": run.status,
                "created_at": run.created_at,
                "tool_count": run.tool_call_count,
            }
        )
    return ProjectDetail(**info, files=files, runs=runs)


@app.put("/projects/{project_id}/active")
def api_switch_project(project_id: str) -> dict[str, object]:
    """切换到某项目：保存当前项目图层 → 换入目标项目图层 → 返回当前图层 id 列表。"""
    if get_project(project_id) is None:
        raise HTTPException(status_code=404, detail=f"未找到项目：{project_id}")
    pid = DATA_REGISTRY.switch_project(project_id)
    return {"project_id": pid, "layers": [layer.id for layer in DATA_REGISTRY.list_layers()]}


@app.delete("/projects/{project_id}")
def api_delete_project(project_id: str) -> dict[str, object]:
    """删除项目（默认项目不可删）。删除前若正处于该项目，先切回默认项目。"""
    if project_id == DEFAULT_PROJECT_ID:
        raise HTTPException(status_code=400, detail="默认项目不可删除")
    if DATA_REGISTRY.current_project_id == project_id:
        DATA_REGISTRY.switch_project(DEFAULT_PROJECT_ID)
    if not delete_project(project_id):
        raise HTTPException(status_code=404, detail=f"未找到项目：{project_id}")
    return {"deleted": project_id}


@app.patch("/projects/{project_id}", response_model=ProjectInfo)
def api_update_project(project_id: str, body: ProjectUpdate) -> ProjectInfo:
    """改名 / 改描述 / 置顶：只更新传了的字段（目录名=id 不动）。"""
    try:
        meta = update_project(
            project_id,
            name=body.name,
            description=body.description,
            pinned=body.pinned,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if meta is None:
        raise HTTPException(status_code=404, detail=f"未找到项目：{project_id}")
    return ProjectInfo(**meta)


@app.post("/projects/{project_id}/open-folder")
def api_open_project_folder(project_id: str) -> dict[str, object]:
    """在操作系统的文件管理器打开项目工作区目录（本机 localhost 桌面功能）。

    win32 用 os.startfile 直接弹资源管理器；其他平台只返回路径由前端提示。
    """
    try:
        pid = assert_project_id(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    d = project_dir(pid)
    opened = False
    if sys.platform == "win32":
        try:
            os.startfile(str(d))  # type: ignore[attr-defined]  # noqa: S606
            opened = True
        except OSError:
            opened = False
    return {"path": str(d), "opened": opened}


# ─────────────────────────────────────────────────────────────────────
# Skills（M1）：可复用分析能力 + 脚本货架 REST 端点
# 与 /tools、/layers 共用同一份 STORE / DATA_REGISTRY。前端 SkillPanel 数据源。
# ─────────────────────────────────────────────────────────────────────


class SkillCreateRequest(BaseModel):
    skill_id: str
    name: str = ""
    description: str = ""
    code: str | None = None
    requirements: list[str] | None = None


class SkillRunRequest(BaseModel):
    params: dict = {}


class SkillEditRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    version: str | None = None
    code: str | None = None
    input_schema: dict | None = None
    output_schema: dict | None = None
    requirements: list[str] | None = None


class SkillPromoteRequest(BaseModel):
    script_name: str
    name: str | None = None
    description: str | None = None


@app.get("/skills/{skill_id}")
def api_get_skill(skill_id: str) -> dict:
    skill = STORE.get(skill_id)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"skill 不存在：{skill_id}")
    return skill.model_dump(mode="json")


@app.post("/skills")
def api_create_skill(body: SkillCreateRequest) -> dict:
    try:
        skill = STORE.create(
            skill_id=body.skill_id,
            name=body.name or body.skill_id,
            description=body.description,
            code=body.code,
            requirements=body.requirements,
        )
    except SkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return skill.model_dump(mode="json")


@app.post("/skills/{skill_id}/run")
def api_run_skill(skill_id: str, body: SkillRunRequest) -> dict:
    """运行 Skill：真子进程执行 → 产物图层注册上图 → 返回 run_id/layers/plot_files。"""
    try:
        return STORE.run(skill_id, body.params or {}, ctx=None)
    except SkillError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/skills/{skill_id}")
def api_edit_skill(skill_id: str, body: SkillEditRequest) -> dict:
    patch = body.model_dump(exclude_unset=True)
    if not patch:
        raise HTTPException(status_code=400, detail="没有需要修改的字段")
    try:
        skill = STORE.update(skill_id, patch)
    except SkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return skill.model_dump(mode="json")


@app.post("/skills/{skill_id}/copy")
def api_copy_skill(skill_id: str) -> dict:
    try:
        skill = STORE.copy_to_workspace(skill_id)
    except SkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return skill.model_dump(mode="json")


@app.post("/skills/{skill_id}/validate")
def api_validate_skill(skill_id: str, body: SkillEditRequest | None = None) -> dict:
    code = body.code if body is not None else None
    try:
        result = STORE.validate(skill_id, code=code)
    except SkillError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return result.model_dump(mode="json")


@app.post("/skills/promote-script")
def api_promote_script(body: SkillPromoteRequest) -> dict:
    try:
        skill = STORE.promote_script(
            body.script_name,
            name=body.name,
            description=body.description,
        )
    except SkillError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return skill.model_dump(mode="json")


@app.get("/scripts")
def api_list_scripts() -> dict:
    """脚本货架（execute_code 归档的历史脚本）。"""
    from app.tools.skill_ops import run_list_scripts

    result = run_list_scripts({})
    return result.output


@app.get("/scripts/{script_name}")
def api_read_script(script_name: str) -> dict:
    from app.tools.skill_ops import run_read_script

    result = run_read_script({"script_name": script_name})
    if not result.success:
        raise HTTPException(status_code=404, detail=result.message)
    return result.output
