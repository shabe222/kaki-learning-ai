from __future__ import annotations
from .enums import EvaluationDimension,NodeType,EvidenceType

NODE_DIMENSIONS={
    NodeType.FOUNDATION:[EvaluationDimension.CONCEPTUAL_UNDERSTANDING,EvaluationDimension.RETENTION,EvaluationDimension.CONSISTENCY],
    NodeType.CONCEPT:[EvaluationDimension.CONCEPTUAL_UNDERSTANDING,EvaluationDimension.APPLICATION,EvaluationDimension.CONSISTENCY],
    NodeType.PROCEDURE:[EvaluationDimension.PROCEDURAL_FLUENCY,EvaluationDimension.INDEPENDENCE,EvaluationDimension.CONSISTENCY,EvaluationDimension.ERROR_RECOVERY],
    NodeType.SKILL:[EvaluationDimension.PROCEDURAL_FLUENCY,EvaluationDimension.APPLICATION,EvaluationDimension.TRANSFER,EvaluationDimension.RETENTION,EvaluationDimension.INDEPENDENCE,EvaluationDimension.CONSISTENCY],
    NodeType.MISCONCEPTION:[EvaluationDimension.CONCEPTUAL_UNDERSTANDING,EvaluationDimension.ERROR_RECOVERY,EvaluationDimension.TRANSFER],
    NodeType.APPLICATION:[EvaluationDimension.APPLICATION,EvaluationDimension.TRANSFER,EvaluationDimension.INDEPENDENCE],
    NodeType.INTEGRATION:[EvaluationDimension.CONCEPTUAL_UNDERSTANDING,EvaluationDimension.APPLICATION,EvaluationDimension.TRANSFER,EvaluationDimension.INDEPENDENCE],
    NodeType.ASSESSMENT:[EvaluationDimension.CONCEPTUAL_UNDERSTANDING,EvaluationDimension.PROCEDURAL_FLUENCY,EvaluationDimension.APPLICATION,EvaluationDimension.CONSISTENCY],
}

def required_dimensions(node_type:NodeType): return list(NODE_DIMENSIONS[node_type])

EVIDENCE_DIMENSIONS={
    EvidenceType.QUESTION_ATTEMPT:[EvaluationDimension.CONCEPTUAL_UNDERSTANDING,EvaluationDimension.APPLICATION,EvaluationDimension.CONSISTENCY],
    EvidenceType.ASSESSMENT_RESULT:[EvaluationDimension.CONCEPTUAL_UNDERSTANDING,EvaluationDimension.APPLICATION,EvaluationDimension.RETENTION,EvaluationDimension.CONSISTENCY],
    EvidenceType.CODE_EXECUTION:[EvaluationDimension.PROCEDURAL_FLUENCY,EvaluationDimension.APPLICATION,EvaluationDimension.INDEPENDENCE,EvaluationDimension.CONSISTENCY],
    EvidenceType.ADVENTURE_EXPLANATION:[EvaluationDimension.CONCEPTUAL_UNDERSTANDING,EvaluationDimension.INDEPENDENCE],
    EvidenceType.ADVENTURE_DISTINCTION:[EvaluationDimension.CONCEPTUAL_UNDERSTANDING,EvaluationDimension.ERROR_RECOVERY],
    EvidenceType.ADVENTURE_TRANSFER:[EvaluationDimension.TRANSFER,EvaluationDimension.APPLICATION,EvaluationDimension.INDEPENDENCE],
    EvidenceType.RESOURCE_OPENED:[],EvidenceType.RESOURCE_COMPLETED:[],
    EvidenceType.NOTE_REFLECTION:[EvaluationDimension.CONCEPTUAL_UNDERSTANDING,EvaluationDimension.RETENTION],
    EvidenceType.DIALOGUE_EXPLANATION:[EvaluationDimension.CONCEPTUAL_UNDERSTANDING,EvaluationDimension.INDEPENDENCE],
    EvidenceType.TEACH_BACK:[EvaluationDimension.CONCEPTUAL_UNDERSTANDING,EvaluationDimension.TRANSFER,EvaluationDimension.INDEPENDENCE],
    EvidenceType.MANUAL_TEACHER_EVIDENCE:[EvaluationDimension.CONCEPTUAL_UNDERSTANDING,EvaluationDimension.APPLICATION],
    EvidenceType.SELF_REPORT:[],
}

def dimensions_for_evidence(evidence_type): return list(EVIDENCE_DIMENSIONS[evidence_type])


def objective_allowed_dimensions(node, objective: str):
    text = objective.lower()
    dims = set()
    if any(k in text for k in ("迁移", "新情境", "综合", "推断")):
        dims |= {EvaluationDimension.TRANSFER, EvaluationDimension.APPLICATION}
    if any(k in text for k in ("应用", "使用", "解决")):
        dims |= {EvaluationDimension.APPLICATION, EvaluationDimension.PROCEDURAL_FLUENCY}
    if any(k in text for k in ("解释", "理解", "概念", "辨析", "因果")):
        dims |= {EvaluationDimension.CONCEPTUAL_UNDERSTANDING}
    if any(k in text for k in ("步骤", "操作", "计算", "编程", "代码")):
        dims |= {EvaluationDimension.PROCEDURAL_FLUENCY}
    if "独立" in text:
        dims |= {EvaluationDimension.INDEPENDENCE}
    if any(k in text for k in ("保持", "复测", "记忆")):
        dims |= {EvaluationDimension.RETENTION}
    return dims or set(required_dimensions(node.node_type))
