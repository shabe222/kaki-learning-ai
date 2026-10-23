from __future__ import annotations

import hashlib
import json
from typing import Any

from .adapter import adapt_request
from .compiler import CandidateContractError, compile_blueprint
from .enums import GenerationMode, Severity
from .fallback import build_fallback_candidate
from .models import (
    CandidateBlueprint,
    LearningGraphRequest,
    OrchestrationResult,
    RunMetadata,
    ValidationReport,
)
from .parser import parse_candidate
from .prompt import build_generation_prompt
from .provider import GraphProvider
from .repair import build_repair_prompt
from .serializer import build_public_view
from .validators import (
    validate_blueprint,
    validate_candidate_contract,
    validate_existing_graph_contract,
    validate_source_contract,
)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _schema_hash() -> str:
    return _hash(json.dumps(CandidateBlueprint.model_json_schema(), sort_keys=True, ensure_ascii=False))


def _review_required(report: ValidationReport) -> bool:
    return any(item.severity == Severity.REVIEW_REQUIRED for item in report.issues)


def _error_issues(report: ValidationReport):
    return [item for item in report.issues if item.severity == Severity.ERROR]


def _has_nonrepairable_error(report: ValidationReport) -> bool:
    return any(not item.repairable for item in _error_issues(report))


class SourceContractError(ValueError):
    def __init__(self, report: ValidationReport):
        self.report = report
        codes = [item.code for item in _error_issues(report)]
        super().__init__(f"source contract rejected: {codes}")


class ExistingGraphContractError(ValueError):
    def __init__(self, report: ValidationReport):
        self.report = report
        codes = [item.code for item in _error_issues(report)]
        super().__init__(f"existing graph contract rejected: {codes}")


class BlueprintContractError(ValueError):
    def __init__(self, report: ValidationReport):
        self.report = report
        codes = [item.code for item in _error_issues(report)]
        super().__init__(f"blueprint contract rejected: {codes}")


class UnrecoverableFallbackError(RuntimeError):
    def __init__(
        self,
        *,
        source_contract_report: ValidationReport,
        existing_graph_report: ValidationReport,
        candidate_report: ValidationReport | None,
        blueprint_report: ValidationReport | None,
        fallback_report: ValidationReport | None,
        cause: Exception | None = None,
    ):
        self.source_contract_report = source_contract_report
        self.existing_graph_report = existing_graph_report
        self.candidate_report = candidate_report
        self.blueprint_report = blueprint_report
        self.fallback_report = fallback_report
        self.cause = cause
        codes = []
        for report in (candidate_report, blueprint_report, fallback_report):
            if report:
                codes.extend(item.code for item in _error_issues(report))
        message = f"fallback could not produce a valid blueprint: {sorted(set(codes))}"
        if cause:
            message += f" ({type(cause).__name__}: {str(cause)[:200]})"
        super().__init__(message)


class LearningGraphOrchestrator:
    def __init__(self, provider: GraphProvider):
        self.provider = provider

    def _fallback(
        self,
        request: LearningGraphRequest,
        source_report: ValidationReport,
        existing_report: ValidationReport,
        *,
        prior_candidate_report: ValidationReport | None,
        prior_blueprint_report: ValidationReport | None,
        cause: Exception | None = None,
    ):
        """Build and validate fallback exactly once; never return an invalid result."""
        fallback_candidate_report = None
        fallback_blueprint_report = None
        try:
            fallback_candidate = build_fallback_candidate(request)
            fallback_candidate_report = validate_candidate_contract(request, fallback_candidate)
            if not fallback_candidate_report.valid:
                raise UnrecoverableFallbackError(
                    source_contract_report=source_report,
                    existing_graph_report=existing_report,
                    candidate_report=prior_candidate_report,
                    blueprint_report=prior_blueprint_report,
                    fallback_report=fallback_candidate_report,
                    cause=cause,
                )
            graph = compile_blueprint(request, fallback_candidate, fallback_used=True)
            fallback_blueprint_report = validate_blueprint(request, graph)
            if not fallback_blueprint_report.valid:
                raise UnrecoverableFallbackError(
                    source_contract_report=source_report,
                    existing_graph_report=existing_report,
                    candidate_report=prior_candidate_report,
                    blueprint_report=prior_blueprint_report,
                    fallback_report=fallback_blueprint_report,
                    cause=cause,
                )
            return graph, fallback_blueprint_report
        except UnrecoverableFallbackError:
            raise
        except Exception as error:
            raise UnrecoverableFallbackError(
                source_contract_report=source_report,
                existing_graph_report=existing_report,
                candidate_report=prior_candidate_report,
                blueprint_report=prior_blueprint_report,
                fallback_report=fallback_blueprint_report or fallback_candidate_report,
                cause=error,
            ) from error

    async def run(self, request_or_payload: LearningGraphRequest | dict[str, Any]) -> OrchestrationResult:
        request = request_or_payload if isinstance(request_or_payload, LearningGraphRequest) else adapt_request(request_or_payload)

        source_report = validate_source_contract(request)
        if not source_report.valid:
            raise SourceContractError(source_report)
        existing_report = validate_existing_graph_contract(request)
        if not existing_report.valid:
            raise ExistingGraphContractError(existing_report)

        prompt = build_generation_prompt(request)
        raw = None
        attempts = 0
        repair_attempted = False
        fallback_reason = None
        mode = GenerationMode.MODEL
        candidate_report: ValidationReport | None = None
        blueprint_report: ValidationReport | None = None

        try:
            raw = await self.provider.generate(prompt)
            attempts += 1
            candidate = parse_candidate(raw)
        except Exception as error:
            fallback_reason = f"{type(error).__name__}: {str(error)[:200]}"
            graph, report = self._fallback(
                request,
                source_report,
                existing_report,
                prior_candidate_report=None,
                prior_blueprint_report=None,
                cause=error,
            )
            mode = GenerationMode.FALLBACK
        else:
            candidate_report = validate_candidate_contract(request, candidate)
            if not candidate_report.valid and _has_nonrepairable_error(candidate_report):
                raise CandidateContractError(candidate_report)

            if not candidate_report.valid:
                repair_attempted = True
                try:
                    raw = await self.provider.generate(build_repair_prompt(request, raw, _error_issues(candidate_report)))
                    attempts += 1
                    candidate = parse_candidate(raw)
                    candidate_report = validate_candidate_contract(request, candidate)
                except Exception as error:
                    fallback_reason = f"repair_failed: {type(error).__name__}: {str(error)[:180]}"
                    graph, report = self._fallback(
                        request,
                        source_report,
                        existing_report,
                        prior_candidate_report=candidate_report,
                        prior_blueprint_report=None,
                        cause=error,
                    )
                    mode = GenerationMode.FALLBACK
                else:
                    if not candidate_report.valid and _has_nonrepairable_error(candidate_report):
                        raise CandidateContractError(candidate_report)
                    if not candidate_report.valid:
                        fallback_reason = "candidate_contract_failed_after_repair"
                        graph, report = self._fallback(
                            request,
                            source_report,
                            existing_report,
                            prior_candidate_report=candidate_report,
                            prior_blueprint_report=None,
                        )
                        mode = GenerationMode.FALLBACK
                    else:
                        graph = compile_blueprint(request, candidate)
                        blueprint_report = validate_blueprint(request, graph)
                        report = blueprint_report
                        mode = GenerationMode.REPAIR
            else:
                graph = compile_blueprint(request, candidate)
                blueprint_report = validate_blueprint(request, graph)
                report = blueprint_report

            if mode != GenerationMode.FALLBACK and not report.valid:
                if _has_nonrepairable_error(report):
                    raise BlueprintContractError(report)
                if not repair_attempted:
                    repair_attempted = True
                    try:
                        raw = await self.provider.generate(build_repair_prompt(request, raw, _error_issues(report)))
                        attempts += 1
                        repaired_candidate = parse_candidate(raw)
                        candidate_report = validate_candidate_contract(request, repaired_candidate)
                    except Exception as error:
                        fallback_reason = f"repair_failed: {type(error).__name__}: {str(error)[:180]}"
                        graph, report = self._fallback(
                            request,
                            source_report,
                            existing_report,
                            prior_candidate_report=candidate_report,
                            prior_blueprint_report=blueprint_report,
                            cause=error,
                        )
                        mode = GenerationMode.FALLBACK
                    else:
                        if not candidate_report.valid and _has_nonrepairable_error(candidate_report):
                            raise CandidateContractError(candidate_report)
                        if candidate_report.valid:
                            graph = compile_blueprint(request, repaired_candidate)
                            blueprint_report = validate_blueprint(request, graph)
                            report = blueprint_report
                            mode = GenerationMode.REPAIR
                        if not candidate_report.valid or not report.valid:
                            if report is not None and _has_nonrepairable_error(report):
                                raise BlueprintContractError(report)
                            fallback_reason = "validation_failed_after_repair"
                            graph, report = self._fallback(
                                request,
                                source_report,
                                existing_report,
                                prior_candidate_report=candidate_report,
                                prior_blueprint_report=blueprint_report,
                            )
                            mode = GenerationMode.FALLBACK
                else:
                    fallback_reason = "validation_failed_after_repair"
                    graph, report = self._fallback(
                        request,
                        source_report,
                        existing_report,
                        prior_candidate_report=candidate_report,
                        prior_blueprint_report=blueprint_report,
                    )
                    mode = GenerationMode.FALLBACK

        if not report.valid:
            raise UnrecoverableFallbackError(
                source_contract_report=source_report,
                existing_graph_report=existing_report,
                candidate_report=candidate_report,
                blueprint_report=blueprint_report,
                fallback_report=report,
            )

        needs_review = graph.needs_review or mode == GenerationMode.FALLBACK or _review_required(report)
        graph.needs_review = needs_review
        graph.status = "needs_review" if needs_review else "valid"

        return OrchestrationResult(
            blueprint=graph,
            validation=report,
            public_view=build_public_view(graph),
            metadata=RunMetadata(
                generation_mode=mode,
                provider=getattr(self.provider, "name", None),
                model_id=getattr(self.provider, "model_id", None),
                raw_attempts=attempts,
                repair_attempted=repair_attempted,
                fallback_reason=fallback_reason,
                prompt_hash=_hash(prompt),
                schema_hash=_schema_hash(),
            ),
            raw_model_output=raw,
            validation_details=report.issues,
        )
