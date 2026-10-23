from __future__ import annotations

import json

from .model_input import model_input_payload
from .models import CandidateBlueprint, LearningGraphRequest, ValidationIssue
from .prompt import SYSTEM_PROMPT

REPAIRABLE_CODES = {
    "INVALID_SCHEMA",
    "DUPLICATE_NODE_KEY",
    "DUPLICATE_UNIT_KEY",
    "DUPLICATE_RESOURCE_PLAN_KEY",
    "DUPLICATE_NODE_CONTINUITY_KEY",
    "DUPLICATE_UNIT_CONTINUITY_KEY",
    "DUPLICATE_RESOURCE_PLAN_CONTINUITY_KEY",
    "UNKNOWN_UNIT_NODE_KEY",
    "UNKNOWN_EDGE_SOURCE_KEY",
    "UNKNOWN_EDGE_TARGET_KEY",
    "UNKNOWN_RESOURCE_NODE_KEY",
    "NODE_NOT_ASSIGNED_TO_UNIT",
    "NODE_ASSIGNED_TO_MULTIPLE_UNITS",
    "DUPLICATE_EDGE",
    "DUPLICATE_NODE_ID",
    "DUPLICATE_UNIT_ID",
    "DUPLICATE_EDGE_ID",
    "DUPLICATE_RESOURCE_PLAN_ID",
    "DUPLICATE_NODE_MEANING",
    "UNKNOWN_NODE_REFERENCE",
    "SELF_DEPENDENCY",
    "PREREQUISITE_CYCLE",
    "ORPHAN_NODE",
    "MISSING_LEARNING_OBJECTIVE",
    "MISSING_KEY_CONCEPT",
    "MISSING_MASTERY_EVIDENCE",
    "UNSUPPORTED_RESOURCE_TYPE",
    "RESOURCE_NODE_MISMATCH",
    "GRAPH_TOO_SHALLOW",
    "GRAPH_TOO_FRAGMENTED",
    "GRAPH_TOO_DENSE",
    "UNAUTHORIZED_COMPLETION_STATE",
    "UNIT_NODE_MISMATCH",
    "LAZY_GENERATION_VIOLATION",
    "REPLACE_SOURCE_SCOPE_VIOLATION",
    "SOURCE_REFERENCE_MISSING",
}


def build_repair_prompt(
    request: LearningGraphRequest,
    raw_output,
    issues: list[ValidationIssue],
) -> str:
    selected = [
        item.model_dump(mode="json")
        for item in issues
        if item.code in REPAIRABLE_CODES
    ]
    safe_context = model_input_payload(request)
    return (
        SYSTEM_PROMPT
        + "\n这是唯一一次结构修复机会。只修复列出的问题，不补写来源未支持的学术事实。"
        + "\n不得修改或补写 existing_graph 中 completed/learning 节点的历史学术合同。"
        + "\nSAFE_CONTEXT:\n"
        + json.dumps(safe_context, ensure_ascii=False, sort_keys=True)
        + "\nISSUES:\n"
        + json.dumps(selected, ensure_ascii=False, sort_keys=True)
        + "\nPREVIOUS_OUTPUT:\n"
        + json.dumps(raw_output, ensure_ascii=False, sort_keys=True)
        + "\nOUTPUT_CONTRACT:\nCandidateBlueprint is bound and validated by the structured response integration and parser."
    )
