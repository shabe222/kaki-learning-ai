from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from services.tide_search_evidence import SearchContext, build_evidence_bundle
from utils.auth import get_current_user_id

router = APIRouter(prefix="/api/tide", tags=["tide-search-evidence"])


class EvidenceRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    shared_perspective: dict = Field(default_factory=dict)
    workspace_object: dict = Field(default_factory=dict)
    artifact_context: dict = Field(default_factory=dict)


@router.post("/search-evidence")
async def search_evidence(
    request: EvidenceRequest,
    current_user_id: int = Depends(get_current_user_id),
):
    del current_user_id
    bundle = await build_evidence_bundle(SearchContext(**request.model_dump()))
    return bundle.public_dict()
