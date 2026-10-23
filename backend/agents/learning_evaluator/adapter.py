from __future__ import annotations

from .models import LearningEvaluationRequest

FORBIDDEN_PUBLIC_AUTHORITY_FIELDS = {
    "evidence_authority_policy",
    "maximum_trust_level",
    "can_assert_verified",
    "can_assert_independence",
    "authority_contract_version",
    "adapter_contract_versions",
    "authenticated_subject_ref",
    "corrected_by_ref",
    "corrected_at",
    "authenticated_actor_ref",
    "actor_role",
    "allowed_correction_reasons",
    "correction_source",
    "correction_contract_version",
    "correction_event_ids",
    "supersedes_correction_event_ids",
}


def _find_forbidden(value, path=""):
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if key in FORBIDDEN_PUBLIC_AUTHORITY_FIELDS:
                found.append(child_path)
            found.extend(_find_forbidden(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_find_forbidden(child, f"{path}[{index}]"))
    return found


def adapt_request(payload: dict) -> LearningEvaluationRequest:
    forbidden = _find_forbidden(payload)
    if forbidden:
        raise ValueError(f"PUBLIC_AUTHORITY_POLICY_FORBIDDEN: {', '.join(forbidden)}")
    mode = payload.get("evaluation_mode")
    if "expected_existing_revision" not in payload:
        raise ValueError("EXPECTED_EXISTING_REVISION_REQUIRED")
    if mode == "INITIAL" and payload.get("expected_existing_revision") != 0:
        raise ValueError("INITIAL_EXPECTED_REVISION_MUST_BE_ZERO")
    return LearningEvaluationRequest.model_validate(payload)
