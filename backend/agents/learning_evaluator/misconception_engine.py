from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from .models import *


def misconception_severity(evidence: list[NormalizedEvidence], evaluated_at: datetime | None = None) -> MisconceptionSeverity:
    evaluated_at = evaluated_at or datetime.now(timezone.utc)
    activities = {e.canonical_activity_key for e in evidence if e.independent}
    trusted = [e for e in evidence if e.trust_level in {TrustLevel.VERIFIED, TrustLevel.SYSTEM_OBSERVED, TrustLevel.STRUCTURED_HUMAN}]
    recent = [e for e in evidence if (evaluated_at - e.occurred_at).days <= 30]
    if len(activities) >= 3 and len(trusted) >= 2 and len(recent) >= 1:
        return MisconceptionSeverity.HIGH
    if len(activities) >= 2 and trusted:
        return MisconceptionSeverity.MODERATE
    return MisconceptionSeverity.LOW


def deterministic_misconceptions(node_id: str, evidence: list[NormalizedEvidence], evaluated_at: datetime | None = None) -> list[Misconception]:
    grouped: dict[str, list[NormalizedEvidence]] = defaultdict(list)
    for item in evidence:
        if item.state == EvidenceState.VALID and (item.normalized_score < 0.6 or item.error_codes or item.negative_signal_codes):
            for code in list(dict.fromkeys(item.error_codes + item.misconception_pattern_codes)):
                grouped[code].append(item)
    out: list[Misconception] = []
    for code, items in grouped.items():
        negative = [e for e in items if e.normalized_score < 0.6 or e.error_codes or e.negative_signal_codes]
        activities = {e.canonical_activity_key for e in negative if e.independent}
        trusted = any(e.trust_level in {TrustLevel.VERIFIED, TrustLevel.SYSTEM_OBSERVED, TrustLevel.STRUCTURED_HUMAN} for e in negative)
        if len(activities) >= 2:
            confidence = min(0.8, 0.35 + 0.12 * len(activities) + (0.1 if trusted else 0))
            out.append(
                Misconception(
                    code=code,
                    node_id=node_id,
                    summary=f"重复出现的错误模式：{code}",
                    evidence_ids=list(dict.fromkeys(e.evidence_id for e in negative)),
                    confidence=confidence,
                    severity=misconception_severity(negative, evaluated_at),
                )
            )
    return out
