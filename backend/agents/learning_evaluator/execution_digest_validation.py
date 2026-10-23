from __future__ import annotations

from enum import Enum
from typing import Any, FrozenSet

from pydantic import ValidationError

from .execution import (
    ExecutionIdentityDigest,
    ExecutionIdentityDomain,
    SUPPORTED_EXECUTION_IDENTITY_ALGORITHMS,
)


class ExecutionIdentityDigestValidationSource(str, Enum):
    PORT_OUTPUT = "PORT_OUTPUT"
    ACTIVE_STATE_IMPORT = "ACTIVE_STATE_IMPORT"
    TERMINAL_STATE_IMPORT = "TERMINAL_STATE_IMPORT"
    COMMITTED_OPERATION = "COMMITTED_OPERATION"
    LEGACY_MIGRATION = "LEGACY_MIGRATION"


_DIGEST_FIELDS = frozenset(
    {"domain", "algorithm_version", "lookup_digest", "collision_guard_digest"}
)


def _invalid_output() -> TypeError:
    return TypeError("EXECUTION_IDENTITY_DIGEST_OUTPUT_INVALID")


def validate_execution_identity_digest(
    value: Any,
    *,
    expected_domain: ExecutionIdentityDomain,
    raw_value: str | None = None,
    allowed_algorithm_versions: FrozenSet[str] = SUPPORTED_EXECUTION_IDENTITY_ALGORITHMS,
    source: ExecutionIdentityDigestValidationSource,
) -> ExecutionIdentityDigest:
    """Re-establish digest facts at every trust boundary.

    The input object's construction history is never trusted. Fields are copied
    into a plain mapping and a new strict model is constructed. The original
    object is never returned.
    """

    if type(expected_domain) is not ExecutionIdentityDomain:
        raise _invalid_output()
    if type(source) is not ExecutionIdentityDigestValidationSource:
        raise _invalid_output()
    if type(allowed_algorithm_versions) is not frozenset or not allowed_algorithm_versions:
        raise _invalid_output()
    if any(type(item) is not str for item in allowed_algorithm_versions):
        raise _invalid_output()

    try:
        if isinstance(value, dict):
            if set(value) != _DIGEST_FIELDS:
                raise _invalid_output()
            raw_fields = {name: value[name] for name in _DIGEST_FIELDS}
        else:
            raw_fields = {name: getattr(value, name) for name in _DIGEST_FIELDS}

        domain_value = raw_fields["domain"]
        if domain_value is expected_domain:
            canonical_domain = expected_domain
        elif type(domain_value) is str and domain_value == expected_domain.value:
            canonical_domain = expected_domain
        else:
            raise _invalid_output()

        payload = {
            "domain": canonical_domain,
            "algorithm_version": raw_fields["algorithm_version"],
            "lookup_digest": raw_fields["lookup_digest"],
            "collision_guard_digest": raw_fields["collision_guard_digest"],
        }
        canonical = ExecutionIdentityDigest.model_validate(payload, strict=True)
    except TypeError:
        raise
    except (AttributeError, KeyError, ValidationError, ValueError):
        raise _invalid_output() from None

    if canonical.algorithm_version not in allowed_algorithm_versions:
        raise TypeError("EXECUTION_IDENTITY_DIGEST_VERSION_INVALID")

    if raw_value is not None:
        if type(raw_value) is not str or not raw_value:
            raise _invalid_output()
        if (
            raw_value == canonical.lookup_digest
            or raw_value == canonical.collision_guard_digest
            or raw_value in canonical.lookup_digest
            or raw_value in canonical.collision_guard_digest
        ):
            raise TypeError("EXECUTION_IDENTITY_DIGEST_RAW_VALUE_LEAK")

    # model_validate above created this object from a plain mapping. Returning a
    # second fresh object makes the non-aliasing contract explicit.
    return ExecutionIdentityDigest.model_validate(
        canonical.model_dump(mode="python"),
        strict=True,
    )
