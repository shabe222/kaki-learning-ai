from __future__ import annotations
from typing import Annotated, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from .source_identity import OpaqueId
from .closed_codes import unknown_evidence_error_codes

class StrictModel(BaseModel):
    model_config=ConfigDict(extra="forbid")


def _validate_error_codes(values):
    unknown = unknown_evidence_error_codes(values or [])
    if unknown:
        raise ValueError("UNKNOWN_EVIDENCE_ERROR_CODE")
    return values

class QuestionAttemptEvidence(StrictModel):
    payload_kind: Literal["question_attempt"]="question_attempt"
    question_ref: OpaqueId
    concept_tags: list[str]=Field(default_factory=list,max_length=20)
    response_kind: str="choice"
    correctness: float=Field(ge=0,le=1)
    score: float=Field(ge=0)
    max_score: float=Field(gt=0)
    attempt_number: int=Field(ge=1,le=1000)
    hint_used: bool=False
    solution_viewed: bool=False
    response_time_seconds: float|None=Field(default=None,ge=0,le=86400)
    error_codes: list[OpaqueId]=Field(default_factory=list,max_length=20)

    _closed_error_codes = field_validator("error_codes")(_validate_error_codes)


class ObjectiveRubricScore(StrictModel):
    objective_id: OpaqueId
    score: float=Field(ge=0,le=1)
    node_contract_digest: OpaqueId
    objective_digest: OpaqueId
    rubric_version: OpaqueId
    assessment_contract_ref: OpaqueId

class AssessmentResultEvidence(StrictModel):
    payload_kind: Literal["assessment_result"]="assessment_result"
    assessment_ref: OpaqueId
    concept_tags: list[str]=Field(default_factory=list,max_length=50)
    score: float=Field(ge=0)
    max_score: float=Field(gt=0)
    passed: bool
    objective_scores: dict[str,float]=Field(default_factory=dict)
    objective_rubric_scores: list[ObjectiveRubricScore]=Field(default_factory=list,max_length=100)
    rubric_version: OpaqueId|None=None

    @model_validator(mode="after")
    def ranges(self):
        if self.score>self.max_score: raise ValueError("score exceeds max_score")
        if any(v<0 or v>1 for v in self.objective_scores.values()): raise ValueError("objective score outside range")
        return self

class CodeExecutionEvidence(StrictModel):
    payload_kind: Literal["code_execution"]="code_execution"
    task_ref: OpaqueId
    concept_tags: list[str]=Field(default_factory=list,max_length=30)
    exit_status: int
    tests_total: int=Field(ge=0,le=10000)
    tests_passed: int=Field(ge=0,le=10000)
    tests_failed: int=Field(ge=0,le=10000)
    runtime_error_category: OpaqueId|None=None
    manual_copy_detected: bool=False
    independent_solution: bool=False

    @model_validator(mode="after")
    def totals(self):
        if self.tests_passed+self.tests_failed>self.tests_total: raise ValueError("test counts exceed total")
        return self

class AdventureEvidence(StrictModel):
    payload_kind: Literal["adventure"]="adventure"
    stage: Literal["explanation","distinction","transfer","remedy"]
    task_ref: OpaqueId
    correctness: float=Field(ge=0,le=1)
    rubric_score: float=Field(ge=0,le=1)
    hint_used: bool=False
    independent: bool=True
    error_codes: list[OpaqueId]=Field(default_factory=list,max_length=20)

    _closed_error_codes = field_validator("error_codes")(_validate_error_codes)

class ResourceInteractionEvidence(StrictModel):
    payload_kind: Literal["resource_interaction"]="resource_interaction"
    resource_ref: OpaqueId
    action: Literal["opened","viewed","completed","generated"]
    progress: float|None=Field(default=None,ge=0,le=1)

class NoteReflectionEvidence(StrictModel):
    payload_kind: Literal["note_reflection"]="note_reflection"
    note_ref: OpaqueId
    safe_excerpt: str=Field(min_length=1,max_length=1200)
    user_authored_ratio: float=Field(ge=0,le=1)
    cited_source_refs: list[OpaqueId]=Field(default_factory=list,max_length=20)
    structured: bool=False

class DialogueExplanationEvidence(StrictModel):
    payload_kind: Literal["dialogue_explanation"]="dialogue_explanation"
    dialogue_ref: OpaqueId
    safe_excerpt: str=Field(min_length=1,max_length=1200)
    target_concept: str=Field(min_length=1,max_length=200)
    user_explaining: bool
    ai_generated_ratio: float=Field(ge=0,le=1)
    rubric_score: float|None=Field(default=None,ge=0,le=1)
    error_codes: list[OpaqueId]=Field(default_factory=list,max_length=20)

    _closed_error_codes = field_validator("error_codes")(_validate_error_codes)

class ManualEvidence(StrictModel):
    payload_kind: Literal["manual"]="manual"
    observation_ref: OpaqueId
    rubric_score: float=Field(ge=0,le=1)
    observer_role: Literal["teacher","reviewer","system"]
    safe_summary: str=Field(min_length=1,max_length=500)
    private_comment: str|None=Field(default=None,max_length=1200)

class SelfReportEvidence(StrictModel):
    payload_kind: Literal["self_report"]="self_report"
    confidence_claim: float=Field(ge=0,le=1)
    safe_statement: str=Field(min_length=1,max_length=500)

EvidencePayload=Annotated[Union[
    QuestionAttemptEvidence,AssessmentResultEvidence,CodeExecutionEvidence,AdventureEvidence,ResourceInteractionEvidence,
    NoteReflectionEvidence,DialogueExplanationEvidence,ManualEvidence,SelfReportEvidence
],Field(discriminator="payload_kind")]
