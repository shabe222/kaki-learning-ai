"""前端静态页面路由 - 13 个 serve_*.html + 静态资源"""
from pathlib import Path
import logging
import os
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response

router = APIRouter()
logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend" / "public" / "ai-workspace"
PUBLIC_DIR = FRONTEND_DIR.parent
if not FRONTEND_DIR.is_dir():
    logger.error("Official AI workspace static directory does not exist: %s", FRONTEND_DIR)

# /assets 的两个候选根：旧工作区静态目录，以及 Vite 构建产物目录。
_ASSET_ROOTS = tuple(
    root
    for root in (
        (FRONTEND_DIR / "assets").resolve(),
        (FRONTEND_DIR.parents[1] / "dist" / "assets").resolve(),
    )
)


def _read_page(name: str):
    path = FRONTEND_DIR / name
    if path.is_file():
        return path.read_text(encoding="utf-8")
    logger.error("Official AI workspace page does not exist: %s", path)
    return HTMLResponse("404", status_code=404)


def _workspace_page_enabled(client_host: str | None) -> bool:
    """The official local workspace is always available; remote access stays opt-in."""
    is_local = client_host in {"127.0.0.1", "::1"}
    explicitly_enabled = os.getenv("ENABLE_AI_WORKSPACE_TEST_PAGE", "").lower() == "true"
    return bool(is_local or explicitly_enabled)


@router.get("/", response_class=HTMLResponse)
@router.get("/index.html", response_class=HTMLResponse)
async def serve_index():
    return RedirectResponse(url="/app.html#home", status_code=307)


@router.get("/app.html", response_class=HTMLResponse)
async def serve_app():
    return _read_page("app.html")


@router.get("/ai-workspace-test.html", response_class=HTMLResponse)
async def serve_ai_workspace_test(request: Request):
    """Official local workspace; non-local access requires an explicit flag."""
    client_host = request.client.host if request.client else None
    if not _workspace_page_enabled(client_host):
        raise HTTPException(status_code=404)
    page = _read_page("ai-workspace-test.html")
    if isinstance(page, Response):
        return page
    return HTMLResponse(
        page,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/profile.html", response_class=HTMLResponse)
async def serve_profile_page():
    return RedirectResponse(url="/app.html#profile", status_code=307)


@router.get("/register.html", response_class=HTMLResponse)
async def serve_register():
    return _read_page("register.html")


@router.get("/terms.html", response_class=HTMLResponse)
async def serve_terms():
    return _read_page("terms.html")


@router.get("/privacy.html", response_class=HTMLResponse)
async def serve_privacy():
    return _read_page("privacy.html")


@router.get("/learning-path.html", response_class=HTMLResponse)
async def serve_learning_path():
    return RedirectResponse(url="/app.html#learn", status_code=307)


@router.get("/learn-detail.html", response_class=HTMLResponse)
async def serve_learn_detail():
    """章节详情页（懒生成入口）"""
    return RedirectResponse(url="/app.html#learn", status_code=307)


@router.get("/exercises.html", response_class=HTMLResponse)
async def serve_exercises():
    return RedirectResponse(url="/app.html#exam", status_code=307)


@router.get("/wizard.html", response_class=HTMLResponse)
async def serve_wizard():
    return _read_page("wizard.html")


@router.get("/profile-detail.html", response_class=HTMLResponse)
async def serve_profile_detail():
    return RedirectResponse(url="/app.html#profile", status_code=307)


@router.get("/resources.html", response_class=HTMLResponse)
async def serve_resources():
    return RedirectResponse(url="/app.html#materials", status_code=307)


@router.get("/report.html", response_class=HTMLResponse)
async def serve_report():
    return RedirectResponse(url="/app.html#report", status_code=307)


@router.get("/profile-center.html", response_class=HTMLResponse)
async def serve_profile_center():
    return RedirectResponse(url="/app.html#profile", status_code=307)


@router.get("/me.html", response_class=HTMLResponse)
async def serve_me():
    return RedirectResponse(url="/app.html#profile", status_code=307)


@router.get("/learn.html", response_class=HTMLResponse)
async def serve_learn():
    return RedirectResponse(url="/app.html#learn", status_code=307)


@router.get("/video.html", response_class=HTMLResponse)
async def serve_video():
    return RedirectResponse(url="/app.html#video", status_code=307)


@router.get("/exam.html", response_class=HTMLResponse)
async def serve_exam():
    return RedirectResponse(url="/app.html#exam", status_code=307)


@router.get("/dashboard.html", response_class=HTMLResponse)
async def serve_dashboard():
    return RedirectResponse(url="/app.html#home", status_code=307)


@router.get("/agents.html", response_class=HTMLResponse)
async def serve_agents():
    """智能体市集（旧前端，路由补回供 banner 提示访问）"""
    return RedirectResponse(url="/app.html#agents", status_code=307)


@router.get("/404.html", response_class=HTMLResponse)
async def serve_404():
    return _read_page("404.html")


@router.get("/favicon.ico")
async def serve_favicon():
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
        '<rect width="64" height="64" rx="14" fill="#fdfaf5"/>'
        '<rect x="12" y="12" width="40" height="40" rx="8" fill="#9a3520"/>'
        '<path d="M21 36c8-14 14-14 22 0" fill="none" stroke="#fceae3" stroke-width="5" stroke-linecap="round"/>'
        '<circle cx="24" cy="27" r="3" fill="#fceae3"/>'
        '<circle cx="40" cy="27" r="3" fill="#fceae3"/>'
        "</svg>"
    )
    return Response(content=svg, media_type="image/svg+xml")


@router.get("/assets/{asset_path:path}")
@router.get("/ai-workspace/assets/{asset_path:path}")
async def serve_assets(asset_path: str):
    # /assets 前缀由两套前端共用：旧工作区静态目录与 Vite 构建产物（frontend/dist）。
    # 先找旧工作区，未命中再回退到构建产物 —— 这样生产构建的哈希资源也能被服务，
    # 同时不需要为同一个路径再挂第二个 owner。
    for assets_root in _ASSET_ROOTS:
        path = (assets_root / asset_path).resolve()
        if path.is_file() and assets_root in path.parents:
            return FileResponse(path)
    raise HTTPException(status_code=404)


@router.get("/styles/{filename}")
@router.get("/ai-workspace/styles/{filename}")
async def serve_styles(filename: str):
    path = FRONTEND_DIR / "styles" / filename
    if path.exists():
        return HTMLResponse(path.read_text(encoding="utf-8"), media_type="text/css")
    raise HTTPException(status_code=404)


@router.get("/scripts/{filename}")
async def serve_scripts(filename: str):
    path = FRONTEND_DIR / "scripts" / filename
    if path.exists():
        return HTMLResponse(path.read_text(encoding="utf-8"), media_type="application/javascript")
    raise HTTPException(status_code=404)


@router.get("/audio/{filename}")
async def serve_audio(filename: str):
    audio_root = (PUBLIC_DIR / "audio").resolve()
    path = (audio_root / filename).resolve()
    if path.is_file() and audio_root in path.parents:
        return FileResponse(path, media_type="application/javascript")
    raise HTTPException(status_code=404)


@router.get("/@vite/client")
async def ignore_vite():
    return HTMLResponse("// empty", media_type="application/javascript")
