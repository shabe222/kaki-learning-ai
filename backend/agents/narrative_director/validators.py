from __future__ import annotations

import re

from .enums import RepairMode, Severity
from .models import NarrativeBlueprint, NarrativeRequest, ValidationIssue
from .scene_catalog import SCENES


_STUDENT_LABELS = (
    "基础薄弱", "能力差", "低能力", "粗心", "笨", "愚蠢", "学渣", "天才学生", "心理问题", "性格问题"
)
_HUMILIATION = ("羞耻", "丢脸", "不配", "没资格", "嘲笑", "责骂", "惩罚你")
_VIOLENCE = ("流血", "斩首", "杀死", "尸体", "肢解", "血肉", "处决")
_PRIVATE_TOKENS = (
    "learner_state", "confidence", "evidence_refs", "accepted_groups", "correct_option",
    "rubric", "prompt_hash", "schema_hash", "provider_returned_model", "_private_"
)
_ANSWER_LEAKS = ("正确答案是", "正确选项是", "标准答案是", "答案为A", "答案为B", "答案为C", "答案为D")
_PROMPT_INJECTION = ("忽略系统", "忽略之前", "泄露提示词", "输出learner_state", "改变schema", "绕过规则")
_GRAND_STAKES = ("世界毁灭", "宇宙毁灭", "所有生命", "末日", "死亡", "永远消失", "无法挽回")
_PLACE_PATTERN = re.compile(
    r"(?:来到|进入|走进|抵达|穿过|位于|在)([^，。；：]{2,14}(?:谷|湖|阁|工坊|桥|营地|宝库|封印场|神殿|竞技场|山谷|森林|岛|城|塔|宫殿|洞窟))"
)


def _issue(
    code: str,
    severity: Severity,
    path: str,
    message: str,
    repair: RepairMode = RepairMode.NONE,
) -> ValidationIssue:
    return ValidationIssue(
        code=code,
        severity=severity,
        path=path,
        message=message,
        repair_mode=repair.value,
    )


def _flatten(blueprint: NarrativeBlueprint) -> dict[str, str]:
    return {
        "world_metaphor.world_rule": blueprint.world_metaphor.world_rule,
        "world_metaphor.alignment_note": blueprint.world_metaphor.alignment_note,
        "world_anomaly.visible_change": blueprint.world_anomaly.visible_change,
        "world_anomaly.local_stakes": blueprint.world_anomaly.local_stakes,
        "route_frames.understanding.action_frame": blueprint.route_frames.understanding.action_frame,
        "route_frames.understanding.success_response": blueprint.route_frames.understanding.success_response,
        "route_frames.understanding.failure_signal": blueprint.route_frames.understanding.failure_signal,
        "route_frames.practice.action_frame": blueprint.route_frames.practice.action_frame,
        "route_frames.practice.success_response": blueprint.route_frames.practice.success_response,
        "route_frames.practice.failure_signal": blueprint.route_frames.practice.failure_signal,
        "opposition_embodiment.appearance": blueprint.opposition_embodiment.appearance,
        "opposition_embodiment.behaviour": blueprint.opposition_embodiment.behaviour,
        "opposition_embodiment.transformation": blueprint.opposition_embodiment.transformation,
        "beats.opening": blueprint.beats.opening,
        "beats.rule_reveal": blueprint.beats.rule_reveal,
        "beats.first_trial": blueprint.beats.first_trial,
        "beats.supply.success_respite": blueprint.beats.supply.success_respite,
        "beats.supply.remediation_reframe": blueprint.beats.supply.remediation_reframe,
        "beats.interleave_turn": blueprint.beats.interleave_turn,
        "beats.boss_reveal": blueprint.beats.boss_reveal,
        "beats.resolution": blueprint.beats.resolution,
    }


def _compact(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _bigrams(value: str) -> set[str]:
    text = _compact(value)
    return {text[index : index + 2] for index in range(max(0, len(text) - 1))}


def _similarity(left: str, right: str) -> float:
    a, b = _bigrams(left), _bigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _unknown_place_mentions(text: str, allowed: set[str]) -> list[str]:
    found: list[str] = []
    for match in _PLACE_PATTERN.finditer(text):
        value = match.group(1).strip()
        if any(name in value or value in name for name in allowed):
            continue
        if value not in found:
            found.append(value)
    return found


def validate_blueprint(
    blueprint: NarrativeBlueprint,
    request: NarrativeRequest,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    frame = request.frame
    fields = _flatten(blueprint)
    all_text = "\n".join(fields.values())

    if blueprint.world_anomaly.affected_object not in frame.allowed_objects:
        issues.append(_issue(
            "UNSUPPORTED_AFFECTED_OBJECT", Severity.ERROR, "world_anomaly.affected_object",
            "affected_object is not part of the deterministic scene frame", RepairMode.STRUCTURAL,
        ))

    if blueprint.opposition_embodiment.form not in frame.allowed_opposition_forms:
        issues.append(_issue(
            "OPPOSITION_FORM_NOT_ALLOWED", Severity.ERROR, "opposition_embodiment.form",
            "opposition form conflicts with the deterministic grammar", RepairMode.STRUCTURAL,
        ))

    if frame.primary_scene_name not in blueprint.beats.opening:
        issues.append(_issue(
            "PRIMARY_SCENE_MISSING", Severity.ERROR, "beats.opening",
            "opening must explicitly use the deterministic primary scene", RepairMode.STRUCTURAL,
        ))

    registered_names = {definition.display_name for definition in SCENES.values()}
    allowed_names = {
        frame.primary_scene_name,
        frame.alternate_scene_name,
        "核心封印场",
        "丰穗宝库与烟火高台",
    }
    for path, text in fields.items():
        for name in registered_names - allowed_names:
            if name in text:
                issues.append(_issue(
                    "SCENE_NARRATIVE_MISMATCH", Severity.ERROR, path,
                    f"narrative mentions registered scene {name!r} outside the deterministic frame",
                    RepairMode.STRUCTURAL,
                ))
        for scene_id in SCENES:
            if scene_id in text:
                issues.append(_issue(
                    "PRIVATE_SCENE_ID_LEAK", Severity.ERROR, path,
                    f"internal scene id {scene_id!r} must not appear in narrative text", RepairMode.CLEAN,
                ))

    for path, text in fields.items():
        allowed_for_field = set(allowed_names)
        unknown = _unknown_place_mentions(text, allowed_for_field)
        if unknown:
            issues.append(_issue(
                "UNREGISTERED_PLACE_NAME", Severity.ERROR, path,
                f"unregistered place names detected: {unknown}", RepairMode.STRUCTURAL,
            ))

    route_similarity = _similarity(
        blueprint.route_frames.understanding.action_frame,
        blueprint.route_frames.practice.action_frame,
    )
    if route_similarity >= 0.78:
        issues.append(_issue(
            "ROUTES_TOO_SIMILAR", Severity.ERROR, "route_frames",
            f"understanding and practice routes are too similar ({route_similarity:.2f})",
            RepairMode.STRUCTURAL,
        ))

    for token in _STUDENT_LABELS:
        if token in all_text:
            issues.append(_issue(
                "STUDENT_LABEL", Severity.ERROR, "$", f"student label detected: {token}", RepairMode.CLEAN,
            ))
    for token in _HUMILIATION:
        if token in all_text:
            issues.append(_issue(
                "HUMILIATION", Severity.ERROR, "$", f"humiliating language detected: {token}", RepairMode.CLEAN,
            ))
    for token in _VIOLENCE:
        if token in all_text:
            issues.append(_issue(
                "VIOLENCE", Severity.ERROR, "$", f"violent language detected: {token}", RepairMode.CLEAN,
            ))
    for token in _PRIVATE_TOKENS:
        if token.lower() in all_text.lower():
            issues.append(_issue(
                "PRIVATE_DATA_LEAK", Severity.ERROR, "$", f"private/internal token detected: {token}", RepairMode.CLEAN,
            ))
    for token in _ANSWER_LEAKS:
        if token in all_text:
            issues.append(_issue(
                "ANSWER_LEAK", Severity.ERROR, "$", f"answer disclosure phrase detected: {token}", RepairMode.CLEAN,
            ))
    for token in _PROMPT_INJECTION:
        if token.lower() in all_text.lower():
            issues.append(_issue(
                "PROMPT_INJECTION_FOLLOWED", Severity.ERROR, "$", f"instruction-like material was followed: {token}", RepairMode.CLEAN,
            ))
    for token in _GRAND_STAKES:
        if token in blueprint.world_anomaly.local_stakes:
            issues.append(_issue(
                "STAKE_TOO_LARGE", Severity.ERROR, "world_anomaly.local_stakes",
                f"stakes exceed the local adventure scope: {token}", RepairMode.STRUCTURAL,
            ))

    academic_source = (
        request.core_concept.name + request.core_concept.explanation + request.core_concept.applicability
    )
    academic_output = blueprint.world_metaphor.world_rule + blueprint.world_metaphor.alignment_note
    academic_overlap = _similarity(academic_source, academic_output)
    concept_present = request.core_concept.name in academic_output
    if not concept_present and academic_overlap == 0:
        issues.append(_issue(
            "ACADEMIC_NO_OVERLAP", Severity.ERROR, "world_metaphor",
            "world metaphor has no detectable relationship to the confirmed core concept",
            RepairMode.CLEAN,
        ))
    elif not concept_present and academic_overlap < 0.04:
        issues.append(_issue(
            "ACADEMIC_REVIEW_REQUIRED", Severity.REVIEW, "world_metaphor",
            f"academic overlap is weak ({academic_overlap:.3f}); independent review is required",
        ))

    if request.misconception.correction in all_text:
        issues.append(_issue(
            "CORRECTION_COPIED_INTO_NARRATIVE", Severity.WARNING, "$",
            "the full correction was copied into narrative text; compiler should keep correction private",
        ))

    joined_without_keys = all_text
    allowed_key_count = 0
    for index, key in enumerate(blueprint.continuity_keys):
        if key in frame.allowed_objects:
            allowed_key_count += 1
        count = joined_without_keys.count(key)
        if count == 0:
            issues.append(_issue(
                "CONTINUITY_INJECTION_NEEDED", Severity.WARNING,
                f"continuity_keys[{index}]", f"continuity key {key!r} is not yet used in model text",
            ))
    if allowed_key_count == 0:
        issues.append(_issue(
            "CONTINUITY_NOT_SCENE_BOUND", Severity.ERROR, "continuity_keys",
            "at least one continuity key must be a real object from the selected scene frame",
            RepairMode.STRUCTURAL,
        ))

    return issues


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(issue.severity == Severity.ERROR for issue in issues)
