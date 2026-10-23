"""Item statistics for generated questions — difficulty and discrimination.

The blind reviewer answers "is this question correct and unambiguous?". Nothing
answered "is it the right difficulty, and does it separate the learners who
know the idea from the ones who don't?". Without that, a question set can be
perfectly sound and still teach nothing: five items everyone gets right measure
nothing, and so do five items nobody gets right.

Two classical numbers, computed from real server-judged attempts:

* **difficulty** (p) — the share of attempts that were correct. Standard
  practice wants most items between 0.3 and 0.85; above 0.95 the item is
  decoration, below 0.2 it is a coin toss nobody wins.
* **discrimination** (D) — the point-biserial correlation between getting this
  item right and scoring high on the rest of the same paper. An item that the
  strong learners miss and the weak learners get is worse than no item at all.

Both are honest about sample size: below ``MIN_ATTEMPTS`` they are reported as
``insufficient_data`` rather than as a number that would be noise. First
attempts only — a retry after seeing the solution is not a measurement of
anything, and the attempt table records that separately.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

# Below this many first attempts, p and D are noise. Reported as such, not rounded.
MIN_ATTEMPTS = 10
# Below this many, discrimination is unstable even though difficulty is usable.
MIN_ATTEMPTS_FOR_DISCRIMINATION = 20

TOO_EASY = 0.95
TOO_HARD = 0.20
WEAK_DISCRIMINATION = 0.15


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def point_biserial(item_scores: Sequence[float], rest_scores: Sequence[float]) -> float | None:
    """Correlation between item correctness and the score on everything else.

    Returns None when the item is constant (everyone right or everyone wrong):
    the correlation is undefined there, and reporting 0.0 would read as "no
    discrimination" when the real finding is "this item cannot discriminate".
    """
    if len(item_scores) != len(rest_scores) or len(item_scores) < 3:
        return None
    if len(set(item_scores)) < 2:
        return None
    item_sd = _stdev(item_scores)
    rest_sd = _stdev(rest_scores)
    if item_sd == 0 or rest_sd == 0:
        return None
    item_mean = _mean(item_scores)
    rest_mean = _mean(rest_scores)
    covariance = sum(
        (item - item_mean) * (rest - rest_mean)
        for item, rest in zip(item_scores, rest_scores)
    ) / (len(item_scores) - 1)
    return covariance / (item_sd * rest_sd)


def _first_attempts(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One row per (learner, item): the first attempt, ignoring retries."""
    first: dict[tuple[Any, str], dict[str, Any]] = {}
    for row in rows:
        if row.get("solution_viewed"):
            # 看过解析之后再答对，测的是阅读，不是掌握。
            continue
        key = (row.get("user_id"), str(row.get("item_identity") or ""))
        number = int(row.get("attempt_number") or 1)
        current = first.get(key)
        if current is None or number < int(current.get("attempt_number") or 1):
            first[key] = dict(row)
    return list(first.values())


def score_items(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Difficulty and discrimination for every item in one paper.

    ``rows`` are first-or-later attempts across the whole paper; this function
    does the first-attempt filtering and the per-learner total itself, so a
    caller can hand it raw attempt rows.
    """
    attempts = _first_attempts(rows)
    by_item: dict[str, list[dict[str, Any]]] = {}
    for row in attempts:
        by_item.setdefault(str(row.get("item_identity") or ""), []).append(row)

    # 每个学习者在这份卷子上的总分，用来算区分度。
    totals: dict[Any, int] = {}
    for row in attempts:
        totals[row.get("user_id")] = totals.get(row.get("user_id"), 0) + (1 if row.get("correct") else 0)

    items: list[dict[str, Any]] = []
    for item_identity, item_rows in sorted(by_item.items()):
        n = len(item_rows)
        correct_count = sum(1 for row in item_rows if row.get("correct"))
        difficulty = correct_count / n if n else 0.0
        flags: list[str] = []
        if n < MIN_ATTEMPTS:
            flags.append("insufficient_data")
        elif difficulty >= TOO_EASY:
            flags.append("too_easy")
        elif difficulty <= TOO_HARD:
            flags.append("too_hard")

        discrimination: float | None = None
        if n >= MIN_ATTEMPTS_FOR_DISCRIMINATION:
            item_scores = [1.0 if row.get("correct") else 0.0 for row in item_rows]
            rest_scores = [
                float(totals.get(row.get("user_id"), 0) - (1 if row.get("correct") else 0))
                for row in item_rows
            ]
            discrimination = point_biserial(item_scores, rest_scores)
            if discrimination is None:
                flags.append("cannot_discriminate")
            elif discrimination < WEAK_DISCRIMINATION:
                flags.append("weak_discrimination")

        items.append({
            "item_identity": item_identity,
            "attempts": n,
            "correct": correct_count,
            "difficulty": round(difficulty, 4),
            "discrimination": None if discrimination is None else round(discrimination, 4),
            "flags": flags,
        })

    measured = [item for item in items if "insufficient_data" not in item["flags"]]
    return {
        "items": items,
        "learners": len(totals),
        "attempts": len(attempts),
        "measured_items": len(measured),
        "mean_difficulty": round(_mean([item["difficulty"] for item in measured]), 4) if measured else None,
        "revision_hints": revision_hints(items),
    }


def revision_hints(items: Sequence[Mapping[str, Any]]) -> list[str]:
    """What a question author should change next time, in the author's language."""
    hints: list[str] = []
    for item in items:
        flags = list(item.get("flags") or [])
        where = str(item.get("item_identity"))
        if "too_easy" in flags:
            hints.append(
                f"{where} 的正确率是 {item['difficulty']:.0%}（{item['attempts']} 次作答），几乎人人答对："
                "把它换成一个需要判断边界或条件的变式，而不是再问一次定义。"
            )
        elif "too_hard" in flags:
            hints.append(
                f"{where} 的正确率只有 {item['difficulty']:.0%}，多数人无从下手："
                "补一个把条件说全的中间步骤，或把它拆成两问。"
            )
        if "weak_discrimination" in flags:
            hints.append(
                f"{where} 的区分度是 {item['discrimination']}，"
                "熟悉这个知识点的人和没掌握的人在这里表现几乎一样：它没有测到你想要的那个差别。"
            )
        if "cannot_discriminate" in flags:
            hints.append(
                f"{where} 全员同分，区分度无法计算：这道题目前不承载任何信息。"
            )
    return hints[:12]


async def statistics_for_artifact(db: Any, *, artifact_id: int, version_id: int | None = None) -> dict[str, Any]:
    """Read the real attempts for one artifact version and score its items."""
    from sqlalchemy import select

    from database.models import InteractiveQuestionAttempt

    statement = select(InteractiveQuestionAttempt).where(
        InteractiveQuestionAttempt.artifact_id == artifact_id,
    )
    if version_id is not None:
        statement = statement.where(InteractiveQuestionAttempt.artifact_version_id == version_id)
    rows = (await db.scalars(statement)).all()
    payload = [
        {
            "user_id": row.user_id,
            "item_identity": row.item_identity,
            "attempt_number": row.attempt_number,
            "correct": bool(row.correct),
            "solution_viewed": bool(row.solution_viewed),
            "hint_used": bool(row.hint_used),
        }
        for row in rows
    ]
    result = score_items(payload)
    result["artifact_id"] = artifact_id
    result["artifact_version_id"] = version_id
    return result


async def calibration_feedback(db: Any, *, node_ref: str, limit: int = 6) -> list[str]:
    """Revision hints from the last papers written for this knowledge node.

    This is what closes the loop: the next question set for a node is told how
    the previous one behaved, instead of re-deriving the same difficulty from
    the same prompt and getting the same too-easy result.
    """
    from sqlalchemy import select

    from database.models import GeneratedItem, GeneratedItemVersion, InteractiveQuestionAttempt

    items = (await db.scalars(select(GeneratedItem).where(
        GeneratedItem.type == "workspace_interactive_lesson",
    ))).all()
    relevant = [
        item for item in items
        if node_ref and node_ref in str(((item.raw_data or {}).get("canonical_purpose") or {}).get("knowledge_refs") or "")
    ][:8]
    if not relevant:
        return []
    hints: list[str] = []
    for item in relevant:
        version = await db.scalar(select(GeneratedItemVersion).where(
            GeneratedItemVersion.artifact_id == item.id,
        ).order_by(GeneratedItemVersion.version_number.desc()))
        if version is None:
            continue
        rows = (await db.scalars(select(InteractiveQuestionAttempt).where(
            InteractiveQuestionAttempt.artifact_version_id == version.id,
        ))).all()
        stats = score_items([
            {
                "user_id": row.user_id,
                "item_identity": row.item_identity,
                "attempt_number": row.attempt_number,
                "correct": bool(row.correct),
                "solution_viewed": bool(row.solution_viewed),
            }
            for row in rows
        ])
        hints.extend(stats["revision_hints"])
        if len(hints) >= limit:
            break
    return hints[:limit]
