from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .compiler import compile_runtime
from .fallback import build_fallback_blueprint
from .json_tools import NarrativeJSONError, parse_json_object_strict
from .llm_protocol import ChatLLM, NarrativeProviderUnconfiguredError, extract_text
from .models import DirectorResult, NarrativeBlueprint, NarrativeRequest, ValidationIssue
from .normalizers import normalize_blueprint
from .prompt_loader import load_prompt
from .repair import choose_repair_mode
from .serializer import build_public_view
from .validators import has_errors, validate_blueprint
from .enums import RepairMode, Severity


@dataclass(frozen=True)
class NarrativeDirectorConfig:
    temperature: float = 0.0
    max_tokens: int = 3000
    timeout_seconds: float = 60.0
    allow_repair: bool = True


class NarrativeDirector:
    def __init__(self, llm: ChatLLM, config: NarrativeDirectorConfig | None = None):
        self.llm = llm
        self.config = config or NarrativeDirectorConfig()

    async def generate(self, request: NarrativeRequest) -> DirectorResult:
        schema = NarrativeBlueprint.model_json_schema()
        schema_json = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
        system = self._system_with_schema("system_prompt.md", schema_json)
        model_context = self._model_context(request)
        user = json.dumps(model_context, ensure_ascii=False, separators=(",", ":"))

        prompt_hash = self._hash(system)
        schema_hash = self._hash(schema_json)
        warnings: list[str] = []
        diagnostics: list[str] = []
        tokens_used = 0
        model_name: str | None = None
        raw_attempts = 0
        blueprint: NarrativeBlueprint | None = None
        issues: list[ValidationIssue] = []
        parse_error: str | None = None
        provider_unavailable = False
        generation_mode = "fallback"

        raw: str | None = None
        try:
            raw_attempts += 1
            raw, model_name, used, finish_reason = await self._call(system, user)
            tokens_used += used
            blueprint = self._parse(raw, finish_reason)
            blueprint, normalized = normalize_blueprint(blueprint, request)
            warnings.extend(normalized)
            issues = validate_blueprint(blueprint, request)
            generation_mode = "model"
        except Exception as error:
            parse_error = f"{type(error).__name__}:{error}"
            provider_unavailable = isinstance(error, NarrativeProviderUnconfiguredError)
            diagnostics.append(parse_error[:500])

        repair_mode = RepairMode.NONE if provider_unavailable else choose_repair_mode(issues, parse_error)
        if self.config.allow_repair and repair_mode != RepairMode.NONE:
            try:
                raw_attempts += 1
                repaired, repair_model, used, finish_reason = await self._repair(
                    request=request,
                    schema=schema,
                    schema_json=schema_json,
                    candidate=blueprint,
                    invalid_raw=raw,
                    issues=issues,
                    mode=repair_mode,
                )
                tokens_used += used
                model_name = repair_model or model_name
                blueprint = self._parse(repaired, finish_reason)
                blueprint, normalized = normalize_blueprint(blueprint, request)
                warnings.extend(normalized)
                issues = validate_blueprint(blueprint, request)
                generation_mode = "repair"
                diagnostics.append(f"repair_mode={repair_mode.value}")
                parse_error = None
            except Exception as error:
                diagnostics.append(f"repair_failed:{type(error).__name__}:{error}"[:500])

        if blueprint is None or has_errors(issues):
            blueprint = build_fallback_blueprint(request)
            blueprint, normalized = normalize_blueprint(blueprint, request)
            warnings.extend(normalized)
            issues = validate_blueprint(blueprint, request)
            generation_mode = "fallback"
            diagnostics.append("deterministic_fallback_used")
            if has_errors(issues):
                codes = [issue.code for issue in issues if issue.severity == Severity.ERROR]
                raise RuntimeError(f"narrative fallback failed validation: {codes}")

        review_codes = [issue.code for issue in issues if issue.severity == Severity.REVIEW]
        warning_codes = [issue.code for issue in issues if issue.severity == Severity.WARNING]
        warnings.extend(review_codes)
        warnings.extend(warning_codes)

        runtime = compile_runtime(
            request,
            blueprint,
            generation_mode=generation_mode,
            model=model_name,
            tokens_used=tokens_used,
            raw_attempts=min(raw_attempts, 2),
            warnings=warnings,
            diagnostics=diagnostics,
            prompt_hash=prompt_hash,
            schema_hash=schema_hash,
            validation_issues=issues,
        )
        public_view = build_public_view(runtime, blueprint)
        return DirectorResult(blueprint=blueprint, runtime=runtime, public_view=public_view)

    async def _call(self, system: str, user: str) -> tuple[str, str | None, int, str | None]:
        result = await asyncio.wait_for(
            self.llm.chat(
                [{"role": "user", "content": user}],
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
        *,
        request: NarrativeRequest,
        schema: dict[str, Any],
        schema_json: str,
        candidate: NarrativeBlueprint | None,
        invalid_raw: str | None,
        issues: list[ValidationIssue],
        mode: RepairMode,
    ) -> tuple[str, str | None, int, str | None]:
        if mode == RepairMode.CLEAN:
            system = self._system_with_schema("clean_regeneration_prompt.md", schema_json)
            payload = {
                "SAFE_NARRATIVE_REQUEST": self._model_context(request),
                "FAILURE_CATEGORIES": [issue.code for issue in issues if issue.severity == Severity.ERROR],
                "REQUIRED_SCHEMA": schema,
            }
        else:
            system = self._system_with_schema("repair_prompt.md", schema_json)
            payload = {
                "SAFE_NARRATIVE_REQUEST": self._model_context(request),
                "CANDIDATE_BLUEPRINT": (
                    candidate.model_dump(mode="json") if candidate is not None else (invalid_raw or "")[:12000]
                ),
                "VALIDATION_ERRORS": [
                    issue.model_dump(mode="json") for issue in issues if issue.severity == Severity.ERROR
                ] or [{"code": "INVALID_JSON", "message": "initial output could not be parsed"}],
                "REQUIRED_SCHEMA": schema,
            }
        return await self._call(system, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))

    def _parse(self, raw: str, finish_reason: str | None) -> NarrativeBlueprint:
        parsed = parse_json_object_strict(raw, finish_reason)
        try:
            return NarrativeBlueprint.model_validate(parsed)
        except ValidationError as error:
            raise NarrativeJSONError(f"schema validation failed: {error}") from error

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _system_with_schema(name: str, schema_json: str) -> str:
        return load_prompt(name) + "\n\n# REQUIRED JSON SCHEMA\n" + schema_json

    @staticmethod
    def _model_context(request: NarrativeRequest) -> dict[str, Any]:
        return {
            "topic": request.topic,
            "domain": request.domain.value,
            "preview_theme": request.preview_theme,
            "goals": request.goals.model_dump(mode="json"),
            "teaching_mode": request.teaching_mode.value,
            "core_concept": request.core_concept.model_dump(mode="json"),
            "misconception": request.misconception.model_dump(mode="json"),
            "route_tasks": request.route_tasks.model_dump(mode="json"),
            "boss_tasks": request.boss_tasks.model_dump(mode="json"),
            "frame": {
                "frame_version": request.frame.frame_version,
                "frame_id": request.frame.frame_id,
                "grammar": request.frame.grammar.value,
                "primary_scene_name": request.frame.primary_scene_name,
                "alternate_scene_name": request.frame.alternate_scene_name,
                "allowed_objects": request.frame.allowed_objects,
                "allowed_opposition_forms": [item.value for item in request.frame.allowed_opposition_forms],
            },
            "focus_adjustment": request.focus_adjustment,
            "locale": request.locale,
        }
