from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .models import LLMPlanEnvelope, PlannerWarningCode, Question


@dataclass(frozen=True)
class RawPlanNormalizationReport:
    """Safe repairs applied to parsed JSON before Pydantic validation."""

    warning_codes: list[str]
    diagnostics: list[str]
    normalized_fields: int = 0


@dataclass(frozen=True)
class PlanNormalizationReport:
    """Non-blocking canonicalization performed before semantic validation."""

    warning_codes: list[str]
    diagnostics: list[str]
    normalized_hint_pairs: int = 0


def _iter_raw_questions(payload: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    plan = payload.get("plan")
    if not isinstance(plan, dict):
        return ()
    challenges = plan.get("challenges")
    if not isinstance(challenges, dict):
        return ()

    items: list[tuple[str, dict[str, Any]]] = []
    for name in (
        "understanding_trial",
        "practice_trial",
        "remedy",
        "understanding_interleave",
        "practice_interleave",
    ):
        question = challenges.get(name)
        if isinstance(question, dict):
            items.append((f"plan.challenges.{name}", question))

    boss = challenges.get("boss")
    if isinstance(boss, list):
        for index, stage in enumerate(boss):
            if not isinstance(stage, dict):
                continue
            question = stage.get("task")
            if isinstance(question, dict):
                items.append((f"plan.challenges.boss.{index}.task", question))
    return tuple(items)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _expanded_hint(value: str) -> str:
    return f"思考提示：{value}" if value else "思考提示：先找出关键关系"


def normalize_raw_plan_payload(payload: dict[str, Any]) -> RawPlanNormalizationReport:
    """Repair only low-risk microcopy before strict schema validation.

    Real providers occasionally return academically usable Chinese microcopy that
    is shorter than the schema's defensive character floor, for example ``相反``
    as a hint or ``确认`` as a private confirmation rationale. Rejecting the
    entire knowledge plan for those fields is disproportionate. This pass is
    deliberately narrow: it never changes questions, answers, accepted groups,
    correct options, source grounding, difficulty, facts, or teaching content.
    """

    normalized_paths: list[str] = []

    for path, question in _iter_raw_questions(payload):
        remediation = question.get("remediation")
        if not isinstance(remediation, dict):
            remediation = None

        hint = _text(question.get("hint"))
        first_hint = _text(remediation.get("first_attempt_hint")) if remediation else ""
        if len(hint) < 4 or (remediation is not None and len(first_hint) < 4):
            canonical_hint = hint if len(hint) >= 4 else _expanded_hint(hint or first_hint)
            if question.get("hint") != canonical_hint:
                question["hint"] = canonical_hint
                normalized_paths.append(f"{path}.hint")
            if remediation is not None and remediation.get("first_attempt_hint") != canonical_hint:
                remediation["first_attempt_hint"] = canonical_hint
                normalized_paths.append(f"{path}.remediation.first_attempt_hint")

        rationale = _text(question.get("academic_rationale"))
        if rationale and len(rationale) < 6:
            question["academic_rationale"] = f"{rationale}，用于检验核心理解"
            normalized_paths.append(f"{path}.academic_rationale")

        rubric = question.get("rubric")
        bands = rubric.get("bands") if isinstance(rubric, dict) else None
        if isinstance(bands, list):
            for index, band in enumerate(bands):
                if not isinstance(band, dict):
                    continue
                focus = _text(band.get("feedback_focus"))
                if focus and len(focus) < 4:
                    band["feedback_focus"] = f"{focus}，并说明依据"
                    normalized_paths.append(f"{path}.rubric.bands.{index}.feedback_focus")

        confirmation = remediation.get("third_attempt_confirmation") if remediation else None
        if isinstance(confirmation, dict):
            confirmation_rationale = _text(confirmation.get("academic_rationale"))
            if confirmation_rationale and len(confirmation_rationale) < 6:
                confirmation["academic_rationale"] = "用于确认学生能独立完成该关键步骤"
                normalized_paths.append(
                    f"{path}.remediation.third_attempt_confirmation.academic_rationale"
                )

    if not normalized_paths:
        return RawPlanNormalizationReport(warning_codes=[], diagnostics=[])

    sample = ",".join(normalized_paths[:6])
    diagnostics = [f"normalized_microcopy_fields:{len(normalized_paths)}"]
    if sample:
        diagnostics.append(f"normalized_microcopy_paths:{sample}")
    return RawPlanNormalizationReport(
        warning_codes=[PlannerWarningCode.MICROCOPY_NORMALIZED.value],
        diagnostics=diagnostics,
        normalized_fields=len(normalized_paths),
    )


def _iter_questions(envelope: LLMPlanEnvelope) -> Iterable[Question]:
    if envelope.plan is None:
        return ()
    challenges = envelope.plan.challenges
    return (
        challenges.understanding_trial,
        challenges.practice_trial,
        challenges.remedy,
        challenges.understanding_interleave,
        challenges.practice_interleave,
        *(stage.task for stage in challenges.boss),
    )


def normalize_plan_envelope(envelope: LLMPlanEnvelope) -> PlanNormalizationReport:
    """Make duplicated hint fields deterministic without accepting unsafe plans.

    `Question.hint` is the canonical first-attempt hint because it is consumed by
    the existing eight-state adventure adapter. The model must still provide a
    complete remediation ladder, but wording differences between the duplicated
    hint fields are not academic or structural failures. The duplicate field is
    synchronized before all ordinary semantic and academic checks continue.
    """

    normalized = 0
    for question in _iter_questions(envelope):
        remediation = question.remediation
        if remediation is None:
            continue
        if remediation.first_attempt_hint != question.hint:
            remediation.first_attempt_hint = question.hint
            normalized += 1

    if normalized == 0:
        return PlanNormalizationReport(warning_codes=[], diagnostics=[])

    return PlanNormalizationReport(
        warning_codes=[PlannerWarningCode.HINT_NORMALIZED.value],
        diagnostics=[f"normalized_hint_pairs:{normalized}"],
        normalized_hint_pairs=normalized,
    )
