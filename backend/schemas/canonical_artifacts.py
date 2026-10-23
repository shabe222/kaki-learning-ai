"""Machine-readable validators for darya.artifact.v1 and companion contracts."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, root_validator, validator


ArtifactBlockType = Literal[
    "HEADING", "TEXT", "CALLOUT", "CONCEPT", "EXAMPLE", "QUESTION", "TASK",
    "CHECKLIST", "CODE", "RUNNABLE_CODE", "DIAGRAM", "IMAGE", "VIDEO", "AUDIO",
    "SOURCE", "REFLECTION", "CHECKPOINT",
]
ArtifactVerdict = Literal["PASS", "REVISE", "REJECT", "BLOCKED"]
ArtifactCanonicalStatus = Literal[
    "PLANNED", "GENERATING", "GENERATED", "IN_REVIEW", "REVISION_REQUIRED",
    "PASSED", "FAILED", "BLOCKED",
]


class ArtifactBlockV1(BaseModel):
    contract: Literal["darya.artifact-block.v1"] = "darya.artifact-block.v1"
    block_id: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    type: ArtifactBlockType
    data: Dict[str, Any] = Field(default_factory=dict)
    knowledge_refs: List[str] = Field(default_factory=list, max_length=100)
    source_refs: List[str] = Field(default_factory=list, max_length=100)
    pedagogy: Dict[str, Any] = Field(default_factory=dict)
    interaction_policy: Dict[str, Any] = Field(default_factory=dict)
    evidence_policy: Dict[str, Any] = Field(default_factory=dict)
    accessibility: Dict[str, Any] = Field(default_factory=dict)

    @validator("knowledge_refs", "source_refs")
    def bounded_refs(cls, value):
        return [str(item)[:240] for item in value]

    @root_validator(skip_on_failure=True)
    def visible_block_payload(cls, values):
        block_type = values.get("type")
        data = values.get("data") or {}
        if block_type in {"HEADING", "TEXT", "CALLOUT", "CONCEPT", "EXAMPLE", "SOURCE"}:
            if not str(data.get("text") or data.get("title") or data.get("label") or "").strip():
                raise ValueError(f"{block_type} block requires visible text/title/label")
        return values


class ArtifactStatusV1(BaseModel):
    contract: Literal["darya.artifact-status.v1"] = "darya.artifact-status.v1"
    generation_status: ArtifactCanonicalStatus
    review_verdict: Optional[ArtifactVerdict] = None
    learning_evaluation: Optional[Dict[str, Any]] = None
    canonical_status: ArtifactCanonicalStatus


class ArtifactReviewIssueV1(BaseModel):
    block_id: str = Field(min_length=3, max_length=100)
    severity: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    code: str = Field(default="REVISION_REQUIRED", min_length=2, max_length=80)
    message: str = Field(min_length=1, max_length=2000)


class ArtifactReviewV1(BaseModel):
    contract: Literal["darya.artifact-review.v1"] = "darya.artifact-review.v1"
    artifact_id: int = Field(ge=1)
    artifact_version_id: int = Field(ge=1)
    version_digest: str = Field(min_length=64, max_length=71)
    review_contract_version: str = Field(default="darya.artifact-review.v1", max_length=64)
    round: int = Field(ge=1, le=100)
    reviewer_role: str = Field(min_length=2, max_length=40)
    provider: Optional[str] = Field(default=None, max_length=80)
    model: Optional[str] = Field(default=None, max_length=120)
    verdict: ArtifactVerdict
    scores: Dict[str, float] = Field(default_factory=dict)
    safe_summary: str = Field(min_length=1, max_length=4000)
    issues: List[ArtifactReviewIssueV1] = Field(default_factory=list, max_length=200)
    preserve: List[str] = Field(default_factory=list, max_length=200)

    @root_validator(skip_on_failure=True)
    def revise_has_issues(cls, values):
        if values.get("verdict") == "REVISE" and not values.get("issues"):
            raise ValueError("REVISE requires at least one block-bound issue")
        return values


class ArtifactLineageV1(BaseModel):
    contract: Literal["darya.artifact-lineage.v1"] = "darya.artifact-lineage.v1"
    source_artifact_id: int = Field(ge=1)
    source_version_id: int = Field(ge=1)
    source_version_digest: str = Field(min_length=64, max_length=71)
    source_block_refs: List[str] = Field(default_factory=list, max_length=200)
    target_artifact_id: int = Field(ge=1)
    target_version_id: int = Field(ge=1)
    transform_type: str = Field(min_length=2, max_length=60)
    transform_contract_version: str = Field(default="darya.artifact-transform.v1", max_length=64)
    knowledge_refs: List[str] = Field(default_factory=list, max_length=200)
    source_refs: List[str] = Field(default_factory=list, max_length=200)
    learner_context_ref: Optional[str] = Field(default=None, max_length=200)
    safe_operation_summary: str = Field(min_length=1, max_length=4000)
    generator_provenance: Dict[str, Any] = Field(default_factory=dict)


class CanonicalArtifactVersionV1(BaseModel):
    version_id: int = Field(ge=1)
    number: int = Field(ge=1)
    parent_version_id: Optional[int] = Field(default=None, ge=1)
    status: ArtifactCanonicalStatus
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created_by_operation: str = Field(min_length=8, max_length=64)
    created_at: str


class CanonicalArtifactV1(BaseModel):
    contract: Literal["darya.artifact.v1"] = "darya.artifact.v1"
    artifact_id: int = Field(ge=1)
    artifact_type: str = Field(min_length=2, max_length=80)
    owner_scope: Literal["authenticated_user"] = "authenticated_user"
    version: CanonicalArtifactVersionV1
    purpose: Dict[str, Any]
    personalization: Dict[str, Any] = Field(default_factory=dict)
    blocks: List[ArtifactBlockV1] = Field(min_length=1, max_length=1000)
    source_refs: List[Any] = Field(default_factory=list, max_length=200)
    lineage: List[Dict[str, Any]] = Field(default_factory=list, max_length=200)
    review: Dict[str, Any] = Field(default_factory=dict)
    status: ArtifactStatusV1
    capabilities: Dict[str, List[str]] = Field(default_factory=dict)
    accessibility: Dict[str, Any] = Field(default_factory=dict)

    @root_validator(skip_on_failure=True)
    def public_contract_has_no_host_or_secret_payload(cls, values):
        payload = json.dumps(values, ensure_ascii=False, default=str)
        forbidden = [
            r"[A-Za-z]:\\(?:Users|Windows|Program Files)\\",
            r"file://", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
            r"(?i)(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*[^,}\s]+",
        ]
        if any(re.search(pattern, payload) for pattern in forbidden):
            raise ValueError("canonical public projection contains a private host path or secret")
        return values


class CreateCanonicalHandoutRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    topic: Optional[str] = Field(default=None, max_length=120)
    content: Optional[str] = Field(default=None, max_length=120000)
    blocks: Optional[List[ArtifactBlockV1]] = Field(default=None, min_length=1, max_length=1000)
    purpose: Dict[str, Any] = Field(default_factory=dict)
    personalization: Dict[str, Any] = Field(default_factory=dict)
    source_refs: List[Any] = Field(default_factory=list, max_length=200)
    thread_id: Optional[str] = Field(default=None, max_length=100)
    experience: Literal["PERSONALIZED_HANDOUT", "PRACTICAL_GUIDE"] = "PERSONALIZED_HANDOUT"
    idempotency_key: str = Field(min_length=8, max_length=240)
    # 内容质量闸门的实测回执。它不参与内容摘要，只作为内部质量记录随版本保存 ——
    # 这样"审阅通过"的依据是量出来的，而不是写死的。
    content_quality: Optional[Dict[str, Any]] = Field(default=None)


class CreateArtifactReviewRequest(BaseModel):
    artifact_version_id: int = Field(ge=1)
    version_digest: str = Field(min_length=64, max_length=71)
    review_contract_version: str = Field(default="darya.artifact-review.v1", max_length=64)
    round: int = Field(ge=1, le=100)
    reviewer_role: str = Field(min_length=2, max_length=40)
    provider: Optional[str] = Field(default=None, max_length=80)
    model: Optional[str] = Field(default=None, max_length=120)
    verdict: ArtifactVerdict
    scores: Dict[str, float] = Field(default_factory=dict)
    safe_summary: str = Field(min_length=1, max_length=4000)
    issues: List[ArtifactReviewIssueV1] = Field(default_factory=list, max_length=200)
    preserve: List[str] = Field(default_factory=list, max_length=200)
    idempotency_key: str = Field(min_length=8, max_length=240)


class CreateReviewedRevisionRequest(BaseModel):
    source_version_id: int = Field(ge=1)
    source_version_digest: str = Field(min_length=64, max_length=71)
    expected_version: int = Field(ge=1)
    title: Optional[str] = Field(default=None, max_length=200)
    content: Optional[str] = Field(default=None, max_length=120000)
    blocks: List[ArtifactBlockV1] = Field(min_length=1, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=240)


class CreatePracticeTransformRequest(BaseModel):
    source_version_id: int = Field(ge=1)
    source_version_digest: str = Field(min_length=64, max_length=71)
    source_block_refs: List[str] = Field(default_factory=list, max_length=200)
    title: str = Field(min_length=1, max_length=200)
    blocks: List[ArtifactBlockV1] = Field(min_length=1, max_length=1000)
    knowledge_refs: List[str] = Field(default_factory=list, max_length=200)
    source_refs: List[str] = Field(default_factory=list, max_length=200)
    learner_context_ref: Optional[str] = Field(default=None, max_length=200)
    safe_operation_summary: str = Field(min_length=1, max_length=4000)
    generator_provenance: Dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=240)
