"""OSS（MinIO）路由 - 3 个 oss_* 路由"""
from fastapi import APIRouter, Depends, HTTPException

from services.oss_service import get_presigned_url, list_files
from utils.auth import get_current_user_id

router = APIRouter()


@router.post("/api/oss/upload")
async def api_oss_upload(
    bucket: str = "uploads",
    object_name: str = "",
    current_user_id: int = Depends(get_current_user_id),
):
    """上传文件说明端点 - 实际通过 multipart 上传

    P0 修复：object_name 前缀强制为 users/{current_user_id}/，防路径穿越
    """
    safe_prefix = f"users/{current_user_id}/"
    if not object_name.startswith(safe_prefix):
        object_name = f"{safe_prefix}{object_name.lstrip('/')}"
    return {
        "message": "请使用 POST multipart/form-data 上传文件",
        "buckets": ["avatars", "mindmaps", "code_screenshots", "exports", "uploads"],
        "example": f"POST /api/oss/upload?bucket=avatars&object_name={object_name}",
        "object_name": object_name,
    }


@router.get("/api/oss/url")
async def api_oss_url(
    bucket: str = "mindmaps",
    object_name: str = "",
    expires_hours: int = 24,
    current_user_id: int = Depends(get_current_user_id),
):
    """获取 MinIO 文件的临时访问链接

    P0 修复：仅允许访问 users/{current_user_id}/ 下的资源
    """
    if not object_name.startswith(f"users/{current_user_id}/"):
        raise HTTPException(status_code=403, detail="无权访问该资源")
    url = await get_presigned_url(bucket, object_name, expires_hours)
    if url:
        return {"success": True, "url": url, "expires_in_hours": expires_hours}
    return {"success": False, "message": "文件不存在或MinIO未启用"}


@router.get("/api/oss/list")
async def api_oss_list(
    bucket: str = "mindmaps",
    prefix: str = "",
    current_user_id: int = Depends(get_current_user_id),
):
    """列出 MinIO 存储桶中的文件

    P0 修复：强制 prefix = users/{current_user_id}/，防越权列举
    """
    safe_prefix = f"users/{current_user_id}/"
    if not prefix.startswith(safe_prefix):
        prefix = safe_prefix
    files = await list_files(bucket, prefix)
    return {"success": True, "bucket": bucket, "files": files}
