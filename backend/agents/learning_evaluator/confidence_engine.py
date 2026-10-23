from __future__ import annotations
from datetime import timedelta
from .models import *

def calculate_confidence(evidence:list[NormalizedEvidence],dimensions:list[DimensionAssessment],evaluated_at,policy:EvaluationPolicy,contradiction_count:int=0)->float:
    valid=[e for e in evidence if e.state==EvidenceState.VALID]
    if not valid:return 0.0
    independent=sum(e.independent for e in valid); verified=sum(e.trust_level in {TrustLevel.VERIFIED,TrustLevel.SYSTEM_OBSERVED} for e in valid)
    coverage=sum(not d.missing_evidence for d in dimensions)/max(1,len(dimensions))
    diversity=len({e.evidence_type for e in valid})/max(1,min(4,len(valid)))
    recent=sum((evaluated_at-e.occurred_at)<=timedelta(days=policy.stale_after_days) for e in valid)/len(valid)
    raw=.12*min(1,len(valid)/5)+.2*min(1,independent/3)+.2*min(1,verified/3)+.22*coverage+.12*min(1,diversity)+.14*recent
    raw-=.18*contradiction_count
    if len(valid)==1:raw=min(raw,.45)
    if independent==0:raw=min(raw,.42)
    if all(e.trust_level in {TrustLevel.SELF_REPORTED,TrustLevel.INFERRED} for e in valid):raw=min(raw,.3)
    return round(max(0,min(1,raw)),4)
