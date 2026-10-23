from __future__ import annotations

import hashlib

from .authority import resolve_effective_authority
from .evidence_models import *
from .models import *
from .objective_mapper import dimensions_for_evidence

TRUST_FACTOR = {
    TrustLevel.VERIFIED: 1.0,
    TrustLevel.SYSTEM_OBSERVED: 0.9,
    TrustLevel.STRUCTURED_HUMAN: 0.75,
    TrustLevel.SELF_REPORTED: 0.25,
    TrustLevel.INFERRED: 0.2,
    TrustLevel.INVALID: 0.0,
}
BASE_WEIGHT = {
    EvidenceType.QUESTION_ATTEMPT: 0.82,
    EvidenceType.ASSESSMENT_RESULT: 0.95,
    EvidenceType.CODE_EXECUTION: 0.92,
    EvidenceType.ADVENTURE_EXPLANATION: 0.65,
    EvidenceType.ADVENTURE_DISTINCTION: 0.72,
    EvidenceType.ADVENTURE_TRANSFER: 0.95,
    EvidenceType.RESOURCE_OPENED: 0.03,
    EvidenceType.RESOURCE_COMPLETED: 0.06,
    EvidenceType.NOTE_REFLECTION: 0.38,
    EvidenceType.DIALOGUE_EXPLANATION: 0.55,
    EvidenceType.TEACH_BACK: 0.75,
    EvidenceType.MANUAL_TEACHER_EVIDENCE: 0.62,
    EvidenceType.SELF_REPORT: 0.08,
}
WEAK_TYPES = {EvidenceType.RESOURCE_OPENED, EvidenceType.RESOURCE_COMPLETED, EvidenceType.SELF_REPORT}


def _score(record: LearningEvidenceRecord):
    p = record.evidence_payload
    if isinstance(p, QuestionAttemptEvidence):
        return p.correctness
    if isinstance(p, AssessmentResultEvidence):
        return p.score / p.max_score
    if isinstance(p, CodeExecutionEvidence):
        return p.tests_passed / p.tests_total if p.tests_total else (1.0 if p.exit_status == 0 else 0.0)
    if isinstance(p, AdventureEvidence):
        return (p.correctness + p.rubric_score) / 2
    if isinstance(p, ResourceInteractionEvidence):
        return 0.1 if p.action in {"opened", "viewed"} else 0.2
    if isinstance(p, NoteReflectionEvidence):
        return min(0.75, 0.3 + 0.45 * p.user_authored_ratio) if p.structured else 0.3 * p.user_authored_ratio
    if isinstance(p, DialogueExplanationEvidence):
        return p.rubric_score if p.rubric_score is not None else (0.5 if p.user_explaining else 0.1)
    if isinstance(p, ManualEvidence):
        return p.rubric_score
    if isinstance(p, SelfReportEvidence):
        return p.confidence_claim
    return 0.0


def canonical_identity(record: LearningEvidenceRecord) -> tuple[str, str, str, str]:
    p = record.evidence_payload
    family = record.evidence_type.value.lower()
    item = record.activity_id or record.source_record_id
    suffix = ""
    if isinstance(p, QuestionAttemptEvidence):
        family, item = "question", p.question_ref
    elif isinstance(p, AssessmentResultEvidence):
        family, item = "assessment", p.assessment_ref
    elif isinstance(p, CodeExecutionEvidence):
        family, item = "code", p.task_ref
    elif isinstance(p, AdventureEvidence):
        family, item, suffix = "adventure", p.task_ref, p.stage
    elif isinstance(p, ResourceInteractionEvidence):
        family, item, suffix = "resource", p.resource_ref, p.action
    elif isinstance(p, DialogueExplanationEvidence):
        family, item, suffix = "dialogue", p.dialogue_ref, p.target_concept
    elif isinstance(p, NoteReflectionEvidence):
        family, item, suffix = "note", p.note_ref, "reflection"
    elif isinstance(p, ManualEvidence):
        family, item = "manual", p.observation_ref
    elif isinstance(p, SelfReportEvidence):
        family, item = "self-report", record.activity_id or record.source_record_id

    def opaque(prefix: str, *parts: str) -> str:
        raw = "|".join(str(x) for x in parts)
        return f"{prefix}-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"

    canonical_item = opaque("item", family, item)
    canonical_activity = opaque("activity", record.node_id, family, item, suffix or "base")
    attempt_group = opaque("attempt", record.attempt_group_id or canonical_activity)
    return opaque("family", family), canonical_activity, canonical_item, attempt_group


def normalize_record(record: LearningEvidenceRecord, policy: EvaluationPolicy, trusted_context: TrustedEvaluationContext, graph_continuity_key: str) -> NormalizedEvidence:
    p = record.evidence_payload
    score = max(0, min(1, _score(record)))
    if trusted_context is None:
        raise ValueError("MISSING_TRUSTED_EVALUATION_CONTEXT")
    effective_trust, effective_verified, effective_independent = resolve_effective_authority(record, trusted_context)
    weight = BASE_WEIGHT[record.evidence_type] * TRUST_FACTOR[effective_trust]
    independent = effective_independent
    hinted = False
    transfer = record.evidence_type == EvidenceType.ADVENTURE_TRANSFER
    errors: list[str] = []
    safe = None
    objective_scores: dict[str, float] = {}
    objective_rubric_scores = []
    quality_codes: list[str] = []
    if isinstance(p, QuestionAttemptEvidence):
        hinted = p.hint_used or p.solution_viewed
        errors = list(p.error_codes)
        if p.solution_viewed:
            weight *= 0.2
            independent = False
            quality_codes.append("SOLUTION_VIEWED")
        elif p.hint_used:
            weight *= 0.55
            quality_codes.append("HINT_USED")
    elif isinstance(p, AssessmentResultEvidence):
        objective_scores = dict(p.objective_scores)
        objective_rubric_scores = list(p.objective_rubric_scores)
    elif isinstance(p, CodeExecutionEvidence):
        if p.manual_copy_detected:
            weight *= 0.15
            independent = False
            quality_codes.append("MANUAL_COPY_DETECTED")
        if not p.independent_solution:
            weight *= 0.65
            independent = False
            quality_codes.append("NOT_INDEPENDENT_SOLUTION")
        if p.runtime_error_category:
            errors = [p.runtime_error_category]
    elif isinstance(p, AdventureEvidence):
        hinted = p.hint_used
        errors = list(p.error_codes)
        independent = independent and p.independent
        if p.hint_used:
            weight *= 0.6
            quality_codes.append("HINT_USED")
        transfer = p.stage == "transfer"
    elif isinstance(p, NoteReflectionEvidence):
        safe = p.safe_excerpt
        independent = independent and p.user_authored_ratio >= 0.7
    elif isinstance(p, DialogueExplanationEvidence):
        safe = p.safe_excerpt
        errors = list(p.error_codes)
        if not p.user_explaining:
            weight *= 0.1
            independent = False
        if p.ai_generated_ratio > 0.4:
            weight *= max(0.05, 1 - p.ai_generated_ratio)
            independent = False
            quality_codes.append("AI_ASSISTED_EXPLANATION")
    elif isinstance(p, ManualEvidence):
        safe = p.safe_summary
    elif isinstance(p, SelfReportEvidence):
        safe = p.safe_statement
        independent = False
    if record.provenance.content_origin == LiteralOrigin.AI_GENERATED:
        weight *= 0.05
        independent = False
        quality_codes.append("AI_GENERATED_ORIGIN")
    weak = record.evidence_type in WEAK_TYPES
    if weak:
        independent = False
    family, canonical_activity, canonical_item, attempt_group = canonical_identity(record)
    activity_key = f"{record.attempt_group_id or '-'}:{record.activity_id or '-'}"
    fp = f"{record.source_system}:{record.source_record_id}"
    negative_codes = list(dict.fromkeys(errors + (["LOW_SCORE"] if score < 0.6 else [])))
    return NormalizedEvidence(
        evidence_id=record.evidence_id,
        event_id=record.event_id,
        node_id=record.node_id,
        evidence_type=record.evidence_type,
        occurred_at=record.occurred_at,
        normalized_score=score,
        effective_weight=max(0, min(1, weight)),
        dimensions=dimensions_for_evidence(record.evidence_type),
        independent=independent,
        trust_level=effective_trust,
        effective_verified=effective_verified,
        subject_ref=record.user_ref,
        graph_id=record.graph_id,
        graph_continuity_key=graph_continuity_key,
        source_system=record.source_system,
        activity_key=activity_key,
        source_fingerprint=fp,
        evidence_family_key=family,
        canonical_activity_key=canonical_activity,
        canonical_item_key=canonical_item,
        attempt_group_key=attempt_group,
        weak=weak,
        transfer=transfer,
        hinted=hinted,
        error_codes=errors,
        objective_scores=objective_scores,
        objective_rubric_scores=objective_rubric_scores,
        negative_signal_codes=negative_codes,
        misconception_pattern_codes=list(errors),
        quality_warning_codes=quality_codes,
        safe_summary=safe,
        raw_private={
            "payload": p.model_dump(mode="json"),
            "provenance": record.provenance.model_dump(mode="json"),
            "claimed_verified": record.verified,
            "claimed_independent": record.independent,
        },
    )
