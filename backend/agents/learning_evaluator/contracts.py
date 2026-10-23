from __future__ import annotations

import hashlib
import json
import re
from typing import Iterable

from .enums import EvaluationDimension


def _digest(prefix: str, payload) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24]}"


def normalize_contract_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def objective_contract_values(node_continuity_key: str, objective_text: str, objective_order: int, required_dimensions: Iterable[EvaluationDimension]):
    text = normalize_contract_text(objective_text)
    dims = sorted(d.value if hasattr(d, "value") else str(d) for d in required_dimensions)
    objective_id = _digest("objective", [node_continuity_key, text])
    objective_digest = _digest("objective-digest", [objective_id, text, dims])
    return objective_id, objective_digest, dims


def mastery_requirement_digest(requirements: Iterable[str]) -> str:
    return _digest("mastery", [normalize_contract_text(x) for x in requirements])


def node_contract_values(node, required_dimensions: Iterable[EvaluationDimension], objective_dimension_resolver):
    objectives = []
    for index, text in enumerate(node.learning_objectives):
        dims = objective_dimension_resolver(node, text)
        objective_id, objective_digest, dim_values = objective_contract_values(
            node.node_continuity_key, text, index, dims
        )
        objectives.append(
            {
                "objective_id": objective_id,
                "objective_text": text,
                "objective_digest": objective_digest,
                "objective_order": index,
                "required_dimensions": dim_values,
            }
        )
    requirement_digest = mastery_requirement_digest(node.mastery_evidence_requirements)
    semantic_objectives = sorted(
        [
            {
                "objective_id": objective["objective_id"],
                "objective_text": normalize_contract_text(objective["objective_text"]),
                "objective_digest": objective["objective_digest"],
                "required_dimensions": sorted(objective["required_dimensions"]),
            }
            for objective in objectives
        ],
        key=lambda item: item["objective_id"],
    )
    node_digest = _digest(
        "node-contract",
        {
            "node_id": node.node_id,
            "node_continuity_key": node.node_continuity_key,
            "node_type": node.node_type.value,
            "objectives": semantic_objectives,
            "mastery_requirement_digest": requirement_digest,
            "difficulty_band": normalize_contract_text(node.difficulty_band),
            "required_dimensions": sorted(d.value if hasattr(d, "value") else str(d) for d in required_dimensions),
        },
    )
    for objective in objectives:
        objective["node_contract_digest"] = node_digest
    return node_digest, requirement_digest, objectives


def authority_context_digest(context) -> str:
    authorities = [
        {
            "source_system": a.source_system,
            "allowed_evidence_types": sorted(x.value for x in a.allowed_evidence_types),
            "maximum_trust_level": a.maximum_trust_level.value,
            "can_assert_verified": a.can_assert_verified,
            "can_assert_independence": a.can_assert_independence,
            "authority_contract_version": a.authority_contract_version,
            "adapter_contract_version": context.adapter_contract_versions.get(a.source_system),
        }
        for a in sorted(context.evidence_authority_policy.authorities, key=lambda x: x.source_system)
    ]
    return _digest(
        "authority-context",
        {
            "context_version": context.context_version,
            "authenticated_subject_ref": context.authenticated_subject_ref,
            "authorities": authorities,
            "unknown_source_maximum_trust": context.evidence_authority_policy.unknown_source_maximum_trust.value,
        },
    )


def node_contract_set_digest(node_contracts, required_dimensions_resolver, objective_dimension_resolver) -> str:
    rows = []
    for node in sorted(node_contracts, key=lambda n: n.node_id):
        node_digest, requirement_digest, objectives = node_contract_values(
            node, required_dimensions_resolver(node.node_type), objective_dimension_resolver
        )
        rows.append(
            {
                "node_id": node.node_id,
                "node_contract_revision": node.node_contract_revision,
                "node_contract_digest": node_digest,
                "mastery_requirement_digest": requirement_digest,
                "objectives": sorted(
                    [
                        {
                            "objective_id": item["objective_id"],
                            "objective_digest": item["objective_digest"],
                            "node_contract_digest": item["node_contract_digest"],
                            "required_dimensions": sorted(item["required_dimensions"]),
                        }
                        for item in objectives
                    ],
                    key=lambda item: item["objective_id"],
                ),
            }
        )
    return _digest("node-contract-set", rows)
