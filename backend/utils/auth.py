"""Password hashing and JWT helpers."""

import asyncio
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from fastapi import Header, HTTPException, Depends
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from config import settings
from database.dependencies import get_db
from database.models import AdminUser, User


def hash_password(password: str) -> str:
    """Hash a password with bcrypt for production-safe storage.
    
    NOTE: This is a synchronous function that blocks the event loop for ~180ms.
    Use hash_password_async() in async contexts to avoid blocking.
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")


async def hash_password_async(password: str) -> str:
    """Async version of hash_password - runs bcrypt in a thread pool."""
    return await asyncio.to_thread(hash_password, password)


async def verify_password(plain: str, hashed: str) -> bool:
    """Verify bcrypt hashes and legacy salt$sha256 hashes.
    bcrypt 放线程池执行，避免阻塞 async 事件循环。"""
    if not hashed:
        return False
    if hashed.startswith(("$2a$", "$2b$", "$2y$")):
        try:
            return await asyncio.to_thread(
                bcrypt.checkpw, plain.encode("utf-8"), hashed.encode("utf-8")
            )
        except (TypeError, ValueError):
            return False

    try:
        salt, stored_hash = hashed.split("$", 1)
    except ValueError:
        return False
    digest = hashlib.sha256(f"{salt}:{plain}".encode()).hexdigest()
    return hmac.compare_digest(digest, stored_hash)


def create_token(user_id: int, username: str) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "exp": datetime.utcnow() + timedelta(days=7),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """Decode a JWT token. Returns None when invalid or expired."""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError:
        return None


async def get_current_user_id(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> int:
    """Extract the current user id from Authorization: Bearer <token>.

    Also verifies the account is still active: a previously-issued JWT for a
    disabled account is rejected here, so one central check enforces it on every
    protected request (not only at login). Uses 401 so the frontend's existing
    401 → clear-session → login redirect path handles it cleanly.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization header format")
    payload = decode_token(parts[1])
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if payload.get("token_type") == "admin":
        raise HTTPException(status_code=403, detail="管理员令牌不能用于学习者接口")
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid user id in token")
    # Reject disabled accounts centrally (same authority as admin dependency).
    result = await db.execute(select(User.id).where(User.id == user_id, User.is_active.is_(True)))
    active_id = result.scalar_one_or_none()
    if active_id is None:
        raise HTTPException(status_code=401, detail="账户已被禁用或登录已失效")
    return user_id


def require_self(path_user_id: int, current_user_id: int) -> None:
    """Require the route user id to match the authenticated user id."""
    if path_user_id != current_user_id:
        raise HTTPException(
            status_code=403,
            detail="Forbidden: cannot access another user's data",
        )


# ===== 管理员认证 =====

def create_admin_token(admin_id: int, username: str, role: str) -> str:
    """创建管理员专用JWT Token，通过token_type字段与用户Token区分"""
    payload = {
        "sub": str(admin_id),
        "username": username,
        "role": role,
        "token_type": "admin",
        "exp": datetime.utcnow() + timedelta(days=7),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


async def get_current_admin_id(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> int:
    """从Authorization头提取管理员ID，校验token_type并验证账户状态"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid authorization header format")
    payload = decode_token(parts[1])
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if payload.get("token_type") != "admin":
        raise HTTPException(status_code=403, detail="Forbidden: admin token required")
    try:
        admin_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid admin id in token")

    # 查询数据库验证管理员存在且已启用
    result = await db.execute(select(AdminUser).where(AdminUser.id == admin_id))
    admin = result.scalar_one_or_none()
    if not admin:
        raise HTTPException(status_code=401, detail="Admin account not found")
    if not admin.is_active:
        raise HTTPException(status_code=401, detail="Admin account has been disabled")
    if str(admin.role).lower() not in {"super_admin", "operations", "observer"}:
        raise HTTPException(status_code=403, detail="管理员角色未获授权")

    return admin_id


def require_admin_role(*roles: str):
    """只允许指定角色的管理员通过。

    token 里一直带着 role，但此前没有任何一处读取它做鉴权。当前只有 super_admin
    一种角色所以不出问题，一旦引入第二种角色就会变成提权面。用于密钥写入、删除等
    真正敏感的管理端操作。
    """
    allowed = {str(r).strip().lower() for r in roles if str(r).strip()}

    async def _dependency(
        authorization: Optional[str] = Header(None),
        db: AsyncSession = Depends(get_db),
    ) -> int:
        admin_id = await get_current_admin_id(authorization=authorization, db=db)
        result = await db.execute(select(AdminUser).where(AdminUser.id == admin_id))
        admin = result.scalar_one_or_none()
        role = str(getattr(admin, "role", "") or "").strip().lower()
        if role not in allowed:
            from services.admin_audit import write_audit_log
            await write_audit_log(db, admin_id, "permission_denied", "system", "role_check",
                success=False, summary=f"角色 {role} 的操作请求被服务端拒绝")
            raise HTTPException(
                status_code=403,
                detail="Forbidden: 当前管理员角色无权执行该操作",
            )
        return admin_id

    return _dependency
