"""学习资源保存服务（PostgreSQL + Milvus 双写）"""
import logging
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import LearningResource
from services.vector_service import insert_resource

logger = logging.getLogger(__name__)


async def save_resources(db: AsyncSession, user_id: int, session_id: str, output: dict):
    """保存学习资源（PostgreSQL + Milvus 双写）"""
    resource_map = {
        "document": ("document", "title"),
        "questions": ("questions", "topic"),
        "mindmap": ("mindmap", "title"),
        "code_examples": ("code_examples", "title"),
        "learning_path": ("learning_path", "title"),
        "video_result": ("video", "object_name"),
    }

    saved_resources = []

    for key, (resource_type, title_field) in resource_map.items():
        data = output.get(key)
        if not data:
            continue

        title = data.get(title_field, "") if isinstance(data, dict) else key

        resource = LearningResource(
            user_id=user_id,
            session_id=session_id,
            resource_type=resource_type,
            title=str(title)[:300],
            content=data if isinstance(data, dict) else {"data": str(data)},
            topic=output.get("user_query", "")
        )
        db.add(resource)
        saved_resources.append(resource)

    await db.commit()

    # Milvus 向量存储（异步，失败不影响主流程）
    for resource in saved_resources:
        try:
            await insert_resource({
                "resource_type": resource.resource_type,
                "title": resource.title or "",
                "topic": resource.topic or "",
                "content": resource.content,
                "user_id": user_id,
            })
        except Exception as e:
            logger.warning(f"[Milvus] 向量存储跳过 ({resource.resource_type}): {e}")

    logger.info(f"[DB] 资源已保存 user_id={user_id} count={len(saved_resources)}")
