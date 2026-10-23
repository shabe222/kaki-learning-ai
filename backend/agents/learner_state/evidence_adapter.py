from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from .models import (
    ContextMode,
    EvidenceResult,
    EvidenceType,
    HintLevel,
    LearnerStateRequest,
    LearningEvidence,
    TaskType,
)


def normalize_topic_key(value: str) -> str:
    text = value.strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff._:-]+", "-", text)
    return text.strip("-")[:160] or "unknown-topic"


def _task_type(node: str, boss_stage: int | None = None) -> TaskType:
    node = (node or "").upper()
    if node == "INTERLEAVE":
        return TaskType.TRANSFER
    if node == "SUPPLY":
        return TaskType.REMEDIATION
    if node == "BOSS":
        if boss_stage == 2:
            return TaskType.TRANSFER
        if boss_stage == 1:
            return TaskType.DISTINCTION
        return TaskType.UNDERSTANDING
    if node == "TRIAL":
        return TaskType.UNDERSTANDING
    return TaskType.UNKNOWN


def _result(item: dict[str, Any]) -> EvidenceResult:
    decision = str(item.get("decision") or "").lower()
    score = item.get("score")
    if decision == "pass":
        return EvidenceResult.CORRECT
    if isinstance(score, (int, float)) and 0.0 < float(score) < 1.0:
        return EvidenceResult.PARTIALLY_CORRECT
    if decision == "needs_review" or score == 0:
        return EvidenceResult.NEEDS_REMEDIATION
    return EvidenceResult.UNKNOWN


def _hint_level_from_count(count: int) -> HintLevel:
    if count <= 0:
        return HintLevel.NONE
    if count >= 2:
        return HintLevel.STRONG
    return HintLevel.MINIMAL


def timeline_to_evidence(
    timeline: list[dict[str, Any]],
    *,
    max_items: int = 8,
    boss_stage: int | None = None,
) -> list[LearningEvidence]:
    """Convert private adventure timeline records into bounded diagnostic evidence.

    The adapter scans explicit ``decision=hint`` events instead of treating an
    available hint on an incorrect answer as proof that the learner used it.
    BOSS attempts are isolated by ``boss_stage`` so understanding, distinction,
    and transfer evidence cannot contaminate each other.
    """

    evidence: list[LearningEvidence] = []
    prior_fail: dict[tuple[str, int | None], bool] = defaultdict(bool)
    hint_count: dict[tuple[str, int | None], int] = defaultdict(int)

    for index, raw in enumerate(timeline):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        node = str(item.get("node") or "UNKNOWN").upper()
        stage_value = item.get("boss_stage")
        try:
            stage = int(stage_value) if stage_value is not None else None
        except (TypeError, ValueError):
            stage = None
        if node == "BOSS" and stage is None:
            stage = boss_stage
        key = (node, stage)
        decision = str(item.get("decision") or "").lower()

        if decision == "hint":
            hint_count[key] += 1
            continue
        if decision not in {"pass", "needs_review"}:
            continue

        result = _result(item)
        explicit_hint_count = hint_count[key]
        legacy_supported_pass = bool(item.get("hint")) and decision == "pass" and prior_fail[key]
        effective_hint_count = explicit_hint_count or (1 if legacy_supported_pass else 0)
        hint_level = _hint_level_from_count(effective_hint_count)
        response = str(item.get("response") or "").strip()
        response_summary = response[:100] if response else None
        attempts = max(1, int(item.get("attempts") or 1))

        self_corrected = False
        corrected_with_support = False
        if result == EvidenceResult.CORRECT and prior_fail[key]:
            if effective_hint_count > 0 or node == "SUPPLY":
                corrected_with_support = True
            else:
                self_corrected = True

        task_type = _task_type(node, stage)
        evidence.append(
            LearningEvidence(
                evidence_id=f"timeline:{index}:{node.lower()}:{stage if stage is not None else 'na'}",
                evidence_type=(
                    EvidenceType.TRANSFER_RESULT
                    if task_type == TaskType.TRANSFER
                    else EvidenceType.REMEDIATION_RESULT
                    if node == "SUPPLY"
                    else EvidenceType.ANSWER
                ),
                task_type=task_type,
                response_summary=response_summary,
                result=result,
                score=float(item["score"]) if isinstance(item.get("score"), (int, float)) else None,
                attempts=attempts,
                reasoning_present=bool(response_summary and len(response_summary) >= 12),
                hint_level_used=hint_level,
                self_corrected=self_corrected,
                corrected_with_support=corrected_with_support,
                transfer_context=task_type == TaskType.TRANSFER,
                recency_rank=index,
                observed_at=item.get("at"),
            )
        )
        if result == EvidenceResult.NEEDS_REMEDIATION:
            prior_fail[key] = True

    selected = evidence[-max_items:]
    for rank, item in enumerate(selected):
        item.recency_rank = rank
    return selected


def build_request_from_project_data(
    *,
    student_id: str | int,
    topic_name: str,
    explicit_confusion: str | None,
    timeline: list[dict[str, Any]],
    boss_stage: int | None = None,
    context_mode: ContextMode = ContextMode.RELEVANT_RECENT,
    recent_related_evidence: list[LearningEvidence] | None = None,
    previous_topic_state=None,
    current_material_summary: str | None = None,
    request_id: str | None = None,
) -> LearnerStateRequest:
    recent = list(recent_related_evidence or [])
    if context_mode == ContextMode.CURRENT_ITEM_ONLY:
        recent = []
    return LearnerStateRequest(
        request_id=request_id,
        student_id=str(student_id),
        topic_key=normalize_topic_key(topic_name),
        topic_name=topic_name,
        explicit_confusion=(explicit_confusion or None),
        current_evidence=timeline_to_evidence(timeline, boss_stage=boss_stage),
        recent_related_evidence=recent,
        previous_topic_state=previous_topic_state,
        current_material_summary=current_material_summary,
        context_mode=context_mode,
    )
