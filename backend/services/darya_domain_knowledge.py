"""Reusable Domain Knowledge Pack contract for DARYA learning domains.

The generic learning kernel owns orchestration.  A pack owns stable domain
meaning, graph structure, observable evidence rules, teaching seeds, and
review constraints.  Adding a bounded domain should not require another
Diagnosis/Expert/Review pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Mapping


@dataclass(frozen=True)
class EvidenceRule:
    pattern: str
    outcome: str
    quality: str
    kind: str
    observation: str
    likely_gap: str
    confidence: float
    basis: str
    understands: tuple[str, ...] = ()
    alternatives: tuple[str, ...] = ()
    blocking_prerequisite: str | None = None

    def matches(self, text: str) -> bool:
        return bool(re.search(self.pattern, text, re.I | re.S))


@dataclass(frozen=True)
class CandidateTemplate:
    kind: str
    lead: str
    prompt: str
    hint: str = ""
    answer_revealed: bool = False
    secondary_concepts: tuple[str, ...] = ()
    assumed_prerequisites: tuple[str, ...] = ()
    claim_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewerPolicy:
    allowed_claim_keys: tuple[str, ...]
    allowed_secondary_concepts: tuple[str, ...] = ()
    required_prompt_terms: tuple[str, ...] = ()
    max_secondary_concepts: int = 0


@dataclass(frozen=True)
class KnowledgeNode:
    id: str
    title: str
    description: str
    prerequisites: tuple[str, ...]
    learning_goal: str
    evidence_requirements: tuple[str, ...]
    common_misconceptions: tuple[str, ...]
    difficulty: str
    route_cues: tuple[str, ...]
    evidence_rules: tuple[EvidenceRule, ...]
    candidate_templates: Mapping[str, CandidateTemplate]
    revised_template: CandidateTemplate
    reviewer_policy: ReviewerPolicy
    memory_analogy: str
    provenance: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResourceSeed:
    """Domain content used by the existing generic semantic resource forms."""

    plain_rule: str
    micro_example: str
    worked_scenario: str
    worked_steps: tuple[str, ...]
    worked_result: str
    practice_instruction: str = "先给出判断，再用一句话说明依据。"
    quick_check_format: str = "先给出二选一判断，再补一句理由。"


@dataclass(frozen=True)
class GoalResolutionRule:
    pattern: str
    node_id: str
    priority: int = 100

    def matches(self, text: str) -> bool:
        return bool(re.search(self.pattern, text, re.I | re.S))


@dataclass(frozen=True)
class DomainKnowledgePack:
    contract_version: str
    domain_id: str
    title: str
    scope: str
    provenance: Mapping[str, Any]
    nodes: Mapping[str, KnowledgeNode]
    edges: tuple[tuple[str, str], ...]
    default_node_id: str
    resource_seeds: Mapping[str, ResourceSeed]
    resolver_signals: Mapping[str, int]
    goal_rules: tuple[GoalResolutionRule, ...] = ()
    review_expectations: tuple[str, ...] = ()
    stable_source_references: tuple[str, ...] = ()
    source_catalog: tuple[Mapping[str, Any], ...] = ()
    claim_source_map: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    memory_context_terms: tuple[str, ...] = ()

    def get_node(self, node_id: str) -> KnowledgeNode:
        return self.nodes[node_id]

    def children(self, node_id: str) -> list[str]:
        return [target for source, target in self.edges if source == node_id]

    def ancestors(self, node_id: str) -> set[str]:
        result: set[str] = set()
        pending = list(self.get_node(node_id).prerequisites)
        while pending:
            candidate = pending.pop()
            if candidate in result:
                continue
            result.add(candidate)
            pending.extend(self.get_node(candidate).prerequisites)
        return result

    def select_goal_node(self, text: str) -> str:
        normalized = str(text or "").lower()
        matching_rules = sorted(
            (rule for rule in self.goal_rules if rule.matches(normalized)),
            key=lambda rule: rule.priority,
            reverse=True,
        )
        if matching_rules:
            return matching_rules[0].node_id
        scores: dict[str, int] = {node_id: 0 for node_id in self.nodes}
        for node_id, node in self.nodes.items():
            scores[node_id] = sum(
                max(1, len(cue)) for cue in node.route_cues
                if cue.lower() in normalized
            )
        ranked = sorted(
            scores,
            key=lambda item: (scores[item], list(self.nodes).index(item)),
            reverse=True,
        )
        return ranked[0] if ranked and scores[ranked[0]] > 0 else self.default_node_id

    def graph_contract(self) -> dict[str, Any]:
        return {
            "contract_version": self.contract_version,
            "domain_id": self.domain_id,
            "title": self.title,
            "scope": self.scope,
            "provenance": dict(self.provenance),
            "stable_source_references": list(self.stable_source_references),
            "provenance_catalog": self.provenance_catalog(),
            "review_expectations": list(self.review_expectations),
            "nodes": [
                {
                    "id": node.id,
                    "title": node.title,
                    "description": node.description,
                    "prerequisites": list(node.prerequisites),
                    "learning_goal": node.learning_goal,
                    "evidence_requirements": list(node.evidence_requirements),
                    "common_misconceptions": list(node.common_misconceptions),
                    "difficulty": node.difficulty,
                    "has_resource_seed": node.id in self.resource_seeds,
                    "claim_source_refs": sorted({
                        source_id
                        for claim_key in node.reviewer_policy.allowed_claim_keys
                        for source_id in self.claim_source_map.get(claim_key, ())
                    }),
                }
                for node in self.nodes.values()
            ],
            "edges": [
                {"source": source, "target": target, "type": "prerequisite"}
                for source, target in self.edges
            ],
        }

    def source_catalog_by_id(self) -> dict[str, Mapping[str, Any]]:
        return {
            str(item.get("source_id")): item
            for item in self.source_catalog if item.get("source_id")
        }

    def provenance_catalog(self) -> dict[str, Any]:
        return {
            "contract_version": "darya_claim_level_knowledge_provenance_v1",
            "domain_id": self.domain_id,
            "sources": [dict(item) for item in self.source_catalog],
            "claim_source_map": {
                key: list(value) for key, value in self.claim_source_map.items()
            },
            "live_search_required_for_mapped_stable_claims": False,
        }

    def validate(self) -> list[str]:
        issues: list[str] = []
        if not self.domain_id or not self.title or not self.scope:
            issues.append("domain_identity_incomplete")
        if self.default_node_id not in self.nodes:
            issues.append("unknown_default_node")
        if len(self.nodes) < 2:
            issues.append("graph_requires_multiple_nodes")
        catalog = self.source_catalog_by_id()
        if not catalog:
            issues.append("missing_provenance_catalog")
        required_source_fields = {
            "source_id", "title", "publisher", "version", "locator",
            "claim_scope", "url", "verification_metadata",
        }
        for source_id, source in catalog.items():
            if not required_source_fields.issubset(source):
                issues.append(f"provenance_source_incomplete:{source_id}")
        for node in self.nodes.values():
            if not node.learning_goal or not node.evidence_requirements:
                issues.append(f"node_contract_incomplete:{node.id}")
            if node.id not in self.resource_seeds:
                issues.append(f"missing_resource_seed:{node.id}")
            for claim_key in node.reviewer_policy.allowed_claim_keys:
                refs = self.claim_source_map.get(claim_key, ())
                if not refs:
                    issues.append(f"unmapped_stable_claim:{node.id}:{claim_key}")
                for source_id in refs:
                    if source_id not in catalog:
                        issues.append(f"unknown_claim_source:{claim_key}:{source_id}")
            for prerequisite in node.prerequisites:
                if prerequisite not in self.nodes:
                    issues.append(f"unknown_prerequisite:{node.id}:{prerequisite}")
        for source, target in self.edges:
            if source not in self.nodes or target not in self.nodes:
                issues.append(f"unknown_edge:{source}:{target}")
            elif source not in self.nodes[target].prerequisites:
                issues.append(f"edge_prerequisite_mismatch:{source}:{target}")
        visiting: set[str] = set()
        visited: set[str] = set()

        def walk(node_id: str) -> None:
            if node_id in visiting:
                issues.append(f"cycle:{node_id}")
                return
            if node_id in visited:
                return
            visiting.add(node_id)
            for prerequisite in self.nodes[node_id].prerequisites:
                walk(prerequisite)
            visiting.remove(node_id)
            visited.add(node_id)

        for key in self.nodes:
            walk(key)
        if not any(len(self.children(key)) >= 2 for key in self.nodes):
            issues.append("graph_requires_branch")
        return issues
