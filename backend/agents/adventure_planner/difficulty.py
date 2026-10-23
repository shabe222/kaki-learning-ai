from __future__ import annotations

from dataclasses import dataclass

from .models import DifficultyLevel, PlannerRequest


ORDER = [
    DifficultyLevel.FOUNDATIONAL,
    DifficultyLevel.STANDARD,
    DifficultyLevel.STANDARD_PLUS,
    DifficultyLevel.CHALLENGING,
    DifficultyLevel.ADVANCED,
]


@dataclass(frozen=True)
class DifficultyDecision:
    level: DifficultyLevel
    trace: list[str]


STUCK_KINDS = {"incorrect_answer", "hint_used", "remediation_needed", "explicit_confusion"}
ADVANCED_KINDS = {"self_explanation", "transfer_success", "correct_answer", "course_level"}


def _index(level: DifficultyLevel) -> int:
    return ORDER.index(level)


def decide_difficulty(request: PlannerRequest) -> DifficultyDecision:
    """审慎偏进取：轻微上调，明显卡住立即回退，单次不跨越过多档。"""
    previous = request.previous_difficulty or DifficultyLevel.STANDARD
    trace = [f"baseline={previous.value}"]
    score = 0.0
    stuck_weight = 0.0

    for evidence in request.learning_evidence:
        weight = evidence.difficulty_signal * evidence.confidence
        score += weight
        if evidence.kind in STUCK_KINDS and weight < 0:
            stuck_weight += abs(weight)
        trace.append(
            f"{evidence.kind}:{evidence.difficulty_signal:+.2f}x{evidence.confidence:.2f}={weight:+.2f}"
        )

    confusion = (request.explicit_confusion or "").lower()
    if any(token in confusion for token in ("完全不懂", "一点都不懂", "看不懂", "彻底卡住", "完全不会")):
        stuck_weight += 0.8
        score -= 0.6
        trace.append("explicit_stuck_phrase=-0.60")

    current_index = _index(previous)
    if stuck_weight >= 0.65:
        target = max(0, current_index - 1)
        trace.append("clear_stuck=true -> retreat_one_level")
        return DifficultyDecision(ORDER[target], trace)

    # First planning defaults to standard; slight positive evidence earns one half-step.
    if request.previous_difficulty is None:
        if score >= 1.35:
            target = _index(DifficultyLevel.STANDARD_PLUS)
            trace.append("strong_positive_initial -> standard_plus (initial cap)")
        elif score >= 0.25:
            target = _index(DifficultyLevel.STANDARD_PLUS)
            trace.append("slight_positive -> standard_plus")
        elif score <= -0.75:
            target = _index(DifficultyLevel.FOUNDATIONAL)
            trace.append("negative_evidence -> foundational")
        else:
            target = _index(DifficultyLevel.STANDARD)
            trace.append("insufficient_evidence -> standard")
        return DifficultyDecision(ORDER[target], trace)

    if score >= 0.35:
        target = min(len(ORDER) - 1, current_index + 1)
        trace.append("positive_evidence -> advance_one_level")
    elif score <= -0.55:
        target = max(0, current_index - 1)
        trace.append("negative_evidence -> retreat_one_level")
    else:
        target = current_index
        trace.append("hold_current_level")
    return DifficultyDecision(ORDER[target], trace)
