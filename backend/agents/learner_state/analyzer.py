from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from .fallback import build_fallback_blueprint
from .json_tools import parse_json_object
from .llm_protocol import StructuredLLM, coerce_llm_response
from .models import AnalyzerMetadata, LearnerStateBlueprint, LearnerStateRequest, LearnerStateResult
from .normalizers import normalize_blueprint
from .prompt_loader import load_prompt
from .state_updater import compile_snapshot
from .validators import LearnerStateValidationError, validate_blueprint


class LearnerStateAnalyzer:
    MAX_TOKENS = 1400
    REPAIR_MAX_TOKENS = 1600
    TIMEOUT_SECONDS = 45.0

    def __init__(self, llm: StructuredLLM, *, configured_model_id: str | None = None) -> None:
        self.llm = llm
        self.configured_model_id = configured_model_id

    def _model_context(self, request: LearnerStateRequest) -> dict[str, Any]:
        recent = [] if request.context_mode.value == "current_item_only" else [
            item.model_dump(mode="json") for item in request.recent_related_evidence
        ]
        return {
            "topic": {
                "topic_key": request.topic_key,
                "topic_name": request.topic_name,
                "current_material_summary": request.current_material_summary,
            },
            "priority_rules": [
                "current_explicit_confusion",
                "current_evidence",
                "recent_same_topic_evidence_as_weak_reference",
                "previous_topic_state_as_weak_reference",
            ],
            "explicit_confusion": request.explicit_confusion,
            "current_evidence": [item.model_dump(mode="json") for item in request.current_evidence],
            "recent_related_evidence": recent,
            "previous_topic_state": (
                request.previous_topic_state.model_dump(mode="json") if request.previous_topic_state else None
            ),
            "context_mode": request.context_mode.value,
            "allowed_evidence_refs": sorted(request.allowed_evidence_refs()),
            "locale": request.locale,
        }

    def _messages(self, request: LearnerStateRequest) -> list[dict[str, str]]:
        schema = LearnerStateBlueprint.model_json_schema()
        user_payload = {
            "instruction": "根据学习证据判断当前主题下的学习状态。只输出一个符合 JSON Schema 的对象。",
            "context": self._model_context(request),
            "json_schema": schema,
        }
        return [
            {"role": "system", "content": load_prompt("system_prompt.md")},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":"))},
        ]

    async def _call(self, messages: list[dict[str, str]], *, repair: bool = False):
        return coerce_llm_response(
            await self.llm.chat(
                messages=messages,
                temperature=0.0 if repair else 0.1,
                max_tokens=self.REPAIR_MAX_TOKENS if repair else self.MAX_TOKENS,
                timeout_seconds=self.TIMEOUT_SECONDS,
                response_format={"type": "json_object"},
            )
        )

    def _parse(self, content: str, request: LearnerStateRequest) -> tuple[LearnerStateBlueprint, list[str]]:
        payload = parse_json_object(content)
        blueprint = LearnerStateBlueprint.model_validate(payload)
        blueprint, warnings = normalize_blueprint(blueprint, request)
        validate_blueprint(blueprint, request)
        return blueprint, warnings

    def _safe_error(self, error: Exception) -> str:
        if isinstance(error, ValidationError):
            items = []
            for item in error.errors()[:8]:
                loc = ".".join(str(part) for part in item.get("loc", ()))
                items.append(f"{loc}:{item.get('type')}")
            return "schema:" + ",".join(items)
        if isinstance(error, LearnerStateValidationError):
            return "semantic:" + ",".join(issue.code for issue in error.issues[:8])
        return f"{type(error).__name__}:{str(error)[:180]}"

    async def analyze(self, request: LearnerStateRequest) -> LearnerStateResult:
        warnings: list[str] = []
        attempts = 0
        finish_reason = None
        provider_model = None
        prompt_tokens = None
        completion_tokens = None
        content_length = None

        try:
            attempts = 1
            response = await self._call(self._messages(request))
            finish_reason = response.finish_reason
            provider_model = response.model
            prompt_tokens = response.prompt_tokens
            completion_tokens = response.completion_tokens
            content_length = len(response.content)
            if not response.content.strip():
                raise ValueError("empty model content")
            if response.finish_reason == "length":
                raise ValueError("model output truncated")
            try:
                blueprint, normalize_warnings = self._parse(response.content, request)
                warnings.extend(normalize_warnings)
            except Exception as first_error:
                warnings.append("learner_state_initial_output_invalid")
                attempts = 2
                repair_payload = {
                    "instruction": "修复下面的学习状态 JSON。保留有证据支持的判断，修复格式、Schema或一致性错误。只输出一个 JSON 对象。",
                    "error_summary": self._safe_error(first_error),
                    "allowed_evidence_refs": sorted(request.allowed_evidence_refs()),
                    "json_schema": LearnerStateBlueprint.model_json_schema(),
                    "failed_output": response.content[:6000],
                }
                repair_messages = [
                    {"role": "system", "content": load_prompt("repair_prompt.md")},
                    {"role": "user", "content": json.dumps(repair_payload, ensure_ascii=False, separators=(",", ":"))},
                ]
                repaired = await self._call(repair_messages, repair=True)
                finish_reason = repaired.finish_reason
                provider_model = repaired.model or provider_model
                prompt_tokens = repaired.prompt_tokens or prompt_tokens
                completion_tokens = repaired.completion_tokens or completion_tokens
                content_length = len(repaired.content)
                blueprint, normalize_warnings = self._parse(repaired.content, request)
                warnings.append("learner_state_repaired")
                warnings.extend(normalize_warnings)

            snapshot = compile_snapshot(blueprint, request, source="model", warnings=warnings)
            return LearnerStateResult(
                state=snapshot,
                metadata=AnalyzerMetadata(
                    generation_mode="model",
                    fallback_used=False,
                    warnings=list(dict.fromkeys(warnings)),
                    raw_attempts=attempts,
                    finish_reason=finish_reason,
                    configured_model_id=self.configured_model_id,
                    provider_returned_model_id=provider_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    content_length=content_length,
                ),
            )
        except Exception as error:
            warnings.append("learner_state_fallback_used")
            warnings.append(f"learner_state_error:{type(error).__name__}")
            blueprint = build_fallback_blueprint(request)
            blueprint, normalize_warnings = normalize_blueprint(blueprint, request)
            warnings.extend(normalize_warnings)
            snapshot = compile_snapshot(blueprint, request, source="fallback", warnings=warnings)
            return LearnerStateResult(
                state=snapshot,
                metadata=AnalyzerMetadata(
                    generation_mode="fallback",
                    fallback_used=True,
                    warnings=list(dict.fromkeys(warnings)),
                    raw_attempts=attempts,
                    finish_reason=finish_reason,
                    configured_model_id=self.configured_model_id,
                    provider_returned_model_id=provider_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    content_length=content_length,
                ),
            )
