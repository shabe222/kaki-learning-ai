"""Competition-facing projections over the existing DARYA learning kernel.

This module adds no Agent and owns no persistence.  It turns the accepted
Diagnosis/Expert/Review/Decision cycle into small inspectable contracts while
the existing thread/domain learning rows remain the source of truth.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from services.darya_domain_knowledge import DomainKnowledgePack
from services.tide_search_evidence import SearchContext, SearchProvider, build_evidence_bundle


PROFILE_CONTRACT_VERSION = "darya_learner_profile_projection_v1"
CLAIM_CONTRACT_VERSION = "darya_claim_validation_v1"
TRACE_CONTRACT_VERSION = "darya_competition_learning_trace_v1"
FIT_CONTRACT_VERSION = "darya_visual_learner_fit_v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any, limit: int = 600) -> str:
    return str(value or "").strip()[:limit]


def claim_validation_contract() -> dict[str, Any]:
    return {
        "contract_version": CLAIM_CONTRACT_VERSION,
        "fields": [
            "claim", "claim_type", "source_scope", "verification_required",
            "supporting_source_refs", "validation_status", "conflict_status", "confidence",
        ],
        "statuses": [
            "SUPPORTED", "DOMAIN_VERIFIED", "EXTERNAL_VERIFIED", "CONFLICTING", "UNVERIFIED",
        ],
        "stable_domain_rule": "Installed-pack claims use the pack provenance catalog; live search is not required when mapped provenance is sufficient.",
        "external_rule": "Current or external claims require the existing Search/Evidence/Trust path before established wording is allowed.",
    }


def privacy_state_boundary_contract() -> dict[str, Any]:
    return {
        "contract_version": "darya_learning_state_boundaries_v1",
        "stores": {
            "EPHEMERAL_TURN": ["candidate generation", "review working state", "current transport events"],
            "THREAD_LEARNING": ["learner evidence", "route", "resource history", "current learner profile projection"],
            "LONG_TERM": ["explicitly confirmed durable preference", "explicitly confirmed future-use lesson"],
            "ARTIFACT": ["persistent generated work only when the artifact workflow is explicitly used"],
        },
        "guards": {
            "wrong_answer_to_long_term_memory": False,
            "resource_history_to_long_term_memory": False,
            "learner_evidence_cross_domain": False,
            "profile_correction_to_long_term_memory_automatic": False,
            "blackboard_available": False,
        },
    }


def validate_candidate_claims(
    candidate: Mapping[str, Any], *, pack: DomainKnowledgePack,
) -> list[dict[str, Any]]:
    """Validate stable candidate claims against the installed pack catalog."""
    records: list[dict[str, Any]] = []
    for claim_key in candidate.get("claim_keys") or []:
        source_refs = list(pack.claim_source_map.get(str(claim_key), ()))
        known_refs = [item for item in source_refs if item in pack.source_catalog_by_id()]
        records.append({
            "contract_version": CLAIM_CONTRACT_VERSION,
            "claim": str(claim_key),
            "claim_type": "STABLE_DOMAIN_CONCEPT",
            "source_scope": pack.domain_id,
            "verification_required": False,
            "supporting_source_refs": known_refs,
            "validation_status": "DOMAIN_VERIFIED" if known_refs else "UNVERIFIED",
            "conflict_status": "NONE",
            "confidence": 0.94 if known_refs else 0.2,
        })
    for claim in candidate.get("external_claims") or []:
        records.append({
            "contract_version": CLAIM_CONTRACT_VERSION,
            "claim": _text(claim),
            "claim_type": "EXTERNAL_OR_CURRENT",
            "source_scope": "EXTERNAL",
            "verification_required": True,
            "supporting_source_refs": [],
            "validation_status": "UNVERIFIED",
            "conflict_status": "NOT_CHECKED",
            "confidence": 0.15,
        })
    return records


def important_claims_pass(records: Sequence[Mapping[str, Any]]) -> bool:
    return all(
        item.get("validation_status") in {"SUPPORTED", "DOMAIN_VERIFIED", "EXTERNAL_VERIFIED"}
        for item in records
    )


def _profile_claim(
    *, field: str, value: Any, status: str, source: str,
    confidence: float, freshness: str, provenance: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "field": field,
        "value": copy.deepcopy(value),
        "status": status,
        "source": source,
        "confidence": round(max(0.0, min(1.0, float(confidence))), 2),
        "freshness": freshness,
        "provenance": copy.deepcopy(dict(provenance)),
    }


def build_learner_profile_projection(
    *, pack: DomainKnowledgePack, learner_goal: str, message: str,
    node: Mapping[str, Any], diagnosis: Mapping[str, Any], evidence: Mapping[str, Any],
    state_after: Mapping[str, Any], route_after: Mapping[str, Any],
    resource_intent: Mapping[str, Any], previous_resource: Mapping[str, Any] | None,
    memory: Mapping[str, Any], correction_detected: bool,
) -> dict[str, Any]:
    provenance = {"thread_scope": "THREAD_LEARNING", "domain_id": pack.domain_id}
    claims = [
        _profile_claim(
            field="current_domain", value=pack.title, status="CONFIRMED",
            source="installed_domain_resolution", confidence=1.0, freshness="CURRENT_THREAD",
            provenance={**provenance, "domain_contract_version": pack.contract_version},
        ),
        _profile_claim(
            field="current_learning_goal", value=learner_goal, status="OBSERVED",
            source="learner_message", confidence=1.0, freshness="CURRENT_THREAD",
            provenance={**provenance, "message_excerpt": _text(message, 180)},
        ),
        _profile_claim(
            field="currently_relevant_nodes",
            value=[item for item in [route_after.get("current_title"), route_after.get("next_title")] if item],
            status="INFERRED", source="adaptive_knowledge_route", confidence=float(route_after.get("confidence") or .55),
            freshness="CURRENT_CYCLE", provenance={**provenance, "route_contract": route_after.get("contract_version")},
        ),
        _profile_claim(
            field="observed_gap", value=diagnosis.get("likely_gap"),
            status="CONFIRMED" if correction_detected else "INFERRED",
            source="learner_correction" if correction_detected else "diagnosis_from_observable_evidence",
            confidence=1.0 if correction_detected else float(diagnosis.get("confidence") or .38),
            freshness="CURRENT_CYCLE", provenance={**provenance, "evidence_kind": evidence.get("kind")},
        ),
        _profile_claim(
            field="recent_evidence", value=evidence.get("observation"), status="OBSERVED",
            source=str(evidence.get("source") or "learner_message"), confidence=float(evidence.get("confidence") or .38),
            freshness="CURRENT_TURN", provenance={**provenance, "excerpt": evidence.get("excerpt"), "quality": evidence.get("quality")},
        ),
        _profile_claim(
            field="current_scaffold_need", value=state_after.get("support_level"), status="INFERRED",
            source="learning_decision", confidence=float(diagnosis.get("confidence") or .38),
            freshness="CURRENT_CYCLE", provenance={**provenance, "state_revision": state_after.get("revision")},
        ),
    ]
    strengths = list(diagnosis.get("appears_to_understand") or [])
    if strengths:
        claims.append(_profile_claim(
            field="demonstrated_strengths", value=strengths, status="OBSERVED",
            source="learner_evidence", confidence=float(diagnosis.get("confidence") or .55),
            freshness="CURRENT_CYCLE", provenance={**provenance, "evidence_quality": evidence.get("quality")},
        ))
    if state_after.get("current_misconception"):
        claims.append(_profile_claim(
            field="active_misconception", value=state_after.get("current_misconception"),
            status="INFERRED", source="diagnosis_from_observable_evidence",
            confidence=float(diagnosis.get("confidence") or .38), freshness="CURRENT_CYCLE",
            provenance={**provenance, "evidence_quality": evidence.get("quality")},
        ))
    if previous_resource:
        claims.append(_profile_claim(
            field="recent_resource_effectiveness", value=previous_resource.get("effectiveness"),
            status="OBSERVED", source="response_to_previous_resource", confidence=.82,
            freshness="CURRENT_CYCLE", provenance={**provenance, "resource_type": previous_resource.get("resource_type")},
        ))
    confirmed_preferences = [
        item for item in memory.get("selected") or []
        if str(item.get("topic") or "").startswith(("learning_preference:", "response_policy:"))
    ]
    if confirmed_preferences:
        claims.append(_profile_claim(
            field="confirmed_learning_preference",
            value=[item.get("content") for item in confirmed_preferences], status="CONFIRMED",
            source="long_term_memory_user_confirmed", confidence=1.0, freshness="DURABLE_REVALIDATABLE",
            provenance={"scope": "LONG_TERM", "memory_ids": [item.get("id") for item in confirmed_preferences], "used_for_mastery": False},
        ))
    return {
        "contract_version": PROFILE_CONTRACT_VERSION,
        "scope": "THREAD_DOMAIN_LEARNING",
        "domain_id": pack.domain_id,
        "unknown_policy": "Unknown remains unknown; absent claims are not inferred.",
        "claims": claims,
        "confidence": round(float(diagnosis.get("confidence") or .38), 2),
        "uncertainty": diagnosis.get("uncertain"),
        "correctable": True,
        "correction_applied": correction_detected,
        "saved_to_long_term_memory": False,
        "updated_at": _now(),
    }


def build_visual_fit_projection(
    *, profile: Mapping[str, Any], pack: DomainKnowledgePack,
    route_before: Mapping[str, Any], route_after: Mapping[str, Any],
    learner_state: Mapping[str, Any], resource_intent: Mapping[str, Any],
    evidence: Mapping[str, Any], previous_resource: Mapping[str, Any] | None,
) -> dict[str, Any]:
    fit = resource_intent.get("fit") or {}
    movement = str(route_after.get("movement") or "stay")
    route_change = (
        route_after.get("reason")
        if movement in {"backward", "forward"}
        else f"先留在“{route_after.get('next_title') or route_after.get('current_title') or '当前这一格'}”，用这一轮证据再判断。"
    )
    status_before = str(learner_state.get("before", {}).get("mastery_status") or "unknown")
    status_after = str(learner_state.get("after", {}).get("mastery_status") or "unknown")
    confidence = float(profile.get("confidence") or .38)
    confidence_label = "证据较清楚" if confidence >= .8 else "初步判断" if confidence >= .55 else "还需区分"
    return {
        "contract_version": FIT_CONTRACT_VERSION,
        "goal": route_after.get("learner_goal"),
        "target": route_after.get("next_title") or route_after.get("current_title"),
        "gap": fit.get("current_gap"),
        "resource_type": resource_intent.get("resource_type"),
        "why_now": fit.get("why_now"),
        "not_chosen_reason": fit.get("not_chosen_reason"),
        "preference_reason": fit.get("preference_reason"),
        "evidence": evidence.get("observation"),
        "evidence_status": "OBSERVED" if evidence.get("supports_update") else "OBSERVED_INSUFFICIENT",
        "change": {"before": status_before, "now": status_after, "changed": status_before != status_after},
        "route": {
            "movement": movement,
            "before": route_before.get("current_title") or route_before.get("next_title"),
            "now": route_after.get("next_title") or route_after.get("current_title"),
            "reason": route_change,
        },
        "previous_resource": previous_resource.get("resource_type") if previous_resource else None,
        "previous_effectiveness": previous_resource.get("effectiveness") if previous_resource else None,
        "confidence_label": confidence_label,
        "uncertainty": profile.get("uncertainty"),
        "profile_statuses": sorted({item.get("status") for item in profile.get("claims") or [] if item.get("status")}),
        "correction_applied": bool(profile.get("correction_applied")),
        "updated_at": _now(),
    }


def build_competition_trace(
    *, diagnosis: Mapping[str, Any], initial_candidate: Mapping[str, Any],
    final_candidate: Mapping[str, Any], review: Mapping[str, Any],
    decision: Mapping[str, Any], route_after: Mapping[str, Any],
    learner_state: Mapping[str, Any], claim_validation: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "contract_version": TRACE_CONTRACT_VERSION,
        "private_chain_of_thought_exposed": False,
        "analysis_result": {
            "role": "DIAGNOSIS", "summary": diagnosis.get("likely_gap"),
            "confidence": diagnosis.get("confidence"), "evidence": diagnosis.get("evidence"),
            "blocking_prerequisite": diagnosis.get("blocking_prerequisite"),
        },
        "generation_candidate": {
            "role": "EXPERT_GENERATOR", "candidate": copy.deepcopy(dict(initial_candidate)),
            "targets_diagnosed_gap": initial_candidate.get("target_gap") == diagnosis.get("likely_gap"),
        },
        "validation_result": {
            "role": "REVIEW_JUDGE", "status": review.get("status"),
            "verdicts": list(review.get("verdicts") or []),
            "materially_changed": bool(review.get("materially_changed")),
            "claim_validation": copy.deepcopy(list(claim_validation)),
            "final_candidate": copy.deepcopy(dict(final_candidate)),
        },
        "decision_result": {
            "role": "LEARNING_DECISION", "action": decision.get("action"),
            "scaffold_level": decision.get("scaffold_level"),
            "resource_type": final_candidate.get("resource_type"),
            "route_movement": route_after.get("movement"), "next_node": route_after.get("next_title"),
            "consumed": copy.deepcopy(dict(decision.get("consumed") or {})),
        },
        "learner_update": copy.deepcopy(dict(learner_state)),
    }


def build_competition_evidence_view(
    *, cycle: Mapping[str, Any], pack: DomainKnowledgePack,
) -> dict[str, Any]:
    roles = cycle.get("roles") or {}
    resource = cycle.get("resource_orchestration") or {}
    return {
        "contract_version": "darya_competition_evidence_view_v1",
        "audience": "DEVELOPMENT_ACCEPTANCE_ONLY",
        "normal_user_visible": False,
        "participants": [
            roles.get(key, {}).get("role") for key in ("diagnosis", "expert", "review", "decision", "companion")
            if isinstance(roles.get(key), Mapping) and roles.get(key, {}).get("role")
        ],
        "structured_trace": copy.deepcopy(dict(cycle.get("competition_trace") or {})),
        "learner_profile": copy.deepcopy(dict(cycle.get("learner_profile") or {})),
        "learner_evidence": copy.deepcopy(dict((cycle.get("learner_state") or {}).get("update") or {})),
        "route_change": copy.deepcopy(dict((cycle.get("route") or {}).get("after") or {})),
        "resource_change": {
            "previous": (resource.get("previous_resource_result") or {}).get("resource_type"),
            "previous_effectiveness": (resource.get("previous_resource_result") or {}).get("effectiveness"),
            "current": (resource.get("resource") or {}).get("resource_type"),
        },
        "provenance_catalog": pack.provenance_catalog(),
        "privacy_boundaries": privacy_state_boundary_contract(),
    }


async def validate_external_learning_claim(
    claim: str, *, source_url: str | None = None, provider: SearchProvider | None = None,
) -> dict[str, Any]:
    """Run one bounded current-claim case through existing Evidence/Trust."""
    message = f"请核实当前技术事实：{_text(claim, 800)}"
    if source_url:
        message += f" 官方来源 {source_url}"
    bundle = await build_evidence_bundle(SearchContext(message=message), provider=provider)
    if bundle.status == "SUPPORTED" and bundle.sufficient:
        status, confidence, wording = "EXTERNAL_VERIFIED", .9, "当前证据支持这条表述，可在来源范围内使用。"
    elif bundle.status == "CONFLICTING":
        status, confidence, wording = "CONFLICTING", .35, "来源存在冲突，只陈述已一致支持的部分，并保留分歧。"
    else:
        status, confidence, wording = "UNVERIFIED", .25, "现有证据还不足以把这条表述当作已建立事实；保留有用的条件式说明。"
    return {
        "contract_version": CLAIM_CONTRACT_VERSION,
        "claim": _text(claim, 800), "claim_type": "CURRENT_TOOLING",
        "source_scope": "EXTERNAL", "verification_required": True,
        "supporting_source_refs": [source.url for source in bundle.sources],
        "validation_status": status,
        "conflict_status": "CONFLICTING" if bundle.conflicts else "NONE",
        "confidence": confidence,
        "evidence_retrieval": bundle.public_dict(),
        "trust_evaluation": {
            "source_types": [source.source_type for source in bundle.sources],
            "relationship": bundle.source_relationship,
            "sufficient": bundle.sufficient,
            "disposition": bundle.review_disposition,
        },
        "final_wording_policy": wording,
    }
