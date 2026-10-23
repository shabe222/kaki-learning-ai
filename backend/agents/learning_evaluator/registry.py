REGISTRY_DECLARATION={
 "agent_id":"learning_evaluator","agent_version":"0.1.10","agent_kind":"decision_agent",
 "capabilities":["evaluate_node_mastery","evaluate_learning_evidence","detect_misconceptions","detect_evidence_gaps","recommend_assessment_actions"],
 "artifact_types":["LEARNING_EVALUATION"],
 "learning_event_types":["node_evaluation_created","node_evaluation_updated","mastery_signal_changed","misconception_detected","evaluation_review_required"]}

def learner_state_signals(result):
    return [{"node_id":n.node_id,"mastery_signal":n.mastery_signal,"confidence":n.confidence,"evidence_sufficiency":n.evidence_sufficiency,
             "misconception_codes":[m.code for m in n.misconceptions],"evaluation_revision":result.evaluation_revision} for n in result.node_assessments]

def planned_learning_events(result):
    event="node_evaluation_created" if result.evaluation_revision==1 else "node_evaluation_updated"
    return [{"event_type":event,"node_id":n.node_id,"evaluation_revision":result.evaluation_revision} for n in result.node_assessments]
