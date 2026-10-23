"""R3-derived semantic runtime for DARYA's Demonstration Surface.

The model produces semantic intent or semantic mutations only. Coordinates,
renderer fields, Excalidraw elements, and viewport decisions remain exclusively
inside the frontend Composer and adapter. The durable conversation action keeps
the current semantic scene so a later turn can mutate the same scene without a
second conversation or a second learning authority.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from services.blackboard.figures import validate_figure


INTENT_SCHEMA_VERSION = "v0.2"
MUTATION_SCHEMA_VERSION = "v0.2"
VALID_STRUCTURES = {
    "compare", "sequence", "cause_effect", "hierarchy", "flow", "before_after",
}
VALID_GROUPS = {
    "left", "right", "row", "root", "cause", "effect", "before", "after",
}
VALID_ITEM_KEYS = {
    "id", "label", "description", "group", "emphasis", "underline", "subItems",
    # A declarative figure: the model names the shape and its measurements,
    # the blackboard compiler works out every coordinate.
    "figure",
}
VALID_RELATION_KEYS = {"from", "to", "type", "label", "emphasis"}
VALID_ANNOTATION_KEYS = {"targetId", "type", "text"}
FORBIDDEN_RENDERER_KEYS = {
    "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "width", "height", "points",
    "boundElements", "frameId", "angle", "seed", "versionNonce", "containerId",
    "originalText", "autoResize", "lineHeight", "startBinding", "endBinding",
    "startArrowhead", "endArrowhead", "elbowed", "polygon", "fileId",
    "strokeWidth", "strokeStyle", "fillStyle", "roughness",
}
VALID_OPS = {"create", "update", "delete", "arrange", "annotate", "emphasize_relation"}
VALID_ARRANGEMENTS = {"swap", "before", "after", "promote", "group"}
NO_EFFECTIVE_CHANGE = "NO_EFFECTIVE_CHANGE"


class DemonstrationGenerationError(RuntimeError):
    """The semantic scene could not be generated truthfully."""


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...]


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _json_object(raw: Any) -> Mapping[str, Any] | None:
    if isinstance(raw, Mapping) and "content" in raw:
        raw = raw["content"]
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def find_forbidden_key(node: Any, path: tuple[str, ...] = ()) -> tuple[str, tuple[str, ...]] | None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            text_key = str(key)
            if text_key in FORBIDDEN_RENDERER_KEYS:
                return text_key, (*path, text_key)
            found = find_forbidden_key(value, (*path, text_key))
            if found:
                return found
    elif isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
        for index, value in enumerate(node):
            found = find_forbidden_key(value, (*path, f"[{index}]"))
            if found:
                return found
    return None


def validate_intent(intent: Any) -> ValidationResult:
    errors: list[str] = []
    if not isinstance(intent, Mapping):
        return ValidationResult(False, ("intent must be an object",))
    if not str(intent.get("title") or "").strip():
        errors.append("title is required")
    if intent.get("structure") not in VALID_STRUCTURES:
        errors.append("unsupported structure")
    items = intent.get("items")
    if not isinstance(items, list) or not 3 <= len(items) <= 14:
        errors.append("items must contain 3 to 14 semantic items")
        items = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            errors.append(f"item {index} must be an object")
            continue
        unknown = set(item) - VALID_ITEM_KEYS
        if unknown:
            errors.append(f"item {index} has unknown keys: {sorted(unknown)}")
        semantic_id = str(item.get("id") or "")
        if not re.fullmatch(r"si-[1-9][0-9]*", semantic_id):
            errors.append(f"item {index} requires a generic si-N id")
        if semantic_id in seen:
            errors.append(f"duplicate semantic id: {semantic_id}")
        seen.add(semantic_id)
        label = str(item.get("label") or "").strip()
        if not label or len(label) > 80:
            errors.append(f"item {semantic_id or index} label is invalid")
        if len(str(item.get("description") or "")) > 240:
            errors.append(f"item {semantic_id or index} description is too long")
        if item.get("group") is not None and item.get("group") not in VALID_GROUPS:
            errors.append(f"item {semantic_id or index} group is invalid")
        if item.get("figure") is not None:
            for error in validate_figure(item["figure"]):
                errors.append(f"item {semantic_id or index} figure: {error}")
    relations = intent.get("relations", [])
    if not isinstance(relations, list):
        errors.append("relations must be an array")
        relations = []
    for relation in relations:
        if not isinstance(relation, Mapping):
            errors.append("relation must be an object")
            continue
        unknown = set(relation) - VALID_RELATION_KEYS
        if unknown:
            errors.append(f"relation has unknown keys: {sorted(unknown)}")
        if relation.get("from") not in seen or relation.get("to") not in seen:
            errors.append("relation references an unknown semantic id")
        if "emphasis" in relation and not isinstance(relation.get("emphasis"), bool):
            errors.append("relation emphasis must be boolean")
    annotations = intent.get("annotations", [])
    if not isinstance(annotations, list):
        errors.append("annotations must be an array")
        annotations = []
    for annotation in annotations:
        if not isinstance(annotation, Mapping):
            errors.append("annotation must be an object")
            continue
        unknown = set(annotation) - VALID_ANNOTATION_KEYS
        if unknown:
            errors.append(f"annotation has unknown keys: {sorted(unknown)}")
        if annotation.get("targetId") not in seen:
            errors.append("annotation references an unknown semantic id")
        if annotation.get("type") not in {"circle", "underline", "highlight", "callout"}:
            errors.append("annotation type is invalid")
    forbidden = find_forbidden_key(intent)
    if forbidden:
        errors.append(f"forbidden renderer key '{forbidden[0]}' at {'.'.join(forbidden[1])}")
    return ValidationResult(not errors, tuple(errors))


def next_revision(current: str | None) -> str:
    value = str(current or "rev-000")
    matched = re.fullmatch(r"rev-(\d+)", value)
    if not matched:
        raise ValueError("REVISION_FORMAT_INVALID")
    return f"rev-{int(matched.group(1)) + 1:03d}"


def validate_mutation(mutation: Any, intent: Mapping[str, Any]) -> ValidationResult:
    errors: list[str] = []
    if not isinstance(mutation, Mapping):
        return ValidationResult(False, ("mutation must be an object",))
    if not isinstance(mutation.get("baseRevision"), str):
        errors.append("baseRevision required")
    operations = mutation.get("operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 4:
        errors.append("operations must contain 1 to 4 minimal changes")
        operations = []
    if sum(
        isinstance(operation, Mapping) and operation.get("op") == "create"
        for operation in operations
    ) > 1:
        errors.append("a mutation may create at most 1 semantic item")
    known = {str(item.get("id")) for item in list(intent.get("items") or []) if isinstance(item, Mapping)}
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping) or operation.get("op") not in VALID_OPS:
            errors.append(f"operation {index} is invalid")
            continue
        op = operation["op"]
        if op == "create":
            semantic_id = str(operation.get("semanticId") or "")
            item = operation.get("item")
            if not re.fullmatch(r"si-[1-9][0-9]*", semantic_id) or semantic_id in known:
                errors.append(f"create operation {index} requires a new generic semantic id")
            if not isinstance(item, Mapping) or not str(item.get("label") or "").strip():
                errors.append(f"create operation {index} requires item.label")
            elif set(item) - (VALID_ITEM_KEYS - {"id"}):
                errors.append(f"create operation {index} contains unknown item keys")
            known.add(semantic_id)
        elif op == "annotate":
            annotation = operation.get("annotation")
            if not isinstance(annotation, Mapping) or set(annotation) - VALID_ANNOTATION_KEYS:
                errors.append(f"annotate operation {index} requires a semantic annotation")
            elif annotation.get("targetId") not in known or annotation.get("type") not in {"circle", "underline", "highlight", "callout"}:
                errors.append(f"annotate operation {index} has an invalid target or type")
        elif op == "emphasize_relation":
            source = operation.get("from")
            target = operation.get("to")
            relations = intent.get("relations") or []
            if not any(
                isinstance(relation, Mapping) and relation.get("from") == source and relation.get("to") == target
                for relation in relations
            ):
                errors.append(f"emphasize_relation operation {index} targets an unknown arrow")
            if operation.get("emphasis") is not True:
                errors.append(f"emphasize_relation operation {index} requires emphasis=true")
        elif op in {"update", "delete"}:
            if operation.get("targetId") not in known:
                errors.append(f"{op} operation {index} targets an unknown semantic id")
            if op == "update":
                patch = operation.get("patch")
                if not isinstance(patch, Mapping) or not patch:
                    errors.append(f"update operation {index} requires a patch")
                elif set(patch) - (VALID_ITEM_KEYS - {"id"}):
                    errors.append(f"update operation {index} contains unknown patch keys")
        else:
            targets = operation.get("targets")
            arrangement = operation.get("arrangement")
            minimum = 1 if arrangement == "promote" else 2
            if arrangement not in VALID_ARRANGEMENTS:
                errors.append(f"arrange operation {index} has an invalid arrangement")
            if not isinstance(targets, list) or len(targets) < minimum or any(target not in known for target in targets):
                errors.append(f"arrange operation {index} has invalid targets")
    forbidden = find_forbidden_key(mutation)
    if forbidden:
        errors.append(f"forbidden renderer key '{forbidden[0]}' at {'.'.join(forbidden[1])}")
    return ValidationResult(not errors, tuple(errors))


def apply_mutation(intent: Mapping[str, Any], mutation: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    validation = validate_mutation(mutation, intent)
    current_revision = str(intent.get("revision") or "rev-000")
    if not validation.valid:
        return deepcopy(dict(intent)), {
            "ok": False, "status": "INVALID_MUTATION",
            "baseRevision": mutation.get("baseRevision"), "currentRevision": current_revision,
            "newRevision": current_revision, "applied": [], "changedSemanticIds": [],
            "skipped": list(validation.errors),
        }
    if mutation["baseRevision"] != current_revision:
        return deepcopy(dict(intent)), {
            "ok": False, "status": "STALE_SCENE_REVISION",
            "baseRevision": mutation["baseRevision"], "currentRevision": current_revision,
            "newRevision": None, "applied": [], "changedSemanticIds": [],
            "skipped": [f"baseRevision {mutation['baseRevision']} != current revision {current_revision}"],
        }
    updated = deepcopy(dict(intent))
    items = list(updated.get("items") or [])
    applied: list[dict[str, Any]] = []
    changed: list[str] = []
    for operation in mutation["operations"]:
        op = operation["op"]
        if op == "create":
            semantic_id = operation["semanticId"]
            items.append({"id": semantic_id, **dict(operation["item"])})
            changed.append(semantic_id)
            applied.append({"op": op, "semanticId": semantic_id, "status": "applied"})
        elif op == "annotate":
            annotation = dict(operation["annotation"])
            annotations = list(updated.get("annotations") or [])
            effective = not any(
                item.get("targetId") == annotation.get("targetId") and item.get("type") == annotation.get("type")
                for item in annotations if isinstance(item, Mapping)
            )
            if effective:
                annotations.append(annotation)
                updated["annotations"] = annotations
                changed.append(str(annotation["targetId"]))
            applied.append({"op": op, "targetId": annotation["targetId"], "status": "applied" if effective else "no-effective-change"})
        elif op == "emphasize_relation":
            relations = list(updated.get("relations") or [])
            position = next(index for index, relation in enumerate(relations) if relation.get("from") == operation["from"] and relation.get("to") == operation["to"])
            effective = relations[position].get("emphasis") is not True
            if effective:
                relations[position] = {**relations[position], "emphasis": True}
                updated["relations"] = relations
                changed.extend([str(operation["from"]), str(operation["to"])])
            applied.append({"op": op, "from": operation["from"], "to": operation["to"], "status": "applied" if effective else "no-effective-change"})
        elif op == "update":
            target = str(operation["targetId"])
            position = next(index for index, item in enumerate(items) if item.get("id") == target)
            patch = dict(operation["patch"])
            effective_patch = {
                key: value for key, value in patch.items()
                if key not in items[position] or items[position].get(key) != value
            }
            unchanged_fields = [key for key in patch if key not in effective_patch]
            if not effective_patch:
                applied.append({
                    "op": op, "targetId": target, "status": "no-effective-change",
                    "effectivePatch": {}, "unchangedFields": unchanged_fields,
                })
                continue
            items[position] = {**items[position], **effective_patch}
            changed.append(target)
            applied.append({
                "op": op, "targetId": target, "status": "applied",
                "effectivePatch": effective_patch, "unchangedFields": unchanged_fields,
            })
        elif op == "delete":
            target = str(operation["targetId"])
            items = [item for item in items if item.get("id") != target]
            updated["relations"] = [
                relation for relation in list(updated.get("relations") or [])
                if target not in {relation.get("from"), relation.get("to")}
            ]
            updated["annotations"] = [
                annotation for annotation in list(updated.get("annotations") or [])
                if annotation.get("targetId") != target
            ]
            changed.append(target)
            applied.append({"op": op, "targetId": target, "status": "applied"})
        else:
            targets = list(operation["targets"])
            arrangement = operation["arrangement"]
            previous_order = [str(item.get("id")) for item in items]
            if arrangement == "swap":
                first = next(index for index, item in enumerate(items) if item.get("id") == targets[0])
                second = next(index for index, item in enumerate(items) if item.get("id") == targets[1])
                items[first], items[second] = items[second], items[first]
            elif arrangement in {"before", "after"}:
                moving = next(item for item in items if item.get("id") == targets[0])
                items = [item for item in items if item.get("id") != targets[0]]
                anchor = next(index for index, item in enumerate(items) if item.get("id") == targets[1])
                items.insert(anchor if arrangement == "before" else anchor + 1, moving)
            elif arrangement == "promote":
                moving = next(item for item in items if item.get("id") == targets[0])
                items = [moving, *[item for item in items if item.get("id") != targets[0]]]
            else:
                target_items = [next(item for item in items if item.get("id") == target) for target in targets]
                items = [item for item in items if item.get("id") not in targets]
                anchor = 0
                items[anchor:anchor] = target_items
            next_order = [str(item.get("id")) for item in items]
            effective = next_order != previous_order
            if effective:
                changed.extend(targets)
            applied.append({
                "op": op, "targets": targets, "arrangement": arrangement,
                "status": "applied" if effective else "no-effective-change",
            })
    updated["items"] = items
    if updated == dict(intent):
        return deepcopy(dict(intent)), {
            "ok": True, "status": NO_EFFECTIVE_CHANGE,
            "baseRevision": mutation["baseRevision"], "currentRevision": current_revision,
            "newRevision": current_revision, "applied": [
                {**operation, "status": "no-effective-change"}
                if operation.get("status") == "applied" else operation
                for operation in applied
            ],
            "changedSemanticIds": [], "skipped": [],
        }
    new_revision = next_revision(current_revision)
    updated["revision"] = new_revision
    final_validation = validate_intent(updated)
    if not final_validation.valid:
        return deepcopy(dict(intent)), {
            "ok": False, "status": "INVALID_MUTATION_RESULT",
            "baseRevision": mutation["baseRevision"], "currentRevision": current_revision,
            "newRevision": current_revision, "applied": [], "changedSemanticIds": [],
            "skipped": list(final_validation.errors),
        }
    return updated, {
        "ok": True, "status": "APPLIED", "baseRevision": current_revision,
        "newRevision": new_revision, "applied": applied,
        "changedSemanticIds": list(dict.fromkeys(changed)),
    }


def validate_mutation_reply(value: Any, intent: Mapping[str, Any]) -> ValidationResult:
    """One reply decides both "same scene?" and "what changes".

    Asking a separate model call whether the follow-up still belongs to the open
    board cost a whole round trip on every follow-up -- the most frequent move in
    a taught conversation -- and the mutation call already had the scene and the
    follow-up in front of it.
    """
    if not isinstance(value, Mapping):
        return ValidationResult(False, ("reply must be an object",))
    if value.get("related") is False:
        return ValidationResult(True, ())
    payload = {key: item for key, item in value.items() if key != "related"}
    return validate_effective_mutation(payload, intent)


def validate_requested_board_edit(value: Any, intent: Mapping[str, Any], message: str) -> ValidationResult:
    """Keep an explicit visual edit bound to the named semantic targets."""
    validation = validate_mutation_reply(value, intent)
    if not validation.valid or not isinstance(value, Mapping):
        return validation
    text = _compact(message)
    mentions = []
    for item in intent.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        label = _compact(str(item.get("label") or "").split("：", 1)[0])
        if len(label) >= 2 and label in text:
            mentions.append((text.index(label), str(item.get("id"))))
    mentions.sort()
    if value.get("related") is False:
        if mentions and ("黑板" in text or "板书" in text):
            return ValidationResult(False, ("the named item is on the current board; keep related=true",))
        return validation
    operations = list(value.get("operations") or [])
    errors = []
    if "圈" in text and mentions:
        target = mentions[0][1]
        already_circled = any(
            isinstance(annotation, Mapping) and annotation.get("targetId") == target and annotation.get("type") == "circle"
            for annotation in intent.get("annotations") or []
        )
        if not already_circled and not any(
            operation.get("op") == "annotate" and (operation.get("annotation") or {}).get("targetId") == target
            and (operation.get("annotation") or {}).get("type") == "circle"
            for operation in operations if isinstance(operation, Mapping)
        ):
            errors.append(f"learner asked to circle {target}; add annotate type=circle on that item, not underline")
    if re.search(r"(?:突出|高亮|强调).{0,12}箭头|箭头.{0,12}(?:突出|高亮|强调)", text) and len(mentions) >= 2:
        mentioned = {semantic_id for _, semantic_id in mentions}
        matching = [relation for relation in intent.get("relations") or [] if isinstance(relation, Mapping)
                    and relation.get("from") in mentioned and relation.get("to") in mentioned]
        if matching and not any(
            operation.get("op") == "emphasize_relation" and any(
                relation.get("from") == operation.get("from") and relation.get("to") == operation.get("to")
                for relation in matching
            ) for operation in operations if isinstance(operation, Mapping)
        ):
            errors.append("learner asked to highlight the named arrow; use emphasize_relation on its from/to pair")
    if ("不要改其他文字" in text or "只圈" in text) and any(
        operation.get("op") in {"create", "delete", "arrange"}
        or operation.get("op") == "update" and (
            not isinstance(operation.get("patch"), Mapping)
            or set(operation["patch"]) - {"emphasis", "underline"}
        )
        for operation in operations if isinstance(operation, Mapping)
    ):
        errors.append("this is a visual-only edit; do not rewrite item text or rearrange the scene")
    return ValidationResult(not errors, tuple(errors))


def validate_effective_mutation(mutation: Any, intent: Mapping[str, Any]) -> ValidationResult:
    """Model-generation gate: a valid payload must also change semantic state."""
    validation = validate_mutation(mutation, intent)
    if not validation.valid:
        return validation
    _updated, receipt = apply_mutation(intent, mutation)
    if receipt["status"] == NO_EFFECTIVE_CHANGE:
        return ValidationResult(False, (
            "mutation has no effective semantic change; change an existing value or add one bounded teaching detail",
        ))
    if not receipt["ok"]:
        return ValidationResult(False, (str(receipt["status"]),))
    return ValidationResult(True, ())


def latest_demonstration(recent_history: Sequence[Mapping[str, Any]] | None) -> Mapping[str, Any] | None:
    for row in reversed(list(recent_history or [])):
        action = row.get("conversation_action") if isinstance(row, Mapping) else None
        demonstration = action.get("demonstration") if isinstance(action, Mapping) and action.get("kind") == "DEMONSTRATE" else None
        if isinstance(demonstration, Mapping) and isinstance(demonstration.get("current_intent"), Mapping):
            return demonstration
    return None


BOARD_LEARNER_GUIDANCE = (
    "\n把板子画成给「这一个人」看的：他已经会的不用从头写起，"
    "他卡住的那一步要多给一层，他这一轮真正在问的那件事必须是板子的主线。\n"
)


def _intent_prompt(
    message: str, *, domain_context: str, route_context: str, learner_context: str = ""
) -> str:
    # Built outside the f-string: Python 3.11 forbids a backslash inside an
    # f-string expression, so this cannot be inlined.
    who = ""
    if learner_context.strip():
        who = "\n" + learner_context.strip() + "\n" + BOARD_LEARNER_GUIDANCE
    return f"""You are DARYA's blackboard planner. You decide what gets written on the board while DARYA talks. Create one DemonstrationIntent v0.2 that explains the learner's exact confusion. Output JSON only, never markdown.

The output is semantic, not visual geometry. Somebody else turns it into chalk: they decide which column each item lands in, how tall a card is, and where an arrow leaves one box and meets the next. So never emit coordinates, sizes, points, renderer fields, or styling primitives. Use only:
{{"title":string,"structure":"compare|sequence|cause_effect|hierarchy|flow|before_after","items":[{{"id":"si-1","label":string,"description":string,"group":"left|right|row|root|cause|effect|before|after","emphasis":boolean,"underline":boolean,"figure":{{...}}}}],"relations":[{{"from":"si-N","to":"si-N","type":"arrow","label":string}}],"annotations":[{{"targetId":"si-N","type":"circle|underline|callout","text":string}}]}}

Rules:
0. Write the title, every label and every description in the learner's own language -- what ends up on the board is read by them, not by you. A Chinese request gets Chinese chalk; do not answer with English headings.
1. Use 5 to 10 stable generic IDs in numeric order: si-1, si-2, etc. IDs must never contain topic words.
2. Make the key distinction understandable spatially. Include every semantic role the user asks to distinguish, including boundaries, paths, state changes, and contrasts.
3. Keep labels short (that is what gets written large) and descriptions to one sentence (that is the smaller line underneath).
4. Use relations and emphasis to expose causality or data flow. Avoid decoration.
5. For nested scopes, explicitly show the outer scope, inner function, read path, rebind path, and the role/boundary of nonlocal when relevant.
6. The board is written as columns, not as a slide: `group` is a spatial hint. "root" is a header node above everything, "left"/"before"/"cause" fill the left side, "right"/"after"/"effect" the right, "row" flows down and continues in the next column. Use "left" and "right" together only for a real contrast.

Declarative figures — use these instead of describing a shape in words. A figure is attached to one item and takes real space, so keep a figure scene to a handful of items:
{{"kind":"right_triangle","legs":[3,4],"right_angle_at":"C","labels":["A","B","C"],"mode":"exact","show_lengths":true}}
{{"kind":"triangle","sides":[3,4,5],"mode":"exact"}}       // SSS, solved by the law of cosines
{{"kind":"isosceles","sides":[5,5,6]}}
{{"kind":"square","side":4}} {{"kind":"circle","r":200}}
{{"kind":"character_sitting_typing","size":520}}   // 一个人坐在键盘前打字，只画线稿
`mode:"exact"` draws at true proportions, so the right angle really is 90 degrees and every written side length is the length drawn -- use it whenever the learner needs to see *why* a property holds. `mode:"schematic"` keeps the true numbers on the labels but relaxes the drawing so a very flat shape is still readable, and the board says "not to scale" itself. Vertex letters, side lengths, the right-angle mark and equal-side ticks are all placed automatically: never add items for them.

A character is an illustration, not a measurement: it carries no numbers and nothing about it has to be read precisely. Use it only when *who is doing the thing* is itself part of what the learner is confused about -- an agent trying a move, a person following a procedure. Never add it as decoration, never put one on a board that already shows one, and never use it where a real figure (a triangle, an axis) is what actually teaches the point.

Current learner request: {json.dumps(message, ensure_ascii=False)}
Current domain context: {json.dumps(domain_context, ensure_ascii=False)}
Active Route context: {json.dumps(route_context, ensure_ascii=False)}
{who}"""


def _mutation_prompt(message: str, current: Mapping[str, Any]) -> str:
    return f"""You are deciding what DARYA's board should do next. The board currently shows the scene below, and the learner has just said something. Decide *first* whether they are still talking about this same scene, and only then what to change. Output one JSON object only, never markdown.

Current intent:
{json.dumps(current, ensure_ascii=False)}

Learner follow-up:
{json.dumps(message, ensure_ascii=False)}

Output:
{{"related":true,"baseRevision":"{current.get('revision')}","operations":[
  {{"op":"update","targetId":"si-N","patch":{{"label":string,"description":string,"emphasis":boolean,"underline":boolean}}}},
  {{"op":"create","semanticId":"si-N","item":{{"label":string,"description":string,"group":string,"emphasis":boolean,"underline":boolean}}}},
  {{"op":"delete","targetId":"si-N"}},
  {{"op":"arrange","targets":["si-N","si-N"],"arrangement":"swap|before|after|promote|group"}},
  {{"op":"annotate","annotation":{{"targetId":"si-N","type":"circle|underline|highlight","text":""}}}},
  {{"op":"emphasize_relation","from":"si-N","to":"si-N","emphasis":true}}
]}}

`related` is the first thing to get right. Set it to true when the follow-up is about the same explanation -- wanting a step emphasised, a case compared, a wording corrected, a detail added, or one part of it asked about. Set it to false when the learner has moved to a different subject or is asking for a different picture altogether, even if they also said "draw" or "now"; when related is false, output only {{"related":false}} and no operations.

Write any new or patched label and description in the learner's own language -- the board is read by them. Use only the smallest operations needed, with at most 4 operations total and at most 1 new semantic item in one turn. If the learner asks to circle an existing item, use annotate type=circle on that exact item; underline is a different action. If they ask to replace an existing underline with a circle, also update that item's underline to false. If the learner asks to highlight an existing arrow, use emphasize_relation on that exact from/to pair. For a purely visual mark, do not rewrite unrelated labels, descriptions, items, or arrows. Preserve existing semantic IDs. New content gets the next unused generic si-N ID. Never emit coordinates, sizes, points, renderer fields, Excalidraw fields, or styling primitives. Every update patch must change at least one value compared with the current item. If emphasis or underline is already true, do not repeat those booleans alone: strengthen a concise description or another existing semantic value so the requested teaching emphasis is real.
"""


async def _model_json(
    llm: Any,
    prompt: str,
    *,
    validator: Any,
    on_token: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    last_errors: tuple[str, ...] = ("model returned no JSON object",)
    working_prompt = prompt
    for _attempt in range(2):
        try:
            if on_token is None or not hasattr(llm, "chat_stream"):
                raw = await llm.chat(
                    messages=[{"role": "user", "content": working_prompt}],
                    temperature=0.0,
                    max_tokens=1600,
                    response_format={"type": "json_object"},
                    use_cache=False,
                    write_cache=False,
                )
            else:
                # Laying out the board is the longest silent stretch of a turn.
                # Streaming it turns that silence into a heartbeat; the JSON is
                # still only parsed once the whole object has arrived.
                pieces: list[str] = []
                try:
                    async for piece in llm.chat_stream(
                        messages=[{"role": "user", "content": working_prompt}],
                        response_format={"type": "json_object"},
                        temperature=0.0,
                        max_tokens=1600,
                    ):
                        pieces.append(piece)
                        on_token(len(piece))
                except TypeError:
                    # A provider whose chat_stream predates response_format.
                    pieces = []
                    async for piece in llm.chat_stream(
                        messages=[{"role": "user", "content": working_prompt}],
                    ):
                        pieces.append(piece)
                        on_token(len(piece))
                raw = "".join(pieces)
        except Exception as exc:
            raise DemonstrationGenerationError(f"DEMONSTRATION_MODEL_UNAVAILABLE:{type(exc).__name__}") from exc
        value = _json_object(raw)
        if value is not None:
            validation = validator(value)
            if validation.valid:
                return dict(value)
            last_errors = validation.errors
        working_prompt = (
            prompt
            + "\nYour previous JSON was rejected by the semantic contract. Correct only these errors: "
            + json.dumps(list(last_errors), ensure_ascii=False)
        )
    raise DemonstrationGenerationError("DEMONSTRATION_SEMANTIC_VALIDATION_FAILED:" + "|".join(last_errors))


async def create_or_mutate_demonstration(
    *,
    message: str,
    scene_id: str,
    recent_history: Sequence[Mapping[str, Any]] | None,
    domain_context: str,
    route_context: str,
    learner_context: str = "",
    llm: Any | None = None,
    on_token: Callable[[int], None] | None = None,
) -> dict[str, Any]:
    if llm is None:
        try:
            from services.llm_service_real import get_llm_service
            llm = await get_llm_service()
        except Exception as exc:
            raise DemonstrationGenerationError("DEMONSTRATION_MODEL_UNAVAILABLE") from exc
    previous = latest_demonstration(recent_history)
    relationship: dict[str, Any] | None = None
    if previous is not None:
        current = deepcopy(dict(previous["current_intent"]))
        reply = await _model_json(
            llm,
            _mutation_prompt(message, current),
            validator=lambda value: validate_requested_board_edit(value, current, message),
            on_token=on_token,
        )
        if reply.get("related") is not False:
            mutation = {key: value for key, value in reply.items() if key != "related"}
            updated, receipt = apply_mutation(current, mutation)
            if not receipt["ok"]:
                raise DemonstrationGenerationError(str(receipt["status"]))
            return {
                "mode": "PRODUCT",
                "operation": "mutation",
                "session_id": str(previous.get("session_id") or scene_id),
                "schema_version": INTENT_SCHEMA_VERSION,
                "mutation_schema_version": MUTATION_SCHEMA_VERSION,
                "base_revision": receipt["baseRevision"],
                "revision": receipt["newRevision"],
                "intent": None,
                "current_intent": updated,
                "mutation": mutation,
                "scene_relationship": {
                    "relationship": "CONTINUE_EXISTING",
                    "confidence": 1.0,
                    "evidence_span": message[:160],
                },
                "semantic_receipt": receipt,
                "learning_authority": {
                    "mastery_mutation": False,
                    "formal_evidence_created": False,
                    "route_authority_created": False,
                },
            }
        # The learner moved on to a different picture: start a fresh scene.
        relationship = {
            "relationship": "START_NEW",
            "confidence": 1.0,
            "evidence_span": message[:160],
        }
        previous = None
    if previous is None:
        intent = await _model_json(
            llm,
            _intent_prompt(
                message,
                domain_context=domain_context,
                route_context=route_context,
                learner_context=learner_context,
            ),
            validator=validate_intent,
            on_token=on_token,
        )
        intent.update({
            "schema_version": INTENT_SCHEMA_VERSION,
            "session_id": scene_id,
            "revision": "rev-000",
        })
        validation = validate_intent(intent)
        if not validation.valid:
            raise DemonstrationGenerationError("DEMONSTRATION_INTENT_INVALID_AFTER_BINDING")
        return {
            "mode": "PRODUCT",
            "operation": "create",
            "session_id": scene_id,
            "schema_version": INTENT_SCHEMA_VERSION,
            "base_revision": None,
            "revision": "rev-000",
            "intent": intent,
            "current_intent": intent,
            "mutation": None,
            "scene_relationship": relationship or {
                "relationship": "START_NEW",
                "confidence": 1.0,
                "evidence_span": message[:160],
            },
            "semantic_receipt": {
                "ok": True,
                "status": "CREATED",
                "baseRevision": None,
                "newRevision": "rev-000",
                "changedSemanticIds": [item["id"] for item in intent["items"]],
            },
            "learning_authority": {
                "mastery_mutation": False,
                "formal_evidence_created": False,
                "route_authority_created": False,
            },
        }
