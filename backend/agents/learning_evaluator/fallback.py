from __future__ import annotations
from .models import *
from .objective_mapper import required_dimensions

def build_fallback_candidate(request:LearningEvaluationRequest,evidence:list[NormalizedEvidence])->EvaluationCandidate:
    actions=[]
    for node in request.node_contracts:
        node_ev=[e for e in evidence if e.node_id==node.node_id and e.state==EvidenceState.VALID]
        dims={d for e in node_ev for d in e.dimensions}
        for dim in required_dimensions(node.node_type):
            if dim not in dims:
                actions.append(CandidateAction(action_type=ActionType.TRANSFER_TASK if dim==EvaluationDimension.TRANSFER else ActionType.COLLECT_MORE_EVIDENCE,
                    target_node_id=node.node_id,reason_codes=["MISSING_DIMENSION"],required_evidence_dimension=dim,priority=2))
    return EvaluationCandidate(objective_mappings=[],misconceptions=[],recommended_actions=actions,global_explanation="确定性保守评估；未生成具体误区。",fallback_used=True)
