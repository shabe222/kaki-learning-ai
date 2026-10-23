"""DARYA Phase 2.9 compact, explicit, cross-thread personal memory."""
from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timedelta
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AIImpression, ChatSession, DaryaLongTermMemory, TideUserContextClaim
from services.darya_domain_registry import installed_packs
from services.impression_service import contains_sensitive


MEMORY_TYPES = {"FACT", "PREFERENCE", "LESSON", "FUTURE_POLICY"}
ACTIVE_STATE = "ACTIVE"
NON_SERVING_STATES = {"SUPERSEDED", "FORGOTTEN"}
TRUST_STATUSES = {"USER_PROVIDED", "USER_CONFIRMED"}
INSPECT_RE = re.compile(r"(?:你记得我什么|记得我的什么|看看.*记忆|我的长期记忆|what do you remember)", re.I)
EXPLICIT_RE = re.compile(r"(?:记住|记下来|以后(?:就)?按|以后.*(?:先|不要|用)|我(?:更|比较)?喜欢)")
LEARNING_PREFERENCE_CORRECTION_RE = re.compile(r"(?:现在)?不喜欢.{0,20}(?:先看|例子|这种方式).{0,30}(?:先|马上|立即).{0,20}(?:规则|练习)")
TECHNICAL_RE = re.compile(r"技术|专业|后端|前端|开发|工程|代码|数据库|部署|框架|React|FastAPI|Python|backend|frontend|technical", re.I)
EDUCATION_RE = re.compile(r"学校|大学|专业|学历|毕业|届学生|求职|岗位|school|university|degree|graduate|career", re.I)
DESIGN_RE = re.compile(r"PPT|演示|设计|视觉|排版|slide|presentation|design", re.I)
LEARNING_RE = re.compile(r"学习|理解|解释|讲解|怎么学|知识点|不懂|没懂|卡住|提升|learn|explain|improve", re.I)
LEARNING_PREFERENCE_TOPICS = {
    "learning_preference:example_first",
    "learning_preference:rule_then_practice",
}
AUTO_OBSERVED_KINDS = {"IDENTITY", "GOAL", "PREFERENCE", "CONSTRAINT"}


class LongTermMemoryError(ValueError):
    pass


class LongTermMemoryConflict(LongTermMemoryError):
    pass


def _now() -> datetime:
    return datetime.utcnow()


def _compact_evidence(value: str) -> str:
    return re.sub(r"\s+", "", str(value or ""))


async def _user_grounded_rows(
    db: AsyncSession, rows: Sequence[DaryaLongTermMemory],
) -> list[DaryaLongTermMemory]:
    """Serve librarian memories only when their evidence came from the user."""
    thread_ids = {
        str(item.source_thread_id)
        for item in rows
        if str((item.provenance or {}).get("source_type") or "").upper()
        == "LIBRARIAN_CONSOLIDATION" and item.source_thread_id
    }
    user_text_by_thread: dict[str, str] = {}
    if thread_ids:
        sessions = list((await db.scalars(select(ChatSession).where(
            ChatSession.thread_id.in_(thread_ids),
        ))).all())
        for session in sessions:
            user_text_by_thread[str(session.thread_id)] = _compact_evidence(" ".join(
                str(message.get("content") or "")
                for message in list(session.messages or [])
                if isinstance(message, dict) and message.get("role") == "user"
            ))
    grounded: list[DaryaLongTermMemory] = []
    for item in rows:
        provenance = item.provenance or {}
        if str(provenance.get("source_type") or "").upper() != "LIBRARIAN_CONSOLIDATION":
            grounded.append(item)
            continue
        evidence = _compact_evidence(str(provenance.get("evidence_span") or ""))
        if evidence and evidence in user_text_by_thread.get(str(item.source_thread_id), ""):
            grounded.append(item)
    return grounded


def _clean_content(value: str) -> str:
    cleaned = " ".join((value or "").strip().split())
    if len(cleaned) < 2:
        raise LongTermMemoryError("记忆内容不能为空。")
    if len(cleaned) > 500:
        raise LongTermMemoryError("记忆内容不能超过 500 个字符。")
    if contains_sensitive(cleaned) or re.search(r"身份证|精确住址|家庭住址|exact address", cleaned, re.I):
        raise LongTermMemoryError("这类敏感信息不会在当前阶段保存为长期记忆。")
    return cleaned


def _freshness_for(memory_type: str, topic: str, content: str) -> tuple[str, datetime | None]:
    if memory_type == "FACT" and re.search(r"\b20\d{2}\b.*(?:届|毕业)|(?:届|毕业).*\b20\d{2}\b", content):
        return "TIME_BOUND", _now() + timedelta(days=730)
    if memory_type == "FACT" and topic.startswith("context:education"):
        return "TIME_BOUND", _now() + timedelta(days=730)
    return "STABLE", None


async def _suppress_conflicting_learning_impressions(
    db: AsyncSession, *, user_id: int, preference_topic: str,
) -> None:
    """Stop an older unconfirmed inference from contradicting an explicit correction."""
    if preference_topic != "learning_preference:rule_then_practice":
        return
    rows = (await db.execute(select(AIImpression).where(
        AIImpression.user_id == user_id,
        AIImpression.impression_key == "learning_style.example_first",
        AIImpression.confirmed_by_user.is_(False),
        AIImpression.status.notin_(("rejected", "confirmed_by_user")),
    ))).scalars().all()
    for item in rows:
        item.status = "rejected"
        item.user_feedback = {
            "action": "superseded_by_explicit_learning_preference",
            "at": _now().isoformat(),
        }
        item.updated_at = _now()


def _effective_state(item: DaryaLongTermMemory) -> str:
    if item.state != ACTIVE_STATE:
        return item.state
    if item.revalidate_after_at is not None and item.revalidate_after_at <= _now():
        return "STALE"
    return ACTIVE_STATE


def public_memory(item: DaryaLongTermMemory) -> dict:
    return {
        "id": item.id,
        "type": item.memory_type,
        "topic": item.topic,
        "content": item.content,
        "provenance": item.provenance or {},
        "epistemic_status": item.epistemic_status,
        "confidence": item.confidence,
        "source_context_id": item.source_context_id,
        "source_claim_id": item.source_claim_id,
        "source_thread_id": item.source_thread_id,
        "source_message_id": item.source_message_id,
        "freshness": item.freshness,
        "revalidate_after_at": item.revalidate_after_at.isoformat() if item.revalidate_after_at else None,
        "state": _effective_state(item),
        "revision": item.revision,
        "created_at": str(item.created_at) if item.created_at else None,
        "updated_at": str(item.updated_at) if item.updated_at else None,
        "last_confirmed_at": str(item.last_confirmed_at) if item.last_confirmed_at else None,
        "last_used_at": str(item.last_used_at) if item.last_used_at else None,
    }


def memory_snapshot(items: Sequence[DaryaLongTermMemory], *, reason: str) -> dict:
    return {
        "scope": "LONG_TERM_PERSONAL",
        "reason": reason,
        "memories": [public_memory(item) for item in items],
    }


def _memory_type_for_claim(claim: TideUserContextClaim) -> str:
    if claim.kind == "preference":
        return "PREFERENCE"
    return "FACT"


async def promote_context_claim(
    db: AsyncSession, *, user_id: int, thread_id: str, claim_id: int,
) -> DaryaLongTermMemory:
    claim = (await db.execute(select(TideUserContextClaim).where(
        TideUserContextClaim.id == claim_id,
        TideUserContextClaim.user_id == user_id,
        TideUserContextClaim.thread_id == thread_id,
    ))).scalar_one_or_none()
    if claim is None:
        raise LongTermMemoryError("这条背景不存在或无权访问。")
    if claim.status != "CONFIRMED":
        raise LongTermMemoryError("请先确认这条背景，再保存为长期记忆。")
    content = _clean_content(claim.value)
    memory_type = _memory_type_for_claim(claim)
    topic = f"context:{claim.kind}:{claim.id}"
    existing = (await db.execute(select(DaryaLongTermMemory).where(
        DaryaLongTermMemory.user_id == user_id,
        DaryaLongTermMemory.source_claim_id == claim.id,
        DaryaLongTermMemory.state == ACTIVE_STATE,
    ).order_by(DaryaLongTermMemory.id.desc()))).scalars().first()
    if existing is not None:
        return existing
    freshness, revalidate_after = _freshness_for(memory_type, topic, content)
    source_reference = dict(claim.source_reference or {})
    item = DaryaLongTermMemory(
        user_id=user_id,
        memory_type=memory_type,
        topic=topic,
        content=content,
        provenance={
            "source_type": "PHASE_2_8_CONTEXT_CLAIM",
            "claim_origin_status": source_reference.get("original_status", claim.status),
            "document_identity": source_reference.get("document_identity"),
            "document_name": source_reference.get("document_name"),
            "page_start": source_reference.get("page_start"),
            "page_end": source_reference.get("page_end"),
            "section": source_reference.get("section"),
            "paragraph_index": source_reference.get("paragraph_index"),
            "span_start": source_reference.get("span_start"),
            "span_end": source_reference.get("span_end"),
            "extracted_at": source_reference.get("extracted_at"),
            "user_correction": source_reference.get("user_correction"),
        },
        epistemic_status="USER_CONFIRMED",
        confidence=1.0,
        source_context_id=thread_id,
        source_claim_id=claim.id,
        source_thread_id=thread_id,
        freshness=freshness,
        revalidate_after_at=revalidate_after,
        state=ACTIVE_STATE,
        revision=1,
        last_confirmed_at=_now(),
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


def build_explicit_candidate(message: str) -> dict | None:
    text = " ".join((message or "").strip().split())
    if not text or not (EXPLICIT_RE.search(text) or LEARNING_PREFERENCE_CORRECTION_RE.search(text)) or contains_sensitive(text):
        return None
    if (
        ("人话" in text or "通俗" in text)
        and ("专业" in text or "术语" in text or "解释" in text or "讲" in text)
    ):
        return {
            "type": "FUTURE_POLICY",
            "topic": "response_policy:plain_language_first",
            "content": "解释专业内容时，先用人话说明，再按需展开专业术语。",
        }
    if "先举例" in text or "例子" in text and "先" in text:
        if re.search(r"(?:不喜欢|不要).{0,12}(?:先看)?例子", text):
            pass
        else:
            return {
                "type": "PREFERENCE",
                "topic": "learning_preference:example_first",
                "content": "理解新概念时偏好先看具体例子，再展开抽象定义。",
            }
    if (
        re.search(r"(?:简短|一句|先).{0,10}(?:规则|定义).{0,16}(?:马上|立即|然后|再).{0,8}(?:练习|做题)", text)
        or LEARNING_PREFERENCE_CORRECTION_RE.search(text)
    ):
        return {
            "type": "PREFERENCE",
            "topic": "learning_preference:rule_then_practice",
            "content": "理解新概念时偏好先看一条简短规则，然后立即用练习检验。",
        }
    if any(term in text for term in ("简洁", "简短", "不要太长")):
        return {
            "type": "PREFERENCE",
            "topic": "response_preference:concise",
            "content": "回答偏好简洁直接，避免不必要的展开。",
        }
    if TECHNICAL_RE.search(text) and re.search(
        r"我(?:熟悉|会|擅长|做过|有.{0,12}经验|正在从).{2,}", text,
    ):
        # A user-authored technical baseline is safe to remember when they
        # explicitly authorize it. Store the compact fact, never the whole
        # transcript or surrounding conversational narrative.
        content = re.sub(r"^.*?(?:请)?(?:记住|记下来)[：:\s]*", "", text).strip()
        content = content.rstrip("。！!")[:500]
        return {
            "type": "FACT",
            "topic": "technical:experience",
            "content": content,
        }
    return None


async def persist_explicit_memory(
    db: AsyncSession, *, user_id: int, thread_id: str, source_message_id: str, message: str,
) -> DaryaLongTermMemory | None:
    candidate = build_explicit_candidate(message)
    if candidate is None:
        return None
    preference_family = candidate["topic"] in LEARNING_PREFERENCE_TOPICS
    existing_query = select(DaryaLongTermMemory).where(
        DaryaLongTermMemory.user_id == user_id,
        DaryaLongTermMemory.state == ACTIVE_STATE,
    )
    if preference_family:
        existing_query = existing_query.where(
            DaryaLongTermMemory.topic.in_(tuple(LEARNING_PREFERENCE_TOPICS))
        )
    else:
        existing_query = existing_query.where(DaryaLongTermMemory.topic == candidate["topic"])
    existing = (await db.execute(existing_query.order_by(DaryaLongTermMemory.id.desc()))).scalars().first()
    if existing is not None and existing.content == candidate["content"]:
        await _suppress_conflicting_learning_impressions(
            db, user_id=user_id, preference_topic=str(candidate["topic"]),
        )
        existing.last_confirmed_at = _now()
        existing.updated_at = _now()
        existing.revision += 1
        await db.commit()
        await db.refresh(existing)
        return existing
    if existing is not None:
        existing.state = "SUPERSEDED"
        existing.updated_at = _now()
        await db.flush()
    freshness, revalidate_after = _freshness_for(candidate["type"], candidate["topic"], candidate["content"])
    item = DaryaLongTermMemory(
        user_id=user_id,
        memory_type=candidate["type"],
        topic=candidate["topic"],
        content=candidate["content"],
        provenance={"source_type": "EXPLICIT_USER_INSTRUCTION", "authorization": "EXPLICIT"},
        epistemic_status="USER_CONFIRMED",
        confidence=1.0,
        source_context_id=thread_id,
        source_thread_id=thread_id,
        source_message_id=source_message_id,
        freshness=freshness,
        revalidate_after_at=revalidate_after,
        state=ACTIVE_STATE,
        revision=1,
        supersedes_id=existing.id if existing else None,
        last_confirmed_at=_now(),
    )
    db.add(item)
    await _suppress_conflicting_learning_impressions(
        db, user_id=user_id, preference_topic=str(candidate["topic"]),
    )
    await db.commit()
    await db.refresh(item)
    return item


async def persist_observed_world_model_claims(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    source_message_id: str,
    current_message: str,
    claims: Sequence[dict],
) -> list[DaryaLongTermMemory]:
    """Persist bounded user-authored identity/goal observations in the existing authority.

    These rows remain OBSERVED, never USER_CONFIRMED.  Model-proposed values are
    accepted only when their evidence span is verbatim in the current message.
    Sensitive data is rejected by the same memory boundary.
    """
    compact_message = re.sub(r"\s+", "", str(current_message or ""))
    saved: list[DaryaLongTermMemory] = []
    for candidate in list(claims)[:8]:
        if not isinstance(candidate, dict):
            continue
        kind = str(candidate.get("kind") or "").upper()
        value = " ".join(str(candidate.get("value") or "").split())[:300]
        span = " ".join(str(candidate.get("source_ref") or candidate.get("evidence_span") or "").split())[:300]
        if kind not in AUTO_OBSERVED_KINDS or not value or not span:
            continue
        if re.sub(r"\s+", "", span) not in compact_message or contains_sensitive(value):
            continue
        digest = hashlib.sha256(f"{kind}\0{value}".encode("utf-8")).hexdigest()[:24]
        topic = f"observed:{kind.lower()}:{digest}"
        existing = (await db.execute(select(DaryaLongTermMemory).where(
            DaryaLongTermMemory.user_id == user_id,
            DaryaLongTermMemory.topic == topic,
            DaryaLongTermMemory.state == ACTIVE_STATE,
        ).order_by(DaryaLongTermMemory.id.desc()))).scalars().first()
        try:
            confidence = max(0.75, min(0.95, float(candidate.get("confidence") or 0.8)))
        except (TypeError, ValueError):
            confidence = 0.8
        if existing is not None:
            existing.confidence = max(existing.confidence, confidence)
            existing.source_thread_id = thread_id
            existing.source_message_id = source_message_id
            existing.updated_at = _now()
            saved.append(existing)
            continue
        freshness = "STABLE" if kind in {"IDENTITY", "PREFERENCE"} else "TIME_BOUND"
        revalidate_after = None if freshness == "STABLE" else _now() + timedelta(days=180)
        item = DaryaLongTermMemory(
            user_id=user_id,
            memory_type="PREFERENCE" if kind == "PREFERENCE" else "FACT",
            topic=topic,
            content=value,
            provenance={
                "source_type": "AUTO_OBSERVED_USER_STATEMENT",
                "authorization": "PRODUCT_POLICY_OBSERVED",
                "evidence_span": span,
                "claim_kind": kind,
            },
            epistemic_status="OBSERVED",
            confidence=confidence,
            source_context_id=thread_id,
            source_thread_id=thread_id,
            source_message_id=source_message_id,
            freshness=freshness,
            revalidate_after_at=revalidate_after,
            state=ACTIVE_STATE,
            revision=1,
        )
        db.add(item)
        await db.flush()
        saved.append(item)
    return saved


async def correct_memory_from_user(
    db: AsyncSession, *, user_id: int, memory_id: int, expected_revision: int,
    correction: str,
) -> DaryaLongTermMemory:
    """Apply a human correction without leaving behavior tied to an obsolete topic.

    Generic facts keep their existing topic. Learning-presentation preferences are
    parsed through the same canonical detector used by conversation, so correcting
    "example first" to "rule then practice" changes both the statement and the
    behavior-driving topic while retaining the superseded row as audit history.
    """
    item = (await db.execute(select(DaryaLongTermMemory).where(
        DaryaLongTermMemory.id == memory_id,
        DaryaLongTermMemory.user_id == user_id,
        DaryaLongTermMemory.state == ACTIVE_STATE,
    ))).scalar_one_or_none()
    if item is None:
        raise LongTermMemoryError("这条长期记忆不存在或已不再生效。")
    if item.revision != expected_revision:
        raise LongTermMemoryConflict("这条记忆已经更新，请刷新后再操作。")
    cleaned = _clean_content(correction)
    candidate = build_explicit_candidate(cleaned)
    if item.topic in LEARNING_PREFERENCE_TOPICS:
        if candidate is None or candidate.get("topic") not in LEARNING_PREFERENCE_TOPICS:
            raise LongTermMemoryError("请明确告诉我新的学习顺序，例如“我喜欢先看例子”或“我喜欢先看短规则再练习”。")
        replacement_topic = str(candidate["topic"])
        replacement_type = str(candidate["type"])
        replacement_content = str(candidate["content"])
    else:
        replacement_topic = item.topic
        replacement_type = item.memory_type
        replacement_content = cleaned

    item.state = "SUPERSEDED"
    item.updated_at = _now()
    freshness, revalidate_after = _freshness_for(
        replacement_type, replacement_topic, replacement_content,
    )
    replacement = DaryaLongTermMemory(
        user_id=item.user_id,
        memory_type=replacement_type,
        topic=replacement_topic,
        content=replacement_content,
        provenance={
            "source_type": "USER_CORRECTION",
            "previous_memory_id": item.id,
            "original_source_type": (item.provenance or {}).get("source_type"),
        },
        epistemic_status="USER_CONFIRMED",
        confidence=1.0,
        source_context_id=item.source_context_id,
        source_claim_id=item.source_claim_id,
        source_thread_id=item.source_thread_id,
        source_message_id=item.source_message_id,
        freshness=freshness,
        revalidate_after_at=revalidate_after,
        state=ACTIVE_STATE,
        revision=item.revision + 1,
        supersedes_id=item.id,
        last_confirmed_at=_now(),
    )
    db.add(replacement)
    await _suppress_conflicting_learning_impressions(
        db, user_id=user_id, preference_topic=replacement_topic,
    )
    await db.commit()
    await db.refresh(replacement)
    return replacement


def is_memory_inspection(message: str) -> bool:
    return bool(INSPECT_RE.search(message or ""))


def _terms(value: str) -> set[str]:
    text = (value or "").casefold()
    terms = set(re.findall(r"[a-z0-9+#.]{2,}", text))
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        terms.update(run[index:index + 2] for index in range(len(run) - 1))
    return terms


def _is_learning_context(message: str) -> bool:
    """Use the installed Domain Pack authority instead of duplicating topic lists."""
    if LEARNING_RE.search(message or ""):
        return True
    normalized = (message or "").casefold()
    return any(
        str(signal).casefold() in normalized
        for pack in installed_packs()
        for signal in pack.resolver_signals
    )


def _score(item: DaryaLongTermMemory, message: str) -> int:
    score = len(_terms(item.content) & _terms(message)) * 2
    topic = item.topic
    if TECHNICAL_RE.search(message):
        if any(key in topic for key in ("skill", "project", "experience", "technical")):
            score += 9
        if topic == "response_policy:plain_language_first":
            score += 7
    if EDUCATION_RE.search(message) and any(key in topic for key in ("education", "goal", "skill", "project")):
        score += 8
    if DESIGN_RE.search(message) and any(key in topic for key in ("design", "presentation", "preference")):
        score += 8
    if _is_learning_context(message) and (
        topic.startswith("learning_preference:")
        or topic.startswith("response_policy:")
        or any(key in topic for key in ("skill", "project", "experience", "education"))
    ):
        score += 5
    return score


async def select_relevant_memories(
    db: AsyncSession, *, user_id: int, message: str, limit: int = 4, mark_used: bool = True,
) -> list[DaryaLongTermMemory]:
    active = list((await db.execute(select(DaryaLongTermMemory).where(
        DaryaLongTermMemory.user_id == user_id,
        DaryaLongTermMemory.state == ACTIVE_STATE,
    ).order_by(DaryaLongTermMemory.updated_at.desc(), DaryaLongTermMemory.id.desc()))).scalars().all())
    active = await _user_grounded_rows(db, active)
    inspect = is_memory_inspection(message)
    if inspect:
        selected = active[:20]
    else:
        ranked = sorted(active, key=lambda item: (-_score(item, message), -item.id))
        selected = [item for item in ranked if _effective_state(item) == ACTIVE_STATE and _score(item, message) > 0][:limit]
    if selected and mark_used and not inspect:
        used_at = _now()
        for item in selected:
            item.last_used_at = used_at
        await db.flush()
        for item in selected:
            await db.refresh(item)
    return selected


async def list_active_memories(db: AsyncSession, *, user_id: int) -> list[DaryaLongTermMemory]:
    rows = list((await db.execute(select(DaryaLongTermMemory).where(
        DaryaLongTermMemory.user_id == user_id,
        DaryaLongTermMemory.state == ACTIVE_STATE,
    ).order_by(DaryaLongTermMemory.updated_at.desc(), DaryaLongTermMemory.id.desc()))).scalars().all())
    # Read-time decay: a memory past its revalidate date no longer serves until
    # it is re-confirmed (by the user or a later corroborating statement).
    effective = [item for item in rows if _effective_state(item) == ACTIVE_STATE]
    return await _user_grounded_rows(db, effective)


async def control_memory(
    db: AsyncSession, *, user_id: int, memory_id: int, action: str,
    expected_revision: int, content: str | None = None,
) -> DaryaLongTermMemory | None:
    item = (await db.execute(select(DaryaLongTermMemory).where(
        DaryaLongTermMemory.id == memory_id,
        DaryaLongTermMemory.user_id == user_id,
        DaryaLongTermMemory.state == ACTIVE_STATE,
    ))).scalar_one_or_none()
    if item is None:
        raise LongTermMemoryError("这条长期记忆不存在或已不再生效。")
    if item.revision != expected_revision:
        raise LongTermMemoryConflict("这条记忆已经更新，请刷新后再操作。")
    if action == "forget":
        item.state = "FORGOTTEN"
        item.content = ""
        item.provenance = {
            "source_type": (item.provenance or {}).get("source_type"),
            "forgotten_at": _now().isoformat(),
        }
        item.revision += 1
        item.updated_at = _now()
        await db.commit()
        return None
    if action != "correct":
        raise LongTermMemoryError("不支持的长期记忆操作。")
    corrected = _clean_content(content or "")
    item.state = "SUPERSEDED"
    item.updated_at = _now()
    freshness, revalidate_after = _freshness_for(item.memory_type, item.topic, corrected)
    replacement = DaryaLongTermMemory(
        user_id=item.user_id,
        memory_type=item.memory_type,
        topic=item.topic,
        content=corrected,
        provenance={
            **(item.provenance or {}),
            "source_type": "USER_CORRECTION",
            "previous_memory_id": item.id,
            "original_source_type": (item.provenance or {}).get("source_type"),
        },
        epistemic_status="USER_CONFIRMED",
        confidence=1.0,
        source_context_id=item.source_context_id,
        source_claim_id=item.source_claim_id,
        source_thread_id=item.source_thread_id,
        source_message_id=item.source_message_id,
        freshness=freshness,
        revalidate_after_at=revalidate_after,
        state=ACTIVE_STATE,
        revision=item.revision + 1,
        supersedes_id=item.id,
        last_confirmed_at=_now(),
    )
    db.add(replacement)
    await db.commit()
    await db.refresh(replacement)
    return replacement


def build_memory_directive(memories: Sequence[DaryaLongTermMemory]) -> str:
    """Render what is remembered for the answering model.

    This used to dump the rows as JSON, internal topic key and all, so the
    answer read "observed:preference:e67f9a37859a52f1" and saw one preference
    listed three times. The ledger's renderer collapses near-copies and drops
    the keys, so both doors onto this table now read the same way -- and this is
    the door that produces the reply the learner actually reads.
    """
    from services.memory_ledger import render_ledger

    serving = [item for item in memories if _effective_state(item) == ACTIVE_STATE]
    if not serving:
        return ""
    block = render_ledger([
        {
            "kind": item.memory_type,
            "topic": item.topic,
            "content": item.content,
            "epistemic_status": item.epistemic_status,
        }
        for item in serving
    ])
    if not block:
        return ""
    return (
        "以下是与本轮问题相关、由用户明确保存或不带判断地观察到的长期信息："
        + chr(10)
        + block
        + chr(10)
        + "只自然使用列出的相关信息，不要宣布「根据长期记忆」，不要让用户重复这些信息；"
        + "标着「我观察到的」的只是线索，不要当成他说过的事实。"
        + "USER_CONFIRMED 或 USER_PROVIDED 都不等于外部 VERIFIED。"
        + "FUTURE_POLICY 与 PREFERENCE 应直接改变回答方式；不要注入未列出的其他记忆。"
    )
