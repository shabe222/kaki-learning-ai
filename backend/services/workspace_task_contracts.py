"""Strict contracts shared by the semantic router, task machine and adapters."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class SemanticAction(str, Enum):
    NEW_TASK = "new_task"
    SUPPLEMENT = "supplement"
    REVISE = "revise"
    CONFIRM = "confirm"
    CANCEL = "cancel"
    REPLACE = "replace"
    QUESTION = "question"
    NORMAL_CHAT = "normal_chat"
    ARTIFACT_ACTION = "artifact_action"


class ArtifactOperation(str, Enum):
    RUN = "run"
    REVISE = "revise"
    EXPORT = "export"
    OPEN = "open"
    EXPLAIN = "explain"


class AgentType(str, Enum):
    EXAM = "exam"
    LECTURE = "lecture"
    CODE = "code"
    VIDEO = "video"
    MINDMAP = "mindmap"
    PRESENTATION = "presentation"
    SEARCH = "search"
    LEARNING_GRAPH = "learning_graph"


class TaskState(str, Enum):
    IDLE = "idle"
    PLANNED = "planned"
    COLLECTING_REQUIREMENTS = "collecting_requirements"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    QUEUED = "queued"
    RUNNING = "running"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    FALLBACK_COMPLETED = "fallback_completed"
    CANCELLED = "cancelled"


class SemanticRoute(BaseModel):
    """The only output accepted from the LLM semantic router."""

    model_config = ConfigDict(extra="forbid")

    action: SemanticAction
    agent_type: Optional[AgentType] = None
    artifact_operation: Optional[ArtifactOperation] = None
    target_artifact_id: Optional[int] = Field(default=None, ge=1)
    slot_updates: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = Field(ge=0.0, le=1.0)
    reason_code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_\-]+$")


class AgentTask(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    task_id: str = Field(default_factory=lambda: str(uuid4()))
    user_id: int
    thread_id: str
    agent_type: AgentType
    slots: Dict[str, Any] = Field(default_factory=dict)
    state: TaskState = TaskState.COLLECTING_REQUIREMENTS
    version: int = 1
    confirmed_version: Optional[int] = None
    execution_key: Optional[str] = None
    artifact_id: Optional[int] = None
    error_code: Optional[str] = None
    conversation_id: Optional[str] = None
    resource_type: Optional[str] = None
    retry_count: int = Field(default=0, ge=0)
    failure_reason: Optional[str] = None
    source_artifact_id: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def model_post_init(self, __context: Any) -> None:
        self.conversation_id = self.conversation_id or self.thread_id
        self.resource_type = self.resource_type or self.agent_type.value
        if self.source_artifact_id is None and self.slots.get("source_artifact_id") is not None:
            self.source_artifact_id = int(self.slots["source_artifact_id"])
        self.failure_reason = self.failure_reason or self.error_code


class TaskDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: Optional[AgentTask] = None
    mode: str = "task"
    reply: str = ""
    execute: bool = False
    execution_payload: Optional[Dict[str, Any]] = None
