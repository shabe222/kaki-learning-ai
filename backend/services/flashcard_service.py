"""Study flashcards — retrieval-practice decks with FSRS spaced repetition.

Generation is LLM-driven under the flashcard-authoring skill; review
scheduling uses the vendored MIT-licensed py-fsrs scheduler
(https://github.com/open-spaced-repetition/py-fsrs, provenance in
backend/skills/flashcard-authoring/manifest.json).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import GeneratedItem
from services.extract_llm_json import extract_json_object
from services.skill_library import get_skill, skill_directive
from services.workspace_artifact_versions import ensure_initial_version

logger = logging.getLogger(__name__)

ALLOWED_CARD_KINDS = {"concept", "fact", "application"}
RATING_TO_FSRS = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}


class FlashcardError(RuntimeError):
    code = "FLASHCARD_ERROR"


def artifact_source_text(item: GeneratedItem, *, limit: int = 8000) -> str:
    """Best-effort plain text out of an artifact's content projection."""
    raw = item.content
    if isinstance(raw, str):
        text = raw
    elif isinstance(raw, list):
        parts = []
        for block in raw:
            if isinstance(block, dict):
                data = block.get("data") or {}
                if isinstance(data, dict) and data.get("text"):
                    parts.append(str(data["text"]))
                elif block.get("text"):
                    parts.append(str(block["text"]))
        text = "\n\n".join(parts)
    else:
        text = json.dumps(raw, ensure_ascii=False) if raw is not None else ""
    if not text and isinstance(item.raw_data, dict):
        text = str(item.raw_data.get("transcript") or "")
    text = text.strip()
    return text[:limit]


def _require_real_llm():
    from config import settings
    from services.llm_service_real import get_llm_service

    if str(settings.LLM_PROVIDER).lower() == "mock":
        raise FlashcardError("记忆卡生成需要配置真实模型（当前为 mock 模式）。")
    return get_llm_service()


async def generate_flashcard_deck(
    db: AsyncSession, *, user_id: int, thread_id: str = "",
    source_artifact_id: int | None = None, topic: str = "",
    llm: Any | None = None,
) -> GeneratedItem:
    """Generate one deck from a source artifact or a topic; honest failure
    when no usable material or no real provider exists."""
    source_text = ""
    if source_artifact_id:
        row = await db.get(GeneratedItem, int(source_artifact_id))
        if row is None or row.user_id != int(user_id):
            raise FlashcardError("源产物不存在或无权访问。")
        source_text = artifact_source_text(row)
        topic = topic or row.title
    if not source_text and not str(topic or "").strip():
        raise FlashcardError("需要提供源产物或主题，不能凭空生成记忆卡。")

    skill = get_skill("flashcard-authoring")
    if skill is None:
        raise FlashcardError("flashcard-authoring 技能包缺失。")
    if llm is None:
        llm = await _require_real_llm()
    prompt = {
        "skill": skill_directive([skill]),
        "topic": str(topic or "")[:200],
        "source_material": source_text or None,
        "requirement": "依据 skill 与源材料生成记忆卡组；输出 JSON。",
    }
    from services.llm_service_real import extract_llm_text
    raw = await llm.chat(
        messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
        temperature=0.2, max_tokens=2600, use_cache=False, write_cache=False,
    )
    parsed = extract_json_object(extract_llm_text(raw))
    cards = _validated_cards((parsed or {}).get("cards"))
    now = datetime.now(timezone.utc)
    from fsrs import Card
    scheduler_state = {card["id"]: Card().to_dict() for card in cards}
    deck = {
        "title": str((parsed or {}).get("title") or (f"记忆卡：{topic}" if topic else "记忆卡"))[:120],
        "cards": cards,
    }
    new_item = GeneratedItem(
        user_id=int(user_id), type="study_flashcard_deck",
        title=deck["title"],
        chapter=str(topic or "")[:120] or None,
        content=json.dumps(deck, ensure_ascii=False),
        raw_data={
            "kind": "flashcards", "status": "ready", "thread_id": str(thread_id or ""),
            "source_artifact_id": int(source_artifact_id) if source_artifact_id else None,
            "fsrs": scheduler_state,
            "generation_provenance": {
                "mode": "skill_llm", "skills": ["flashcard-authoring"],
                "scheduler": "py-fsrs (MIT, open-spaced-repetition/py-fsrs)",
            },
            "created_at": now.isoformat(),
        },
    )
    db.add(new_item)
    await db.flush()
    await ensure_initial_version(db, new_item)
    await db.commit()
    await db.refresh(new_item)
    return new_item


def _validated_cards(raw_cards: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_cards, list) or not (6 <= len(raw_cards) <= 16):
        raise FlashcardError("模型未返回有效数量的记忆卡。")
    cards: list[dict[str, Any]] = []
    for index, entry in enumerate(raw_cards):
        if not isinstance(entry, dict):
            raise FlashcardError("记忆卡格式无效。")
        front = str(entry.get("front") or "").strip()
        back = str(entry.get("back") or "").strip()
        kind = str(entry.get("kind") or "concept").strip().lower()
        source = str(entry.get("source") or "").strip()
        if not front or not back or kind not in ALLOWED_CARD_KINDS:
            raise FlashcardError("记忆卡内容校验失败。")
        cards.append({
            "id": f"c{index + 1}", "front": front[:300], "back": back[:600],
            "kind": kind, "source": source[:160],
        })
    return cards


def _load_deck(item: GeneratedItem) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        deck = json.loads(item.content or "{}")
    except json.JSONDecodeError as exc:
        raise FlashcardError("记忆卡数据损坏。") from exc
    if not isinstance(deck, dict) or not isinstance(deck.get("cards"), list):
        raise FlashcardError("记忆卡数据损坏。")
    return deck, dict(item.raw_data or {})


def due_cards(item: GeneratedItem, *, now: datetime | None = None) -> list[dict[str, Any]]:
    """Cards whose FSRS due time has arrived, earliest first."""
    deck, metadata = _load_deck(item)
    state_map = dict(metadata.get("fsrs") or {})
    from fsrs import Card
    moment = (now or datetime.now(timezone.utc))
    due: list[tuple[datetime, dict[str, Any]]] = []
    for card in deck["cards"]:
        state = state_map.get(str(card["id"]))
        if not isinstance(state, dict):
            continue
        fsrs_card = Card.from_dict(state)
        if fsrs_card.due <= moment:
            due.append((fsrs_card.due, card))
    due.sort(key=lambda pair: pair[0])
    return [card for _, card in due]


def review_card(item: GeneratedItem, *, card_id: str, rating: int) -> dict[str, Any]:
    """Apply one FSRS review; returns the updated schedule projection."""
    if rating not in RATING_TO_FSRS:
        raise FlashcardError("评分必须是 1-4。")
    deck, metadata = _load_deck(item)
    state_map = dict(metadata.get("fsrs") or {})
    target = next((card for card in deck["cards"] if str(card["id"]) == str(card_id)), None)
    if target is None:
        raise FlashcardError("卡片不存在。")
    from fsrs import Card as FsrsCard, Rating, Scheduler
    state = state_map.get(str(card_id))
    fsrs_card = FsrsCard.from_dict(state) if isinstance(state, dict) else FsrsCard()
    scheduler = Scheduler()
    updated, _log = scheduler.review_card(fsrs_card, Rating(rating))
    state_map[str(card_id)] = updated.to_dict()
 
    metadata["fsrs"] = state_map
    return {
        "card": target,
        "state": state_map[str(card_id)],
        "due": updated.due.isoformat(),
        "stability": updated.stability,
        "difficulty": updated.difficulty,
        "raw_metadata": metadata,
    }
