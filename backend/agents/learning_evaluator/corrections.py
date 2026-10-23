from __future__ import annotations

from .enums import CorrectionActorRole, CorrectionReason, EvidenceType


SYSTEM_ONLY_REASONS = {
    CorrectionReason.WRONG_USER,
    CorrectionReason.WRONG_NODE,
    CorrectionReason.INVALID_SOURCE,
    CorrectionReason.CORRUPTED_RECORD,
}
SCORING_REASONS = {CorrectionReason.SCORING_ERROR}


def actor_may_correct(actor_role: CorrectionActorRole, reason: CorrectionReason, evidence_type: EvidenceType) -> bool:
    if actor_role in {CorrectionActorRole.SYSTEM, CorrectionActorRole.ADMIN}:
        return True
    if reason in SYSTEM_ONLY_REASONS:
        return False
    if actor_role in {CorrectionActorRole.TEACHER, CorrectionActorRole.REVIEWER}:
        if reason in SCORING_REASONS:
            return evidence_type in {
                EvidenceType.QUESTION_ATTEMPT,
                EvidenceType.ASSESSMENT_RESULT,
                EvidenceType.CODE_EXECUTION,
                EvidenceType.ADVENTURE_EXPLANATION,
                EvidenceType.ADVENTURE_DISTINCTION,
                EvidenceType.ADVENTURE_TRANSFER,
                EvidenceType.MANUAL_TEACHER_EVIDENCE,
            }
        return evidence_type == EvidenceType.MANUAL_TEACHER_EVIDENCE or reason == CorrectionReason.RETRACTED_MANUAL_EVIDENCE
    if actor_role == CorrectionActorRole.SUBJECT_USER:
        return reason == CorrectionReason.USER_RETRACTION and evidence_type in {
            EvidenceType.SELF_REPORT,
            EvidenceType.NOTE_REFLECTION,
            EvidenceType.DIALOGUE_EXPLANATION,
            EvidenceType.TEACH_BACK,
        }
    return False
