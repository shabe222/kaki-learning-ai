from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import (
    EdgeType,
    GenerationMode,
    NodeStatus,
    NodeType,
    ResourcePlanStatus,
    ResourceTrigger,
    ResourceType,
    Severity,
    UpdateMode,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class SourceRef(StrictModel):
    ref_id: str = Field(min_length=1, max_length=100)
    kind: str = Field(min_length=1, max_length=30)
    title: str | None = Field(default=None, max_length=200)
    content_hash: str | None = Field(default=None, max_length=128)
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)


class SourceOutlineItem(StrictModel):
    outline_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    level: int = Field(ge=1, le=8)
    order: int = Field(ge=0, le=10000)
    parent_outline_id: str | None = Field(default=None, max_length=100)
    source_ref_id: str | None = Field(default=None, max_length=100)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def page_order(self):
        if self.page_start and self.page_end and self.page_end < self.page_start:
            raise ValueError("page_end must be >= page_start")
        return self


class SourceSummary(StrictModel):
    summary_id: str = Field(min_length=1, max_length=100)
    source_ref_id: str = Field(min_length=1, max_length=100)
    outline_id: str | None = Field(default=None, max_length=100)
    text: str = Field(min_length=4, max_length=4000)
    key_terms: list[str] = Field(default_factory=list, max_length=30)


class ResourceCapability(StrictModel):
    resource_type: ResourceType
    agent_key: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    constraints: dict[str, Any] = Field(default_factory=dict)


class ExistingResourcePlanSummary(StrictModel):
    resource_plan_id: str
    continuity_key: str | None = None
    node_id: str
    resource_type: ResourceType
    purpose: str = ""
    generation_spec: dict[str, Any] = Field(default_factory=dict)
    required_inputs: list[str] = Field(default_factory=list)
    trigger: ResourceTrigger = ResourceTrigger.USER_STARTS_LEARNING
    status: ResourcePlanStatus = ResourcePlanStatus.PLANNED
    priority: int = Field(default=50, ge=1, le=100)
    artifact_id: int | None = None
    private_annotations: dict[str, Any] = Field(default_factory=dict)


class ExistingNodeSummary(StrictModel):
    node_id: str
    continuity_key: str | None = None
    title: str
    summary: str = ""
    node_type: NodeType = NodeType.CONCEPT
    learning_objectives: list[str] = Field(default_factory=list)
    key_concepts: list[str] = Field(default_factory=list)
    mastery_evidence: list[str] = Field(default_factory=list)
    difficulty_band: Literal["introductory", "basic", "intermediate", "advanced"] = "basic"
    estimated_effort_minutes: int = Field(default=30, ge=5, le=1200)
    source_refs: list[str] = Field(default_factory=list)
    resource_plan_ids: list[str] = Field(default_factory=list)
    status: NodeStatus = NodeStatus.PLANNED
    unit_id: str | None = None


class ExistingEdgeSummary(StrictModel):
    edge_id: str
    source_node_id: str
    target_node_id: str
    edge_type: EdgeType
    reason: str = ""


class ExistingUnitSummary(StrictModel):
    unit_id: str
    continuity_key: str | None = None
    title: str
    summary: str = ""
    node_ids: list[str] = Field(default_factory=list)
    unit_order: int = 0


class ExistingGraphSummary(StrictModel):
    graph_id: str
    graph_revision: int = Field(ge=1)
    graph_title: str = ""
    graph_summary: str = ""
    learning_units: list[ExistingUnitSummary] = Field(default_factory=list)
    nodes: list[ExistingNodeSummary] = Field(default_factory=list)
    edges: list[ExistingEdgeSummary] = Field(default_factory=list)
    resource_plans: list[ExistingResourcePlanSummary] = Field(default_factory=list)


class GraphConstraints(StrictModel):
    min_nodes: int = Field(default=2, ge=1, le=100)
    max_nodes: int = Field(default=40, ge=2, le=200)
    max_edges: int = Field(default=120, ge=1, le=1000)
    max_depth: int = Field(default=12, ge=1, le=50)
    max_resources_per_node: int = Field(default=5, ge=1, le=12)
    requested_resource_types: list[ResourceType] = Field(default_factory=list)
    forbidden_public_keys: list[str] = Field(
        default_factory=lambda: [
            "raw_model_output",
            "source_private_metadata",
            "validation_details",
            "repair_reason",
            "fallback_reason",
            "provider",
            "model_id",
            "prompt",
            "prompt_hash",
            "schema_hash",
            "profile",
            "learner_state",
            "database",
            "generation_spec",
            "required_inputs",
            "agent_key",
            "internal_instruction",
            "source_query",
            "private_annotations",
        ]
    )

    @model_validator(mode="after")
    def bounds(self):
        if self.min_nodes > self.max_nodes:
            raise ValueError("min_nodes must be <= max_nodes")
        return self


class LearningGraphRequest(StrictModel):
    request_id: str = Field(min_length=4, max_length=100)
    graph_id: str | None = Field(default=None, max_length=100)
    graph_revision: int = Field(default=0, ge=0)
    user_goal: str = Field(min_length=2, max_length=1000)
    domain: str = Field(min_length=2, max_length=100)
    locale: str = Field(default="zh-CN", max_length=20)
    source_refs: list[SourceRef] = Field(default_factory=list, max_length=40)
    source_outline: list[SourceOutlineItem] = Field(min_length=1, max_length=500)
    source_summaries: list[SourceSummary] = Field(default_factory=list, max_length=200)
    existing_graph_summary: ExistingGraphSummary | None = None
    resource_capabilities: list[ResourceCapability] = Field(min_length=1, max_length=40)
    constraints: GraphConstraints = Field(default_factory=GraphConstraints)
    update_mode: UpdateMode = UpdateMode.CREATE
    replaced_source_ref_ids: list[str] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def consistency(self):
        if self.update_mode == UpdateMode.CREATE:
            if self.existing_graph_summary is not None:
                raise ValueError("CREATE must not include existing_graph_summary")
            if self.graph_revision != 0:
                raise ValueError("CREATE graph_revision must be 0")
            if self.replaced_source_ref_ids:
                raise ValueError("replaced_source_ref_ids is only allowed for REPLACE_SOURCE")
        elif self.existing_graph_summary is None:
            raise ValueError("existing_graph_summary is required for update modes")

        if self.update_mode == UpdateMode.REPLACE_SOURCE:
            if not self.replaced_source_ref_ids:
                raise ValueError("replaced_source_ref_ids is required for REPLACE_SOURCE")
        elif self.replaced_source_ref_ids:
            raise ValueError("replaced_source_ref_ids is only allowed for REPLACE_SOURCE")

        if self.existing_graph_summary:
            if self.graph_id and self.graph_id != self.existing_graph_summary.graph_id:
                raise ValueError("graph_id conflicts with existing_graph_summary")
            if self.graph_revision and self.graph_revision != self.existing_graph_summary.graph_revision:
                raise ValueError("graph_revision conflicts with existing_graph_summary")

            historical_allowed = {
                NodeStatus.COMPLETED,
                NodeStatus.LEARNING,
                NodeStatus.SUPERSEDED,
            }
            for node in self.existing_graph_summary.nodes:
                incomplete = not (
                    node.learning_objectives
                    and node.key_concepts
                    and node.mastery_evidence
                )
                if incomplete and node.status not in historical_allowed:
                    raise ValueError(
                        f"active existing node contract is incomplete: {node.node_id}"
                    )

        ref_ids = {r.ref_id for r in self.source_refs}
        for item in self.source_outline:
            if item.source_ref_id and item.source_ref_id not in ref_ids:
                raise ValueError(f"unknown source_ref_id in source_outline: {item.source_ref_id}")
        for summary in self.source_summaries:
            if summary.source_ref_id not in ref_ids:
                raise ValueError(f"unknown source_ref_id in source_summaries: {summary.source_ref_id}")
        return self



# Explicit provider-facing projection. These models intentionally omit durable database
# identities and every private resource-generation field.
class ModelLearningUnitContext(StrictModel):
    continuity_key: str
    title: str
    summary: str
    node_continuity_keys: list[str]
    unit_order: int


class ModelKnowledgeNodeContext(StrictModel):
    continuity_key: str
    title: str
    summary: str
    node_type: NodeType
    learning_objectives: list[str]
    key_concepts: list[str]
    mastery_evidence: list[str]
    difficulty_band: Literal["introductory", "basic", "intermediate", "advanced"]
    estimated_effort_minutes: int
    safe_source_ref_ids: list[str]
    status: NodeStatus


class ModelEdgeContext(StrictModel):
    source_node_continuity_key: str
    target_node_continuity_key: str
    edge_type: EdgeType
    reason: str


class ModelResourcePlanContext(StrictModel):
    continuity_key: str
    node_continuity_key: str
    resource_type: ResourceType
    purpose: str
    trigger: ResourceTrigger
    status: ResourcePlanStatus
    materialized: bool


class ExistingGraphModelContext(StrictModel):
    graph_revision: int
    graph_title: str
    graph_summary: str
    learning_units: list[ModelLearningUnitContext]
    nodes: list[ModelKnowledgeNodeContext]
    edges: list[ModelEdgeContext]
    resource_plans: list[ModelResourcePlanContext]


class ModelSourceRef(StrictModel):
    ref_id: str
    kind: str
    title: str | None = None
    content_hash: str | None = None


class ModelGraphConstraints(StrictModel):
    min_nodes: int
    max_nodes: int
    max_edges: int
    max_depth: int
    max_resources_per_node: int
    requested_resource_types: list[ResourceType]


class LearningGraphModelInput(StrictModel):
    user_goal: str
    domain: str
    locale: str
    source_refs: list[ModelSourceRef]
    source_outline: list[SourceOutlineItem]
    source_summaries: list[SourceSummary]
    existing_graph: ExistingGraphModelContext | None = None
    enabled_resource_types: list[ResourceType]
    constraints: ModelGraphConstraints
    update_mode: UpdateMode
    replaced_source_ref_ids: list[str]

# Model-owned candidate schema. Program assigns durable IDs and revisions.
class CandidateLearningUnit(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    continuity_key: str | None = Field(default=None, max_length=100)
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=4, max_length=500)
    node_keys: list[str] = Field(min_length=1, max_length=100)
    unit_order: int = Field(ge=0, le=1000)


class CandidateKnowledgeNode(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    continuity_key: str | None = Field(default=None, max_length=100)
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=4, max_length=800)
    node_type: NodeType
    learning_objectives: list[str] = Field(min_length=1, max_length=8)
    key_concepts: list[str] = Field(min_length=1, max_length=20)
    mastery_evidence: list[str] = Field(min_length=1, max_length=8)
    difficulty_band: Literal["introductory", "basic", "intermediate", "advanced"]
    estimated_effort_minutes: int = Field(ge=5, le=1200)
    source_ref_ids: list[str] = Field(default_factory=list, max_length=20)
    status: Literal[NodeStatus.PLANNED, NodeStatus.READY] = NodeStatus.PLANNED


class CandidateKnowledgeEdge(StrictModel):
    source_key: str = Field(min_length=1, max_length=100)
    target_key: str = Field(min_length=1, max_length=100)
    edge_type: EdgeType
    reason: str = Field(min_length=4, max_length=300)


class CandidateResourcePlan(StrictModel):
    key: str = Field(min_length=1, max_length=100)
    continuity_key: str | None = Field(default=None, max_length=100)
    node_key: str = Field(min_length=1, max_length=100)
    resource_type: ResourceType
    purpose: str = Field(min_length=4, max_length=300)
    generation_spec: dict[str, Any] = Field(default_factory=dict)
    required_inputs: list[str] = Field(default_factory=list, max_length=30)
    trigger: ResourceTrigger
    status: Literal[ResourcePlanStatus.PLANNED] = ResourcePlanStatus.PLANNED
    priority: int = Field(default=50, ge=1, le=100)


class CandidateBlueprint(StrictModel):
    blueprint_version: Literal["0.1"] = "0.1"
    graph_title: str = Field(min_length=2, max_length=200)
    graph_summary: str = Field(min_length=8, max_length=1000)
    learning_units: list[CandidateLearningUnit] = Field(min_length=1, max_length=80)
    nodes: list[CandidateKnowledgeNode] = Field(min_length=1, max_length=200)
    edges: list[CandidateKnowledgeEdge] = Field(default_factory=list, max_length=1000)
    resource_plans: list[CandidateResourcePlan] = Field(default_factory=list, max_length=1000)
    validation_hints: list[str] = Field(default_factory=list, max_length=30)
    continuity_keys: list[str] = Field(default_factory=list, max_length=30)


class LearningUnit(StrictModel):
    unit_id: str
    continuity_key: str
    title: str
    summary: str
    node_ids: list[str]
    unit_order: int


class KnowledgeNode(StrictModel):
    node_id: str
    continuity_key: str
    title: str
    summary: str
    node_type: NodeType
    learning_objectives: list[str]
    key_concepts: list[str]
    mastery_evidence: list[str]
    difficulty_band: Literal["introductory", "basic", "intermediate", "advanced"]
    estimated_effort_minutes: int
    source_refs: list[str]
    resource_plan_ids: list[str]
    status: NodeStatus
    unit_id: str | None = None


class KnowledgeEdge(StrictModel):
    edge_id: str
    source_node_id: str
    target_node_id: str
    edge_type: EdgeType
    reason: str


class ResourcePlan(StrictModel):
    resource_plan_id: str
    continuity_key: str
    node_id: str
    resource_type: ResourceType
    purpose: str
    generation_spec: dict[str, Any]
    required_inputs: list[str]
    trigger: ResourceTrigger
    status: ResourcePlanStatus
    priority: int
    artifact_id: int | None = None
    private_annotations: dict[str, Any] = Field(default_factory=dict)


class ChangeSet(StrictModel):
    added_units: list[str] = Field(default_factory=list)
    updated_units: list[str] = Field(default_factory=list)
    superseded_units: list[str] = Field(default_factory=list)
    added_nodes: list[str] = Field(default_factory=list)
    updated_nodes: list[str] = Field(default_factory=list)
    superseded_nodes: list[str] = Field(default_factory=list)
    added_edges: list[str] = Field(default_factory=list)
    removed_edges: list[str] = Field(default_factory=list)
    added_resource_plans: list[str] = Field(default_factory=list)
    updated_resource_plans: list[str] = Field(default_factory=list)
    removed_resource_plans: list[str] = Field(default_factory=list)
    preserved_generating_resource_plans: list[str] = Field(default_factory=list)
    preserved_generated_resource_plans: list[str] = Field(default_factory=list)
    preserved_failed_resource_plans: list[str] = Field(default_factory=list)
    # Backward-compatible aggregate retained for v0.1.0 consumers.
    resource_plan_changes: list[str] = Field(default_factory=list)


class LearningGraphBlueprint(StrictModel):
    blueprint_version: Literal["0.1"] = "0.1"
    graph_id: str
    graph_revision: int = Field(ge=1)
    graph_title: str
    graph_summary: str
    learning_units: list[LearningUnit]
    nodes: list[KnowledgeNode]
    edges: list[KnowledgeEdge]
    resource_plans: list[ResourcePlan]
    validation_hints: list[str] = Field(default_factory=list)
    continuity_keys: list[str] = Field(default_factory=list)
    changes: ChangeSet = Field(default_factory=ChangeSet)
    status: Literal["draft", "needs_review", "valid"] = "draft"
    fallback_used: bool = False
    needs_review: bool = False


class ValidationIssue(StrictModel):
    code: str
    severity: Severity
    path: str
    message: str
    repairable: bool = False


class ValidationReport(StrictModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
    graph_depth: int = 0
    root_node_ids: list[str] = Field(default_factory=list)
    terminal_node_ids: list[str] = Field(default_factory=list)


class PublicLearningUnit(StrictModel):
    unit_id: str
    title: str
    summary: str
    node_ids: list[str]
    unit_order: int


class PublicKnowledgeNode(StrictModel):
    node_id: str
    title: str
    summary: str
    node_type: NodeType
    learning_objectives: list[str]
    key_concepts: list[str]
    mastery_evidence: list[str]
    difficulty_band: Literal["introductory", "basic", "intermediate", "advanced"]
    estimated_effort_minutes: int
    source_refs: list[str]
    resource_plan_ids: list[str]
    status: NodeStatus
    unit_id: str | None = None


class PublicKnowledgeEdge(StrictModel):
    edge_id: str
    source_node_id: str
    target_node_id: str
    edge_type: EdgeType
    reason: str


class PublicResourcePlan(StrictModel):
    resource_plan_id: str
    node_id: str
    resource_type: ResourceType
    purpose: str
    trigger: ResourceTrigger
    status: ResourcePlanStatus
    priority: int
    artifact_id: int | None = None


class LearningGraphPublicView(StrictModel):
    graph_id: str
    graph_revision: int
    graph_title: str
    graph_summary: str
    learning_units: list[PublicLearningUnit]
    nodes: list[PublicKnowledgeNode]
    edges: list[PublicKnowledgeEdge]
    resource_plans: list[PublicResourcePlan]
    status: str


class RunMetadata(StrictModel):
    generation_mode: GenerationMode
    provider: str | None = None
    model_id: str | None = None
    raw_attempts: int = 0
    repair_attempted: bool = False
    fallback_reason: str | None = None
    prompt_hash: str
    schema_hash: str


class OrchestrationResult(StrictModel):
    blueprint: LearningGraphBlueprint
    validation: ValidationReport
    public_view: LearningGraphPublicView
    metadata: RunMetadata
    raw_model_output: Any | None = None
    validation_details: list[ValidationIssue] = Field(default_factory=list)
