"""Due review as an input to what comes next.

The FSRS scheduler was already implemented and already correct — and completely
isolated. Cards moved through their intervals on a study-resources screen while
the learning route decided what to teach next from mastery and evidence alone.
Nothing ever asked "is this about to be forgotten?".

That is the one structural advantage a teaching agent has over a general
assistant: it can choose to bring something back at the moment it is about to
fade. This module exposes the schedule as a decision input, and it is
deliberately read-only — it reports what is due, it never writes mastery or
creates evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import GeneratedItem
from services.flashcard_service import due_cards

FLASHCARD_ITEM_TYPE = "study_flashcard_deck"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _deck_node_refs(item: GeneratedItem) -> list[str]:
    """Which knowledge nodes a deck belongs to, from its own recorded provenance."""
    raw = dict(item.raw_data or {})
    refs: list[str] = []
    for key in ("knowledge_refs", "node_refs"):
        value = raw.get(key)
        if isinstance(value, (list, tuple)):
            refs.extend(str(entry) for entry in value if str(entry).strip())
    purpose = raw.get("canonical_purpose")
    if isinstance(purpose, Mapping):
        value = purpose.get("knowledge_refs")
        if isinstance(value, (list, tuple)):
            refs.extend(str(entry) for entry in value if str(entry).strip())
    node = raw.get("node_id") or raw.get("node_ref")
    if node:
        refs.append(str(node))
    return sorted(set(refs))


def summarize_decks(decks: Iterable[GeneratedItem], *, now: datetime | None = None) -> dict[str, Any]:
    """Due counts across decks, with the node each deck belongs to."""
    moment = now or _now()
    per_deck: list[dict[str, Any]] = []
    node_refs: set[str] = set()
    earliest: datetime | None = None
    total_due = 0
    for item in decks:
        try:
            due = due_cards(item, now=moment)
        except Exception:  # noqa: BLE001 - 一张坏卡片不能拖垮整个调度视图
            continue
        if not due:
            continue
        due_times: list[datetime] = []
        for card in due:
            raw_due = str((card.get("fsrs") or {}).get("due") or card.get("due") or "")
            parsed = _parse(raw_due)
            if parsed is not None:
                due_times.append(parsed)
        deck_refs = _deck_node_refs(item)
        node_refs.update(deck_refs)
        total_due += len(due)
        deck_earliest = min(due_times) if due_times else None
        if deck_earliest is not None and (earliest is None or deck_earliest < earliest):
            earliest = deck_earliest
        per_deck.append({
            "artifact_id": item.id,
            "title": item.title,
            "due_count": len(due),
            "node_refs": deck_refs,
            "earliest_due": deck_earliest.isoformat() if deck_earliest else None,
        })
    per_deck.sort(key=lambda row: (-int(row["due_count"]), str(row["title"] or "")))
    return {
        "due_count": total_due,
        "deck_count": len(per_deck),
        "decks": per_deck[:8],
        "node_refs": sorted(node_refs),
        "earliest_due": earliest.isoformat() if earliest else None,
        "checked_at": moment.isoformat(),
    }


def _parse(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def due_review_signal(
    db: AsyncSession, *, user_id: int, node_ref: str | None = None,
) -> dict[str, Any]:
    """What is due right now, optionally narrowed to one knowledge node.

    Read-only by construction: it reads decks and their stored FSRS state, and
    returns a projection. It cannot change a schedule or touch mastery.
    """
    decks = (await db.scalars(select(GeneratedItem).where(
        GeneratedItem.user_id == user_id,
        GeneratedItem.type == FLASHCARD_ITEM_TYPE,
    ))).all()
    summary = summarize_decks(decks)
    if not node_ref:
        return summary
    wanted = str(node_ref)
    matching = [deck for deck in summary["decks"] if wanted in list(deck.get("node_refs") or [])]
    return {
        **summary,
        "node_ref": wanted,
        "due_count": sum(int(deck["due_count"]) for deck in matching),
        "deck_count": len(matching),
        "decks": matching[:8],
    }


def review_reason(signal: Mapping[str, Any]) -> str:
    """One sentence a learner would understand, or empty when nothing is due."""
    due = int(signal.get("due_count") or 0)
    if due <= 0:
        return ""
    node = str(signal.get("node_ref") or "").strip()
    where = f"这个知识点上" if node else "你的记忆卡里"
    return (
        f"{where}有 {due} 张卡片到期了。先在快忘记的时候把它们取回来，再往下讲新的 —— "
        "间隔重复的效果就来自这一次提取。"
    )
