from __future__ import annotations

from .enums import EvidenceType, TrustLevel
from .models import EvidenceAuthorityPolicy, EvidenceSourceAuthority, LearningEvidenceRecord, LiteralOrigin, TrustedEvaluationContext

TRUST_ORDER = {
    TrustLevel.INVALID: 0,
    TrustLevel.INFERRED: 1,
    TrustLevel.SELF_REPORTED: 2,
    TrustLevel.STRUCTURED_HUMAN: 3,
    TrustLevel.SYSTEM_OBSERVED: 4,
    TrustLevel.VERIFIED: 5,
}
SUPPORTED_TRUSTED_CONTEXT_VERSIONS = {"trusted-evaluation-context-v3"}


def empty_authority_policy() -> EvidenceAuthorityPolicy:
    return EvidenceAuthorityPolicy(authorities=[], unknown_source_maximum_trust=TrustLevel.INFERRED)


def find_authority(policy: EvidenceAuthorityPolicy, source_system: str) -> EvidenceSourceAuthority | None:
    return next((a for a in policy.authorities if a.source_system == source_system), None)


def cap_trust(requested: TrustLevel, maximum: TrustLevel) -> TrustLevel:
    return requested if TRUST_ORDER[requested] <= TRUST_ORDER[maximum] else maximum


def resolve_effective_authority(record: LearningEvidenceRecord, context: TrustedEvaluationContext) -> tuple[TrustLevel, bool, bool]:
    if not isinstance(context, TrustedEvaluationContext):
        raise ValueError("MISSING_TRUSTED_EVALUATION_CONTEXT")
    policy = context.evidence_authority_policy
    authority = find_authority(policy, record.source_system)
    if authority is None:
        raise ValueError("UNKNOWN_AUTHORITY_SOURCE")
    adapter_version = context.adapter_contract_versions.get(record.source_system)
    version_valid = adapter_version == authority.authority_contract_version
    allowed = bool(version_valid and record.evidence_type in authority.allowed_evidence_types)
    maximum = authority.maximum_trust_level if allowed else TrustLevel.INVALID
    effective_trust = cap_trust(record.provenance.trust_level, maximum)
    effective_verified = bool(allowed and authority.can_assert_verified and record.verified)
    effective_independent = bool(allowed and authority.can_assert_independence and record.independent)
    if not effective_verified and effective_trust == TrustLevel.VERIFIED:
        effective_trust = TrustLevel.SYSTEM_OBSERVED if allowed else TrustLevel.INVALID
    if record.evidence_type == EvidenceType.SELF_REPORT:
        effective_trust = TrustLevel.SELF_REPORTED
        effective_verified = False
        effective_independent = False
    if record.evidence_type in {EvidenceType.RESOURCE_OPENED, EvidenceType.RESOURCE_COMPLETED}:
        effective_independent = False
        if effective_trust == TrustLevel.VERIFIED:
            effective_trust = TrustLevel.SYSTEM_OBSERVED
    if record.evidence_type == EvidenceType.MANUAL_TEACHER_EVIDENCE:
        effective_trust = TrustLevel.STRUCTURED_HUMAN if allowed else TrustLevel.INFERRED
        effective_verified = bool(allowed and authority.can_assert_verified and record.verified)
    if record.provenance.content_origin == LiteralOrigin.AI_GENERATED:
        effective_trust = TrustLevel.INFERRED
        effective_verified = False
        effective_independent = False
    return effective_trust, effective_verified, effective_independent
