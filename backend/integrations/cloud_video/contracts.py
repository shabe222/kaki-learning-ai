"""Strict public contracts for the internal KAKI teaching-video pipeline."""
from __future__ import annotations

from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _clean_list(value: Any, *, item_limit: int = 180, list_limit: int = 12) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    result: list[str] = []
    for item in value:
        cleaned = _clean(item, item_limit)
        if cleaned and cleaned not in result:
            result.append(cleaned)
        if len(result) >= list_limit:
            break
    return result


class StyleContext(BaseModel):
    """Expression preferences only; this model cannot carry learning facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    tone: str = ""
    pace: str = ""
    example_preference: str = ""
    visual_preference: str = ""

    @classmethod
    def from_profile(cls, profile: Mapping[str, Any] | None) -> "StyleContext":
        source = dict(profile or {})
        return cls(
            tone=_clean(source.get("tone") or source.get("preferred_tone"), 80),
            pace=_clean(source.get("pace") or source.get("preferred_pace"), 40),
            example_preference=_clean(source.get("example_preference"), 120),
            visual_preference=_clean(source.get("visual_preference"), 120),
        )


class EvidenceTeachingContext(BaseModel):
    """Server-trusted teaching facts; Profile guesses never enter this model."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    learning_goal: str = ""
    graph_node: str = ""
    verified_weaknesses: list[str] = Field(default_factory=list)
    next_action: str = ""

    @classmethod
    def from_trusted_evidence(
        cls, evidence: Mapping[str, Any] | None,
    ) -> "EvidenceTeachingContext":
        source = dict(evidence or {})
        return cls(
            learning_goal=_clean(source.get("learning_goal"), 200),
            graph_node=_clean(source.get("graph_node"), 160),
            verified_weaknesses=_clean_list(source.get("verified_weaknesses")),
            next_action=_clean(source.get("next_action"), 240),
        )


class VideoGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    topic: str = Field(min_length=1, max_length=200)
    user_id: int = Field(gt=0)
    thread_id: str = Field(min_length=1, max_length=100)
    duration_sec: int = Field(default=45, ge=15, le=600)
    provider: Literal["s2v", "emo"] = "s2v"
    dry_run: bool = False
    style_context: StyleContext = Field(default_factory=StyleContext)
    evidence_context: EvidenceTeachingContext = Field(default_factory=EvidenceTeachingContext)
    instruction: str = Field(default="", max_length=500)
    source_context: str = Field(default="", max_length=12000)
    # 版本级视觉快照：kaki | ocean。只决定帧的调色与材质，不决定讲稿内容或镜头内容。
    theme: str = Field(default="kaki", max_length=20)
