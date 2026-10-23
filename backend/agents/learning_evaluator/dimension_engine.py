from __future__ import annotations
from .models import *
from .objective_mapper import required_dimensions

def score_band(score:float,has:bool=True)->ScoreBand:
    if not has:return ScoreBand.NO_EVIDENCE
    if score<.4:return ScoreBand.LOW
    if score<.75:return ScoreBand.MEDIUM
    return ScoreBand.HIGH

def build_dimension_assessments(node:NodeEvaluationContract,evidence:list[NormalizedEvidence])->list[DimensionAssessment]:
    out=[]
    for dim in required_dimensions(node.node_type):
        relevant=[e for e in evidence if dim in e.dimensions and e.state==EvidenceState.VALID]
        denom=sum(e.effective_weight for e in relevant)
        score=sum(((e.normalized_score if (dim!=EvaluationDimension.INDEPENDENCE or e.independent) else min(.2,e.normalized_score))*e.effective_weight) for e in relevant)/denom if denom else 0.0
        support=[e.evidence_id for e in relevant if e.normalized_score>=.6]
        counter=[e.evidence_id for e in relevant if e.normalized_score<.4]
        independent=sum(1 for e in relevant if e.independent)
        confidence=min(1,.2*len(relevant)+.2*independent+.3*(denom/min(2,max(denom,1e-9)))) if relevant else 0
        out.append(DimensionAssessment(dimension=dim,score_band=score_band(score,bool(relevant)),score=round(score,4),confidence=round(confidence,4),
            supporting_evidence_ids=support,counter_evidence_ids=counter,missing_evidence=not bool(relevant),rationale_code="EVIDENCE_WEIGHTED" if relevant else "MISSING_EVIDENCE"))
    return out
