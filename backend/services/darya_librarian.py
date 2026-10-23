"""DARYA Continuity Brain 01: the Background Librarian.

The Librarian lives on the timeline, not in the current turn. It reads recent
conversations and real system state, then consolidates Shared State with the
same strong model in an independent context:

- Long-term Memory (DaryaLongTermMemory) via add/reinforce/revise/supersede/forget
- User Model (AIImpression) over fixed scholar dimensions with confidence
- Open Loops (DaryaOpenLoop) for anything promised or started but unfinished

Deterministic code owns truth boundaries: evidence spans must be verbatim in
the recent conversation, sensitive content is rejected, USER_CONFIRMED memory
is never rewritten by the model, scopes and lifetimes are enforced, and closed
loops stay closed. The Librarian is continuity intelligence, not a second
planner; Foreground DARYA re-judges every turn from the Continuity Brief.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AIImpression, ChatSession, DaryaLongTermMemory, DaryaOpenLoop
from services.tide_long_term_memory import (
    ACTIVE_STATE,
    NON_SERVING_STATES,
    contains_sensitive,
    select_relevant_memories,
)

logger = logging.getLogger("rjianb.librarian")

# What dimensions decide "what kind of learner/scholar a person is". The User
# Model is organized along these dimensions; each belief carries its own
# confidence and evidence, and may be reinforced or overturned by behavior.
USER_MODEL_DIMENSIONS: dict[str, str] = {
    "background": "知识基础与经验水平：学过什么、在哪里卡住、从多深的地方起步",
    "domain_stage": "领域与阶段：正在学什么领域、处于入门/练习/迁移/熟练哪个阶段",
    "method_preference": "方法取向：例子先还是规则先、偏动手还是偏讲解、偏视觉还是偏文字",
    "autonomy": "自主程度：想自己完成再对照，还是想被带着走；何时想要提示何时想要答案",
    "rhythm": "节奏与时间：每次可投入多久、间隔、耐力与减量习惯",
    "motivation": "动机情境：职业需要、兴趣、应试、要教别人，还是解决眼前问题",
    "meta_cognition": "元认知：是否会自检、如何对待错误、能否说出自己哪里不懂",
    "current_state": "短期状态：精力、情绪、最近的生活变化；短效，应快速衰减",
}

MEMORY_KINDS = {"IDENTITY", "GOAL", "PREFERENCE", "CONSTRAINT", "SITUATION"}
MEMORY_SCOPES = {"ACCOUNT", "STAGE", "THREAD"}
LOOP_OPEN_STATES = {"PLANNED", "IN_PROGRESS", "WAITING", "BLOCKED"}
LOOP_CLOSED_STATES = {"COMPLETED", "CANCELLED", "SUPERSEDED"}
LOOP_ORIGINS = {"USER_REQUEST", "ASSISTANT_COMMITMENT", "PLAN"}

_SCOPE_TTL_DAYS = {"ACCOUNT": 180, "STAGE": 14, "THREAD": 3}
_STABLE_KINDS = {"IDENTITY", "PREFERENCE"}
_MAX_OPS_PER_TYPE = 5
_MIN_TURN_CHARS = 12
_PASS_DEBOUNCE_SECONDS = 90.0

_last_pass_at: dict[str, float] = {}


def _now() -> datetime:
    return datetime.utcnow()


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _scope_ttl(kind: str, scope: str) -> tuple[str, datetime | None]:
    if scope == "ACCOUNT" and kind in _STABLE_KINDS:
        return "STABLE", None
    return "TIME_BOUND", _now() + timedelta(days=_SCOPE_TTL_DAYS.get(scope, 14))


async def _load_context(db: AsyncSession, *, user_id: int, thread_id: str) -> dict[str, Any]:
    sessions = list((await db.scalars(
        select(ChatSession).where(ChatSession.user_id == user_id)
    )).all())
    target = next((s for s in sessions if s.thread_id == thread_id), None)
    recent: list[dict[str, str]] = []
    if target is not None:
        for row in list(target.messages or [])[-14:]:
            if isinstance(row, dict) and row.get("role") in {"user", "assistant"}:
                content = str(row.get("content") or "").strip()
                if content:
                    recent.append({"role": str(row["role"]), "content": content[:600]})
    memories = list((await db.scalars(
        select(DaryaLongTermMemory).where(
            DaryaLongTermMemory.user_id == user_id,
            DaryaLongTermMemory.state == ACTIVE_STATE,
        ).order_by(DaryaLongTermMemory.updated_at.desc()).limit(20)
    )).all())
    impressions = list((await db.scalars(
        select(AIImpression).where(
            AIImpression.user_id == user_id,
            AIImpression.status == "active",
        ).order_by(AIImpression.confidence.desc()).limit(12)
    )).all())
    loops = list((await db.scalars(
        select(DaryaOpenLoop).where(
            DaryaOpenLoop.user_id == user_id,
            DaryaOpenLoop.status.in_(LOOP_OPEN_STATES),
        ).order_by(DaryaOpenLoop.updated_at.desc()).limit(10)
    )).all())
    return {
        "recent": recent,
        "memories": [
            {"topic": m.topic, "kind": m.provenance.get("kind"), "scope": m.scope,
             "content": m.content[:200], "status": m.epistemic_status}
            for m in memories
        ],
        "impressions": [
            {"dimension": i.dimension, "summary": i.summary[:160],
             "confidence": i.confidence, "evidence_count": i.evidence_count}
            for i in impressions
        ],
        "open_loops": [
            {"statement": l.statement[:200], "origin": l.origin, "status": l.status}
            for l in loops
        ],
    }


async def _librarian_decision(context: dict[str, Any], llm: Any) -> dict[str, Any]:
    prompt = (
        "你是 DARYA 的 Librarian：站在时间线上的记忆整理者，不与用户直接对话。"
        "基于最近对话做增量巩固：add/reinforce/revise/supersede/forget，而不是不断追加。\n"
        "规则：\n"
        "1. memory_ops 只记录用户本人真实说过的、关于用户或用户目标的信息；evidence_span 必须是最近 user 消息的逐字原文。"
        "绝不把 assistant 的回答、承诺、自我能力描述或错误说法保存成用户事实、约束、偏好或画像；不确定就跳过。\n"
        "2. 用户纠正了旧值时用 revise 或 supersede（给 target_topic），不要并存两个矛盾值。\n"
        "3. impression_ops 只允许这些维度：" + "、".join(sorted(USER_MODEL_DIMENSIONS)) + "。"
        "一次行为不要贴永久标签；置信度 0.2-0.95；已有同维度推断时用 reinforce 或 revise。\n"
        "4. loop_ops 记录还没走完的事：用户要求的事（USER_REQUEST）、DARYA 答应过的事（ASSISTANT_COMMITMENT）。"
        "已经完成的必须 complete，不再留成提醒。\n"
        "5. 每类操作最多 " + str(_MAX_OPS_PER_TYPE) + " 条；没有值得写的就输出空数组。\n"
        "只输出JSON："
        '{"memory_ops":[{"op":"add|reinforce|revise|supersede|forget","kind":"IDENTITY|GOAL|PREFERENCE|CONSTRAINT|SITUATION",'
        '"value":"","evidence_span":"","target_topic":"","scope":"ACCOUNT|STAGE|THREAD"}],'
        '"impression_ops":[{"op":"add|reinforce|revise","dimension":"","summary":"","confidence":0.0,"evidence":""}],'
        '"loop_ops":[{"op":"open|progress|wait|block|complete|cancel","origin":"USER_REQUEST|ASSISTANT_COMMITMENT|PLAN",'
        '"statement":"","evidence":""}],'
        '"should_not_repeat":[""]}\n'
        "最近对话：" + json.dumps(context["recent"], ensure_ascii=False) + "\n"
        "现有长期记忆：" + json.dumps(context["memories"], ensure_ascii=False) + "\n"
        "用户模型：" + json.dumps(context["impressions"], ensure_ascii=False) + "\n"
        "未关闭的开放循环：" + json.dumps(context["open_loops"], ensure_ascii=False)
    )
    raw = await asyncio.wait_for(llm.chat(
        messages=[{"role": "user", "content": prompt}], temperature=0,
        max_tokens=1600, response_format={"type": "json_object"},
        use_cache=False, write_cache=False,
    ), timeout=55)
    if isinstance(raw, Mapping):
        raw = raw.get("content", raw)
    value = json.loads(raw) if isinstance(raw, str) else raw
    return value if isinstance(value, dict) else {}


def _ops(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)][: _MAX_OPS_PER_TYPE]


async def _apply_memory_ops(db: AsyncSession, *, user_id: int, thread_id: str, ops: list[dict],
                            recent_text: str) -> dict[str, int]:
    applied = {"add": 0, "reinforce": 0, "revise": 0, "supersede": 0, "forget": 0}
    for op in ops:
        action = str(op.get("op") or "").lower()
        kind = str(op.get("kind") or "").upper()
        value = " ".join(str(op.get("value") or "").split())[:300]
        span = " ".join(str(op.get("evidence_span") or "").split())[:300]
        scope = str(op.get("scope") or "").upper() or "ACCOUNT"
        if scope not in MEMORY_SCOPES:
            scope = "ACCOUNT"
        if not value or kind not in MEMORY_KINDS or action not in applied:
            continue
        if action in {"add", "revise", "supersede"}:
            if not span or _compact(span) not in recent_text or contains_sensitive(value):
                continue
        digest = _digest(f"{kind}\0{value}")
        topic = f"observed:{kind.lower()}:{digest}"
        if action == "add":
            existing = (await db.execute(select(DaryaLongTermMemory).where(
                DaryaLongTermMemory.user_id == user_id,
                DaryaLongTermMemory.state == ACTIVE_STATE,
                DaryaLongTermMemory.topic == topic,
            ))).scalars().first()
            if existing is not None:
                existing.confidence = min(0.95, max(existing.confidence, 0.85))
                existing.updated_at = _now()
                applied["reinforce"] += 1
                continue
            freshness, revalidate = _scope_ttl(kind, scope)
            db.add(DaryaLongTermMemory(
                user_id=user_id,
                memory_type="PREFERENCE" if kind == "PREFERENCE" else "FACT",
                topic=topic,
                content=value,
                provenance={"kind": kind, "source_type": "LIBRARIAN_CONSOLIDATION",
                            "evidence_span": span},
                epistemic_status="OBSERVED",
                confidence=0.85,
                scope=scope,
                source_thread_id=thread_id,
                freshness=freshness,
                revalidate_after_at=revalidate,
            ))
            applied["add"] += 1
            continue
        # reinforce/revise/supersede/forget act on an existing row.
        target: DaryaLongTermMemory | None = None
        target_topic = str(op.get("target_topic") or "")
        if target_topic:
            target = (await db.execute(select(DaryaLongTermMemory).where(
                DaryaLongTermMemory.user_id == user_id,
                DaryaLongTermMemory.state == ACTIVE_STATE,
                DaryaLongTermMemory.topic == target_topic,
            ))).scalars().first()
        if target is None:
            matches = list((await db.scalars(select(DaryaLongTermMemory).where(
                DaryaLongTermMemory.user_id == user_id,
                DaryaLongTermMemory.state == ACTIVE_STATE,
                DaryaLongTermMemory.epistemic_status != "USER_CONFIRMED",
            ).order_by(DaryaLongTermMemory.updated_at.desc()).limit(20))).all())
            if action in {"revise", "supersede"}:
                # The new value cannot match the old content by definition;
                # a correction targets the most recent belief of the same kind.
                target = next((m for m in matches if (m.provenance or {}).get("kind") == kind), None)
            else:
                needle = _compact(value)[:24]
                target = next((m for m in matches if needle and needle in _compact(m.content)), None)
        if target is None:
            continue
        # Boundary: the model may not rewrite what the user explicitly confirmed.
        if action != "add" and target.epistemic_status == "USER_CONFIRMED":
            continue
        if action == "reinforce":
            target.confidence = min(0.95, target.confidence + 0.05)
            target.updated_at = _now()
            applied["reinforce"] += 1
        elif action == "forget":
            target.state = "FORGOTTEN"
            applied["forget"] += 1
        elif action in {"revise", "supersede"}:
            if _compact(span) not in recent_text:
                continue
            target.state = "SUPERSEDED"
            freshness, revalidate = _scope_ttl(kind, scope)
            db.add(DaryaLongTermMemory(
                user_id=user_id,
                memory_type=target.memory_type,
                topic=target.topic,
                content=value,
                provenance={"kind": kind, "source_type": "LIBRARIAN_CONSOLIDATION",
                            "evidence_span": span, "supersedes_content": target.content[:200]},
                epistemic_status="OBSERVED",
                confidence=0.85,
                scope=scope,
                source_thread_id=thread_id,
                revision=target.revision + 1,
                supersedes_id=target.id,
                freshness=freshness,
                revalidate_after_at=revalidate,
            ))
            applied["revise" if action == "revise" else "supersede"] += 1
    return applied


async def _apply_impression_ops(
    db: AsyncSession, *, user_id: int, ops: list[dict], recent_user_text: str,
) -> dict[str, int]:
    applied = {"add": 0, "reinforce": 0, "revise": 0}
    for op in ops:
        action = str(op.get("op") or "").lower()
        dimension = str(op.get("dimension") or "").lower()
        summary = " ".join(str(op.get("summary") or "").split())[:240]
        evidence = str(op.get("evidence") or "")[:300]
        if dimension not in USER_MODEL_DIMENSIONS or not summary or action not in applied:
            continue
        if not evidence or _compact(evidence) not in recent_user_text:
            continue
        if contains_sensitive(summary):
            continue
        try:
            confidence = max(0.2, min(0.95, float(op.get("confidence") or 0.5)))
        except (TypeError, ValueError):
            confidence = 0.5
        key = f"user_model:{dimension}"
        existing = (await db.execute(select(AIImpression).where(
            AIImpression.user_id == user_id,
            AIImpression.impression_key == key,
        ))).scalars().first()
        if existing is None:
            if action == "reinforce":
                continue
            db.add(AIImpression(
                user_id=user_id,
                impression_key=key,
                dimension=dimension,
                summary=summary,
                confidence=confidence,
                scope="ACCOUNT",
                evidence_count=1,
                evidence_refs=[evidence] if evidence else [],
                first_observed_at=_now(),
                last_observed_at=_now(),
                status="active",
                source_type="librarian_consolidation",
            ))
            applied["add"] += 1
            continue
        existing.last_observed_at = _now()
        existing.evidence_count = (existing.evidence_count or 0) + 1
        if evidence and evidence not in list(existing.evidence_refs or [])[:0] + []:
            refs = list(existing.evidence_refs or [])
            if evidence not in refs:
                refs.append(evidence)
                existing.evidence_refs = refs[-5:]
        if action == "reinforce":
            existing.confidence = min(0.95, max(existing.confidence, confidence) + 0.05)
            applied["reinforce"] += 1
        elif action == "revise":
            existing.summary = summary
            existing.confidence = confidence
            applied["revise"] += 1
    return applied


async def _apply_loop_ops(db: AsyncSession, *, user_id: int, thread_id: str, ops: list[dict],
                          recent_text: str) -> dict[str, int]:
    applied = {"open": 0, "progress": 0, "close": 0}
    for op in ops:
        action = str(op.get("op") or "").lower()
        origin = str(op.get("origin") or "USER_REQUEST").upper()
        statement = " ".join(str(op.get("statement") or "").split())[:400]
        evidence = str(op.get("evidence") or "")[:400]
        if not statement or origin not in LOOP_ORIGINS:
            continue
        loop_key = _digest(f"{origin}\0{_compact(statement)[:120]}")
        if action == "open":
            if evidence and _compact(evidence) not in recent_text:
                continue
            existing = (await db.execute(select(DaryaOpenLoop).where(
                DaryaOpenLoop.user_id == user_id,
                DaryaOpenLoop.loop_key == loop_key,
                DaryaOpenLoop.status.in_(LOOP_OPEN_STATES),
            ))).scalars().first()
            if existing is not None:
                existing.updated_at = _now()
                continue
            db.add(DaryaOpenLoop(
                user_id=user_id,
                thread_id=thread_id,
                loop_key=loop_key,
                origin=origin,
                statement=statement,
                status="IN_PROGRESS",
                detail={"evidence": evidence},
                evidence_span=evidence or None,
                source_message_id=None,
            ))
            applied["open"] += 1
            continue
        open_loops = list((await db.scalars(select(DaryaOpenLoop).where(
            DaryaOpenLoop.user_id == user_id,
            DaryaOpenLoop.status.in_(LOOP_OPEN_STATES),
        ).order_by(DaryaOpenLoop.updated_at.desc()).limit(10))).all())
        target = next((l for l in open_loops if l.loop_key == loop_key), None)
        if target is None:
            needle = _compact(statement)[:24]
            target = next((l for l in open_loops if needle and needle in _compact(l.statement)), None)
        if target is None:
            continue
        if action in {"progress", "wait", "block"}:
            target.status = {"progress": "IN_PROGRESS", "wait": "WAITING", "block": "BLOCKED"}[action]
            target.updated_at = _now()
            applied["progress"] += 1
        elif action in {"complete", "cancel"}:
            target.status = "COMPLETED" if action == "complete" else "CANCELLED"
            target.closed_at = _now()
            target.updated_at = _now()
            applied["close"] += 1
    return applied


async def run_librarian_pass(db: AsyncSession, *, user_id: int, thread_id: str,
                             llm: Any = None, force: bool = False) -> dict[str, Any]:
    """One consolidation pass. Returns a server-side report (never user-facing)."""
    debounce_key = f"{user_id}:{thread_id}"
    if not force:
        last = _last_pass_at.get(debounce_key)
        if last is not None and time.monotonic() - last < _PASS_DEBOUNCE_SECONDS:
            return {"skipped": "debounced"}
    if llm is None:
        from services.llm_service_real import get_llm_service
        llm = await get_llm_service()
    context = await _load_context(db, user_id=user_id, thread_id=thread_id)
    recent_chars = sum(len(item["content"]) for item in context["recent"])
    if recent_chars < _MIN_TURN_CHARS:
        return {"skipped": "nothing to consolidate"}
    decision = await _librarian_decision(context, llm)
    recent_text = _compact(" ".join(
        item["role"] + item["content"] for item in context["recent"]
    ))
    recent_user_text = _compact(" ".join(
        item["content"] for item in context["recent"] if item["role"] == "user"
    ))
    report = {
        "memory": await _apply_memory_ops(db, user_id=user_id, thread_id=thread_id,
                                          ops=_ops(decision.get("memory_ops")), recent_text=recent_user_text),
        "impressions": await _apply_impression_ops(db, user_id=user_id,
                                                   ops=_ops(decision.get("impression_ops")),
                                                   recent_user_text=recent_user_text),
        "loops": await _apply_loop_ops(db, user_id=user_id, thread_id=thread_id,
                                       ops=_ops(decision.get("loop_ops")), recent_text=recent_text),
    }
    await db.commit()
    _last_pass_at[debounce_key] = time.monotonic()
    if any(sum(v.values()) for v in report.values()):
        logger.info("librarian pass applied: user=%s thread=%s report=%s", user_id, thread_id,
                    json.dumps(report, ensure_ascii=False))
    return report


def run_librarian_pass_detached(factory: Any, *, user_id: int, thread_id: str) -> None:
    """Fire-and-forget consolidation with its own session; never blocks the stream."""
    async def _runner() -> None:
        try:
            async with factory() as db:
                await run_librarian_pass(db, user_id=user_id, thread_id=thread_id)
        except Exception as exc:
            logger.warning("librarian pass failed user=%s thread=%s: %s",
                           user_id, thread_id, type(exc).__name__)

    try:
        asyncio.get_running_loop().create_task(_runner())
    except RuntimeError as exc:
        logger.warning("librarian detached task unavailable: %s", exc)


async def build_continuity_brief(db: AsyncSession, *, user_id: int, thread_id: str,
                                 message: str = "") -> dict[str, Any]:
    """Short, high-value hand-off from the timeline to Foreground DARYA.

    Deterministic assembly from Shared State; it informs the orchestrator's
    judgment but never commands it.
    """
    open_loops = list((await db.scalars(select(DaryaOpenLoop).where(
        DaryaOpenLoop.user_id == user_id,
        DaryaOpenLoop.status.in_(LOOP_OPEN_STATES),
    ).order_by(DaryaOpenLoop.updated_at.desc()).limit(6))).all())
    recently_closed = list((await db.scalars(select(DaryaOpenLoop).where(
        DaryaOpenLoop.user_id == user_id,
        DaryaOpenLoop.status == "COMPLETED",
        DaryaOpenLoop.closed_at >= _now() - timedelta(days=7),
    ).order_by(DaryaOpenLoop.closed_at.desc()).limit(3))).all())
    memories = await select_relevant_memories(db, user_id=user_id, message=message or thread_id)
    impressions = list((await db.scalars(select(AIImpression).where(
        AIImpression.user_id == user_id,
        AIImpression.status == "active",
        AIImpression.confidence >= 0.4,
    ).order_by(AIImpression.confidence.desc()).limit(5))).all())
    now = _now()
    return {
        "open_loops": [
            {"statement": loop.statement[:160], "origin": loop.origin, "status": loop.status}
            for loop in open_loops
        ],
        "promises_to_fulfill": [
            loop.statement[:160] for loop in open_loops if loop.origin == "ASSISTANT_COMMITMENT"
        ],
        "recently_completed_do_not_repeat": [loop.statement[:120] for loop in recently_closed],
        "relevant_memories": [
            {"content": m.content[:160], "status": m.epistemic_status, "scope": m.scope}
            for m in memories[:6]
        ],
        "user_model": [
            {"dimension": i.dimension, "summary": i.summary[:120], "confidence": round(i.confidence, 2)}
            for i in impressions
        ],
        "stale_beliefs": [
            m.content[:120] for m in memories
            if m.revalidate_after_at is not None and m.revalidate_after_at < now
        ][:3],
    }
