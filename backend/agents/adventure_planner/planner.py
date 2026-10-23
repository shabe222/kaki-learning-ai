from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .academic_verifier import AcademicVerifier, DefaultAcademicVerifier
from .blueprint_compiler import compile_blueprint
from .blueprint_validators import validate_blueprint
from .context_builder import build_model_context
from .difficulty import decide_difficulty
from .fallback import build_fallback_plan
from .json_tools import JSONExtractionError, parse_json_object
from .llm_protocol import ChatLLM, PlannerProviderUnconfiguredError, extract_text
from .models import (
    BlueprintEnvelope,
    Clarification,
    ClarificationReason,
    LLMPlanEnvelope,
    PlannerMetadata,
    PlannerRequest,
    PlannerResult,
    PlannerWarningCode,
)
from .normalizers import normalize_plan_envelope, normalize_raw_plan_payload
from .prompt_loader import load_prompt
from .validators import validate_plan


class PlanSemanticValidationError(ValueError):
    pass


class PlanOutputTruncatedError(ValueError):
    pass


class PlanEmptyOutputError(ValueError):
    pass


@dataclass(frozen=True)
class PlannerConfig:
    temperature: float = 0.2
    max_tokens: int = 6400
    timeout_seconds: float = 60.0
    truncation_retry_max_tokens: int = 8000
    truncation_retry_timeout_seconds: float = 80.0
    allow_repair: bool = True


class AdventureKnowledgePlanner:
    def __init__(
        self,
        llm: ChatLLM,
        config: PlannerConfig | None = None,
        academic_verifier: AcademicVerifier | None = None,
    ):
        self.llm = llm
        self.config = config or PlannerConfig()
        self.academic_verifier = academic_verifier or DefaultAcademicVerifier()

    async def generate(self, request: PlannerRequest) -> PlannerResult:
        decision = decide_difficulty(request)
        model_context = build_model_context(request)
        model_context["required_difficulty"] = decision.level.value
        fingerprint = self._fingerprint(model_context)
        preflight = self._preflight_clarification(request)
        if preflight is not None:
            return self._result(
                envelope=LLMPlanEnvelope(status="needs_clarification", clarification=preflight),
                mode="deterministic",
                request=request,
                request_fingerprint=fingerprint,
                model_name=None,
                tokens_used=0,
                warnings=[],
                diagnostics=[],
                difficulty_trace=decision.trace,
                raw_attempts=0,
            )
        schema = BlueprintEnvelope.model_json_schema()
        warning_codes: list[str] = []
        diagnostics: list[str] = []
        raw_attempts = 0
        model_name: str | None = None
        tokens_used = 0
        raw = ""
        first_error: Exception | None = None

        try:
            raw_attempts += 1
            raw, model_name, used, finish_reason = await self._call_model(model_context, schema)
            tokens_used += used
            self._record_finish_reason("model", finish_reason, diagnostics)
            self._ensure_complete_output(raw, finish_reason)
            envelope = self._parse_and_validate(
                raw,
                request,
                decision.level,
                warning_codes=warning_codes,
                diagnostics=diagnostics,
            )
            return self._result(
                envelope=envelope,
                mode="model",
                request=request,
                request_fingerprint=fingerprint,
                model_name=model_name,
                tokens_used=tokens_used,
                warnings=warning_codes,
                diagnostics=diagnostics,
                difficulty_trace=decision.trace,
                raw_attempts=raw_attempts,
            )
        except Exception as error:
            first_error = error
            self._record_error(error, warning_codes, diagnostics)

        if self.config.allow_repair and raw.strip():
            try:
                raw_attempts += 1
                if isinstance(first_error, PlanOutputTruncatedError):
                    self._append_unique(diagnostics, "repair_strategy:regenerate_after_truncation")
                    repaired, repair_model, used, repair_finish_reason = (
                        await self._regenerate_after_truncation(model_context, schema)
                    )
                else:
                    self._append_unique(diagnostics, "repair_strategy:repair_invalid_output")
                    repaired, repair_model, used, repair_finish_reason = await self._repair(
                        raw,
                        diagnostics,
                        model_context,
                        schema,
                    )
                tokens_used += used
                model_name = repair_model or model_name
                self._record_finish_reason("repair", repair_finish_reason, diagnostics)
                self._ensure_complete_output(repaired, repair_finish_reason)
                envelope = self._parse_and_validate(
                    repaired,
                    request,
                    decision.level,
                    warning_codes=warning_codes,
                    diagnostics=diagnostics,
                )
                return self._result(
                    envelope=envelope,
                    mode="repair",
                    request=request,
                    request_fingerprint=fingerprint,
                    model_name=model_name,
                    tokens_used=tokens_used,
                    warnings=warning_codes,
                    diagnostics=diagnostics,
                    difficulty_trace=decision.trace,
                    raw_attempts=raw_attempts,
                )
            except Exception as repair_error:
                self._append_unique(warning_codes, PlannerWarningCode.REPAIR_FAILED.value)
                self._record_error(repair_error, warning_codes, diagnostics)

        fallback = build_fallback_plan(request, decision.level)
        if not warning_codes:
            warning_codes.append(PlannerWarningCode.INTERNAL_ERROR.value)
        envelope = LLMPlanEnvelope(status="ready", plan=fallback)
        return self._result(
            envelope=envelope,
            mode="fallback",
            request=request,
            request_fingerprint=fingerprint,
            model_name=model_name,
            tokens_used=tokens_used,
            warnings=warning_codes,
            diagnostics=diagnostics,
            difficulty_trace=decision.trace,
            raw_attempts=raw_attempts,
        )

    def _result(
        self,
        *,
        envelope: LLMPlanEnvelope,
        mode: str,
        request: PlannerRequest,
        request_fingerprint: str,
        model_name: str | None,
        tokens_used: int,
        warnings: list[str],
        diagnostics: list[str],
        difficulty_trace: list[str],
        raw_attempts: int,
    ) -> PlannerResult:
        content_fingerprint = (
            self._fingerprint(envelope.plan.model_dump(mode="json")) if envelope.plan is not None else None
        )
        return PlannerResult(
            schema_version="1.1",
            status=envelope.status,
            clarification=envelope.clarification,
            plan=envelope.plan,
            metadata=PlannerMetadata(
                generation_mode=mode,
                request_fingerprint=request_fingerprint,
                content_fingerprint=content_fingerprint,
                model=model_name,
                tokens_used=tokens_used,
                warnings=warnings,
                diagnostics=diagnostics,
                difficulty_trace=difficulty_trace,
                raw_attempts=raw_attempts,
                draft_revision=request.draft_revision,
                adjustment_count=request.adjustment_count,
                plan_state="draft",
            ),
        )

    async def _call_model(
        self,
        model_context: dict[str, Any],
        schema: dict[str, Any],
    ) -> tuple[str, str | None, int, str | None]:
        system = self._system_with_schema("system_prompt.md", schema)
        user_message = json.dumps(model_context, ensure_ascii=False, separators=(",", ":"))
        result = await asyncio.wait_for(
            self.llm.chat(
                [{"role": "user", "content": user_message}],
                system_prompt=system,
                temperature=self.config.temperature,
                max_tokens=self.config.max_tokens,
                response_format={"type": "json_object"},
                use_cache=False,
                write_cache=False,
            ),
            timeout=self.config.timeout_seconds,
        )
        return extract_text(result)

    async def _repair(
        self,
        raw: str,
        diagnostics: list[str],
        model_context: dict[str, Any],
        schema: dict[str, Any],
    ) -> tuple[str, str | None, int, str | None]:
        system = self._system_with_schema("repair_prompt.md", schema)
        payload = {
            "required_context": model_context,
            "validation_errors": diagnostics[-8:],
            "invalid_output": raw[:16000],
        }
        result = await asyncio.wait_for(
            self.llm.chat(
                [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                system_prompt=system,
                temperature=0.0,
                max_tokens=self.config.max_tokens,
                response_format={"type": "json_object"},
                use_cache=False,
                write_cache=False,
            ),
            timeout=self.config.timeout_seconds,
        )
        return extract_text(result)


    async def _regenerate_after_truncation(
        self,
        model_context: dict[str, Any],
        schema: dict[str, Any],
    ) -> tuple[str, str | None, int, str | None]:
        """Regenerate from source context after a length stop.

        A truncated JSON fragment is not a useful repair input. Feeding it back
        repeats thousands of incomplete tokens and often truncates again. This
        retry starts from the authoritative context, uses a deterministic
        temperature, a larger bounded output allowance, and an explicit compact
        contract while preserving the exact public schema.
        """
        system = self._system_with_schema(
            "system_prompt.md",
            schema,
            extra=(
                "\n\nTRUNCATION RETRY REQUIREMENTS:\n"
                "The previous response reached the provider output limit. "
                "Regenerate the complete compact blueprint object from scratch. Return compact JSON "
                "without indentation. Use only the required blueprint tasks and "
                "short academic fields. Do not invent rubric, remediation, metadata, "
                "repeated hint fields, or full-plan template structures. Never omit "
                "required fields."
            ),
        )
        user_message = json.dumps(model_context, ensure_ascii=False, separators=(",", ":"))
        result = await asyncio.wait_for(
            self.llm.chat(
                [{"role": "user", "content": user_message}],
                system_prompt=system,
                temperature=0.0,
                max_tokens=self.config.truncation_retry_max_tokens,
                response_format={"type": "json_object"},
                use_cache=False,
                write_cache=False,
            ),
            timeout=self.config.truncation_retry_timeout_seconds,
        )
        return extract_text(result)

    @staticmethod
    def _system_with_schema(
        prompt_name: str,
        schema: dict[str, Any],
        *,
        extra: str = "",
    ) -> str:
        compact_schema = json.dumps(
            schema,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return load_prompt(prompt_name) + extra + "\n\nJSON Schema:\n" + compact_schema

    @staticmethod
    def _record_finish_reason(
        stage: str,
        finish_reason: str | None,
        diagnostics: list[str],
    ) -> None:
        if finish_reason:
            AdventureKnowledgePlanner._append_unique(
                diagnostics,
                f"{stage}_finish_reason:{finish_reason[:40]}",
            )

    @staticmethod
    def _ensure_complete_output(raw: str, finish_reason: str | None) -> None:
        if not raw.strip():
            raise PlanEmptyOutputError("provider returned empty content")
        if finish_reason == "length":
            raise PlanOutputTruncatedError("provider stopped because the output token limit was reached")

    def _parse_and_validate(
        self,
        raw: str,
        request: PlannerRequest,
        required_difficulty,
        *,
        warning_codes: list[str],
        diagnostics: list[str],
    ):
        parsed = parse_json_object(raw)

        # v0.3.0 model path: compact academic blueprint -> deterministic full plan.
        if "blueprint" in parsed or (parsed.get("status") == "needs_clarification" and "plan" not in parsed):
            blueprint_envelope = BlueprintEnvelope.model_validate(parsed)
            blueprint_errors = validate_blueprint(blueprint_envelope, request)
            if blueprint_errors:
                raise PlanSemanticValidationError("; ".join(blueprint_errors))
            if blueprint_envelope.status == "needs_clarification":
                self._append_unique(diagnostics, "blueprint_path:clarification")
                return LLMPlanEnvelope(
                    status="needs_clarification",
                    clarification=blueprint_envelope.clarification,
                )

            assert blueprint_envelope.blueprint is not None
            plan = compile_blueprint(blueprint_envelope.blueprint, request, required_difficulty)
            envelope = LLMPlanEnvelope(status="ready", plan=plan)
            self._append_unique(diagnostics, "blueprint_schema_version:0.3")
            self._append_unique(diagnostics, "compiled_from_blueprint")
        else:
            # Compatibility path for stored v0.2.x fixtures and in-flight drafts.
            raw_normalization = normalize_raw_plan_payload(parsed)
            for warning in raw_normalization.warning_codes:
                self._append_unique(warning_codes, warning)
            for diagnostic in raw_normalization.diagnostics:
                self._append_unique(diagnostics, diagnostic)
            envelope = LLMPlanEnvelope.model_validate(parsed)
            self._append_unique(diagnostics, "legacy_full_plan_compatibility_path")

        normalization = normalize_plan_envelope(envelope)
        for warning in normalization.warning_codes:
            self._append_unique(warning_codes, warning)
        for diagnostic in normalization.diagnostics:
            self._append_unique(diagnostics, diagnostic)

        errors = validate_plan(envelope, request, required_difficulty)
        if envelope.plan is not None:
            errors.extend(
                f"academic:{finding.code}:{finding.message}"
                for finding in self.academic_verifier.verify(envelope.plan, request)
            )
        if errors:
            raise PlanSemanticValidationError("; ".join(errors))
        return envelope

    @staticmethod
    def _preflight_clarification(request: PlannerRequest) -> Clarification | None:
        if request.clarification_count >= 1:
            return None
        unresolved = [item for item in request.source_conflicts if not item.resolved]
        if not unresolved:
            return None
        conflict = unresolved[0]
        source_titles = {source.source_id: source.title for source in request.selected_sources}
        options = [source_titles.get(source_id, source_id) for source_id in conflict.source_ids[:2]]
        if len(options) < 2:
            options = ["采用当前题目中的表述", "采用当前资料中的表述"]
        return Clarification(
            reason=ClarificationReason.SOURCE_CONFLICT,
            question="这次旅程应以哪一份材料的表述为准？",
            options=options[:2],
        )

    @staticmethod
    def _fingerprint(context: Any) -> str:
        raw = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @classmethod
    def _record_error(cls, error: Exception, warnings: list[str], diagnostics: list[str]) -> None:
        cls._append_unique(warnings, cls._warning_code(error))
        cls._append_unique(diagnostics, cls._safe_error(error))

    @staticmethod
    def _append_unique(values: list[str], value: str) -> None:
        if value not in values:
            values.append(value)

    @staticmethod
    def _warning_code(error: Exception) -> str:
        if isinstance(error, PlannerProviderUnconfiguredError):
            return PlannerWarningCode.PROVIDER_UNCONFIGURED.value
        if isinstance(error, asyncio.TimeoutError) or "timeout" in error.__class__.__name__.lower():
            return PlannerWarningCode.TIMEOUT.value
        if isinstance(error, JSONExtractionError):
            return PlannerWarningCode.INVALID_JSON.value
        if isinstance(error, ValidationError):
            return PlannerWarningCode.SCHEMA_INVALID.value
        if isinstance(error, PlanSemanticValidationError):
            return PlannerWarningCode.SEMANTIC_VALIDATION_FAILED.value
        if isinstance(error, PlanOutputTruncatedError):
            return PlannerWarningCode.OUTPUT_TRUNCATED.value
        if isinstance(error, PlanEmptyOutputError):
            return PlannerWarningCode.EMPTY_OUTPUT.value

        text = str(error).lower()
        class_name = error.__class__.__name__.lower()
        if any(token in text for token in ("api key 未配置", "api_key 未配置", "not_configured", "provider is not configured")):
            return PlannerWarningCode.PROVIDER_UNCONFIGURED.value
        status_code = getattr(getattr(error, "response", None), "status_code", None)
        if status_code in (401, 403) or "auth" in text or "鉴权" in text:
            return PlannerWarningCode.PROVIDER_AUTH_FAILED.value
        if status_code == 429 or "rate limit" in text or "限流" in text:
            return PlannerWarningCode.PROVIDER_RATE_LIMITED.value
        if "network" in text or "网络" in text or "connect" in class_name or "http" in class_name:
            return PlannerWarningCode.PROVIDER_NETWORK_ERROR.value
        return PlannerWarningCode.INTERNAL_ERROR.value

    @staticmethod
    def _safe_error(error: Exception) -> str:
        if isinstance(error, ValidationError):
            return "schema:" + "; ".join(
                f"{'.'.join(str(item) for item in entry['loc'])}:{entry['msg']}"
                for entry in error.errors()[:8]
            )
        if isinstance(error, JSONExtractionError):
            return f"json:{str(error)[:300]}"
        if isinstance(error, PlanSemanticValidationError):
            return f"semantic:{str(error)[:500]}"
        if isinstance(error, PlanOutputTruncatedError):
            return "output_truncated"
        if isinstance(error, PlanEmptyOutputError):
            return "empty_output"
        if isinstance(error, PlannerProviderUnconfiguredError):
            return "provider_unconfigured"
        if isinstance(error, asyncio.TimeoutError):
            return "timeout"
        status_code = getattr(getattr(error, "response", None), "status_code", None)
        if status_code:
            return f"{error.__class__.__name__}:http_{status_code}"
        return f"{error.__class__.__name__}:{str(error)[:300]}"
