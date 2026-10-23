"""Final admin workbench. Object access and writes are enforced on the server."""
import asyncio
from weakref import WeakValueDictionary
from datetime import datetime, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from database.dependencies import get_db
from database.models import (AdminUser, User, GeneratedItem, GeneratedItemVersion, ArtifactReview,
    ChatSession, AgentInvocation, WorkspaceAgentTask, InteractiveQuestionAttempt, GeneratedItemCodeRun)
from database.admin_models import (AdminPreference, AdminAccountPurpose, AdminIssue, AdminSampleGrant,
    AdminAnnouncementRevision, AdminRetry, AdminCapabilityProbe, AdminRuntimeSetting)
from utils.auth import get_current_admin_id, require_admin_role
from services import admin_workbench as service
from services.admin_audit import write_audit_log, list_audit_logs
from services.admin_users import get_user_detail
from services.admin_system import get_current_config

router = APIRouter(prefix="/api/admin/workbench")
operator = require_admin_role("super_admin", "operations")
supervisor = require_admin_role("super_admin")
_retry_locks = WeakValueDictionary()


def data(value): return {"success": True, "data": value}


class PreferenceRequest(BaseModel):
    theme: Literal["kaki", "ocean"]
    compact: bool = False
    auto_refresh: bool = True


@router.get("/preferences")
async def preferences(admin: int = Depends(get_current_admin_id), db: AsyncSession = Depends(get_db)):
    row = await db.get(AdminPreference, admin)
    who = await db.get(AdminUser, admin)
    return data({"theme": row.theme if row else None, "compact": row.compact if row else False,
        "auto_refresh": row.auto_refresh if row else True, "admin_id": admin, "username": who.username,
        "role": who.role, "can_operate": who.role in {"super_admin", "operations"}, "can_configure": who.role == "super_admin"})


@router.put("/preferences")
async def save_preferences(body: PreferenceRequest, admin: int = Depends(get_current_admin_id), db: AsyncSession = Depends(get_db)):
    row = await db.get(AdminPreference, admin)
    if row is None: row = AdminPreference(admin_id=admin); db.add(row)
    for key, value in body.model_dump().items(): setattr(row, key, value)
    await db.commit()
    return data(body.model_dump())


@router.get("/overview")
async def overview(scope: Literal["business", "all"] = "business", admin: int = Depends(get_current_admin_id), db: AsyncSession = Depends(get_db)):
    return data(await service.overview(db, scope))


@router.get("/users")
@router.get("/tasks")
@router.get("/quality")
async def listing(request: Request, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    admin: int = Depends(get_current_admin_id), db: AsyncSession = Depends(get_db)):
    params = dict(request.query_params); params.update(page=page, page_size=page_size)
    try: return data(await {"users": service.users, "tasks": service.tasks, "quality": service.qualities}[request.url.path.rsplit("/", 1)[-1]](db, params))
    except (ValueError, TypeError): raise HTTPException(422, "筛选条件格式不正确")


@router.get("/users/{user_id}")
async def user_detail(user_id: int, admin: int = Depends(get_current_admin_id), db: AsyncSession = Depends(get_db)):
    result = await get_user_detail(db, user_id)
    if result is None: raise HTTPException(404, "用户不存在")
    q = service.user_projection().subquery()
    metadata = (await db.execute(select(q).where(q.c.user_id == user_id))).mappings().one()
    result.update(service.pack(metadata))
    attempts = await db.scalar(select(func.count(InteractiveQuestionAttempt.id)).where(InteractiveQuestionAttempt.user_id == user_id))
    code_runs = await db.scalar(select(func.count(GeneratedItemCodeRun.id)).where(GeneratedItemCodeRun.user_id == user_id))
    result.update(formal_attempt_count=attempts, code_run_count=code_runs, profile_completion=None)
    timeline = []
    for model, date, label, ident in ((ChatSession, ChatSession.last_message_at, "会话活动", ChatSession.id),
        (GeneratedItem, GeneratedItem.created_at, "生成产物", GeneratedItem.id),
        (InteractiveQuestionAttempt, InteractiveQuestionAttempt.submitted_at, "正式提交", InteractiveQuestionAttempt.id),
        (GeneratedItemCodeRun, GeneratedItemCodeRun.created_at, "代码运行", GeneratedItemCodeRun.id)):
        rows = (await db.execute(select(ident.label("id"), date.label("time")).where(model.user_id == user_id).order_by(date.desc()).limit(8))).mappings()
        timeline.extend({**service.pack(r), "label": label} for r in rows)
    result["activity_timeline"] = sorted(timeline, key=lambda r: r["time"] or "", reverse=True)[:20]
    result["management_records"] = (await list_audit_logs(db, target_type="user", target_id=str(user_id), page_size=100))[0]
    return data(result)


class PurposeRequest(BaseModel):
    purpose: Literal["learner", "qa", "demo"]
    reason: str = Field(min_length=3, max_length=500)


@router.put("/users/{user_id}/purpose")
async def purpose(user_id: int, body: PurposeRequest, admin: int = Depends(supervisor), db: AsyncSession = Depends(get_db)):
    if not await db.get(User, user_id): raise HTTPException(404, "用户不存在")
    row = await db.get(AdminAccountPurpose, user_id)
    before = row.purpose if row else "未确认"
    if row is None: row = AdminAccountPurpose(user_id=user_id); db.add(row)
    row.purpose, row.reason, row.admin_id = body.purpose, body.reason.strip(), admin
    await write_audit_log(db, admin, "account_purpose", "user", str(user_id), summary=f"{before} → {body.purpose}；{body.reason}")
    return data({"purpose": row.purpose})


@router.get("/search")
async def search(keyword: str = Query(min_length=1, max_length=100), admin: int = Depends(get_current_admin_id), db: AsyncSession = Depends(get_db)):
    users = await service.users(db, {"keyword": keyword, "page_size": 8})
    q = service.task_projection()
    stmt = select(q.c.key, q.c.user_id).where(q.c.key.contains(keyword)).limit(8)
    tasks = [dict(r) for r in (await db.execute(stmt)).mappings()]
    artifacts = []
    if keyword.isdigit():
        artifacts = [dict(r) for r in (await db.execute(select(GeneratedItem.id.label("artifact_id"), GeneratedItem.type).where(GeneratedItem.id == int(keyword)))).mappings()]
    return data({"users": users["items"], "tasks": tasks, "artifacts": artifacts})


@router.get("/issues")
async def issues(state: str = "active", user_id: int | None = None, admin: int = Depends(get_current_admin_id), db: AsyncSession = Depends(get_db)):
    await service.sync_issues(db)
    stmt = select(AdminIssue)
    if state == "active": stmt = stmt.where(AdminIssue.state != "closed")
    elif state != "all": stmt = stmt.where(AdminIssue.state == state)
    if user_id is not None: stmt = stmt.where(AdminIssue.user_id == user_id)
    rows = (await db.execute(stmt.order_by(AdminIssue.first_seen_at.desc()).limit(100))).scalars()
    return data([{"id": r.id, "category": r.category, "severity": r.severity, "object_type": r.object_type, "object_id": r.object_id,
        "user_id": r.user_id, "state": r.state, "first_seen_at": service.stamp(r.first_seen_at), "last_seen_at": service.stamp(r.last_seen_at),
        "owner_id": r.owner_id, "result": r.result, "evidence": r.evidence} for r in rows])


class IssueRequest(BaseModel):
    state: Literal["acknowledged", "handling", "resolved", "closed"]
    result: str = Field(min_length=3, max_length=1000)
    evidence: str = Field(default="", max_length=500)


@router.put("/issues/{issue_id}")
async def handle_issue(issue_id: int, body: IssueRequest, admin: int = Depends(operator), db: AsyncSession = Depends(get_db)):
    row = await db.get(AdminIssue, issue_id)
    if row is None: raise HTTPException(404, "事项不存在")
    if body.state in {"resolved", "closed"} and not body.evidence.strip(): raise HTTPException(422, "登记结果和关闭事项必须填写证据定位")
    before = row.state
    row.state, row.result, row.evidence, row.owner_id = body.state, body.result.strip(), body.evidence.strip(), admin
    await write_audit_log(db, admin, "issue_update", "issue", str(issue_id), summary=f"{before} → {body.state}；{body.result}；证据：{body.evidence}"[:500])
    return data({"state": row.state})


class SupportRequest(BaseModel):
    result: str = Field(min_length=3, max_length=500)


@router.post("/users/{user_id}/support")
async def support(user_id: int, body: SupportRequest, admin: int = Depends(operator), db: AsyncSession = Depends(get_db)):
    if not await db.get(User, user_id): raise HTTPException(404, "用户不存在")
    await write_audit_log(db, admin, "support_note", "user", str(user_id), summary=body.result.strip())
    return data({"saved": True})


@router.get("/quality/{artifact_id}")
async def quality_detail(artifact_id: int, admin: int = Depends(get_current_admin_id), db: AsyncSession = Depends(get_db)):
    result = await service.qualities(db, {"artifact_id": artifact_id})
    if not result["items"]: raise HTTPException(404, "产物不存在")
    versions = (await db.execute(select(GeneratedItemVersion.id, GeneratedItemVersion.version_number, GeneratedItemVersion.parent_version_id,
        GeneratedItemVersion.status, GeneratedItemVersion.content_digest, GeneratedItemVersion.created_at).where(GeneratedItemVersion.artifact_id == artifact_id).order_by(GeneratedItemVersion.version_number.desc()))).mappings()
    reviews = (await db.execute(select(ArtifactReview.review_id, ArtifactReview.artifact_version_id, ArtifactReview.verdict,
        ArtifactReview.round, ArtifactReview.authority_kind, ArtifactReview.review_contract_version, ArtifactReview.created_at,
        *[ArtifactReview.scores[k].as_integer().label(k) for k in ("correctness", "clarity", "completeness", "grounding", "instructional_value")]).where(ArtifactReview.artifact_id == artifact_id))).mappings()
    grant = await db.scalar(select(AdminSampleGrant.id).join(GeneratedItem, GeneratedItem.id == AdminSampleGrant.artifact_id)
        .join(AdminAccountPurpose, AdminAccountPurpose.user_id == GeneratedItem.user_id).where(AdminSampleGrant.admin_id == admin,
        AdminSampleGrant.artifact_id == artifact_id, AdminSampleGrant.active == True, AdminAccountPurpose.purpose.in_(("qa", "demo"))))
    keys = ("goal_fit", "clarity", "internal_consistency", "unsupported_claim_risk", "artifact_integrity", "topic_fidelity", "expert_depth", "structure_fit", "specificity")
    raw = GeneratedItem.raw_data
    receipt = (await db.execute(select(*[raw["content_quality"]["scores"][k].as_integer().label(k) for k in keys],
        raw["content_quality"]["rounds"].as_integer().label("rounds"), raw["content_quality"]["revision_rounds"].as_integer().label("revision_rounds"),
        raw["content_quality"]["measured"].as_boolean().label("measured")).where(GeneratedItem.id == artifact_id))).mappings().one()
    formal = (await db.execute(select(ArtifactReview.review_id, ArtifactReview.artifact_version_id,
        *[ArtifactReview.scores[k].as_integer().label(k) for k in keys]).where(ArtifactReview.artifact_id == artifact_id, ArtifactReview.authority_kind == "TRUSTED_REVIEWER"))).mappings()
    dimensions = [{"source": r["review_id"], "version_id": r["artifact_version_id"], "scores": {k:r[k] for k in keys if isinstance(r[k],(int,float))}} for r in formal]
    if receipt["measured"]: dimensions.append({"source":"内容质量回执", "version_id":result["items"][0]["version_id"], "scores":{k:receipt[k] for k in keys if isinstance(receipt[k],(int,float))}})
    # Only categorical issue fields may be read without a sample ACL.
    defect_fields = []
    for index in range(6):
        defect_fields += [ArtifactReview.issues_json[index][k].as_string().label(f"{k}_{index}") for k in ("severity", "code", "category")]
    defect_rows = (await db.execute(select(ArtifactReview.review_id, *defect_fields).where(ArtifactReview.artifact_id == artifact_id, ArtifactReview.authority_kind == "TRUSTED_REVIEWER"))).mappings()
    import re
    defects = []
    for r in defect_rows:
        for index in range(6):
            codes = {k:r[f"{k}_{index}"] for k in ("severity","code","category") if isinstance(r[f"{k}_{index}"],str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_:-]{0,79}",r[f"{k}_{index}"])}
            if codes: defects.append({"review_id":r["review_id"],**codes})
    return data({**result["items"][0], "versions": [service.pack(r) for r in versions], "reviews": [service.pack(r) for r in reviews],
        "dimensions":dimensions, "defects":defects, "revision_rounds":receipt["revision_rounds"], "content_review_rounds":receipt["rounds"],
        "sample_authorized": bool(grant), "privacy_note": "仅展示状态、版本及实测数值。正文复核需要独立样本授权。"})


@router.get("/quality/{artifact_id}/sample")
async def sample(artifact_id: int, version_id: int, admin: int = Depends(get_current_admin_id), db: AsyncSession = Depends(get_db)):
    grant = await db.scalar(select(AdminSampleGrant.id).where(AdminSampleGrant.admin_id == admin, AdminSampleGrant.artifact_id == artifact_id, AdminSampleGrant.active == True))
    purpose = await db.scalar(select(AdminAccountPurpose.purpose).join(GeneratedItem, GeneratedItem.user_id == AdminAccountPurpose.user_id).where(GeneratedItem.id == artifact_id))
    if not grant or purpose not in {"qa", "demo"}: raise HTTPException(403, "此身份没有该管理维护样本的正文授权")
    row = (await db.execute(select(GeneratedItemVersion.content_snapshot, GeneratedItemVersion.content_digest, GeneratedItemVersion.version_number).where(GeneratedItemVersion.id == version_id, GeneratedItemVersion.artifact_id == artifact_id))).mappings().one_or_none()
    if row is None: raise HTTPException(404, "样本版本不存在")
    await write_audit_log(db, admin, "sample_read", "artifact", str(artifact_id), summary=f"读取已授权样本版本 {version_id}；未记录正文")
    return data(dict(row))


class GrantRequest(BaseModel):
    admin_id: int
    artifact_id: int
    reason: str = Field(min_length=3, max_length=500)
    active: bool = True


@router.put("/sample-grants")
async def grant_sample(body: GrantRequest, admin: int = Depends(supervisor), db: AsyncSession = Depends(get_db)):
    purpose = await db.scalar(select(AdminAccountPurpose.purpose).join(GeneratedItem, GeneratedItem.user_id == AdminAccountPurpose.user_id).where(GeneratedItem.id == body.artifact_id))
    if purpose not in {"qa", "demo"}: raise HTTPException(403, "只允许明确标记为测试或演示的管理维护样本")
    if not await db.get(AdminUser, body.admin_id): raise HTTPException(404, "管理员不存在")
    row = await db.scalar(select(AdminSampleGrant).where(AdminSampleGrant.admin_id == body.admin_id, AdminSampleGrant.artifact_id == body.artifact_id))
    if row is None: row = AdminSampleGrant(admin_id=body.admin_id, artifact_id=body.artifact_id); db.add(row)
    row.active, row.granted_by, row.reason = body.active, admin, body.reason.strip()
    await write_audit_log(db, admin, "sample_grant", "artifact", str(body.artifact_id), summary=f"管理员 {body.admin_id}；授权 {body.active}；{body.reason}")
    return data({"active": row.active})


@router.get("/tasks/detail")
async def task_detail(key: str, admin: int = Depends(get_current_admin_id), db: AsyncSession = Depends(get_db)):
    found = await service.tasks(db, {"key": key})
    if not found["items"]: raise HTTPException(404, "任务不存在")
    row = found["items"][0]
    quality = await service.qualities(db, {"artifact_id": row["artifact_id"]}) if row["artifact_id"] else {"items": []}
    links = (await db.execute(select(AdminRetry.source_artifact_id, AdminRetry.target_artifact_id, AdminRetry.created_at).where(
        (AdminRetry.source_artifact_id == row["artifact_id"]) | (AdminRetry.target_artifact_id == row["artifact_id"])))).mappings()
    can_retry = row["type"] == "workspace_research" and row["status"] in {"failed", "partial"}
    return data({**row, "quality": quality["items"][0]["quality"] if quality["items"] else "unreviewed",
        "retry_eligible": can_retry, "cancel_eligible": False,
        "retry_reason": "仅失败或部分交付的研究作业可创建关联的新尝试；其他任务没有已验证的管理重试契约。",
        "cancel_reason": "当前执行器没有管理端取消契约，不能保证中止已提交给供应商的工作。",
        "retry_links": [service.pack(r) for r in links], "stages": [row["stage"]] if row["stage"] else [],
        "usage_tokens": None, "cost": None})


class RetryRequest(BaseModel):
    artifact_id: int
    request_key: str = Field(min_length=16, max_length=64)
    reason: str = Field(min_length=3, max_length=500)
    confirmed_cost: bool


@router.post("/tasks/retry")
async def retry(body: RetryRequest, background: BackgroundTasks, admin: int = Depends(operator), db: AsyncSession = Depends(get_db)):
    lock = _retry_locks.setdefault(body.request_key, asyncio.Lock())
    async with lock:
        return await _retry_transaction(body, background, admin, db)


async def _retry_transaction(body, background, admin, db):
    if not body.confirmed_cost: raise HTTPException(422, "需要确认供应商用量及新产物交付影响")
    existing = await db.scalar(select(AdminRetry).where(AdminRetry.request_key == body.request_key))
    if existing:
        if existing.source_artifact_id != body.artifact_id or existing.admin_id != admin: raise HTTPException(409, "重试键已用于不同请求")
        return data({"target_artifact_id": existing.target_artifact_id, "reused": True})
    source = await db.get(GeneratedItem, body.artifact_id)
    if not source or source.type != "workspace_research" or str((source.raw_data or {}).get("status", "")).upper() not in {"FAILED", "PARTIAL"}:
        raise HTTPException(409, "当前作业不符合管理重试条件")
    # Server-owned continuation consumes existing input internally; never projects it to admin.
    from services.research_jobs import create_research_job
    from routers.research import _run_detached
    meta = source.raw_data or {}
    target = await create_research_job(db, user_id=source.user_id, thread_id=meta.get("thread_id", ""), question=meta.get("question", ""), depth=meta.get("depth", "standard"))
    db.add(AdminRetry(request_key=body.request_key, source_artifact_id=source.id, target_artifact_id=target.id, admin_id=admin, reason=body.reason.strip()))
    from sqlalchemy.exc import IntegrityError
    try:
        await write_audit_log(db, admin, "task_retry", "artifact", str(source.id), summary=f"新尝试 {target.id}；{body.reason}")
    except IntegrityError:
        await db.rollback()
        existing = await db.scalar(select(AdminRetry).where(AdminRetry.request_key == body.request_key))
        if existing and existing.source_artifact_id == body.artifact_id and existing.admin_id == admin:
            return data({"target_artifact_id": existing.target_artifact_id, "reused": True})
        raise HTTPException(409, "重试键已用于不同请求")
    background.add_task(_run_detached, target.id)
    return data({"target_artifact_id": target.id, "reused": False})


@router.get("/announcements/{announcement_id}/history")
async def history(announcement_id: int, admin: int = Depends(get_current_admin_id), db: AsyncSession = Depends(get_db)):
    from database.models import Announcement, UserAnnouncementRead
    rows = (await db.execute(select(AdminAnnouncementRevision).where(AdminAnnouncementRevision.announcement_id == announcement_id).order_by(AdminAnnouncementRevision.id.desc()))).scalars()
    reads = await db.scalar(select(func.count(UserAnnouncementRead.id)).where(UserAnnouncementRead.announcement_id == announcement_id, UserAnnouncementRead.is_read == True))
    audience = await db.scalar(select(func.count(User.id)).where(User.is_active == True))
    if not await db.get(Announcement, announcement_id): raise HTTPException(404, "公告不存在")
    return data({"revisions": [{"id": r.id, "admin_id": r.admin_id, "action": r.action, "snapshot": r.snapshot, "created_at": service.stamp(r.created_at)} for r in rows],
        "read_count": reads, "current_active_audience": audience, "notice": "阅读为累计真实回执；分母是当前启用账户，包含测试账户，不是发布时快照。"})


@router.get("/permissions")
async def permissions(admin: int = Depends(get_current_admin_id), db: AsyncSession = Depends(get_db)):
    who = await db.get(AdminUser, admin)
    rows = (await db.execute(select(AdminUser.id, AdminUser.username, AdminUser.display_name, AdminUser.role, AdminUser.is_active))).mappings() if who.role == "super_admin" else [{"id": who.id, "username": who.username, "role": who.role, "is_active": who.is_active}]
    return data({"items": [dict(r) for r in rows], "roles": ["super_admin", "operations", "observer"]})


class RoleRequest(BaseModel):
    role: Literal["super_admin", "operations", "observer"]
    reason: str = Field(min_length=3, max_length=500)


@router.put("/permissions/{target_id}")
async def role(target_id: int, body: RoleRequest, admin: int = Depends(supervisor), db: AsyncSession = Depends(get_db)):
    row = await db.get(AdminUser, target_id)
    if row is None: raise HTTPException(404, "管理员不存在")
    if target_id == admin: raise HTTPException(409, "不能修改自己的角色")
    before = row.role; row.role = body.role
    await write_audit_log(db, admin, "admin_role", "admin", str(target_id), summary=f"{before} → {body.role}；{body.reason}")
    return data({"role": row.role})


@router.get("/health")
async def health(request: Request, admin: int = Depends(get_current_admin_id), db: AsyncSession = Depends(get_db)):
    from routers.system import health_check
    status = await health_check()
    await db.execute(select(literal_one()))
    probes = (await db.execute(select(AdminCapabilityProbe))).scalars().all()
    q = service.task_projection()
    capabilities = []
    for name, kinds in (("对话", ("tutor",)), ("资料生成", ("document", "workspace_lecture", "workspace_handout", "workspace_presentation")),
        ("检索与研究", ("workspace_research", "search")), ("视频", ("workspace_video", "video"))):
        last = await db.scalar(select(func.max(q.c.created_at)).where(q.c.type.in_(kinds), q.c.status.in_(service.SUCCESS), q.c.record_kind != "artifact_only"))
        capabilities.append({"name": name, "state": "not_probed", "last_success_at": service.stamp(last), "basis": "最后成功为历史记录，不能证明当前可用；未进行主动能力试验"})
    conversation_probe = next((p for p in probes if p.capability == "conversation"), None)
    if conversation_probe:
        capabilities[0].update(
            state="probe_passed" if conversation_probe.state == "passed" else "probe_failed",
            last_success_at=service.stamp(conversation_probe.checked_at) if conversation_probe.state == "passed" else capabilities[0]["last_success_at"],
            checked_at=service.stamp(conversation_probe.checked_at),
            basis="最近一次主动对话试验；结果与时间见试验记录，仅证明本次链路，不保证持续在线",
        )
    last_review = await db.scalar(select(func.max(ArtifactReview.created_at)).where(ArtifactReview.authority_kind == "TRUSTED_REVIEWER"))
    capabilities.append({"name": "正式评审", "state": "not_probed", "last_success_at": service.stamp(last_review), "basis": "最近可信评审记录；未进行主动能力试验"})
    return data({"base": status, "database_probe": "connected", "checked_at": service.stamp(datetime.utcnow()), "api_routes": len(request.app.routes),
        "capabilities": capabilities, "probes": [{"capability": r.capability, "state": r.state, "checked_at": service.stamp(r.checked_at), "basis": r.basis} for r in probes],
        "config": get_current_config()})


def literal_one():
    from sqlalchemy import literal
    return literal(1)


class ProbeRequest(BaseModel):
    confirmed_cost: bool


@router.post("/probe")
async def probe(body: ProbeRequest, admin: int = Depends(supervisor), db: AsyncSession = Depends(get_db)):
    if not body.confirmed_cost: raise HTTPException(422, "主动对话试验会消耗供应商额度，需要明确确认")
    try:
        from services.llm_service_real import get_llm_service, extract_llm_text
        llm = await get_llm_service()
        response = await asyncio.wait_for(llm.chat([{"role": "user", "content": "Reply only OK. This is an administrator connectivity test."}], max_tokens=8, use_cache=False, write_cache=False), timeout=45)
        state = "passed" if extract_llm_text(response).strip() else "failed"
    except Exception:
        state = "failed"  # Do not put provider exceptions or credentials in the admin projection.
    row = await db.get(AdminCapabilityProbe, "conversation")
    if row is None: row = AdminCapabilityProbe(capability="conversation"); db.add(row)
    row.state, row.checked_at, row.basis = state, datetime.utcnow(), "一次真实对话供应商请求；只验证该链路，未验证生成、检索、评审和视频"
    await write_audit_log(db, admin, "capability_probe", "system", "conversation", success=state == "passed", summary=row.basis)
    return data({"state": state, "checked_at": service.stamp(row.checked_at)})


@router.post("/storybook-probe")
async def storybook_probe(body: ProbeRequest, admin: int = Depends(supervisor), db: AsyncSession = Depends(get_db)):
    if not body.confirmed_cost: raise HTTPException(422, "需要确认一次绘本供应商连接试验")
    from services.storybook_provider import read_glm_key, GLM_CHAT_URL, DEFAULT_CHAT_MODEL
    credential = await read_glm_key(db)
    if not credential: raise HTTPException(409, "绘本凭据未配置")
    import httpx
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(GLM_CHAT_URL, headers={"Authorization": f"Bearer {credential}"}, json={"model":DEFAULT_CHAT_MODEL,"messages":[{"role":"user","content":"Reply OK."}],"max_tokens":8})
            state = "passed" if response.status_code == 200 and response.json().get("choices") else "failed"
    except Exception: state = "failed"
    row = await db.get(AdminCapabilityProbe, "storybook")
    if row is None: row = AdminCapabilityProbe(capability="storybook"); db.add(row)
    row.state, row.checked_at, row.basis = state, datetime.utcnow(), "绘本供应商文本连接试验；不代表图像生成能力通过"
    await write_audit_log(db, admin, "capability_probe", "system", "storybook", success=state=="passed", summary=row.basis)
    return data({"state":state,"checked_at":service.stamp(row.checked_at)})


async def apply_runtime_settings(db):
    from config import settings
    row = await db.get(AdminRuntimeSetting, "LLM_MODEL")
    if row:
        settings.LLM_MODEL = row.value
        if settings.LLM_PROVIDER == "deepseek": settings.DEEPSEEK_MODEL = row.value
        row.applied_at = datetime.utcnow()
        await db.commit()


@router.get("/config")
async def runtime_config(admin: int = Depends(get_current_admin_id), db: AsyncSession = Depends(get_db)):
    row = await db.get(AdminRuntimeSetting, "LLM_MODEL")
    return data({"config":get_current_config(),"pending_model":row.value if row else None,"previous_model":row.previous_value if row else None,
        "applied_at":service.stamp(row.applied_at) if row else None,"source":"管理配置数据库优先于环境变量（服务重启时应用）" if row else "服务环境变量"})


class ModelRequest(BaseModel):
    model: str = Field(min_length=1,max_length=100,pattern=r"^[a-zA-Z0-9._:/-]+$")
    reason: str = Field(min_length=3,max_length=300)


@router.put("/config")
async def save_model(body: ModelRequest, admin: int = Depends(supervisor), db: AsyncSession = Depends(get_db)):
    row = await db.get(AdminRuntimeSetting, "LLM_MODEL")
    current = get_current_config()["LLM_MODEL"]["runtime"]
    if row is None: row = AdminRuntimeSetting(key="LLM_MODEL",value=current); db.add(row)
    before = row.value
    if body.model != before: row.previous_value, row.value, row.applied_at = before, body.model, None
    await write_audit_log(db,admin,"model_config","system","LLM_MODEL",summary=f"{before} → {body.model}；重启生效；{body.reason}")
    return data({"needs_restart":True,"pending_model":row.value})
