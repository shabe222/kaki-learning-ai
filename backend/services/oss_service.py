"""
MinIO OSS 服务 - 多媒体文件存储（思维导图、代码截图、头像等）
支持本地文件存储作为 Fallback 方案
"""
from __future__ import annotations
import logging
import io
import os
from pathlib import Path
from typing import Optional, BinaryIO
from datetime import timedelta

logger = logging.getLogger(__name__)

# 全局客户端
_minio_client: Optional[object] = None
_minio_enabled: bool = False

# 本地文件存储配置
LOCAL_STORAGE_PATH = Path(__file__).parent.parent / "local_storage"

# 存储桶
BUCKETS = {
    "avatars": "用户头像",
    "mindmaps": "思维导图图片",
    "code-screenshots": "代码截图",
    "exports": "学习报告导出",
    "uploads": "用户上传文件",
    "videos": "教学视频",
}


def _init_local_storage():
    """初始化本地存储目录"""
    try:
        for bucket_name in BUCKETS:
            bucket_path = LOCAL_STORAGE_PATH / bucket_name
            bucket_path.mkdir(parents=True, exist_ok=True)
        logger.info("[LocalStorage] 本地存储目录初始化成功")
    except Exception as e:
        logger.warning(f"[LocalStorage] 初始化失败: {e}")


async def init_minio(
    endpoint: str = "localhost:9000",
    access_key: str = "minioadmin",
    secret_key: str = "minioadmin",
    secure: bool = False,
) -> Optional[object]:
    """初始化 MinIO 连接 + 创建存储桶，失败时优雅降级"""
    global _minio_client, _minio_enabled

    try:
        from minio import Minio
        from minio.error import S3Error

        logger.info(f"[MinIO] 尝试连接: {endpoint}")
        _minio_client = Minio(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )

        # 创建所有桶
        for bucket_name in BUCKETS:
            try:
                if not _minio_client.bucket_exists(bucket_name):
                    _minio_client.make_bucket(bucket_name)
                    logger.info(f"[MinIO] 创建存储桶: {bucket_name} ({BUCKETS[bucket_name]})")
                else:
                    logger.debug(f"[MinIO] 存储桶已存在: {bucket_name}")
            except S3Error as e:
                if e.code != "BucketAlreadyOwnedByYou":
                    logger.warning(f"[MinIO] 创建存储桶失败 {bucket_name}: {e}")

        _minio_enabled = True
        logger.info("[MinIO] 连接成功")
        return _minio_client

    except ImportError:
        logger.warning("[MinIO] minio 库未安装，使用本地文件存储")
        _minio_enabled = False
        _init_local_storage()
        return None
    except Exception as e:
        logger.warning(f"[MinIO] 连接失败 ({e})，使用本地文件存储")
        _minio_enabled = False
        _init_local_storage()
        return None


async def close_minio():
    """关闭 MinIO 连接"""
    global _minio_client
    _minio_client = None
    logger.info("[MinIO] 连接已释放")


def get_minio() -> Optional[object]:
    return _minio_client


# ===== 本地文件存储实现 =====

def _save_local_file(bucket: str, object_name: str, data: bytes) -> Optional[str]:
    """保存文件到本地"""
    try:
        file_path = LOCAL_STORAGE_PATH / bucket / object_name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(data)
        logger.info(f"[LocalStorage] 保存成功: {bucket}/{object_name} ({len(data)} bytes)")
        return f"{bucket}/{object_name}"
    except Exception as e:
        logger.error(f"[LocalStorage] 保存失败: {e}")
        return None


def _load_local_file(bucket: str, object_name: str) -> Optional[bytes]:
    """从本地加载文件"""
    try:
        file_path = LOCAL_STORAGE_PATH / bucket / object_name
        if file_path.exists():
            data = file_path.read_bytes()
            logger.info(f"[LocalStorage] 加载成功: {bucket}/{object_name}")
            return data
        return None
    except Exception as e:
        logger.warning(f"[LocalStorage] 加载失败: {e}")
        return None


def _delete_local_file(bucket: str, object_name: str) -> bool:
    """删除本地文件"""
    try:
        file_path = LOCAL_STORAGE_PATH / bucket / object_name
        if file_path.exists():
            file_path.unlink()
            logger.info(f"[LocalStorage] 删除成功: {bucket}/{object_name}")
        return True
    except Exception as e:
        logger.warning(f"[LocalStorage] 删除失败: {e}")
        return False


def _list_local_files(bucket: str, prefix: str = "", limit: int = 100) -> list[dict]:
    """列出本地文件"""
    try:
        bucket_path = LOCAL_STORAGE_PATH / bucket
        if not bucket_path.exists():
            return []

        result = []
        for i, file_path in enumerate(bucket_path.rglob("*")):
            if i >= limit:
                break
            if file_path.is_file():
                relative_path = file_path.relative_to(bucket_path)
                if prefix and not str(relative_path).startswith(prefix):
                    continue
                result.append({
                    "name": str(relative_path),
                    "size": file_path.stat().st_size,
                    "last_modified": str(file_path.stat().st_mtime),
                })
        return result
    except Exception as e:
        logger.warning(f"[LocalStorage] 列表失败: {e}")
        return []


# ===== 文件操作（统一接口） =====

async def upload_file(
    bucket: str,
    object_name: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> Optional[str]:
    """
    上传文件（优先使用 MinIO，失败时使用本地存储）

    Args:
        bucket: 存储桶名（avatars/mindmaps/code_screenshots/exports/uploads）
        object_name: 对象名（如 user_123/mindmap_20260524.png）
        data: 文件字节数据
        content_type: MIME 类型

    Returns:
        对象的 URL 路径，失败返回 None
    """
    if _minio_enabled and _minio_client:
        try:
            from minio.error import S3Error
            _minio_client.put_object(
                bucket_name=bucket,
                object_name=object_name,
                data=io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
            logger.info(f"[MinIO] 上传成功: {bucket}/{object_name} ({len(data)} bytes)")
            return f"{bucket}/{object_name}"
        except Exception as e:
            logger.warning(f"[MinIO] 上传失败 ({e})，尝试本地存储")

    return _save_local_file(bucket, object_name, data)


async def upload_text(
    bucket: str,
    object_name: str,
    text: str,
    content_type: str = "text/plain",
) -> Optional[str]:
    """上传文本内容"""
    return await upload_file(bucket, object_name, text.encode("utf-8"), content_type)


async def get_file(bucket: str, object_name: str) -> Optional[bytes]:
    """下载文件（优先使用 MinIO，失败时使用本地存储）"""
    if _minio_enabled and _minio_client:
        try:
            from minio.error import S3Error
            response = _minio_client.get_object(bucket_name=bucket, object_name=object_name)
            data = response.read()
            response.close()
            response.release_conn()
            logger.info(f"[MinIO] 下载成功: {bucket}/{object_name}")
            return data
        except Exception as e:
            logger.warning(f"[MinIO] 下载失败 ({e})，尝试本地存储")

    return _load_local_file(bucket, object_name)


async def get_text(bucket: str, object_name: str) -> Optional[str]:
    """下载文本"""
    data = await get_file(bucket, object_name)
    return data.decode("utf-8") if data else None


async def get_presigned_url(
    bucket: str,
    object_name: str,
    expires_hours: int = 24,
) -> Optional[str]:
    """
    生成预签名 URL（临时访问链接）

    Args:
        bucket: 存储桶名
        object_name: 对象名
        expires_hours: 过期时间（小时）

    Returns:
        临时 HTTPS/HTTP 链接或本地路径
    """
    if _minio_enabled and _minio_client:
        try:
            from minio.error import S3Error
            url = _minio_client.presigned_get_object(
                bucket_name=bucket,
                object_name=object_name,
                expires=timedelta(hours=expires_hours),
            )
            return url
        except Exception as e:
            logger.warning(f"[MinIO] 生成URL失败 ({e})，使用本地路径")

    # 本地文件返回相对路径
    local_path = LOCAL_STORAGE_PATH / bucket / object_name
    if local_path.exists():
        return f"/local-files/{bucket}/{object_name}"
    return None


async def delete_file(bucket: str, object_name: str) -> bool:
    """删除文件（同时删除 MinIO 和本地）"""
    success = False

    if _minio_enabled and _minio_client:
        try:
            from minio.error import S3Error
            _minio_client.remove_object(bucket_name=bucket, object_name=object_name)
            logger.info(f"[MinIO] 已删除: {bucket}/{object_name}")
            success = True
        except Exception as e:
            logger.warning(f"[MinIO] 删除失败: {e}")

    local_success = _delete_local_file(bucket, object_name)
    return success or local_success


async def list_files(bucket: str, prefix: str = "", limit: int = 100) -> list[dict]:
    """列出文件（优先 MinIO，失败时使用本地）"""
    if _minio_enabled and _minio_client:
        try:
            from minio.error import S3Error
            objects = _minio_client.list_objects(
                bucket_name=bucket,
                prefix=prefix,
                recursive=True,
            )
            result = []
            for i, obj in enumerate(objects):
                if i >= limit:
                    break
                result.append({
                    "name": obj.object_name,
                    "size": obj.size,
                    "last_modified": str(obj.last_modified),
                })
            return result
        except Exception as e:
            logger.warning(f"[MinIO] 列表失败 ({e})，使用本地存储")

    return _list_local_files(bucket, prefix, limit)
