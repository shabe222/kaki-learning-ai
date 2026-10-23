"""认证路由 - register + login"""
from pathlib import Path

from fastapi import APIRouter, HTTPException, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.models import StudentProfile, User
from database.dependencies import get_db
from schemas.auth import RegisterRequest, LoginRequest, ChangePasswordRequest
from utils.auth import hash_password, hash_password_async, verify_password, create_token, get_current_user_id
from services.profile_service import is_profile_completed

router = APIRouter()

AVATAR_DIR = Path(__file__).resolve().parents[1] / "uploads" / "avatars"
AVATAR_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}
AVATAR_MAX_BYTES = 2 * 1024 * 1024


@router.post("/api/auth/register")
async def api_register(req: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """用户注册 - 数据库持久化"""
    # 检查用户名是否已存在
    result = await db.execute(select(User).options(selectinload(User.profile)).where(User.username == req.username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")

    # 创建用户
    user = User(
        username=req.username,
        password_hash=hash_password(req.password),
        email=req.email
    )
    db.add(user)
    await db.flush()
    # 注册即标记画像 onboarding 为 skipped（deferred）：独立的画像引导聊天已下线，
    # 画像由主聊天承担，用户登录后直接进入主界面，不被强跳到单独引导。
    db.add(StudentProfile(
        user_id=user.id,
        profile_data={"profile_onboarding": {"status": "skipped", "reason": "registration_default"}},
    ))
    await db.commit()
    await db.refresh(user)

    # 生成 token
    token = create_token(user.id, user.username)

    return {
        "success": True,
        "message": "注册成功",
        "data": {
            "user_id": user.id,
            "username": user.username,
            "token": token,
            "avatar_url": user.avatar_url,
        }
    }


@router.post("/api/auth/login")
async def api_login(req: LoginRequest, db: AsyncSession = Depends(get_db)):
    """用户登录"""
    result = await db.execute(
        select(User)
        .options(selectinload(User.profile))
        .where(User.username == req.username)
    )
    user = result.scalar_one_or_none()

    if not user or not await verify_password(req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="账户已被禁用")

    token = create_token(user.id, user.username)

    return {
        "success": True,
        "message": "登录成功",
        "data": {
            "user_id": user.id,
            "username": user.username,
            "token": token,
            "email": user.email,
            "avatar_url": user.avatar_url,
            "has_profile": is_profile_completed(user.profile)
        }
    }


@router.get("/api/auth/me")
async def api_current_user(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """当前登录用户资料。"""
    result = await db.execute(
        select(User)
        .options(selectinload(User.profile))
        .where(User.id == current_user_id)
    )
    user = result.scalar_one_or_none()

    if not user:
      raise HTTPException(status_code=401, detail="用户不存在或登录已失效")

    if not user.is_active:
      raise HTTPException(status_code=403, detail="账户已被禁用")

    return {
        "success": True,
        "message": "获取当前用户成功",
        "data": {
            "user_id": user.id,
            "username": user.username,
            "email": user.email,
            "avatar_url": user.avatar_url,
            "has_profile": is_profile_completed(user.profile),
        }
    }


def _avatar_path(user_id: int) -> Path | None:
    if not AVATAR_DIR.is_dir():
        return None
    for ext in (".png", ".jpg", ".webp"):
        candidate = AVATAR_DIR / f"{user_id}{ext}"
        if candidate.is_file():
            return candidate
    return None


@router.post("/api/auth/avatar")
async def api_upload_avatar(
    file: UploadFile = File(...),
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """上传当前用户头像。写入 users.avatar_url（既有正式字段，无新 schema）。"""
    media_type = (file.content_type or "").lower()
    if media_type not in AVATAR_TYPES:
        raise HTTPException(status_code=400, detail="头像只支持 PNG / JPG / WebP 图片")
    # 先按上限+1 读取，避免把任意大的 body 整个拉进内存再判断。
    payload = await file.read(AVATAR_MAX_BYTES + 1)
    if not payload:
        raise HTTPException(status_code=400, detail="文件为空")
    if len(payload) > AVATAR_MAX_BYTES:
        raise HTTPException(status_code=400, detail="头像图片不能超过 2MB")

    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    # 清掉旧格式文件，保证任一时刻只有一份头像事实
    for ext in AVATAR_TYPES.values():
        stale = AVATAR_DIR / f"{current_user_id}{ext}"
        if stale.is_file():
            stale.unlink()
    final_path = AVATAR_DIR / f"{current_user_id}{AVATAR_TYPES[media_type]}"
    tmp_path = AVATAR_DIR / f".{current_user_id}.uploading"
    tmp_path.write_bytes(payload)
    tmp_path.replace(final_path)

    result = await db.execute(select(User).where(User.id == current_user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在或登录已失效")
    user.avatar_url = f"/api/auth/avatar/{current_user_id}"
    await db.commit()
    return {"success": True, "message": "头像已更新", "data": {"avatar_url": user.avatar_url}}


@router.get("/api/auth/avatar/{user_id}")
async def api_get_avatar(
    user_id: int,
    current_user_id: int = Depends(get_current_user_id),
):
    """读取头像。头像属于个人资料，仅本人可读（学生端无社交场景）。"""
    if user_id != current_user_id:
        raise HTTPException(status_code=403, detail="无权访问该头像")
    path = _avatar_path(user_id)
    if path is None:
        raise HTTPException(status_code=404, detail="还没有设置头像")
    media = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}[path.suffix]
    return FileResponse(path, media_type=media)


@router.delete("/api/auth/avatar")
async def api_delete_avatar(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """恢复默认头像：清除文件并把 avatar_url 置空。"""
    result = await db.execute(select(User).where(User.id == current_user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在或登录已失效")
    user.avatar_url = None
    await db.commit()
    old = _avatar_path(current_user_id)
    if old is not None:
        old.unlink()
    return {"success": True, "message": "已恢复默认头像", "data": {"avatar_url": None}}


@router.post("/api/auth/change-password")
async def api_change_password(
    req: ChangePasswordRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """修改当前登录用户密码。必须先验证当前密码，只持久化新密码哈希。"""
    result = await db.execute(select(User).where(User.id == current_user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在或登录已失效")

    if not await verify_password(req.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="当前密码不正确")

    if len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码至少需要 6 个字符")

    user.password_hash = await hash_password_async(req.new_password)
    await db.commit()

    return {"success": True, "message": "密码已更新，下次登录请使用新密码"}
