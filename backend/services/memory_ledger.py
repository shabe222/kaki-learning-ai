"""One small door for saving and reading what DARYA should carry forward.

The durable-memory table and its boundaries already existed; what did not exist
was a way for the model to *use* them. Saving was reachable only through
``build_explicit_candidate`` -- a closed list of five phrasings plus one
technical-experience case. If the learner did not happen to say one of those
sentences, nothing was ever kept, however much it mattered: not that they keep
confusing two things, not that a particular example finally made it click, not
that they asked never to be quizzed before breakfast.

So this module adds the missing verb. ``MemoryLedger`` gives the same three
operations for every kind of durable meaning -- remember it, recall it, and
retire it -- and keeps the boundary rules that the existing authority enforces:

* nothing sensitive is stored;
* a model-proposed memory must quote the learner's own words, and the quote must
  actually appear in this turn -- a paraphrase is not evidence;
* model-proposed rows are OBSERVED, never USER_CONFIRMED. Nothing here can
  promote a guess into a fact, and nothing here touches mastery or the route.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DaryaLongTermMemory
from services.impression_service import contains_sensitive

ACTIVE = "ACTIVE"
RETIRED = "RETIRED"

# The kinds of durable meaning worth carrying between conversations. Deliberately
# a short list: a ledger that accepts anything becomes a transcript.
MEMORY_KINDS = ("FACT", "PREFERENCE", "LESSON", "FUTURE_POLICY")

# How the row came to be. Model-proposed stays OBSERVED so it can never be told
# back to the learner as something they said.
STATUS_USER_STATED = "USER_PROVIDED"
STATUS_MODEL_OBSERVED = "OBSERVED"

MAX_CONTENT = 500
MAX_TOPIC = 120


@dataclass(frozen=True)
class MemoryCandidate:
    """One thing worth keeping, with the words that justify keeping it."""

    kind: str
    topic: str
    content: str
    evidence_span: str
    confidence: float = 0.7
    user_stated: bool = False

    def normalised(self) -> "MemoryCandidate":
        return MemoryCandidate(
            kind=str(self.kind or "").strip().upper(),
            topic=" ".join(str(self.topic or "").split())[:MAX_TOPIC],
            content=" ".join(str(self.content or "").split())[:MAX_CONTENT],
            evidence_span=" ".join(str(self.evidence_span or "").split()),
            confidence=max(0.0, min(1.0, float(self.confidence))),
            user_stated=bool(self.user_stated),
        )


@dataclass(frozen=True)
class LedgerResult:
    saved: bool
    reason: str
    memory_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"saved": self.saved, "reason": self.reason, "memory_id": self.memory_id}


# The shared `contains_sensitive` boundary covers protected traits -- religion,
# politics, diagnoses, orientation. It does not cover identifying data, so an
# ID number or a phone number would otherwise be kept forever in a table that
# survives every conversation. A durable memory is the last place that belongs.
PII_RE = re.compile(
    r"\d{11,}"                                  # mobile / ID / card number runs
    r"|[\w.+-]+@[\w-]+\.[\w.]+"                 # email
    r"|(?:身份证|护照|社保|银行卡|学号|工号|家庭住址|详细地址|收货地址|手机号|联系电话)"
)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def looks_like_pii(text: str) -> bool:
    return bool(PII_RE.search(str(text or "")))


def check_candidate(candidate: MemoryCandidate, *, current_message: str) -> str:
    """Why this may not be kept, or an empty string when it may.

    Returning the reason rather than a bool keeps the refusal explainable: the
    caller can say "我没记，因为这句话不是你说的" instead of silently dropping it.
    """
    item = candidate.normalised()
    if item.kind not in MEMORY_KINDS:
        return f"unknown memory kind: {item.kind or '(empty)'}"
    if not item.topic:
        return "memory needs a topic"
    if not item.content:
        return "memory needs content"
    if contains_sensitive(f"{item.topic} {item.content}"):
        return "refused: looks sensitive"
    if looks_like_pii(f"{item.topic} {item.content}"):
        return "refused: looks like identifying information"
    if not item.evidence_span:
        return "refused: no quote from the learner to stand on"
    if _compact(item.evidence_span) not in _compact(current_message):
        # The single most valuable check here. A model that can supply any
        # "quote" can launder its own guess into the learner's mouth.
        return "refused: the quote is not in what the learner actually said"
    return ""


async def remember(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    message_id: str | None,
    candidate: MemoryCandidate,
    current_message: str,
) -> LedgerResult:
    """Keep one durable thing, or explain why it was not kept."""
    item = candidate.normalised()
    refusal = check_candidate(item, current_message=current_message)
    if refusal:
        return LedgerResult(saved=False, reason=refusal)

    existing = (
        await db.execute(
            select(DaryaLongTermMemory).where(
                DaryaLongTermMemory.user_id == user_id,
                DaryaLongTermMemory.topic == item.topic,
                DaryaLongTermMemory.state == ACTIVE,
            )
        )
    ).scalars().all()
    for row in existing:
        if _compact(row.content) == _compact(item.content):
            # Already known. Re-saving it would only add a duplicate the learner
            # later has to clean up.
            return LedgerResult(saved=False, reason="already remembered", memory_id=row.id)

    row = DaryaLongTermMemory(
        user_id=user_id,
        memory_type=item.kind,
        topic=item.topic,
        content=item.content,
        provenance={
            "source": "memory_ledger",
            "evidence_span": item.evidence_span,
            "user_stated": item.user_stated,
        },
        epistemic_status=STATUS_USER_STATED if item.user_stated else STATUS_MODEL_OBSERVED,
        confidence=item.confidence,
        scope="ACCOUNT",
        source_thread_id=thread_id,
        source_message_id=message_id,
        freshness="STABLE",
        state=ACTIVE,
    )
    db.add(row)
    await db.flush()
    return LedgerResult(saved=True, reason="remembered", memory_id=row.id)


def _query_terms(text: str) -> set:
    """Terms to look for, as n-grams.

    Chinese has no spaces, so the maximal run is useless as a term: "二次函数
    开口怎么判断" would never appear inside "用黑板讲过「二次函数开口方向」".
    Sliding 2- and 3-grams makes the overlap visible, which is the whole point.
    """
    terms: set = set()
    body = str(text or "")
    for run in re.findall(r"[A-Za-z_]{3,}", body):
        terms.add(run.lower())
    for run in re.findall(r"[一-鿿]+", body):
        for size in (2, 3):
            for index in range(len(run) - size + 1):
                terms.add(run[index : index + size])
    return terms


def relevance(memory: Mapping[str, Any], query: str) -> int:
    """How much this memory has to do with what is being asked right now.

    Ranking in the ledger rather than trusting recency matters as soon as an
    account has more than a screenful: newest-twelve is a window that slides
    away from the thing the learner is actually working on.
    """
    terms = _query_terms(query)
    if not terms:
        return 0
    haystack = f"{memory.get('topic') or ''} {memory.get('content') or ''}".lower()
    return sum(min(len(term), 6) for term in terms if term in haystack)


async def recall(
    db: AsyncSession,
    *,
    user_id: int,
    kinds: Sequence[str] = (),
    limit: int = 12,
    query: str = "",
) -> list[dict[str, Any]]:
    """Read back what is kept -- the most relevant first, newest to break ties."""
    statement = select(DaryaLongTermMemory).where(
        DaryaLongTermMemory.user_id == user_id,
        DaryaLongTermMemory.state == ACTIVE,
    )
    wanted = [str(kind).upper() for kind in kinds if kind]
    if wanted:
        statement = statement.where(DaryaLongTermMemory.memory_type.in_(tuple(wanted)))
    # Fetch a wider window than asked for, then rank, so relevance can promote
    # something older that the newest-N cut would have dropped.
    statement = statement.order_by(DaryaLongTermMemory.updated_at.desc()).limit(120)
    rows = (await db.execute(statement)).scalars().all()
    rows = sorted(rows, key=lambda row: relevance(
        {"topic": row.topic, "content": row.content}, query
    ), reverse=True)[: max(1, min(60, limit))]
    return [
        {
            "id": row.id,
            "kind": row.memory_type,
            "topic": row.topic,
            "content": row.content,
            "epistemic_status": row.epistemic_status,
            "confidence": row.confidence,
            "evidence_span": (row.provenance or {}).get("evidence_span", ""),
        }
        for row in rows
    ]


async def retire(
    db: AsyncSession, *, user_id: int, memory_id: int, reason: str = "retired_by_darya"
) -> LedgerResult:
    """Stop serving a memory without destroying the audit trail."""
    row = (
        await db.execute(
            select(DaryaLongTermMemory).where(
                DaryaLongTermMemory.id == memory_id,
                DaryaLongTermMemory.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return LedgerResult(saved=False, reason="no such memory")
    row.state = RETIRED
    row.provenance = {**(row.provenance or {}), "retired_reason": reason}
    await db.flush()
    return LedgerResult(saved=True, reason=reason, memory_id=row.id)


def proposals_from_model(payload: Any, *, current_message: str) -> list[MemoryCandidate]:
    """Turn whatever the model offered into checked candidates.

    Accepts a loose list of dicts because the shape comes from a model; every
    field is validated afterwards by ``check_candidate`` rather than trusted here.
    """
    if isinstance(payload, Mapping):
        payload = payload.get("memories") or payload.get("candidates") or []
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes, bytearray)):
        return []
    out: list[MemoryCandidate] = []
    for raw in list(payload)[:8]:
        if not isinstance(raw, Mapping):
            continue
        out.append(
            MemoryCandidate(
                kind=str(raw.get("kind") or raw.get("type") or ""),
                topic=str(raw.get("topic") or ""),
                content=str(raw.get("content") or ""),
                evidence_span=str(raw.get("evidence_span") or ""),
                confidence=float(raw.get("confidence") or 0.7),
                user_stated=bool(raw.get("user_stated")),
            ).normalised()
        )
    del current_message
    return out


# A stored topic is an internal key -- "observed:preference:e67f9a37859a52f1" or
# "lesson:board:b9cd41485af0e471". Printing those spends prompt space on hex and
# tells the model nothing, so only a topic that reads like words is shown.
_INTERNAL_TOPIC = re.compile(r":|^[0-9a-f]{8,}$")


def readable_topic(topic: Any) -> str:
    text = str(topic or "")
    if not text or _INTERNAL_TOPIC.search(text):
        return ""
    return text


def _signature(content: Any) -> frozenset:
    """Character bigrams of a memory's content, for near-duplicate detection."""
    compact = re.sub(r"[^\w一-鿿]", "", str(content or ""))
    if len(compact) < 2:
        return frozenset([compact]) if compact else frozenset()
    return frozenset(compact[i:i + 2] for i in range(len(compact) - 1))


def _similar(a: frozenset, b: frozenset, threshold: float = 0.6) -> bool:
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= threshold


def collapse(
    memories: Sequence[Mapping[str, Any]], *, threshold: float = 0.6
) -> list[Mapping[str, Any]]:
    """One entry per thing actually known.

    The same fact arrives by several routes -- the world-model observer, an
    explicit save, a lesson -- each with its own topic key, so topic-keyed
    dedupe lets "偏好用黑板/板书方式学习" and "希望用黑板/板书方式讲解" both
    survive. Reading both lets one preference look like two, so near-copies
    within a kind merge, preferring what the learner stated over what was
    inferred and then the higher confidence.
    """
    ordered = sorted(
        memories,
        key=lambda item: (
            0 if item.get("epistemic_status") == STATUS_USER_STATED else 1,
            -float(item.get("confidence") or 0.0),
        ),
    )
    kept: list[Mapping[str, Any]] = []
    signatures: dict[str, list] = {}
    for item in ordered:
        kind = str(item.get("kind") or "?")
        signature = _signature(item.get("content"))
        seen = signatures.setdefault(kind, [])
        if any(_similar(signature, other, threshold) for other in seen):
            continue
        seen.append(signature)
        kept.append(item)
    return kept


def render_ledger(memories: Iterable[Mapping[str, Any]], *, limit: int = 12) -> str:
    """The read side, as a bounded block for a prompt.

    Collapsed, so one preference reads as one preference, and with internal keys
    stripped, so every line is meaning rather than bookkeeping.
    """
    rows = []
    for item in collapse(list(memories)):
        status = "他说的" if item.get("epistemic_status") == STATUS_USER_STATED else "我观察到的"
        topic = readable_topic(item.get("topic"))
        lead = f"{topic}：" if topic else ""
        rows.append(f"- [{item.get('kind')}/{status}] {lead}{str(item.get('content'))[:160]}")
    if not rows:
        return ""
    header = "【你已经记住的（只在相关时使用，可以直接引用）】"
    return chr(10).join([header, *rows[:limit]])


def ledger_summary(memories: Sequence[Mapping[str, Any]]) -> str:
    """One line for the turn log: counts by kind, so growth is visible."""
    counts: dict[str, int] = {}
    for item in memories:
        key = str(item.get("kind") or "?")
        counts[key] = counts.get(key, 0) + 1
    return json.dumps(counts, ensure_ascii=False, sort_keys=True)
