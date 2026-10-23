from __future__ import annotations

import re
import secrets
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class ProviderUnavailableError(RuntimeError):
    pass


class ProviderTimeoutError(RuntimeError):
    pass


class ProviderResponseParseError(RuntimeError):
    pass


class ProviderInvocationFailure(RuntimeError):
    """Fixed public error for non-allowlisted provider boundary failures."""

    code = "PROVIDER_INVOCATION_FAILED"
    public_message = "Provider invocation failed."

    def __init__(self):
        super().__init__(self.public_message)


class DiagnosticExceptionCode(str, Enum):
    PROVIDER_FILESYSTEM_EXCEPTION = "PROVIDER_FILESYSTEM_EXCEPTION"
    PROVIDER_PERMISSION_EXCEPTION = "PROVIDER_PERMISSION_EXCEPTION"
    PROVIDER_CONNECTION_EXCEPTION = "PROVIDER_CONNECTION_EXCEPTION"
    PROVIDER_PLATFORM_TIMEOUT_EXCEPTION = "PROVIDER_PLATFORM_TIMEOUT_EXCEPTION"
    PROVIDER_RUNTIME_EXCEPTION = "PROVIDER_RUNTIME_EXCEPTION"
    PROVIDER_VALUE_EXCEPTION = "PROVIDER_VALUE_EXCEPTION"
    PROVIDER_TYPE_EXCEPTION = "PROVIDER_TYPE_EXCEPTION"
    UNEXPECTED_PROVIDER_EXCEPTION = "UNEXPECTED_PROVIDER_EXCEPTION"


class DiagnosticOperationStage(str, Enum):
    INITIAL = "initial"
    REPAIR = "repair"


class DiagnosticFailureCode(str, Enum):
    PROVIDER_INVOCATION_FAILED = "PROVIDER_INVOCATION_FAILED"


_SAFE_CORRELATION_RE = re.compile(r"^diag_[0-9a-f]{32}$")


class ProviderDiagnosticEvent(BaseModel):
    """Closed, immutable diagnostic event. It cannot carry free text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    exception_code: DiagnosticExceptionCode
    operation_stage: DiagnosticOperationStage
    correlation_id: str = Field(min_length=37, max_length=37, pattern=r"^diag_[0-9a-f]{32}$")
    failure_code: DiagnosticFailureCode = DiagnosticFailureCode.PROVIDER_INVOCATION_FAILED

    def model_post_init(self, __context: Any) -> None:
        # Keep the invariant explicit even if a future Pydantic configuration changes.
        if not _SAFE_CORRELATION_RE.fullmatch(self.correlation_id):
            raise ValueError("INVALID_DIAGNOSTIC_CORRELATION_ID")


# Backward name retained only as an alias to the closed event model.
InternalDiagnosticEvent = ProviderDiagnosticEvent


@runtime_checkable
class DiagnosticCorrelationIdFactory(Protocol):
    def create(self) -> str: ...


class SecureRandomDiagnosticCorrelationIdFactory:
    """Default boundary-owned opaque correlation identifier generator."""

    def create(self) -> str:
        return "diag_" + secrets.token_hex(16)


@runtime_checkable
class InternalDiagnosticSink(Protocol):
    def emit(self, event: ProviderDiagnosticEvent) -> None: ...


def classify_provider_exception(exc: Exception) -> DiagnosticExceptionCode:
    """Map only exact built-in classes; dynamic subclasses are untrusted."""

    kind = type(exc)
    exact = {
        OSError: DiagnosticExceptionCode.PROVIDER_FILESYSTEM_EXCEPTION,
        FileNotFoundError: DiagnosticExceptionCode.PROVIDER_FILESYSTEM_EXCEPTION,
        PermissionError: DiagnosticExceptionCode.PROVIDER_PERMISSION_EXCEPTION,
        ConnectionError: DiagnosticExceptionCode.PROVIDER_CONNECTION_EXCEPTION,
        TimeoutError: DiagnosticExceptionCode.PROVIDER_PLATFORM_TIMEOUT_EXCEPTION,
        RuntimeError: DiagnosticExceptionCode.PROVIDER_RUNTIME_EXCEPTION,
        ValueError: DiagnosticExceptionCode.PROVIDER_VALUE_EXCEPTION,
        TypeError: DiagnosticExceptionCode.PROVIDER_TYPE_EXCEPTION,
    }
    return exact.get(kind, DiagnosticExceptionCode.UNEXPECTED_PROVIDER_EXCEPTION)


class EvaluationProvider(Protocol):
    def generate(self, prompt: str) -> str | dict[str, Any]: ...


class StaticProvider:
    def __init__(self, response):
        self.response = response
        self.calls = 0
        self.prompts = []

    def generate(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        return self.response


class FailingProvider:
    def __init__(self, error=None):
        self.error = error if error is not None else ProviderUnavailableError("provider unavailable")
        self.calls = 0
        self.prompts = []

    def generate(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        raise self.error
