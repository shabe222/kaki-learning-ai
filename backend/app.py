"""
FastAPI 应用入口

启动：py -3.11 app.py 或 uvicorn app:app
本文件只负责：lifespan + 中间件 + 路由注册 + uvicorn 启动
所有业务路由已拆分到 routers/ 目录
"""
import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings, validate_production_secrets
from database.connection import init_db, close_db, init_redis, close_redis, get_db_session
from services.vector_service import init_milvus, close_milvus, rebuild_vocabulary
from services.oss_service import init_minio, close_minio
from utils.db import get_build_db_url
from utils.errors import BusinessError
from middleware.logging_middleware import StructuredLoggingMiddleware
from middleware.rate_limit_middleware import RateLimitMiddleware
from middleware.slow_query_middleware import SlowQueryMiddleware

# 业务路由
from routers.frontend_pages import router as frontend_pages_router
from routers.auth import router as auth_router
from routers.profile import router as profile_router
from routers.chat import router as chat_router
from routers.video import router as video_router
from routers.search import router as search_router
from routers.oss import router as oss_router
from routers.system import router as system_router
from routers.knowledge_graph import router as knowledge_graph_router  # 4 路由（KG 4端点）
from routers.wizard import router as wizard_router
from routers.study import router as study_router
from routers.materials import router as materials_router
from routers.plans import router as plans_router
from routers.exam import router as exam_router
from routers.agents_day4 import router as agents_day4_router  # 7 端点：profile init + document/question/mindmap/code/path/tutor
from routers.community import router as community_router  # 1 端点：active-peers
from routers.agent_api import router as agent_api_router  # 8 agent 单点 + 1 workflow + 1 ctx 读取
from routers.agent_records import router as agent_records_router  # 5 端点：Agent 记录页（stats/list/trend/recent-outputs/detail）
from routers.style_reference import router as style_reference_router  # 3 端点：style 列表 / 元信息 / 生成（SOP image2 角色）
from routers.items import router as items_router  # 3 端点：产物保存 / 列表 / 删除
from routers.learning_path import router as learning_path_router  # 4 端点：9 章状态 / start / 详情 / 懒生成
from routers.learning_graph import router as learning_graph_router  # T01: 多图谱 CRUD + 节点管理 + 双视图
from routers.learning_evaluator import router as learning_evaluator_router
from routers.notes import router as notes_router  # T02: 笔记 CRUD + 搜索 + 关联
from routers.learning_workspace import router as learning_workspace_router
from routers.dashboard_system import router as dashboard_system_router  # 首页独立业务系统
from routers.admin_auth import router as admin_auth_router
from routers.admin_overview import router as admin_overview_router
from routers.admin_users import router as admin_users_router
from routers.admin_announcements import router as admin_announcements_router
from routers.admin_operations import router as admin_operations_router
from routers.admin_system import router as admin_system_router
from routers.admin_audit import router as admin_audit_router
from routers.admin_workbench import router as admin_workbench_router
from routers.profile_onboarding import router as profile_onboarding_router
from routers.harvest_adventure import router as harvest_adventure_router
from routers.impressions import router as impressions_router
from routers.memories import router as memories_router
from routers.workspace_tutor import router as workspace_tutor_router
from routers.workspace_search import router as workspace_search_router
from routers.workspace_agents import router as workspace_agents_router
from routers.workspace_inputs import router as workspace_inputs_router
from routers.workspace_materials import router as workspace_materials_router
from routers.proactive_tutor import router as proactive_tutor_router
from routers.tide_shared_perspective import router as tide_shared_perspective_router
from routers.tide_workspace_object import router as tide_workspace_object_router
from routers.tide_artifact_action import router as tide_artifact_action_router
from routers.tide_search_evidence import router as tide_search_evidence_router
from routers.tide_user_context import router as tide_user_context_router
from routers.tide_long_term_memory import router as tide_long_term_memory_router
from routers.study_time import router as darya_study_time_router
from routers.darya_current_perspective import router as darya_current_perspective_router
from routers.darya_learning_loop import router as darya_learning_loop_router
from routers.personalized_artifacts import router as personalized_artifacts_router
from routers.tiered_assessments import router as tiered_assessments_router
from routers.code_labs import router as code_labs_router
from routers.conversation_capability_actions import router as conversation_capability_actions_router
from routers.darya_self_understanding import router as darya_self_understanding_router
from routers.darya_game_state import router as darya_game_state_router
from routers.darya_storybook_config import router as darya_storybook_config_router
from routers.darya_storybook_proxy import router as darya_storybook_proxy_router
from routers.study_resources import router as study_resources_router
from routers.research import router as research_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时 fail-fast 校验：生产模式下 SECRET_KEY 必须是真值
    validate_production_secrets()

    # 启动时就解析一次对话链路（/darya 真正使用的那个工厂）。
    # 好处有两个：/api/health 从第一秒起就报真实状态，而不是等第一次对话才发现问题；
    # 生产模式下 provider 配错会在启动时直接失败，而不是运行中静默降级。
    try:
        from services.llm_service_real import get_llm_service, get_runtime_llm_status

        await get_llm_service()
        logger.info("[App] 对话链路已就绪: %s", get_runtime_llm_status())
    except Exception as exc:
        if settings.MODE == "production":
            logger.error("[App] 对话链路初始化失败: %s", exc)
            raise
        logger.warning("[App] 对话链路初始化失败（开发模式继续）: %s", exc)

    # 启动时初始化数据库
    logger.info("[App] 正在初始化数据库...")
    db_url = get_build_db_url()
    logger.info(f"[App] 数据库: {db_url.split('@')[-1] if '@' in db_url else db_url[:50]}")
    await init_db(db_url)
    from routers.admin_workbench import apply_runtime_settings
    async with get_db_session() as admin_db:
        await apply_runtime_settings(admin_db)
    from services.video_generation_jobs import fail_interrupted_video_jobs
    async with get_db_session() as db:
        interrupted = await fail_interrupted_video_jobs(db)
        if interrupted:
            logger.warning("[CloudVideo] startup reconciled %s interrupted jobs", interrupted)
    from services.proactive_tutor import proactive_scheduler_loop
    proactive_stop = asyncio.Event()
    proactive_task = asyncio.create_task(proactive_scheduler_loop(proactive_stop))

    # Redis 缓存
    if settings.REDIS_ENABLED:
        await init_redis(settings.REDIS_URL)

    # Milvus 向量数据库
    if settings.MILVUS_ENABLED:
        try:
            await init_milvus(settings.MILVUS_URI)
            await rebuild_vocabulary()
        except Exception as e:
            logger.warning(f"[App] Milvus 初始化失败（不影响核心功能）: {e}")

    # MinIO OSS
    if settings.MINIO_ENABLED:
        try:
            await init_minio(
                endpoint=settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                secure=settings.MINIO_SECURE
            )
        except Exception as e:
            logger.warning(f"[App] MinIO 初始化失败（不影响核心功能）: {e}")

    logger.info("[App] 初始化完成")
    yield
    proactive_stop.set()
    try:
        await asyncio.wait_for(proactive_task, timeout=5)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        proactive_task.cancel()
    # 关闭时清理
    await close_db()
    if settings.REDIS_ENABLED:
        await close_redis()
    await close_milvus()
    await close_minio()
    logger.info("[App] 已关闭")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    description="基于LangGraph的多智能体个性化学习系统",
    lifespan=lifespan
)

# 生成的图片资产（材料插图、思维导图等）由后端直接以 /static 提供
from pathlib import Path as _StaticPath
from fastapi.staticfiles import StaticFiles as _StaticFiles

_STATIC_ROOT = _StaticPath(__file__).resolve().parent / "static"
_STATIC_ROOT.mkdir(parents=True, exist_ok=True)
app.mount("/static", _StaticFiles(directory=_STATIC_ROOT), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=any(o != "*" for o in settings.CORS_ORIGINS),
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["*"],
)

# ===== 中间件顺序（Day 4 修正）=====
# Starlette/FastAPI 中间件按 LIFO 执行：最后 add_middleware 的最外层
# 目标顺序（从外到内）：
#   SlowQueryMiddleware  (最外层) → 捕获完整请求耗时（含 CORS preflight）
#   RateLimitMiddleware  (次外层) → 在鉴权与 LLM 调用之前对高成本端点短路
#   StructuredLoggingMiddleware (中层) → 注入 X-Request-Id + 输出 JSON 日志
#   CORSMiddleware  (最内层) → 处理 preflight / 跨域头
app.add_middleware(StructuredLoggingMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SlowQueryMiddleware)


# ===== UTF-8 JSON body safety net =====
# 修复：axios / fetch / curl 等客户端发送 `Content-Type: application/json`（无 charset）时，
# 部分 Starlette 解析路径会默认按 ISO-8859-1 解码，导致中文字段变成 `?`。
# 强制把 JSON 请求体的 Content-Type 补上 `charset=utf-8`，让 Pydantic 走 UTF-8 解码。
@app.middleware("http")
async def ensure_utf8_json_body(request: Request, call_next):
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json") and "charset" not in content_type.lower():
        # 改写 ASGI scope，让下游的 request.body() 仍按 UTF-8 解析
        # 关键：重设 content_type header
        new_headers = [
            (k, v) for k, v in request.scope.get("headers", [])
            if k.lower() != b"content-type"
        ] + [(b"content-type", b"application/json; charset=utf-8")]
        request.scope["headers"] = new_headers
    response = await call_next(request)
    if request.url.path.startswith("/api/admin/"):
        response.headers["Cache-Control"] = "no-store, private"
    return response

# ===== 全局异常处理（Day 4 — 1a，Day 5 强化 request_id 注入）=====
@app.exception_handler(BusinessError)
async def business_error_handler(request, exc: BusinessError):
    """
    统一业务异常 → 标准 JSON 响应
    - 状态码 = ErrorCode 定义中的 http_status
    - body 格式：{"error": {"code", "message", "request_id", "detail?", "extra?"}}
    - 注入 X-Request-Id 响应头，便于前后端联合定位
    """
    # 优先使用 request.state.request_id（中间件已注入），其次用 exc.request_id
    request_id = getattr(request.state, "request_id", None) or exc.request_id
    body = {
        "error": {
            "code": exc.code,
            "message": exc.message,
            "request_id": request_id,
        }
    }
    if exc.detail and exc.detail != exc.message:
        body["error"]["detail"] = exc.detail
    if exc.extra:
        body["error"]["extra"] = exc.extra
    response = JSONResponse(status_code=exc.http_status, content=body)
    response.headers["X-Request-Id"] = request_id
    return response

# 业务路由注册
app.include_router(frontend_pages_router)  # 14 路由（10 页面 + 静态资源 + vite）
app.include_router(auth_router)            # 2 路由
app.include_router(profile_router)         # 2 路由
app.include_router(chat_router)            # 4 路由（history + chat + stream + quick）
app.include_router(video_router)           # 5 路由 — 必须早于 agents_day4，确保 /api/video/generate 使用鉴权视频契约
app.include_router(agents_day4_router)     # 7 端点（其中旧 /api/video/generate 会被上面的新视频契约覆盖）
app.include_router(search_router)          # 1 路由
app.include_router(oss_router)             # 3 路由
app.include_router(system_router)          # 4 路由（resources + progress + health + quote/today）
app.include_router(knowledge_graph_router) # 4 路由（KG 4 端点 — 抽自 wizard）
app.include_router(wizard_router)          # 已存在
app.include_router(study_router)           # 已存在
app.include_router(materials_router)       # 已存在
app.include_router(plans_router)           # 已存在
app.include_router(exam_router)            # 已存在
app.include_router(community_router)       # 1 端点：active-peers（同班同学活跃榜）
app.include_router(agent_api_router)        # 8 agent 单点 + 1 workflow + 1 ctx（共享上下文池双入口）
app.include_router(style_reference_router)  # 3 端点：list styles / get style meta / generate refs（SOP image2 角色）
app.include_router(items_router)            # 3 端点：产物保存 / 列表 / 删除
app.include_router(agent_records_router)    # 5 端点：Agent 记录页（stats/list/trend/recent-outputs/detail）
app.include_router(learning_path_router)   # 4 端点：9 章状态 / start / 详情 / 懒生成
app.include_router(learning_graph_router)  # T01: 多图谱 CRUD + 节点管理 + 双视图（~16 端点）
app.include_router(learning_evaluator_router)  # Evaluator v0.1.10 formal Public View
app.include_router(notes_router)  # T02: 笔记 CRUD + 搜索 + 关联（~9 端点）
app.include_router(learning_workspace_router) # 学习工作台 / 小节进度 / 今日任务
app.include_router(dashboard_system_router) # 消息 / 待办 / 活动 / 建议
app.include_router(admin_auth_router)
app.include_router(admin_overview_router)
app.include_router(admin_users_router)
app.include_router(admin_announcements_router)
app.include_router(admin_operations_router)
app.include_router(admin_system_router)
app.include_router(admin_audit_router)
app.include_router(admin_workbench_router)
app.include_router(profile_onboarding_router)
app.include_router(harvest_adventure_router)
app.include_router(impressions_router)
app.include_router(memories_router)
app.include_router(workspace_tutor_router)
app.include_router(workspace_search_router)
app.include_router(workspace_agents_router)
app.include_router(workspace_inputs_router)
app.include_router(workspace_materials_router)
app.include_router(proactive_tutor_router)
app.include_router(tide_shared_perspective_router)
app.include_router(tide_workspace_object_router)
app.include_router(tide_artifact_action_router)
app.include_router(tide_search_evidence_router)
app.include_router(tide_user_context_router)
app.include_router(tide_long_term_memory_router)
app.include_router(darya_study_time_router)  # 2 路由（学习时长心跳 + 汇总）
app.include_router(darya_current_perspective_router)
app.include_router(darya_learning_loop_router)
app.include_router(personalized_artifacts_router)
app.include_router(tiered_assessments_router)
app.include_router(code_labs_router)
app.include_router(conversation_capability_actions_router)
app.include_router(darya_self_understanding_router)
app.include_router(darya_game_state_router)
app.include_router(darya_storybook_config_router)
app.include_router(darya_storybook_proxy_router)
app.include_router(study_resources_router)
app.include_router(research_router)


# ===== 生产前端托管 =====
# 以前前端只能靠 Vite dev server（`npm run dev`）跑，它顺带提供 /api 代理；
# 一旦改成静态托管，这个代理就没了，所有接口会 404。
# 这里让 FastAPI 直接托管构建产物，单进程即可对外，不再依赖 dev server。
#
# 只在 dist 存在且未显式关闭时生效；/api、/static、/docs 等真实路由优先命中，
# 不会被 SPA 兜底吞掉（兜底路由最后注册）。
_FRONTEND_DIST = _StaticPath(__file__).resolve().parent.parent / "frontend" / "dist"
_SPA_ROUTE_HINT = "/{full_path:path}"


def _mount_frontend(dist_dir: _StaticPath | None = None) -> bool:
    """把已构建的前端挂到根路径。返回是否真的挂上了。"""
    from fastapi.responses import FileResponse, JSONResponse
    from starlette.routing import Route as _Route

    dist = dist_dir or _FRONTEND_DIST
    index_file = dist / "index.html"
    if not index_file.is_file():
        logger.warning("[Frontend] 未找到 %s，后端只提供 API（开发时请用 start.ps1）", index_file)
        return False

    assets_dir = dist / "assets"
    # 注意：/assets 前缀已由 routers/frontend_pages.py 的 serve_assets 拥有
    # （旧工作区静态目录 + 构建产物回退）。这里不再挂第二个 owner，
    # 否则同一个路径会有两个来源，行为取决于注册顺序。
    del assets_dir

    async def spa(request):
        # 真实的顶层文件（favicon、manifest、widget 脚本等）直接返回，
        # 其余交给前端路由（/darya、/darya/login、/admin/login ...）。
        relative = (request.path_params.get("full_path") or "").strip("/")
        if relative:
            candidate = (dist / relative).resolve()
            try:
                candidate.relative_to(dist.resolve())
            except ValueError:
                return JSONResponse({"detail": "not found"}, status_code=404)
            if candidate.is_file():
                return FileResponse(candidate)
        return FileResponse(index_file)

    app.router.routes.append(_Route(_SPA_ROUTE_HINT, spa))
    logger.info("[Frontend] 已由后端托管构建产物: %s", dist)
    return True


if settings.MODE == "production" and getattr(settings, "SERVE_FRONTEND", True):
    _mount_frontend()


if __name__ == "__main__":
    print("=" * 60)
    print(f"{settings.APP_NAME} v{settings.VERSION}")
    print(f"Frontend+Backend: http://127.0.0.1:{settings.PORT}")
    print(f"API docs: http://127.0.0.1:{settings.PORT}/docs")
    print(f"Mode: {settings.MODE}")
    print(f"DB Engine: {settings.DB_ENGINE}")
    print("=" * 60)

    import uvicorn
    uvicorn.run("app:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
