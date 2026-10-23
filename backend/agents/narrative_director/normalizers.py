from __future__ import annotations

from .enums import InteractionKind
from .models import NarrativeBlueprint, NarrativeRequest
from .scene_catalog import SCENES


_GRAND_STAKES = ("世界毁灭", "宇宙毁灭", "所有生命", "永远消失", "末日", "死亡", "无法挽回")


_ACTION_PREFIX: dict[InteractionKind, str] = {
    InteractionKind.EXPLAIN: "先用自己的话解释规则，再让场景回应这段解释：",
    InteractionKind.ORDERING: "先重排被打乱的顺序，再观察链条是否恢复：",
    InteractionKind.STEP_COMPLETION: "先补齐缺失的一步，再验证机制能否继续：",
    InteractionKind.COMPARE: "先并排比较两个线索，再指出改变结论的差异：",
    InteractionKind.FIND_ERROR: "先沿异常痕迹定位错误，再说明它为何影响结果：",
    InteractionKind.COUNTEREXAMPLE: "先找出能推翻假象的反例，再解释冲突所在：",
    InteractionKind.APPLICATION: "先识别旧规则与新情境的联系，再独立应用：",
    InteractionKind.CODE_TASK: "先检查控制逻辑和边界，再修复并验证运行结果：",
    InteractionKind.REWRITE: "先重组表达，让主线和依据重新对应：",
    InteractionKind.OPEN_RESPONSE: "先完整说明判断依据，再让世界反馈是否成立：",
    InteractionKind.MULTIPLE_CHOICE: "先比较候选方案的条件，再作出选择：",
}


def _replace_known_scene_names(text: str, allowed_names: set[str], replacement: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    value = text
    for definition in SCENES.values():
        if definition.display_name in allowed_names:
            continue
        if definition.display_name in value:
            value = value.replace(definition.display_name, replacement)
            warnings.append(f"scene_name_normalized:{definition.display_name}->{replacement}")
    for scene_id, definition in SCENES.items():
        if scene_id in value:
            value = value.replace(scene_id, definition.display_name if definition.display_name in allowed_names else replacement)
            warnings.append(f"scene_id_removed:{scene_id}")
    return value, warnings


def _route_similarity(left: str, right: str) -> float:
    def grams(value: str) -> set[str]:
        compact = "".join(ch for ch in value if not ch.isspace() and ch not in "，。；：、,.!！?？")
        return {compact[i : i + 2] for i in range(max(0, len(compact) - 1))}
    a, b = grams(left), grams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def normalize_blueprint(
    blueprint: NarrativeBlueprint,
    request: NarrativeRequest,
) -> tuple[NarrativeBlueprint, list[str]]:
    data = blueprint.model_dump(mode="json")
    warnings: list[str] = []
    frame = request.frame

    if data["world_anomaly"]["affected_object"] not in frame.allowed_objects:
        old = data["world_anomaly"]["affected_object"]
        data["world_anomaly"]["affected_object"] = frame.allowed_objects[0]
        warnings.append(f"affected_object_normalized:{old}->{frame.allowed_objects[0]}")

    if data["opposition_embodiment"]["form"] not in [item.value for item in frame.allowed_opposition_forms]:
        old = data["opposition_embodiment"]["form"]
        data["opposition_embodiment"]["form"] = frame.allowed_opposition_forms[0].value
        warnings.append(f"opposition_form_normalized:{old}->{frame.allowed_opposition_forms[0].value}")

    opening = data["beats"]["opening"]
    if frame.primary_scene_name not in opening:
        data["beats"]["opening"] = f"{frame.primary_scene_name}里，{opening}"
        warnings.append("primary_scene_injected_into_opening")

    field_paths = [
        ("world_anomaly", "visible_change"),
        ("world_anomaly", "local_stakes"),
        ("route_frames", "understanding", "action_frame"),
        ("route_frames", "understanding", "success_response"),
        ("route_frames", "understanding", "failure_signal"),
        ("route_frames", "practice", "action_frame"),
        ("route_frames", "practice", "success_response"),
        ("route_frames", "practice", "failure_signal"),
        ("opposition_embodiment", "appearance"),
        ("opposition_embodiment", "behaviour"),
        ("opposition_embodiment", "transformation"),
        ("beats", "opening"),
        ("beats", "rule_reveal"),
        ("beats", "first_trial"),
        ("beats", "supply", "success_respite"),
        ("beats", "supply", "remediation_reframe"),
        ("beats", "interleave_turn"),
        ("beats", "boss_reveal"),
        ("beats", "resolution"),
    ]
    allowed_names = {frame.primary_scene_name, frame.alternate_scene_name, "核心封印场", "丰穗宝库与烟火高台"}
    for path in field_paths:
        parent = data
        for key in path[:-1]:
            parent = parent[key]
        value, field_warnings = _replace_known_scene_names(parent[path[-1]], allowed_names, frame.primary_scene_name)
        parent[path[-1]] = value
        warnings.extend(field_warnings)

    if any(token in data["world_anomaly"]["local_stakes"] for token in _GRAND_STAKES):
        data["world_anomaly"]["local_stakes"] = (
            f"如果{data['world_anomaly']['affected_object']}继续异常，"
            f"{frame.primary_scene_name}通往下一处的路线就不会显现。"
        )
        warnings.append("grand_stakes_reduced_to_local")

    understanding = data["route_frames"]["understanding"]["action_frame"]
    practice = data["route_frames"]["practice"]["action_frame"]
    if _route_similarity(understanding, practice) >= 0.72:
        u_kind = request.route_tasks.understanding_trial.interaction_kind
        p_kind = request.route_tasks.practice_trial.interaction_kind
        data["route_frames"]["understanding"]["action_frame"] = ("理解路线：" + _ACTION_PREFIX[u_kind] + understanding)[:140]
        data["route_frames"]["practice"]["action_frame"] = ("实战路线：" + _ACTION_PREFIX[p_kind] + practice)[:140]
        warnings.append("route_frames_differentiated")

    keys: list[str] = []
    affected = data["world_anomaly"]["affected_object"]
    for value in [affected, *data["continuity_keys"]]:
        value = str(value).strip()
        if 2 <= len(value) <= 16 and value not in keys:
            keys.append(value)
        if len(keys) == 3:
            break
    data["continuity_keys"] = keys or [frame.allowed_objects[0]]

    return NarrativeBlueprint.model_validate(data), list(dict.fromkeys(warnings))
