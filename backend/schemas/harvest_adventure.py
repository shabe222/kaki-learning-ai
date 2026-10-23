from typing import Any, Literal

from pydantic import BaseModel, Field


class AdventurePrepareRequest(BaseModel):
    """POST /api/adventure/prepare — 由服务端构造，前端不应直接提交私有字段。"""
    topic_hint: str = Field(min_length=2, max_length=120)
    explicit_confusion: str | None = Field(default=None, max_length=600)
    thread_id: str | None = Field(default=None, max_length=100)
    source_ids: list[str] = Field(default_factory=list, max_length=20)


class AdventurePrepareAdjustRequest(BaseModel):
    plan_id: str = Field(min_length=1, max_length=64)
    focus_adjustment: str = Field(min_length=2, max_length=500)


class AdventureStartRequest(BaseModel):
    topic: str = Field(min_length=2, max_length=120)
    magic_school: Literal["fire", "water", "wind", "wood"]
    source_ids: list[str] = Field(default_factory=list, max_length=20)
    thread_id: str | None = Field(default=None, max_length=100)
    source_type: str | None = Field(default=None, max_length=40)
    force_new: bool = Field(default=False)
    prepared_plan_id: str | None = Field(default=None, max_length=64)
    identity_id: str | None = Field(default=None, max_length=40)
    talent_id: str | None = Field(default=None, max_length=40)


class AdventureAdvanceRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)
    action: Literal["continue", "submit", "roll", "choose_branch", "choose_strategy", "hint", "claim"]
    payload: dict[str, Any] = Field(default_factory=dict)


class AdventureRewardEquipRequest(BaseModel):
    artifact_id: int = Field(gt=0)
    replace_confirmed: bool = Field(default=False)


class AdventureRewardUnequipRequest(BaseModel):
    artifact_id: int = Field(gt=0)
