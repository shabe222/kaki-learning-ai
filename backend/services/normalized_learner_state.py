"""Read-only learner-state projection shared by every learning consumer.

The projection does not own mastery, profile, graph, evidence, or Tutor facts.
It merges existing authorities with explicit precedence and records the source
of every preference/mastery signal.  Formal Evaluator facts always win over
legacy capability/profile fallbacks for the same knowledge point.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Any, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import models as db_models


PREFERENCE_TAXONOMY: tuple[str, ...] = (
    "visual", "video", "reading", "document", "practice", "code",
    "example", "step_by_step", "concise", "detailed",
)

_PREFERENCE_TERMS: dict[str, tuple[str, ...]] = {
    "visual": ("图示", "图解", "可视化", "思维导图", "图表", "动画演示"),
    "video": ("视频", "短视频", "看视频", "视频讲解"),
    "reading": ("阅读", "读文章", "看文字", "文字材料", "文字学习"),
    "document": ("文档", "讲义", "笔记", "结构化说明", "学习资料"),
    "practice": ("练习", "做题", "实践", "动手", "边做边学", "先练"),
    "code": ("代码", "编程", "python", "代码示例", "自己写"),
    "example": ("例子", "示例", "案例", "实例", "例题"),
    "step_by_step": ("一步一步", "分步骤", "拆开讲", "慢慢来", "一次一个"),
    "concise": ("简短", "精简", "直接", "短一点", "少说"),
    "detailed": ("详细", "细致", "展开讲", "完整解释", "讲清楚"),
}

_STRUCTURED_VALUE_MAP: dict[str, tuple[str, ...]] = {
    "visual": ("visual", "diagram", "mindmap"),
    "video": ("video", "auditory"),
    "reading": ("reading", "text"),
    "document": ("document", "lecture", "handout"),
    "practice": ("practice", "practice_first", "hands_on", "interactive", "kinesthetic"),
    "code": ("code", "coding", "programming"),
    "example": ("example", "example_first", "worked_example"),
    "step_by_step": ("stepwise", "step_by_step", "scaffolding"),
    "concise": ("concise", "short"),
    "detailed": ("detailed", "long"),
}

_RESOURCE_TO_PREFERENCE = {
    "mindmap": "visual", "ppt": "visual", "presentation": "visual",
    "workspace_presentation": "visual", "interactive_lesson": "visual",
    "video": "video", "document": "document", "lecture": "document",
    "workspace_lecture": "document", "note": "reading", "reading": "reading",
    "question": "practice", "exam": "practice", "quiz": "practice",
    "practice": "practice", "code": "code", "workspace_code": "code",
}


class PreferenceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preference: str
    evidence_type: Literal["explicit", "behavioral", "profile"]
    source: str
    polarity: Literal["supporting", "opposing"] = "supporting"
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_count: int = Field(default=1, ge=1)
    evidence_refs: list[str] = Field(default_factory=list)
    observed_at: str | None = None


class NormalizedLearnerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    current_goal: str | None = None
    current_topic: str | None = None
    knowledge_level: str = "unknown"
    topic_mastery: str = "unknown"
    primary_barrier: dict[str, Any] = Field(default_factory=dict)
    support_level: str = "minimal_scaffold"
    transfer_readiness: str = "not_ready"
    next_teaching_focus: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    mastery_by_knowledge_point: dict[str, float] = Field(default_factory=dict)
    mastery_sources: dict[str, str] = Field(default_factory=dict)
    weak_points: list[str] = Field(default_factory=list)
    strengths: list[str] = Field(default_factory=list)
    preferred_modalities: list[str] = Field(default_factory=list)
    preference_evidence: list[PreferenceEvidence] = Field(default_factory=list)
    presentation_preference: str = "balanced"
    practice_preference: str = "balanced"
    code_preference: str = "balanced"
    pace: str = "balanced"
    motivation: dict[str, Any] = Field(default_factory=dict)
    recent_errors: list[dict[str, Any]] = Field(default_factory=list)
    recent_evidence: list[dict[str, Any]] = Field(default_factory=list)
    recent_learning_events: list[dict[str, Any]] = Field(default_factory=list)
    recent_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    current_tutor_session: dict[str, Any] | None = None
    last_learning_time: str | None = None
    state_version: str
    authority_order: list[str] = Field(default_factory=lambda: [
        "formal_evaluator", "server_learning_event", "explicit_preference",
        "behavioral_preference", "legacy_profile_fallback",
    ])


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def preference_tokens(value: Any) -> list[str]:
    """Map text/structured values to the public preference taxonomy."""
    values = value if isinstance(value, (list, tuple, set)) else [value]
    found: list[str] = []
    for raw in values:
        text = _text(raw).lower()
        if not text:
            continue
        for preference in PREFERENCE_TAXONOMY:
            structured = _STRUCTURED_VALUE_MAP[preference]
            if text in structured or any(term.lower() in text for term in _PREFERENCE_TERMS[preference]):
                if preference not in found:
                    found.append(preference)
    return found


def _impression_preferences(item: Any) -> list[str]:
    key = _text(getattr(item, "impression_key", "")).lower()
    summary = _text(getattr(item, "summary", ""))
    direct = key.rsplit(".", 1)[-1]
    aliases = {
        "practice_first": "practice", "hands_on": "practice",
        "example_first": "example", "stepwise": "step_by_step",
        "avoid_video": "video", "avoid_document": "document",
        "avoid_code": "code", "avoid_practice": "practice",
    }
    values = []
    mapped = aliases.get(direct, direct)
    if mapped in PREFERENCE_TAXONOMY:
        values.append(mapped)
    for value in preference_tokens(summary):
        if value not in values:
            values.append(value)
    return values


def _add_evidence(
    output: list[PreferenceEvidence], *, preference: str, evidence_type: str,
    source: str, confidence: float, refs: Iterable[str] = (), count: int = 1,
    polarity: str = "supporting", observed_at: str | None = None,
) -> None:
    if preference not in PREFERENCE_TAXONOMY:
        return
    output.append(PreferenceEvidence(
        preference=preference,
        evidence_type=evidence_type,
        source=source,
        polarity=polarity,
        confidence=max(0.0, min(1.0, float(confidence))),
        evidence_count=max(1, int(count)),
        evidence_refs=list(dict.fromkeys(str(ref) for ref in refs if str(ref))),
        observed_at=observed_at,
    ))


def _profile_preference_evidence(profile: Any | None) -> list[PreferenceEvidence]:
    if profile is None:
        return []
    output: list[PreferenceEvidence] = []
    profile_data = dict(getattr(profile, "profile_data", None) or {})
    normalized = dict(profile_data.get("normalized_profile") or {})
    onboarding = dict(profile_data.get("profile_onboarding") or {})
    onboarding_entries = dict(onboarding.get("entries") or {})

    explicit_values: list[Any] = [normalized.get("preferred_modalities")]
    self_description = dict(normalized.get("self_description") or {})
    explicit_values.append(self_description.get("preference"))
    explicit_values.append(dict(onboarding_entries.get("learning_preference") or {}).get("value"))
    for raw in explicit_values:
        for preference in preference_tokens(raw):
            _add_evidence(
                output, preference=preference, evidence_type="explicit",
                source="onboarding_confirmed_preference", confidence=1.0,
                refs=[f"student-profile:{getattr(profile, 'profile_version', 1)}:onboarding"],
            )

    cognitive = dict(getattr(profile, "cognitive_profile", None) or {})
    behavior = dict(getattr(profile, "behavior_pattern", None) or {})
    structured_values = [
        cognitive.get("learning_style"), cognitive.get("teaching_method"),
        cognitive.get("response_style"), cognitive.get("learning_pace"),
        behavior.get("content_format_preference"), behavior.get("exercise_type_preference"),
        behavior.get("response_style"), behavior.get("learning_pace"),
    ]
    for raw in structured_values:
        for preference in preference_tokens(raw):
            _add_evidence(
                output, preference=preference, evidence_type="profile",
                source="structured_student_profile", confidence=0.85,
                refs=[f"student-profile:{getattr(profile, 'profile_version', 1)}:structured"],
            )
    return output


def _impression_evidence(items: Iterable[Any]) -> list[PreferenceEvidence]:
    output: list[PreferenceEvidence] = []
    for item in items:
        if _text(getattr(item, "status", "")).lower() in {"rejected", "uncertain"}:
            continue
        # evidence_refs 既可能是带 polarity 的 dict，也可能是 Librarian 存入的纯文本摘录；
        # 只有 dict 才参与支持/反对计数，文本摘录不能让偏好计数崩溃。
        raw_refs = getattr(item, "evidence_refs", None)
        if isinstance(raw_refs, str):
            try:
                raw_refs = json.loads(raw_refs)
            except (TypeError, ValueError):
                raw_refs = [raw_refs]
        refs = [ref for ref in (raw_refs or []) if isinstance(ref, dict)]
        supporting = [ref for ref in refs if str(ref.get("polarity") or "supporting") == "supporting"]
        opposing = [ref for ref in refs if str(ref.get("polarity") or "" ) == "opposing"]
        confirmed = bool(getattr(item, "confirmed_by_user", False))
        explicit = confirmed or any(bool(ref.get("explicit")) for ref in supporting)
        # A single behavioral click is never promoted to a stable preference.
        if not explicit and len(supporting) < 4:
            continue
        evidence_type = "explicit" if explicit else "behavioral"
        confidence = 1.0 if confirmed else float(getattr(item, "confidence", 0.0) or 0.0)
        key = _text(getattr(item, "impression_key", "")).lower()
        forced_opposing = ".avoid_" in key
        for preference in _impression_preferences(item):
            _add_evidence(
                output, preference=preference, evidence_type=evidence_type,
                source="confirmed_ai_impression" if confirmed else "conversation_preference_evidence",
                confidence=confidence, count=max(1, len(supporting)),
                refs=[f"impression:{getattr(item, 'id', '')}", *[str(ref.get("source_id") or "") for ref in refs]],
                polarity="opposing" if forced_opposing else "supporting",
                observed_at=_iso(getattr(item, "last_observed_at", None)),
            )
        # Existing positive impressions with repeated opposing evidence are weakened.
        if opposing and not forced_opposing:
            for preference in _impression_preferences(item):
                _add_evidence(
                    output, preference=preference, evidence_type="behavioral",
                    source="conversation_opposing_evidence", confidence=min(0.9, 0.45 + 0.1 * len(opposing)),
                    count=len(opposing), refs=[str(ref.get("source_id") or "") for ref in opposing],
                    polarity="opposing", observed_at=_iso(getattr(item, "last_observed_at", None)),
                )
    return output


def _event_preference_evidence(events: Iterable[Any], artifacts_by_id: dict[int, Any]) -> list[PreferenceEvidence]:
    grouped: dict[str, list[Any]] = {}
    for event in events:
        event_type = _text(getattr(event, "event_type", "")).lower()
        if "opened" not in event_type and "selected" not in event_type:
            continue
        payload = dict(getattr(event, "payload", None) or {})
        artifact = artifacts_by_id.get(int(getattr(event, "artifact_id", 0) or 0))
        kind = _text(payload.get("artifact_kind") or payload.get("resource_type") or payload.get("type") or getattr(artifact, "type", "")).lower()
        preference = next((mapped for token, mapped in _RESOURCE_TO_PREFERENCE.items() if token in kind), None)
        if preference:
            grouped.setdefault(preference, []).append(event)
    output: list[PreferenceEvidence] = []
    for preference, rows in grouped.items():
        if len(rows) < 4:
            continue
        _add_evidence(
            output, preference=preference, evidence_type="behavioral",
            source="repeated_resource_selection", confidence=min(0.85, 0.6 + 0.05 * len(rows)),
            count=len(rows), refs=[f"workspace-event:{row.id}" for row in rows[:12]],
            observed_at=_iso(max((row.created_at for row in rows if row.created_at), default=None)),
        )
    return output


def merge_preference_evidence(evidence: Iterable[PreferenceEvidence]) -> tuple[list[str], list[PreferenceEvidence]]:
    ordered = list(evidence)
    scores: dict[str, float] = {key: 0.0 for key in PREFERENCE_TAXONOMY}
    type_weight = {"explicit": 1.25, "profile": 0.8, "behavioral": 0.65}
    for item in ordered:
        score = type_weight[item.evidence_type] * item.confidence
        score *= 1.0 + min(0.45, 0.05 * max(0, item.evidence_count - 1))
        scores[item.preference] += score if item.polarity == "supporting" else -score
    modalities = [key for key in PREFERENCE_TAXONOMY if scores[key] >= 0.55]
    modalities.sort(key=lambda key: (-scores[key], PREFERENCE_TAXONOMY.index(key)))
    return modalities, ordered


def _legacy_capability_points(profile: Any | None) -> dict[str, float]:
    matrix = dict(getattr(profile, "capability_matrix", None) or {}) if profile is not None else {}
    points: dict[str, float] = {}
    for chapter, raw in matrix.items():
        values = dict(raw or {}) if isinstance(raw, dict) else {}
        candidates = values.get("knowledge_points") if isinstance(values.get("knowledge_points"), dict) else values
        for name, detail in dict(candidates or {}).items():
            if name in {"score", "mastery", "confidence", "notes", "average"}:
                continue
            value = detail.get("score", detail.get("mastery")) if isinstance(detail, dict) else detail
            try:
                score = float(value)
            except (TypeError, ValueError):
                continue
            points[f"{chapter}/{name}"] = round(score / 100.0 if score > 1 else score, 3)
    return points


def merge_mastery_sources(
    legacy_mastery: dict[str, float], formal_mastery: dict[str, float]
) -> tuple[dict[str, float], dict[str, str]]:
    """Merge projections while making the formal Evaluator authoritative per key."""
    mastery = dict(legacy_mastery)
    sources = {key: "legacy_profile_fallback" for key in legacy_mastery}
    for key, value in formal_mastery.items():
        mastery[key] = value
        sources[key] = "formal_evaluator"
    return mastery, sources


async def _formal_mastery(db: AsyncSession, user_id: int) -> tuple[dict[str, float], list[dict[str, Any]], list[str]]:
    record_model = getattr(db_models, "LearningEvaluationResultRecord", None)
    node_model = getattr(db_models, "GraphNode", None)
    if record_model is None or node_model is None:
        return {}, [], []
    records = list((await db.execute(
        select(record_model).where(record_model.user_id == user_id)
        .order_by(desc(record_model.created_at), desc(record_model.id)).limit(120)
    )).scalars().all())
    if not records:
        return {}, [], []
    node_ids = {int(row.node_id) for row in records}
    nodes = list((await db.execute(select(node_model).where(node_model.id.in_(node_ids)))).scalars().all())
    titles = {int(node.id): _text(node.title) or _text(node.external_id) or f"node-{node.id}" for node in nodes}
    mastery: dict[str, float] = {}
    evidence: list[dict[str, Any]] = []
    weak: list[str] = []
    records_by_node: dict[int, list[Any]] = {}
    for item in records:
        records_by_node.setdefault(int(item.node_id), []).append(item)
    seen_nodes: set[int] = set()
    for row in records:
        node_id = int(row.node_id)
        if node_id in seen_nodes:
            continue
        seen_nodes.add(node_id)
        projection = dict(row.public_projection or {})
        assessments = list(projection.get("node_assessments") or [])
        assessment = assessments[0] if assessments else {}
        signal = assessment.get("mastery_signal")
        try:
            score = max(0.0, min(1.0, float(signal)))
        except (TypeError, ValueError):
            continue
        title = titles.get(node_id, _text(assessment.get("node_id")) or f"node-{node_id}")
        previous_status = None
        previous_score = None
        prior_rows = records_by_node.get(node_id, [])[1:2]
        if prior_rows:
            prior_projection = dict(prior_rows[0].public_projection or {})
            prior_assessments = list(prior_projection.get("node_assessments") or [])
            prior = prior_assessments[0] if prior_assessments else {}
            previous_status = _text(prior.get("assessment_status")) or None
            try:
                previous_score = max(0.0, min(1.0, float(prior.get("mastery_signal"))))
            except (TypeError, ValueError):
                previous_score = None
        mastery[title] = round(score, 3)
        misconceptions = list(assessment.get("misconceptions") or assessment.get("unresolved_misconceptions") or [])
        if score < 0.6 or misconceptions:
            weak.append(title)
        evidence.append({
            "source": "formal_evaluator", "result_ref": row.result_ref,
            "evaluation_revision": int(row.evaluation_revision or 0),
            "knowledge_point": title, "mastery": round(score, 3),
            "assessment_status": _text(assessment.get("assessment_status")) or "INSUFFICIENT_EVIDENCE",
            "previous_assessment_status": previous_status,
            "previous_mastery": round(previous_score, 3) if previous_score is not None else None,
            "change": round(score - previous_score, 3) if previous_score is not None else None,
            "evidence_event_ids": list(row.evidence_event_ids or []),
            "created_at": _iso(row.created_at),
        })
    return mastery, evidence[:20], list(dict.fromkeys(weak))


def _knowledge_level(mastery: dict[str, float], normalized: dict[str, Any], cognitive: dict[str, Any]) -> str:
    if mastery:
        average = sum(mastery.values()) / len(mastery)
        return "secure" if average >= 0.8 else "developing" if average >= 0.55 else "emerging"
    current = dict(normalized.get("current_level") or {})
    return _text(current.get("ml") or cognitive.get("knowledge_level") or cognitive.get("level")).lower() or "unknown"


def _state_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "nls-" + sha256(canonical.encode("utf-8")).hexdigest()[:20]


async def get_normalized_learner_state(db: AsyncSession, user_id: int) -> NormalizedLearnerState:
    """Build one bounded read model without mutating any source authority."""
    profile_model = db_models.StudentProfile
    profile = await db.scalar(select(profile_model).where(profile_model.user_id == user_id))
    profile_data = dict(getattr(profile, "profile_data", None) or {}) if profile else {}
    normalized = dict(profile_data.get("normalized_profile") or {})
    cognitive = dict(getattr(profile, "cognitive_profile", None) or {}) if profile else {}
    motivation_profile = dict(getattr(profile, "motivation_profile", None) or {}) if profile else {}

    impression_model = db_models.AIImpression
    impressions = list((await db.execute(
        select(impression_model).where(impression_model.user_id == user_id)
        .order_by(desc(impression_model.updated_at)).limit(80)
    )).scalars().all())

    event_model = db_models.WorkspaceLearningEvent
    events = list((await db.execute(
        select(event_model).where(event_model.user_id == user_id)
        .order_by(desc(event_model.id)).limit(200)
    )).scalars().all())

    artifact_model = db_models.GeneratedItem
    artifacts = list((await db.execute(
        select(artifact_model).where(artifact_model.user_id == user_id)
        .order_by(desc(artifact_model.id)).limit(30)
    )).scalars().all())
    artifacts_by_id = {int(item.id): item for item in artifacts}

    tutor_model = db_models.TutorSession
    tutor = await db.scalar(
        select(tutor_model).where(tutor_model.user_id == user_id)
        .order_by(desc(tutor_model.updated_at), desc(tutor_model.id)).limit(1)
    )

    preference_evidence = [
        *_profile_preference_evidence(profile),
        *_impression_evidence(impressions),
        *_event_preference_evidence(events, artifacts_by_id),
    ]
    preferred, preference_evidence = merge_preference_evidence(preference_evidence)

    formal_mastery, formal_evidence, formal_weak = await _formal_mastery(db, user_id)
    legacy_mastery = _legacy_capability_points(profile)
    mastery, mastery_sources = merge_mastery_sources(legacy_mastery, formal_mastery)

    weak_model = db_models.WeakPoint
    weak_rows = list((await db.execute(
        select(weak_model).where(weak_model.user_id == user_id, weak_model.status == "active")
        .order_by(desc(weak_model.occurrence_count), desc(weak_model.id)).limit(30)
    )).scalars().all())
    weak_points = list(dict.fromkeys([
        *formal_weak,
        *[str(row.knowledge_point) for row in weak_rows if str(row.knowledge_point) not in formal_mastery],
        *[key for key, value in mastery.items() if value < 0.55],
    ]))[:20]
    strengths = [key for key, value in mastery.items() if value >= 0.8 and key not in weak_points][:20]

    current_node = None
    if tutor:
        current_node = next((item for item in list(tutor.plan_nodes or []) if str(item.get("id")) == str(tutor.current_node_id)), None)
    goal_value = dict(normalized.get("learning_goal") or {}).get("primary")
    current_goal = _text(getattr(tutor, "goal", "") or goal_value or getattr(profile, "target_role", "")) or None
    current_topic = _text((current_node or {}).get("title") or normalized.get("current_focus") or (artifacts[0].chapter if artifacts else "")) or None

    recent_events = [{
        "event_id": int(row.id), "event_type": row.event_type,
        "thread_id": row.thread_id, "artifact_id": int(row.artifact_id),
        "created_at": _iso(row.created_at),
    } for row in events[:20]]
    recent_artifacts = [{
        "artifact_id": int(row.id), "type": row.type, "title": row.title,
        "topic": row.chapter, "created_at": _iso(row.created_at),
    } for row in artifacts[:12]]
    recent_errors = []
    for row in events:
        payload = dict(row.payload or {})
        grading = list(payload.get("grading") or [])
        wrong_count = sum(1 for item in grading if item.get("correct") is False)
        if wrong_count:
            recent_errors.append({
                "event_id": int(row.id), "event_type": row.event_type,
                "wrong_count": wrong_count, "created_at": _iso(row.created_at),
            })
        if len(recent_errors) >= 12:
            break

    latest_times = [
        *[row.created_at for row in events if row.created_at],
        *[row.created_at for row in artifacts if row.created_at],
        *([tutor.updated_at] if tutor and tutor.updated_at else []),
    ]
    last_learning_time = _iso(max(latest_times)) if latest_times else None

    presentation = "concise" if "concise" in preferred and "detailed" not in preferred else "detailed" if "detailed" in preferred else "balanced"
    practice_preference = "preferred" if "practice" in preferred else "balanced"
    code_preference = "preferred" if "code" in preferred else "balanced"
    pace = "step_by_step" if "step_by_step" in preferred else _text(cognitive.get("learning_pace")).lower() or "balanced"
    motivation = dict(normalized.get("motivation") or {})
    motivation.update({key: value for key, value in motivation_profile.items() if key not in motivation and value is not None})

    tutor_payload = None if tutor is None else {
        "session_id": tutor.session_id, "thread_id": tutor.thread_id,
        "status": tutor.status, "goal": tutor.goal,
        "daily_minutes": int(tutor.daily_minutes or 20),
        "current_node_id": tutor.current_node_id,
        "progress_pct": round(float(tutor.progress_pct or 0), 1),
        "next_action": dict(tutor.next_action or {}),
        "updated_at": _iso(tutor.updated_at),
    }

    version_payload = {
        "user_id": user_id,
        "profile_version": int(getattr(profile, "profile_version", 0) or 0),
        "latest_event_id": int(events[0].id) if events else 0,
        "latest_artifact_id": int(artifacts[0].id) if artifacts else 0,
        "formal_revisions": [(item.get("result_ref"), item.get("evaluation_revision")) for item in formal_evidence],
        "tutor": (tutor.session_id, _iso(tutor.updated_at)) if tutor else None,
        "preferences": [(item.preference, item.evidence_type, item.polarity, item.evidence_count) for item in preference_evidence],
    }
    level = _knowledge_level(formal_mastery or mastery, normalized, cognitive)
    topic_score = mastery.get(current_topic or "") if current_topic else None
    confidence = min(1.0, 0.45 + 0.1 * len(formal_evidence)) if formal_evidence else min(0.6, float(getattr(profile, "confidence_score", 0.0) or 0.0))
    topic_mastery = (
        "secure" if topic_score is not None and topic_score >= 0.8
        else "partial" if topic_score is not None and topic_score >= 0.55
        else "emerging" if topic_score is not None
        else level if level in {"secure", "developing", "emerging"} else "unknown"
    )
    evidence_refs = list(dict.fromkeys(
        str(ref)
        for item in formal_evidence
        for ref in [item.get("result_ref"), *list(item.get("evidence_event_ids") or [])]
        if ref
    ))[:20]
    return NormalizedLearnerState(
        user_id=user_id,
        current_goal=current_goal,
        current_topic=current_topic,
        knowledge_level=level,
        topic_mastery=topic_mastery,
        primary_barrier={
            "category": "knowledge_gap" if weak_points else "none_observed",
            "description": f"{weak_points[0]}仍需要加强" if weak_points else "当前没有足够证据确认主要障碍",
            "evidence_refs": evidence_refs[:8],
        },
        support_level="step_by_step" if "step_by_step" in preferred else "minimal_scaffold",
        transfer_readiness="ready" if topic_score is not None and topic_score >= 0.8 else "not_ready",
        next_teaching_focus=weak_points[0] if weak_points else current_topic,
        confidence=confidence,
        evidence_refs=evidence_refs,
        mastery_by_knowledge_point=mastery,
        mastery_sources=mastery_sources,
        weak_points=weak_points,
        strengths=strengths,
        preferred_modalities=preferred,
        preference_evidence=preference_evidence,
        presentation_preference=presentation,
        practice_preference=practice_preference,
        code_preference=code_preference,
        pace=pace,
        motivation=motivation,
        recent_errors=recent_errors,
        recent_evidence=formal_evidence,
        recent_learning_events=recent_events,
        recent_artifacts=recent_artifacts,
        current_tutor_session=tutor_payload,
        last_learning_time=last_learning_time,
        state_version=_state_digest(version_payload),
    )
