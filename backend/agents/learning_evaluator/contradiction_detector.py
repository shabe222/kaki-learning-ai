from __future__ import annotations
from .models import *

def detect_contradictions(node_id:str,evidence:list[NormalizedEvidence])->list[Contradiction]:
    out=[]; valid=[e for e in evidence if e.state==EvidenceState.VALID]
    successes=[e for e in valid if e.normalized_score>=.75 and not e.weak]; failures=[e for e in valid if e.normalized_score<.4 and not e.weak]
    if successes and failures:
        old=max(successes,key=lambda e:e.occurred_at); recent=max(failures,key=lambda e:e.occurred_at)
        if recent.occurred_at>old.occurred_at and old.evidence_id!=recent.evidence_id:
            out.append(Contradiction(contradiction_code="RECENT_FAILURE_AFTER_OLD_SUCCESS",node_id=node_id,involved_evidence_ids=[old.evidence_id,recent.evidence_id],severity=Severity.WARNING,recommended_follow_up=ActionType.SPACED_RECHECK))
    routine=[e for e in successes if not e.transfer]; transfer_fail=[e for e in failures if e.transfer]
    if routine and transfer_fail and routine[-1].evidence_id!=transfer_fail[-1].evidence_id:
        out.append(Contradiction(contradiction_code="TRANSFER_FAILURE_AFTER_ROUTINE_SUCCESS",node_id=node_id,involved_evidence_ids=[routine[-1].evidence_id,transfer_fail[-1].evidence_id],severity=Severity.REVIEW_REQUIRED,recommended_follow_up=ActionType.TRANSFER_TASK))
    self_high=[e for e in valid if e.evidence_type==EvidenceType.SELF_REPORT and e.normalized_score>=.8]
    verified_low=[e for e in failures if e.trust_level in {TrustLevel.VERIFIED,TrustLevel.SYSTEM_OBSERVED}]
    if self_high and verified_low and self_high[-1].evidence_id!=verified_low[-1].evidence_id:
        out.append(Contradiction(contradiction_code="HIGH_SELF_REPORT_LOW_VERIFIED_RESULT",node_id=node_id,involved_evidence_ids=[self_high[-1].evidence_id,verified_low[-1].evidence_id],severity=Severity.WARNING,recommended_follow_up=ActionType.TARGETED_QUESTION))
    return out

def detect_evidence_quality_warnings(node_id:str,evidence:list[NormalizedEvidence])->list[EvidenceQualityWarning]:
    out=[]
    for e in evidence:
        if e.state==EvidenceState.VALID and e.normalized_score>=.75 and e.hinted:
            out.append(EvidenceQualityWarning(warning_code="CORRECT_WITH_HEAVY_HINTS",node_id=node_id,evidence_ids=[e.evidence_id],severity=Severity.WARNING,recommended_follow_up=ActionType.TARGETED_QUESTION))
    return out
