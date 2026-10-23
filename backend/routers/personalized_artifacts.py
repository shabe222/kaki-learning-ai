from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from database.dependencies import get_db
from schemas.canonical_artifacts import CreateCanonicalHandoutRequest
from services.canonical_artifacts import build_canonical_artifact, create_canonical_handout
from services.darya_learning_loop import get_learning_route_state, get_learning_state
from services.personalized_learning_artifacts import build_personalized_artifact_plan
from utils.auth import get_current_user_id

router = APIRouter(prefix="/api/personalized-artifacts", tags=["personalized-artifacts"])
class GenerateRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=100); concept_key: str = Field(min_length=3, max_length=120)
    experience: str = Field(pattern=r"^(PERSONALIZED_HANDOUT|PRACTICAL_GUIDE)$"); idempotency_key: str = Field(min_length=8, max_length=240)
@router.post("/generate")
async def generate(req: GenerateRequest, current_user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    state = await get_learning_state(db, user_id=current_user_id, thread_id=req.thread_id, concept_key=req.concept_key); route = await get_learning_route_state(db, user_id=current_user_id, thread_id=req.thread_id)
    if state is None or route is None: raise HTTPException(status_code=409, detail={"code":"LEARNER_CONTEXT_UNAVAILABLE","message":"当前学习状态不足，不能伪造个性化资料。"})
    preference = (((state.latest_cycle or {}).get("resource_orchestration") or {}).get("intent") or {}).get("relevant_context", {}).get("presentation_mode")
    plan = await build_personalized_artifact_plan(experience=req.experience, state=state.__dict__, route=route.__dict__, preference=preference)
    item, version, replayed = await create_canonical_handout(db, user_id=current_user_id, request=CreateCanonicalHandoutRequest(title=plan["title"], topic=plan["purpose"]["learning_goal"], blocks=plan["blocks"], purpose=plan["purpose"], personalization=plan["personalization"], source_refs=plan["source_refs"], thread_id=req.thread_id, experience=req.experience, idempotency_key=req.idempotency_key))
    canonical = await build_canonical_artifact(db, item=item, version=version); await db.commit()
    # 如实回传这条内容的生成方式：skill_llm = 模型生成，deterministic = 模型不可用时的
    # 确定性计划。此前这个标记被丢掉，调用方无法区分两者。
    generation = dict(plan.get("generation") or {"mode": "unknown"})
    return {
        "success": True,
        "data": canonical,
        "plan": plan["plan"],
        "generation": generation,
        "degraded": generation.get("mode") != "skill_llm",
        "idempotent_replay": replayed,
    }
