from typing import Any, Literal

from pydantic import BaseModel, Field


GameSceneId = Literal["scene01", "scene02", "scene03", "scene04Ready", "scene04", "scene05Ready"]


class CanonicalGameStateUpdateRequest(BaseModel):
    base_revision: int = Field(ge=1)
    current_scene: GameSceneId
    story_state: dict[str, Any] = Field(default_factory=dict)
    presentation_checkpoint: dict[str, Any] = Field(default_factory=dict)
