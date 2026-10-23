from __future__ import annotations
from typing import Any
from .models import LearningGraphBlueprint

# Formal project integration must implement this port using its existing LearningGraph/GraphNode service.
class LearningGraphPersistencePort:
    async def save_blueprint(self, *, user_id: int, blueprint: LearningGraphBlueprint, idempotency_key: str) -> dict[str, Any]:
        raise NotImplementedError

# Artifact generation remains outside this orchestrator. The existing GeneratedItem store is the fact source.
class ResourceGenerationPort:
    async def materialize_plan(self, *, user_id: int, thread_id: str, graph_id: str, node_id: str, resource_plan_id: str) -> int:
        """Return existing GeneratedItem.id after a real resource agent succeeds."""
        raise NotImplementedError

# Learning evidence remains outside this orchestrator. Existing WorkspaceLearningEvent is the fact source.
class LearningEventPort:
    async def emit(self, *, user_id: int, thread_id: str, artifact_id: int, event_type: str, payload: dict[str, Any]) -> None:
        raise NotImplementedError
