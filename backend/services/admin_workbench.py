"""Metadata-only administrative projections from canonical product authorities.

Never select learner messages, titles, prompts, content, raw errors or whole JSON
documents here. JSON extraction is restricted to state codes and numeric facts.
"""
from datetime import datetime, timedelta, timezone
import re
from sqlalchemy import select, func, case, literal, union_all, or_, and_
from database.models import (User, ChatSession, GeneratedItem, GeneratedItemVersion,
    WorkspaceAgentTask, AgentInvocation, ArtifactReview, StudentProfile,
    AnswerRecord, InteractiveQuestionAttempt, GeneratedItemCodeRun)
from database.admin_models import AdminAccountPurpose, AdminIssue

SUCCESS = {"success", "succeeded", "completed", "ready"}
FAILED = {"failed", "error", "blocked"}
RUNNING = {"running", "retrying", "queued", "planned", "generating", "searching", "reading", "synthesizing", "reviewing", "generating_script", "generating_audio", "generating_avatar", "composing"}


def stamp(v):
    return v.replace(tzinfo=timezone.utc).isoformat() if isinstance(v, datetime) else v


def pack(row):
    return {k: stamp(v) for k, v in dict(row).items()}


def purpose():
    # Explicit labels take precedence; legacy QA naming is visible as inferred.
    inferred = or_(*[User.username.ilike(p) for p in ("darya_%", "qa_%", "test_%", "demo_%", "adminqa_%")])
    return func.coalesce(AdminAccountPurpose.purpose, case((inferred, "inferred_qa"), else_="learner"))


def user_projection():
    week = datetime.utcnow() - timedelta(days=7)
    chats = select(ChatSession.user_id.label("uid"), func.max(ChatSession.last_message_at).label("last"),
        func.count().label("sessions"), func.sum(ChatSession.message_count).label("messages"),
        func.sum(case((ChatSession.last_message_at >= week, 1), else_=0)).label("week_sessions")).group_by(ChatSession.user_id).subquery()
    items = select(GeneratedItem.user_id.label("uid"), func.max(GeneratedItem.created_at).label("last"),
        func.count().label("items"), func.sum(case((GeneratedItem.created_at >= week, 1), else_=0)).label("week_items")).group_by(GeneratedItem.user_id).subquery()
    task_rows = task_projection()
    calls = select(task_rows.c.user_id.label("uid"), func.count().label("tasks"),
        func.sum(case((task_rows.c.status.in_(FAILED), 1), else_=0)).label("errors"))\
        .where(task_rows.c.record_kind != "artifact_only").group_by(task_rows.c.user_id).subquery()
    submissions = select(AnswerRecord.user_id.label("uid"), func.max(AnswerRecord.created_at).label("last"), func.count().label("submissions")).group_by(AnswerRecord.user_id).subquery()
    # Aggregate events once, rather than expanding a nested max CASE exponentially.
    events = union_all(
        select(User.id.label("uid"), User.created_at.label("at")),
        select(ChatSession.user_id, ChatSession.last_message_at),
        select(GeneratedItem.user_id, GeneratedItem.created_at),
        select(AgentInvocation.user_id, AgentInvocation.created_at),
        select(WorkspaceAgentTask.user_id, WorkspaceAgentTask.created_at),
        select(AnswerRecord.user_id, AnswerRecord.created_at),
        select(InteractiveQuestionAttempt.user_id, InteractiveQuestionAttempt.submitted_at),
        select(GeneratedItemCodeRun.user_id, GeneratedItemCodeRun.created_at),
    ).subquery()
    activity = select(events.c.uid, func.max(events.c.at).label("last")).group_by(events.c.uid).subquery()
    last = activity.c.last
    return select(User.id.label("user_id"), User.username, User.is_active, User.created_at,
        last.label("last_active_at"), purpose().label("purpose"),
        (select(func.count(StudentProfile.id)).where(StudentProfile.user_id == User.id).scalar_subquery() > 0).label("has_profile"),
        func.coalesce(chats.c.sessions, 0).label("chat_count"), func.coalesce(chats.c.messages, 0).label("message_count"),
        func.coalesce(chats.c.week_sessions, 0).label("week_sessions"), func.coalesce(items.c.week_items, 0).label("week_items"),
        func.coalesce(items.c["items"], 0).label("item_count"), func.coalesce(calls.c.tasks, 0).label("task_count"),
        func.coalesce(calls.c.errors, 0).label("error_count"),
        func.coalesce(submissions.c.submissions, 0).label("answer_record_count"))\
        .outerjoin(activity, activity.c.uid == User.id).outerjoin(AdminAccountPurpose, AdminAccountPurpose.user_id == User.id)\
        .outerjoin(chats, chats.c.uid == User.id).outerjoin(items, items.c.uid == User.id)\
        .outerjoin(calls, calls.c.uid == User.id)\
        .outerjoin(submissions, submissions.c.uid == User.id)


def task_projection():
    inv = select((literal("call:") + func.cast(AgentInvocation.id, __import__("sqlalchemy").String)).label("key"),
        literal("agent_task").label("record_kind"), AgentInvocation.id.label("id"), AgentInvocation.user_id,
        AgentInvocation.agent_name.label("type"), func.lower(AgentInvocation.status).label("status"), AgentInvocation.created_at,
        AgentInvocation.duration_ms, AgentInvocation.output_id.label("artifact_id"),
        literal(None).label("stage"), case((AgentInvocation.status.in_(FAILED), "task_failed"), else_=None).label("error_category"),
        literal("complete").label("tracking"))
    job = select((literal("task:") + WorkspaceAgentTask.task_id).label("key"), literal("workspace_task").label("record_kind"),
        WorkspaceAgentTask.id, WorkspaceAgentTask.user_id, WorkspaceAgentTask.agent_type.label("type"), func.lower(WorkspaceAgentTask.state).label("status"),
        WorkspaceAgentTask.created_at, literal(None).label("duration_ms"), WorkspaceAgentTask.artifact_id,
        WorkspaceAgentTask.state.label("stage"), case((WorkspaceAgentTask.state.in_(FAILED), "task_failed"), else_=None).label("error_category"), literal("complete").label("tracking"))
    job_state = func.lower(GeneratedItem.raw_data["status"].as_string())
    # Only research/video have a documented durable job contract on the artifact.
    durable = GeneratedItem.type.in_(("workspace_research", "workspace_video"))
    artifact = select((literal("artifact:") + func.cast(GeneratedItem.id, __import__("sqlalchemy").String)).label("key"),
        case((durable, "durable_job"), else_="artifact_only").label("record_kind"), GeneratedItem.id, GeneratedItem.user_id,
        GeneratedItem.type.label("type"), case((durable, func.coalesce(job_state, "unknown")), else_="artifact_exists").label("status"),
        GeneratedItem.created_at, literal(None).label("duration_ms"), GeneratedItem.id.label("artifact_id"),
        case((durable, job_state), else_=None).label("stage"), case((and_(durable, job_state.in_(FAILED)), "task_failed"), else_=None).label("error_category"),
        case((durable, "job_state"), else_="missing_call").label("tracking"))\
        .where(~GeneratedItem.id.in_(select(AgentInvocation.output_id).where(AgentInvocation.output_id.is_not(None))),
            ~GeneratedItem.id.in_(select(WorkspaceAgentTask.artifact_id).where(WorkspaceAgentTask.artifact_id.is_not(None))))
    return union_all(inv, job, artifact).subquery()


def quality_projection():
    latest_review = select(ArtifactReview.review_id).where(ArtifactReview.artifact_version_id == GeneratedItem.current_version_id,
        ArtifactReview.artifact_id == GeneratedItem.id, ArtifactReview.user_id == GeneratedItem.user_id,
        ArtifactReview.version_digest == GeneratedItemVersion.content_digest,
        ArtifactReview.authority_kind == "TRUSTED_REVIEWER").order_by(ArtifactReview.round.desc(), ArtifactReview.created_at.desc()).limit(1).correlate(GeneratedItem, GeneratedItemVersion).scalar_subquery()
    return select(GeneratedItem.id.label("artifact_id"), GeneratedItem.user_id, GeneratedItem.type,
        GeneratedItem.created_at, GeneratedItem.current_version_id.label("version_id"), GeneratedItem.current_version_number.label("version_number"),
        GeneratedItemVersion.status.label("generation_status"), ArtifactReview.verdict,
        ArtifactReview.review_id, ArtifactReview.round.label("review_round"), ArtifactReview.review_contract_version.label("contract"),
        ArtifactReview.issues_json[0]["code"].as_string().label("defect_code"),
        ArtifactReview.created_at.label("reviewed_at"), GeneratedItem.raw_data["content_quality"]["verdict"].as_string().label("content_verdict"),
        GeneratedItem.raw_data["content_quality"]["measured"].as_boolean().label("measured"),
        GeneratedItem.raw_data["content_review"]["verdict"].as_string().label("research_verdict"),
        GeneratedItem.raw_data["status"].as_string().label("job_status"))\
        .outerjoin(GeneratedItemVersion, GeneratedItemVersion.id == GeneratedItem.current_version_id)\
        .outerjoin(ArtifactReview, ArtifactReview.review_id == latest_review)


def quality_state(row):
    formal = str(row.get("verdict") or "").lower()
    content = str(row.get("content_verdict") or row.get("research_verdict") or "").lower()
    state = str(row.get("job_status") or row.get("generation_status") or "").lower()
    verdict = formal or content
    if verdict in {"pass", "passed"}: return "passed"
    if verdict in {"block", "blocked", "reject", "rejected"}: return "rejected"
    if verdict in {"revise", "revision_required"}: return "revision_required"
    if state == "partial": return "partial"
    if state in RUNNING: return "generating"
    return "unreviewed"


async def paginate(db, query, page=1, page_size=20):
    total = await db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).mappings().all()
    return {"items": [pack(r) for r in rows], "total": total, "page": page, "page_size": page_size,
        "updated_at": stamp(datetime.utcnow())}


def date_boundary(value, end=False):
    # Date controls represent Shanghai natural days; storage is UTC without tz.
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        parsed -= timedelta(hours=8)
    return parsed + timedelta(days=1) if end else parsed


def quality_expression(q):
    verdict = func.lower(func.coalesce(q.c.verdict, q.c.content_verdict, q.c.research_verdict, ""))
    state = func.lower(func.coalesce(q.c.job_status, q.c.generation_status, ""))
    return case((verdict.in_(("pass", "passed")), "passed"), (verdict.in_(("block", "blocked", "reject", "rejected")), "rejected"),
        (verdict.in_(("revise", "revision_required")), "revision_required"), (state == "partial", "partial"), (state.in_(RUNNING), "generating"), else_="unreviewed")


async def users(db, params):
    q = user_projection().subquery()
    stmt = select(q)
    keyword = params.get("keyword", "").strip()
    if keyword: stmt = stmt.where(or_(q.c.username.ilike(f"%{keyword}%"), q.c.user_id == (int(keyword) if keyword.isdigit() else -1)))
    if params.get("status") in {"active", "disabled"}: stmt = stmt.where(q.c.is_active == (params["status"] == "active"))
    if params.get("purpose"): stmt = stmt.where(q.c.purpose == params["purpose"])
    if params.get("errors") == "yes": stmt = stmt.where(q.c.error_count > 0)
    for field, column in (("start_date", q.c.last_active_at), ("end_date", q.c.last_active_at)):
        if params.get(field):
            date = date_boundary(params[field], field == "end_date")
            stmt = stmt.where(column >= date if field == "start_date" else column < date)
    order = q.c.created_at if params.get("sort") == "created" else q.c.last_active_at
    return await paginate(db, stmt.order_by(order.desc(), q.c.user_id.desc()), int(params.get("page", 1)), int(params.get("page_size", 20)))


async def tasks(db, params):
    q = task_projection()
    quality = quality_projection().subquery()
    verdict = quality_expression(quality)
    stmt = select(q, verdict.label("quality"), case((q.c.artifact_id.is_(None), "not_delivered"), else_="artifact_linked").label("delivery"))\
        .outerjoin(quality, quality.c.artifact_id == q.c.artifact_id)
    for field in ("user_id", "status", "type", "record_kind", "error_category", "key", "artifact_id"):
        if params.get(field): stmt = stmt.where(q.c[field] == params[field])
    for field in ("start_date", "end_date"):
        if params.get(field):
            date = date_boundary(params[field], field == "end_date")
            stmt = stmt.where(q.c.created_at >= date if field == "start_date" else q.c.created_at < date)
    if params.get("quality"): stmt = stmt.where(verdict == params["quality"])
    if params.get("execution_only") == "yes": stmt = stmt.where(q.c.record_kind != "artifact_only")
    if params.get("delivery") in {"not_delivered", "artifact_linked"}:
        stmt = stmt.where(q.c.artifact_id.is_(None) if params["delivery"] == "not_delivered" else q.c.artifact_id.is_not(None))
    return await paginate(db, stmt.order_by(q.c.created_at.desc(), q.c.key), int(params.get("page", 1)), int(params.get("page_size", 20)))


async def qualities(db, params):
    q = quality_projection().subquery()
    stmt = select(q)
    for field in ("user_id", "type", "artifact_id", "generation_status", "defect_code"):
        if params.get(field): stmt = stmt.where(q.c[field] == params[field])
    for field in ("start_date", "end_date"):
        if params.get(field):
            date = date_boundary(params[field], field == "end_date")
            stmt = stmt.where(q.c.created_at >= date if field == "start_date" else q.c.created_at < date)
    # SQL classification keeps filtered totals and pagination truthful.
    quality = quality_expression(q)
    if params.get("quality"): stmt = stmt.where(quality == params["quality"])
    if params.get("tracking") == "missing": stmt = stmt.where(q.c.version_id.is_(None))
    data = await paginate(db, stmt.order_by(q.c.created_at.desc(), q.c.artifact_id.desc()), int(params.get("page", 1)), int(params.get("page_size", 20)))
    for row in data["items"]:
        if not isinstance(row["defect_code"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_:-]{0,79}", row["defect_code"]): row["defect_code"] = None
        row["quality"] = quality_state(row)
        row["review_source"] = "正式版本审查" if row["review_id"] else "内容质量回执" if row["content_verdict"] or row["research_verdict"] else "未审查"
    return data


async def sync_issues(db):
    q = task_projection()
    rows = (await db.execute(select(q).where(q.c.status.in_(FAILED)))).mappings().all()
    quality = await qualities(db, {"quality": "rejected", "page_size": 100})
    while len(quality["items"]) < quality["total"]:
        page = await qualities(db, {"quality": "rejected", "page_size": 100, "page": len(quality["items"]) // 100 + 1})
        quality["items"].extend(page["items"])
    sources = [(r["key"], "task_failed", "task", r["key"], r["user_id"], r["created_at"]) for r in rows]
    sources.extend((f"quality:{r['artifact_id']}:{r['version_id']}", "quality_rejected", "artifact", str(r["artifact_id"]), r["user_id"], datetime.fromisoformat(r["created_at"]).replace(tzinfo=None)) for r in quality["items"])
    existing = set((await db.execute(select(AdminIssue.source_key))).scalars())
    for key, category, kind, object_id, uid, date in sources:
        if key not in existing:
            from sqlalchemy.exc import IntegrityError
            try:
                async with db.begin_nested():
                    db.add(AdminIssue(source_key=key, category=category, object_type=kind, object_id=object_id, user_id=uid,
                        first_seen_at=date or datetime.utcnow(), last_seen_at=date or datetime.utcnow()))
                    await db.flush()
            except IntegrityError:
                pass  # Another request already materialized this exact source.
    if sources: await db.commit()


async def overview(db, scope="business"):
    uq = user_projection().subquery()
    business = uq.c.purpose == "learner" if scope != "all" else literal(True)
    user_ids = select(uq.c.user_id).where(business)
    week = datetime.utcnow() - timedelta(days=7)
    tq = task_projection()
    measured = tq.c.record_kind != "artifact_only"
    recent = (await db.execute(select(tq).where(tq.c.created_at >= week, tq.c.user_id.in_(user_ids), measured))).mappings().all()
    terminal = [r for r in recent if r["status"] in SUCCESS | FAILED]
    success = sum(r["status"] in SUCCESS for r in terminal)
    durations = [r["duration_ms"] for r in recent if r["duration_ms"] is not None]
    days = []
    for offset in range(6, -1, -1):
        # China natural-day bins; rolling 7d aggregates are labelled separately.
        day = (datetime.utcnow() + timedelta(hours=8) - timedelta(days=offset)).date()
        lo = datetime.combine(day, datetime.min.time()) - timedelta(hours=8)
        hi = lo + timedelta(days=1)
        counts = await db.scalar(select(func.count(GeneratedItem.id)).where(GeneratedItem.user_id.in_(user_ids), GeneratedItem.created_at >= lo, GeneratedItem.created_at < hi))
        task_count = await db.scalar(select(func.count()).select_from(tq).where(tq.c.user_id.in_(user_ids), measured, tq.c.created_at >= lo, tq.c.created_at < hi))
        days.append({"date": str(day), "artifacts": counts, "tasks": task_count})
    return {"scope": scope, "window": "近七天滚动统计；趋势按上海自然日", "source": "用户 / 会话元数据 / 正式产物 / 调用与作业记录", "updated_at": stamp(datetime.utcnow()),
        "users": await db.scalar(select(func.count()).select_from(uq).where(business)),
        "active_users_7d": await db.scalar(select(func.count()).select_from(uq).where(business, uq.c.last_active_at >= week)),
        "artifacts_7d": await db.scalar(select(func.count(GeneratedItem.id)).where(GeneratedItem.user_id.in_(user_ids), GeneratedItem.created_at >= week)),
        "tasks_7d": len(recent), "terminal_sample": len(terminal), "success_rate": round(success / len(terminal) * 100, 1) if terminal else None,
        "avg_duration_ms": round(sum(durations) / len(durations)) if durations else None, "duration_sample": len(durations),
        "usage_tokens": None, "cost": None, "trend": days,
        "purpose_note": "测试、演示及按旧命名识别的测试账户默认排除；用途标签可在用户详情确认。"}
