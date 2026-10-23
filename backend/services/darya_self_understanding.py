"""Human-readable projection of DARYA's existing understanding authorities.

This module creates no profile or memory store. It only projects active durable
memory and revocable AI impressions into calm, correctable user-facing claims.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from services.impression_service import list_user_impressions
from services.tide_long_term_memory import list_active_memories


MEMORY_COPY: dict[str, tuple[str, str]] = {
    "learning_preference:example_first": (
        "你更容易从具体例子进入新概念。",
        "所以在学习状态允许时，我会先放一个例子，再收回关键规则。",
    ),
    "learning_preference:rule_then_practice": (
        "你更喜欢先看一条短规则，再马上练一下。",
        "所以在学习状态允许时，我会先给规则抓手，然后尽快让你用一次。",
    ),
    "response_policy:plain_language_first": (
        "你希望专业内容先用人话讲清楚。",
        "所以我会先给直白解释，再按需要补专业术语。",
    ),
    "response_preference:concise": (
        "你偏好简洁直接的回答。",
        "所以我会减少不必要的展开，但不会省略关键判断依据。",
    ),
}


def _memory_claim(item: Any) -> dict[str, Any]:
    statement, why = MEMORY_COPY.get(
        str(item.topic),
        (str(item.content), "相关时我会自然使用这条由你确认的信息，不让它替代正式学习判断。"),
    )
    observed = str(item.epistemic_status or "").upper() == "OBSERVED"
    if observed:
        if statement.startswith("用户是"):
            statement = "你是" + statement[3:]
        elif statement.startswith("用户"):
            statement = "你" + statement[2:]
        elif statement.startswith("我"):
            statement = "你" + statement[1:]
        why = "这是你自然提到的普通信息；相关时我会用它保持连续，但不会把它当成外部核实或正式学习判断。"
    return {
        "id": f"memory:{item.id}",
        "source_kind": "memory",
        "source_id": item.id,
        "statement": statement,
        "why_it_matters": why,
        "certainty": "observed" if observed else "confirmed",
        "certainty_label": "你这次提到" if observed else "你告诉过我",
        "can_confirm": False,
        "can_correct": True,
        "can_forget": True,
        "revision": item.revision,
        "updated_at": str(item.updated_at or item.created_at or ""),
    }


def _impression_claim(item: Any) -> dict[str, Any]:
    confirmed = bool(item.confirmed_by_user) or item.status == "confirmed_by_user"
    uncertain = item.status == "uncertain"
    if confirmed:
        certainty, label = "confirmed", "你确认过"
    elif uncertain:
        certainty, label = "uncertain", "还需要确认"
    else:
        certainty, label = "inferred", "我目前的理解"
    return {
        "id": f"impression:{item.id}",
        "source_kind": "impression",
        "source_id": item.id,
        "statement": str(item.summary),
        "why_it_matters": "相关时我会据此调整讲解节奏或形式；它不是对你的定论。",
        "certainty": certainty,
        "certainty_label": label,
        "can_confirm": not confirmed,
        "can_correct": True,
        "can_forget": True,
        "revision": None,
        "updated_at": str(item.updated_at or item.last_observed_at or ""),
    }


def _claim_key_text(statement: str) -> str:
    return re.sub(r"[\s，。；：、,.;;:（）()\"'“”？?！!]+", "", str(statement or ""))


def _bigrams(text: str) -> set[str]:
    return {text[index:index + 2] for index in range(len(text) - 1)} if len(text) > 1 else {text}


def _same_observed_fact(left: str, right: str) -> bool:
    a, b = _claim_key_text(left), _claim_key_text(right)
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    if SequenceMatcher(None, a, b).ratio() >= 0.66:
        return True
    # 换了措辞但描述同一件事的两条提及，字符二元组重合度会显著高于不同事实。
    first, second = _bigrams(a), _bigrams(b)
    overlap = len(first & second) / max(1, min(len(first), len(second)))
    return overlap >= 0.55


def _dedupe_observed_claims(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge near-duplicate naturally-mentioned facts at projection time only.

    The durable memory store is untouched; the visible profile shows one entry
    per distinct fact and keeps the most specific statement of each cluster.
    """
    kept: list[dict[str, Any]] = []
    for claim in claims:
        if claim.get("certainty") != "observed":
            kept.append(claim)
            continue
        duplicate_index = next(
            (
                index
                for index, existing in enumerate(kept)
                if existing.get("certainty") == "observed"
                and _same_observed_fact(str(existing["statement"]), str(claim["statement"]))
            ),
            None,
        )
        if duplicate_index is None:
            kept.append(claim)
            continue
        existing = kept[duplicate_index]
        if len(_claim_key_text(str(claim["statement"]))) > len(_claim_key_text(str(existing["statement"]))):
            kept[duplicate_index] = claim
    return kept


async def build_self_understanding(db: AsyncSession, *, user_id: int) -> dict[str, Any]:
    memories = await list_active_memories(db, user_id=user_id)
    impressions = await list_user_impressions(db, user_id)
    claims = _dedupe_observed_claims([_memory_claim(item) for item in memories])
    active_topics = {str(item.topic) for item in memories}
    claims.extend(
        _impression_claim(item)
        for item in impressions
        if item.status not in {"rejected"}
        and not (
            item.impression_key == "learning_style.example_first"
            and "learning_preference:example_first" in active_topics
        )
    )
    rank = {"confirmed": 0, "observed": 1, "inferred": 2, "uncertain": 3}
    claims.sort(key=lambda claim: (rank.get(claim["certainty"], 9), claim["updated_at"]), reverse=False)
    groups = {
        key: [claim for claim in claims if claim["certainty"] == key]
        for key in ("confirmed", "observed", "inferred", "uncertain")
    }
    return {
        "contract_version": "darya_self_understanding_v1",
        "claims": claims,
        "groups": groups,
        "empty": not claims,
        "boundaries": {
            "formal_learning_state_dominates": True,
            "soft_preferences_used_for_mastery": False,
            "second_profile_store_created": False,
        },
    }
