from __future__ import annotations

import copy
from dataclasses import replace

from .candidate_validator import validate_candidate_assessment
from .compiler import compile_evaluation, stable_id
from .contracts import authority_context_digest, node_contract_set_digest
from .deduplicator import normalize_and_deduplicate
from .evidence_validator import EvaluationContractError, issue, validate_all_preflight
from .execution_digest_validation import (
    ExecutionIdentityDigestValidationSource,
    validate_execution_identity_digest,
)
from .execution import (
    AbortOutcome, CommitOutcome, CommittedEvaluationOperation, EvaluationExecutionPort, EvaluationOperationIdentity,
    ExecutionIdentityDigest, ExecutionIdentityDigestCollision, ExecutionIdentityDomain, ExecutionStorageFailure, ReservationOutcome,
)
from .execution_codes import (
    ABORT_TO_TERMINAL, COMMIT_TO_ISSUE, FIXED_ISSUE_MESSAGES, RESERVATION_TO_ISSUE,
    AbortReasonCode, CommitConflictCode, EvaluationIssueCode, ReservationConflictCode, ReservationStatus,
)
from .fallback import build_fallback_candidate
from .idempotency import evaluation_request_digest, existing_state_digest
from .model_input import build_model_input
from .models import *
from .objective_mapper import objective_allowed_dimensions, required_dimensions
from .operation_binding import EvaluationOperationBinding, compare_bindings
from .parser import CandidateParseError, parse_candidate
from .prompt import build_prompt
from .provider import (
    DiagnosticCorrelationIdFactory,
    DiagnosticFailureCode,
    DiagnosticOperationStage,
    InternalDiagnosticSink,
    ProviderDiagnosticEvent,
    ProviderInvocationFailure,
    ProviderResponseParseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    SecureRandomDiagnosticCorrelationIdFactory,
    classify_provider_exception,
)
from .repair import repair_candidate_once
from .result_authenticity import (
    EvaluationResultAuthenticityPort,
    EvaluationResultEnvelope,
    canonical_result_digest,
)
from .result_validator import validate_evaluation_result


class UnrecoverableEvaluationFallbackError(RuntimeError):
    def __init__(self, preflight_report=None, candidate_report=None, result_report=None, cause=None):
        self.preflight_report = preflight_report
        self.candidate_report = candidate_report
        self.result_report = result_report
        self.cause = cause
        super().__init__("deterministic evaluation fallback failed")


class EvaluationResultContractError(RuntimeError):
    def __init__(self, report):
        self.report = report
        super().__init__("compiled evaluation result invalid")


ProviderParseError = ProviderResponseParseError

_MISSING = object()
_PROVIDER_FALLBACK_EXCEPTIONS = (
    ProviderUnavailableError,
    ProviderTimeoutError,
    ProviderResponseParseError,
)


def _contract_error(code: EvaluationIssueCode, message: str | None = None) -> EvaluationContractError:
    if type(code) is not EvaluationIssueCode:
        code = EvaluationIssueCode.EXECUTION_PORT_CONTRACT_VIOLATION
    fixed = FIXED_ISSUE_MESSAGES[code]
    return EvaluationContractError(
        ValidationReport.from_issues([issue(code, fixed, repairable=False)])
    )


def _execution_port_violation() -> EvaluationContractError:
    return _contract_error(EvaluationIssueCode.EXECUTION_PORT_CONTRACT_VIOLATION)


def _validate_reservation_outcome(value) -> ReservationOutcome:
    if type(value) is not ReservationOutcome:
        raise _execution_port_violation()
    try:
        value.validate_shape()
        if type(value.status) is not ReservationStatus:
            raise TypeError
        if value.conflict_code is not None and type(value.conflict_code) is not ReservationConflictCode:
            raise TypeError
        return value
    except Exception:
        raise _execution_port_violation() from None


def _validate_commit_outcome(value) -> CommitOutcome:
    if type(value) is not CommitOutcome:
        raise _execution_port_violation()
    try:
        value.validate_shape()
        if value.conflict_code is not None and type(value.conflict_code) is not CommitConflictCode:
            raise TypeError
        return value
    except Exception:
        raise _execution_port_violation() from None


def _validate_committed_operation(value) -> CommittedEvaluationOperation:
    if type(value) is not CommittedEvaluationOperation:
        raise _execution_port_violation()
    try:
        if type(value.binding) is not EvaluationOperationBinding:
            raise TypeError
        value.binding.validate_committed()
        if value.operation_binding_digest != value.binding.operation_binding_digest:
            raise ValueError
        request_digest = validate_execution_identity_digest(
            value.request_id_digest,
            expected_domain=ExecutionIdentityDomain.REQUEST_ID,
            source=ExecutionIdentityDigestValidationSource.COMMITTED_OPERATION,
        )
        idempotency_digest = validate_execution_identity_digest(
            value.idempotency_key_digest,
            expected_domain=ExecutionIdentityDomain.IDEMPOTENCY_KEY,
            source=ExecutionIdentityDigestValidationSource.COMMITTED_OPERATION,
        )
        return replace(
            value,
            request_id_digest=request_digest,
            idempotency_key_digest=idempotency_digest,
        )
    except (AttributeError, TypeError, ValueError):
        raise _execution_port_violation() from None


def _evaluation_identity(request: LearningEvaluationRequest) -> str:
    if request.existing_evaluation_summary is not None:
        return request.existing_evaluation_summary.evaluation_id
    return stable_id(
        "eval",
        request.subject_ref,
        request.graph_ref.graph_id,
        request.graph_ref.graph_continuity_key,
    )


def _operation_identity(request: LearningEvaluationRequest) -> EvaluationOperationIdentity:
    return EvaluationOperationIdentity(
        subject_ref=request.subject_ref,
        graph_id=request.graph_ref.graph_id,
        evaluation_id=_evaluation_identity(request),
        request_id=request.request_id,
        idempotency_key=request.idempotency_key,
    )


def _precommit_binding(
    request: LearningEvaluationRequest,
    context: TrustedEvaluationContext,
    request_digest: str,
    state_digest: str,
) -> EvaluationOperationBinding:
    expected_revision = request.expected_existing_revision
    if expected_revision is None:
        raise _contract_error(EvaluationIssueCode.EXPECTED_EXISTING_REVISION_REQUIRED, "expected revision is mandatory")
    authority_digest = authority_context_digest(context)
    contract_digest = node_contract_set_digest(
        request.node_contracts,
        required_dimensions,
        objective_allowed_dimensions,
    )
    return EvaluationOperationBinding(
        subject_ref=request.subject_ref,
        graph_id=request.graph_ref.graph_id,
        graph_continuity_key=request.graph_ref.graph_continuity_key,
        evaluation_id=_evaluation_identity(request),
        request_id=request.request_id,
        idempotency_key=request.idempotency_key,
        evaluation_mode=request.evaluation_mode.value,
        expected_existing_revision=expected_revision,
        result_revision=expected_revision + 1,
        request_digest=request_digest,
        existing_state_digest=state_digest,
        trusted_context_digest=authority_digest,
        authority_digest=authority_digest,
        node_contract_set_digest=contract_digest,
        canonical_result_digest=None,
        result_ref=None,
    )


class _ProviderBoundaryAdapter:
    """Single provider invocation boundary with fixed public failure semantics."""

    def __init__(self, runner: "LearningEvaluatorRunner", stage: DiagnosticOperationStage):
        self._runner = runner
        self._stage = stage

    def generate(self, prompt):
        return self._runner._invoke_provider(prompt, operation_stage=self._stage)


class LearningEvaluatorRunner:
    def __init__(
        self,
        provider,
        fallback_builder=build_fallback_candidate,
        *,
        execution_port: EvaluationExecutionPort | None = None,
        authenticity_port: EvaluationResultAuthenticityPort | None = None,
        diagnostic_sink: InternalDiagnosticSink | None = None,
        diagnostic_correlation_factory: DiagnosticCorrelationIdFactory | None = None,
        trusted_context=None,
        idempotency_port=None,
    ):
        del trusted_context
        self.provider = provider
        self.fallback_builder = fallback_builder
        self.execution_port = execution_port if execution_port is not None else idempotency_port
        self.authenticity_port = authenticity_port
        self.diagnostic_sink = diagnostic_sink
        self.diagnostic_correlation_factory = (
            diagnostic_correlation_factory or SecureRandomDiagnosticCorrelationIdFactory()
        )
        self.idempotency_port = self.execution_port

    def _validate_dependencies(self, trusted_context) -> TrustedEvaluationContext:
        if trusted_context is _MISSING or not isinstance(trusted_context, TrustedEvaluationContext):
            raise _contract_error(EvaluationIssueCode.MISSING_TRUSTED_EVALUATION_CONTEXT,
                "trusted evaluation context must be explicitly supplied for every run",
            )
        if self.execution_port is None:
            raise _contract_error(EvaluationIssueCode.MISSING_EVALUATION_EXECUTION_PORT,
                "a transactional EvaluationExecutionPort is mandatory",
            )
        required_execution_methods = ("reserve", "commit", "abort", "load_committed", "load_envelope")
        if not all(callable(getattr(self.execution_port, name, None)) for name in required_execution_methods):
            raise _contract_error(EvaluationIssueCode.EVALUATION_EXECUTION_PORT_INVALID, "execution port contract incomplete")
        if self.authenticity_port is None:
            raise _contract_error(EvaluationIssueCode.MISSING_RESULT_AUTHENTICITY_PORT,
                "EvaluationResultAuthenticityPort is mandatory",
            )
        if not all(callable(getattr(self.authenticity_port, name, None)) for name in ("issue", "verify")):
            raise _contract_error(EvaluationIssueCode.RESULT_AUTHENTICITY_PORT_INVALID, "authenticity port contract incomplete")
        return trusted_context

    def _emit_diagnostic(
        self,
        *,
        exception_code,
        operation_stage: DiagnosticOperationStage,
    ) -> None:
        sink = self.diagnostic_sink
        if sink is None:
            return
        try:
            correlation_id = self.diagnostic_correlation_factory.create()
            event = ProviderDiagnosticEvent(
                exception_code=exception_code,
                operation_stage=operation_stage,
                correlation_id=correlation_id,
                failure_code=DiagnosticFailureCode.PROVIDER_INVOCATION_FAILED,
            )
            sink.emit(event)
        except Exception:
            # Correlation generation, event validation, or Sink failure cannot alter
            # the fixed public provider failure and cannot start recursive diagnostics.
            return

    def _invoke_provider(
        self,
        prompt,
        *,
        operation_stage: DiagnosticOperationStage,
    ):
        exception_code = None
        try:
            return self.provider.generate(prompt)
        except _PROVIDER_FALLBACK_EXCEPTIONS:
            raise
        except (KeyboardInterrupt, SystemExit, GeneratorExit):
            raise
        except Exception as exc:
            # Convert only the exception type through a closed exact-class registry.
            # The exception object and its message never leave this boundary.
            exception_code = classify_provider_exception(exc)
        assert exception_code is not None
        self._emit_diagnostic(
            exception_code=exception_code,
            operation_stage=operation_stage,
        )
        raise ProviderInvocationFailure() from None

    def _restore_committed(
        self,
        committed,
        request: LearningEvaluationRequest,
        context: TrustedEvaluationContext,
        request_digest: str,
        state_digest: str,
    ) -> EvaluationResultEnvelope:
        committed = _validate_committed_operation(committed)
        current_precommit = _precommit_binding(request, context, request_digest, state_digest)
        committed_binding = committed.binding
        committed_binding.validate_committed()
        if committed.operation_binding_digest != committed_binding.operation_binding_digest:
            raise _contract_error(EvaluationIssueCode.RESERVATION_RESULT_BINDING_MISMATCH,
                "committed operation binding digest is invalid",
            )
        expected_precommit = EvaluationOperationBinding(
            **{
                **committed_binding.canonical_payload(),
                "canonical_result_digest": None,
                "result_ref": None,
            }
        )
        if compare_bindings(current_precommit, expected_precommit):
            raise _contract_error(EvaluationIssueCode.RESERVATION_RESULT_BINDING_MISMATCH,
                "current request does not match committed operation binding",
            )

        try:
            envelope = self.execution_port.load_envelope(committed_binding.result_ref)
        except ExecutionIdentityDigestCollision:
            raise _contract_error(EvaluationIssueCode.EXECUTION_IDENTITY_DIGEST_COLLISION) from None
        except ExecutionStorageFailure:
            raise _contract_error(EvaluationIssueCode.EVALUATION_EXECUTION_STORAGE_FAILURE) from None
        except EvaluationContractError:
            raise
        except Exception:
            raise _execution_port_violation() from None
        if envelope is None:
            raise _contract_error(EvaluationIssueCode.IDEMPOTENT_REPLAY_RECORD_INCOMPLETE,
                "committed operation has no trusted envelope",
            )
        if type(envelope) is not EvaluationResultEnvelope:
            raise _execution_port_violation()
        try:
            verified = self.authenticity_port.verify(envelope, context)
        except (ValueError, TypeError):
            raise _contract_error(EvaluationIssueCode.RESULT_AUTHENTICITY_MISMATCH,
                "committed result envelope failed verification",
            ) from None

        if verified.operation_binding != committed_binding:
            raise _contract_error(EvaluationIssueCode.RESERVATION_RESULT_BINDING_MISMATCH,
                "envelope binding does not match committed operation",
            )
        if envelope.operation_binding_digest != committed.operation_binding_digest:
            raise _contract_error(EvaluationIssueCode.RESERVATION_RESULT_BINDING_MISMATCH,
                "envelope binding digest does not match committed operation",
            )
        if canonical_result_digest(verified.result) != committed_binding.canonical_result_digest:
            raise _contract_error(EvaluationIssueCode.RESULT_DIGEST_MISMATCH,
                "committed result digest does not match envelope",
            )
        if verified.validation_request.request_id != request.request_id:
            raise _contract_error(EvaluationIssueCode.RESERVATION_RESULT_BINDING_MISMATCH, "request identity mismatch")
        if verified.validation_request.idempotency_key != request.idempotency_key:
            raise _contract_error(EvaluationIssueCode.RESERVATION_RESULT_BINDING_MISMATCH, "idempotency identity mismatch")
        report = validate_evaluation_result(verified.result, request, context)
        if not report.valid:
            raise EvaluationResultContractError(report)
        return copy.deepcopy(envelope)

    def _fallback_candidate(
        self,
        request: LearningEvaluationRequest,
        evidence: list[NormalizedEvidence],
        preflight: ValidationReport,
        prior_candidate_report: ValidationReport | None,
        cause: Exception | None = None,
    ) -> EvaluationCandidate:
        try:
            fallback = self.fallback_builder(request, evidence)
            report = validate_candidate_assessment(request, fallback, evidence)
            if not report.valid:
                raise EvaluationContractError(report)
            return fallback
        except (EvaluationContractError, RuntimeError, ValueError, TypeError) as failure:
            report = failure.report if isinstance(failure, EvaluationContractError) else None
            raise UnrecoverableEvaluationFallbackError(
                preflight, prior_candidate_report, report, cause or failure
            ) from failure

    def run(
        self,
        request: LearningEvaluationRequest,
        trusted_context=_MISSING,
        trusted_correction_context: TrustedCorrectionContext | None = None,
    ) -> EvaluationResultEnvelope:
        context = self._validate_dependencies(trusted_context)

        preflight = validate_all_preflight(request, context, trusted_correction_context)
        if not preflight.valid:
            raise EvaluationContractError(preflight)

        try:
            request_digest = evaluation_request_digest(request, context, trusted_correction_context)
        except (ValueError, TypeError) as exc:
            raise _contract_error(EvaluationIssueCode.EVALUATION_REQUEST_DIGEST_INVALID) from None
        state_digest = existing_state_digest(request.existing_evaluation_summary)
        precommit_binding = _precommit_binding(request, context, request_digest, state_digest)
        try:
            reservation = _validate_reservation_outcome(self.execution_port.reserve(precommit_binding))
        except ExecutionIdentityDigestCollision:
            raise _contract_error(EvaluationIssueCode.EXECUTION_IDENTITY_DIGEST_COLLISION) from None
        except ExecutionStorageFailure:
            raise _contract_error(EvaluationIssueCode.EVALUATION_EXECUTION_STORAGE_FAILURE) from None
        except EvaluationContractError:
            raise
        except Exception:
            raise _execution_port_violation() from None
        if reservation.status is ReservationStatus.IDEMPOTENT_REPLAY:
            if reservation.committed is None:
                raise _contract_error(EvaluationIssueCode.IDEMPOTENT_REPLAY_RECORD_INCOMPLETE, "replay has no committed operation")
            return self._restore_committed(
                reservation.committed,
                request,
                context,
                request_digest,
                state_digest,
            )
        if reservation.status is ReservationStatus.IDEMPOTENCY_CONFLICT:
            assert reservation.conflict_code is not None
            raise _contract_error(RESERVATION_TO_ISSUE[reservation.conflict_code])
        if reservation.status is ReservationStatus.REVISION_CONFLICT:
            assert reservation.conflict_code is not None
            raise _contract_error(RESERVATION_TO_ISSUE[reservation.conflict_code])
        if reservation.status is ReservationStatus.IN_PROGRESS:
            assert reservation.conflict_code is not None
            raise _contract_error(RESERVATION_TO_ISSUE[reservation.conflict_code])
        if reservation.status is not ReservationStatus.RESERVED or reservation.reservation_token is None:
            raise _execution_port_violation()
        token = reservation.reservation_token

        operation_committed = False
        commit_boundary_entered = False
        try:
            evidence, dedup_issues = normalize_and_deduplicate(request, context)
            required = {n.node_id: required_dimensions(n.node_type) for n in request.node_contracts}
            model_input = build_model_input(request, evidence, required)
            candidate: EvaluationCandidate | None = None
            candidate_report: ValidationReport | None = None
            provider_output = None
            fallback_allowed = False
            initial_provider = _ProviderBoundaryAdapter(self, DiagnosticOperationStage.INITIAL)
            repair_provider = _ProviderBoundaryAdapter(self, DiagnosticOperationStage.REPAIR)

            try:
                provider_output = initial_provider.generate(build_prompt(model_input))
            except _PROVIDER_FALLBACK_EXCEPTIONS:
                fallback_allowed = True

            if not fallback_allowed:
                try:
                    candidate = parse_candidate(provider_output)
                except CandidateParseError as parse_exc:
                    parse_report = ValidationReport.from_issues(
                        [
                            ValidationIssue(
                                code=EvaluationIssueCode.MODEL_PARSE_ERROR,
                                severity=Severity.ERROR,
                                message=str(parse_exc),
                                repairable=True,
                            )
                        ]
                    )
                    try:
                        candidate = repair_candidate_once(
                            repair_provider, model_input, provider_output, parse_report
                        )
                    except (CandidateParseError, ProviderUnavailableError, ProviderTimeoutError):
                        fallback_allowed = True

            if candidate is not None:
                candidate_report = validate_candidate_assessment(request, candidate, evidence)
                if not candidate_report.valid:
                    errors = [i for i in candidate_report.issues if i.severity == Severity.ERROR]
                    if any(not i.repairable for i in errors):
                        raise EvaluationContractError(candidate_report)
                    try:
                        candidate = repair_candidate_once(
                            repair_provider, model_input, provider_output, candidate_report
                        )
                    except (CandidateParseError, ProviderUnavailableError, ProviderTimeoutError):
                        candidate = None
                        fallback_allowed = True
                    if candidate is not None:
                        candidate_report = validate_candidate_assessment(request, candidate, evidence)
                        if not candidate_report.valid:
                            if any(
                                not i.repairable
                                for i in candidate_report.issues
                                if i.severity == Severity.ERROR
                            ):
                                raise EvaluationContractError(candidate_report)
                            candidate = None
                            fallback_allowed = True

            if candidate is None:
                if not fallback_allowed:
                    raise _contract_error(EvaluationIssueCode.CANDIDATE_UNAVAILABLE,
                        "candidate generation failed outside the fallback allowlist",
                    )
                candidate = self._fallback_candidate(request, evidence, preflight, candidate_report)

            result = compile_evaluation(
                request,
                evidence,
                candidate,
                dedup_issues,
                context,
                trusted_correction_context,
            )
            result.processed_requests = []
            result_report = validate_evaluation_result(result, request, context)
            if not result_report.valid:
                if candidate.fallback_used:
                    raise UnrecoverableEvaluationFallbackError(
                        preflight,
                        candidate_report,
                        result_report,
                        EvaluationResultContractError(result_report),
                    )
                raise EvaluationResultContractError(result_report)
            result.validation_report = result_report

            result_digest = canonical_result_digest(result)
            committed_binding = precommit_binding.with_result(
                canonical_result_digest=result_digest
            )
            envelope = self.authenticity_port.issue(
                result,
                context,
                request,
                committed_binding,
            )
            verified = self.authenticity_port.verify(envelope, context)
            if verified.operation_binding != committed_binding:
                raise _contract_error(EvaluationIssueCode.RESERVATION_RESULT_BINDING_MISMATCH,
                    "authenticity adapter changed operation binding",
                )
            final_report = validate_evaluation_result(
                verified.result, verified.validation_request, context
            )
            if not final_report.valid:
                raise EvaluationResultContractError(final_report)

            try:
                # After entering the commit boundary the caller must not invent a
                # terminal state. A storage/transport/Port failure may leave the
                # durable truth ACTIVE or COMMITTED; repository truth wins.
                commit_boundary_entered = True
                committed = _validate_commit_outcome(
                    self.execution_port.commit(token, committed_binding, envelope)
                )
            except ExecutionIdentityDigestCollision:
                raise _contract_error(EvaluationIssueCode.EXECUTION_IDENTITY_DIGEST_COLLISION) from None
            except ExecutionStorageFailure:
                raise _contract_error(EvaluationIssueCode.EVALUATION_EXECUTION_STORAGE_FAILURE) from None
            except EvaluationContractError:
                raise
            except Exception:
                raise _execution_port_violation() from None
            if not committed.committed:
                assert committed.conflict_code is not None
                raise _contract_error(COMMIT_TO_ISSUE[committed.conflict_code])
            operation_committed = True
            return envelope
        finally:
            if not operation_committed and not commit_boundary_entered:
                try:
                    self.execution_port.abort(token, AbortReasonCode.RUNNER_CONTRACT_FAILURE)
                except (KeyboardInterrupt, SystemExit, GeneratorExit):
                    raise
                except Exception:
                    # Abort storage/Port failure cannot replace the original controlled
                    # failure or leak an implementation message. Durable ACTIVE truth wins.
                    pass
