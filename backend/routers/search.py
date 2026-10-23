"""Search route with generated-item snapshots for tutor actions."""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from database.dependencies import get_db
from database.models import GeneratedItem
from schemas.search import SearchRequest
from services.vector_service import search_resources
from utils.auth import get_current_user_id

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/api/search")
async def api_search(
    request: SearchRequest,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Search learning resources and persist the returned result snapshot."""
    if request.user_id is not None and request.user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Cannot search another user")
    try:
        results = await search_resources(
            query=request.query,
            user_id=current_user_id,
            resource_type=request.resource_type,
            limit=request.limit,
        )
        try:
            item = GeneratedItem(
                user_id=current_user_id,
                type="tutor_action:search",
                title=f"Search resources - {request.query[:80]}",
                chapter="AI tutor action",
                content=json.dumps(results, ensure_ascii=False),
                raw_data={
                    "action": "search",
                    "source_question": request.query,
                    "payload": results,
                    "request": {
                        "resource_type": request.resource_type,
                        "limit": request.limit,
                    },
                },
            )
            db.add(item)
            await db.commit()
        except Exception:
            await db.rollback()
            logger.warning("[search] result snapshot save failed", exc_info=True)
        return {"success": True, "data": results, "query": request.query}
    except Exception as e:
        logger.error("Search error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
