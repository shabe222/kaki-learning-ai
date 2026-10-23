"""Thread-bounded Shared Perspective foundation for Tide Phase 2.1.

This layer deliberately stores only the work that one authenticated user and
Tide are doing in one conversation.  It does not read or write cross-thread
memory, learner profile, impressions, or long-term policy.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Any, Literal

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import TideSharedPerspectiveSnapshot


PerspectiveStatus = Literal["observed", "inferred", "confirmed"]
PerspectiveAction = Literal["confirm", "correct", "reject"]

FIELD_NAMES = (
    "goal",
    "goal_kind",
    "parallel_goals",
    "archived_goals",
    "stage",
    "constraints",
    "current_artifacts",
    "confirmed_decisions",
    "unfinished_items",
    "next_action",
    "user_facts",
)
LIST_FIELDS = {"constraints", "current_artifacts", "confirmed_decisions", "unfinished_items", "user_facts", "parallel_goals", "archived_goals"}

# 目标系统：真实目标识别、并行路径与证据归档。
_TRIVIAL_GOAL_PATTERN = re.compile(
    r"^(?:[哎嗯哦呃啊呀嘿喂好行嗯嘞哇]+[。！!，,～~\s]*)$"
    r"|^(?:哈哈+|呵呵+|嘿嘿|嘻嘻|谢谢|多谢|你好|您好|在吗|没事|随便|不知道|没有了|不用了)"
    r"|[。！!，,\s]+$",
    re.I,
)
_GOAL_KIND_TEACHING = re.compile(r"学会|搞懂|弄明白|理解|掌握|复习|备考|考试|做题|练习|讲解|学习|课程|背|成绩|补习|教我|辅导", re.I)
_GOAL_KIND_LIFE = re.compile(r"迷茫|焦虑|压力|坚持|习惯|拖延|选择|方向|未来|人生|信心|动力|失眠|Efficiency|自律", re.I)
_PARALLEL_GOAL_SIGNAL = re.compile(r"另外[^。]{0,8}想|我还想|我也想|同时想|顺便想|还想养成|还想学|还想准备", re.I)
_GOAL_COMPLETION_SIGNAL = re.compile(
    r"学会了?|搞懂了|弄明白了|掌握了|搞定了|做完了|完成了|考完了|通过了|练熟了|已经会了|解决掉了",
    re.I,
)


def _is_trivial_goal_text(text: str) -> bool:
    """闲聊、语气词、过短寒暄不能成为目标候选。"""
    cleaned = str(text or "").strip()
    if not cleaned or len(cleaned) < 2:
        return True
    return bool(_TRIVIAL_GOAL_PATTERN.match(cleaned))


def _goal_kind(text: str) -> str:
    if _GOAL_KIND_TEACHING.search(str(text or "")):
        return "teaching"
    if _GOAL_KIND_LIFE.search(str(text or "")):
        return "life_guidance"
    return "task"


def _goal_entry(text: str, kind: str | None = None) -> dict[str, Any]:
    return {"text": str(text)[:240], "kind": kind or _goal_kind(text)}




def _archive_goal(fields: dict[str, Any], entry: dict[str, Any], reason: str, source_reference: dict[str, Any]) -> None:
    """把完成（或被替代）的目标移入归档，右栏底部可见。"""
    archived = fields.get("archived_goals")
    value = list(archived.get("value") or []) if isinstance(archived, dict) else []
    record = {**entry, "reason": reason[:80], "archived_at": datetime.now(timezone.utc).isoformat()}
    value = [item for item in value if _goal_signature(item.get("text")) != _goal_signature(entry.get("text"))]
    value.insert(0, record)
    _set_candidate(fields, "archived_goals", value[:12], "observed", source_reference, explicit_change=True)

_EXPLICIT_GOAL_PATTERNS = (
    re.compile(
        r"(?:I am|I'm|we are|we're)\s+(?:currently\s+)?"
        r"(?:preparing|working on|building|writing|studying)\s+(.+)",
        re.I,
    ),
    re.compile(r"(?:我|我们)(?:正在|现在在|在)?(?:准备|做|处理|完善|写|学习)\s*(.+)"),
)
_EXPLICIT_STAGE_PATTERNS = (
    re.compile(r"(?:当前|现在)?(?:阶段|进度)(?:是|为|到了?)?\s*[:：]?\s*([^。！？!?]+)"),
    re.compile(r"(?:处于|进入|到了)\s*([^。！？!?]{2,40})(?:阶段)?"),
    re.compile(r"(?:stage is|currently at|we are at)\s+([^.!?]+)", re.I),
)
_CONSTRAINT_SIGNAL = re.compile(
    r"截止|deadline|明天|今晚|后天|本周|时间不够|来不及|只能|必须|限制|预算|要求",
    re.I,
)
_DECISION_SIGNAL = re.compile(r"决定|确定|就用|保留|不再|已经选|we decided|we will use|keep|drop", re.I)
_UNFINISHED_SIGNAL = re.compile(r"还没|尚未|未完成|待处理|剩下|还需要|unfinished|not done|remaining", re.I)
_ACTION_SIGNAL = re.compile(
    r"帮我|请|下一步|改进|完善|修改|整理|完成|检查|开始|"
    r"help me|next|improve|revise|refine|finish|review|start",
    re.I,
)
_TASK_SHIFT_SIGNAL = re.compile(
    r"考试|复习|考研|就业|求职|找工作|岗位|面试|答辩|汇报|演示|计划|资料|"
    r"exam|study|graduate|job|career|interview|presentation|plan|handout",
    re.I,
)


class SharedPerspectiveError(ValueError):
    def __init__(self, message: str, *, code: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any, max_length: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n。！？!?.,")[:max_length]


def _source(kind: str, reference_id: str | int | None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": kind}
    if reference_id is not None:
        payload["id"] = str(reference_id)
    payload.update({key: value for key, value in extra.items() if value is not None})
    return payload


def _field(value: Any, status: PerspectiveStatus, source_reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "value": value,
        "status": status,
        "source_reference": source_reference,
        "updated_at": _now(),
    }


def _field_value(fields: dict[str, Any], name: str) -> Any:
    item = fields.get(name)
    return item.get("value") if isinstance(item, dict) else None


def _same_field(left: Any, right: Any) -> bool:
    if not isinstance(left, dict) or not isinstance(right, dict):
        return left == right
    return (
        left.get("value") == right.get("value")
        and left.get("status") == right.get("status")
        and left.get("source_reference") == right.get("source_reference")
    )


def _extract(patterns: tuple[re.Pattern[str], ...], message: str) -> str | None:
    for pattern in patterns:
        match = pattern.search(message)
        if match:
            value = _clean_text(match.group(1), 240)
            if value:
                return value
    return None


def _sentence_with_signal(message: str, signal: re.Pattern[str]) -> str | None:
    for sentence in re.split(r"(?<=[。！？!?])\s*|\n+", message):
        cleaned = _clean_text(sentence, 320)
        if cleaned and signal.search(cleaned):
            return cleaned
    return None


def _merge_list(existing: Any, value: Any, limit: int = 8) -> list[Any]:
    result = list(existing) if isinstance(existing, list) else ([] if existing in (None, "") else [existing])
    additions = value if isinstance(value, list) else [value]
    for item in additions:
        if item not in (None, "") and item not in result:
            result.append(item)
    return result[-limit:]


def _goal_signature(value: Any) -> str:
    """Keep enough topic signal to tell a new task from a paraphrase."""
    text = _clean_text(value, 320).lower()
    text = re.sub(r"(?:我想|我要|我准备|我打算|我的目标是|请帮我|帮我|正在|现在|准备|希望|需要)", "", text)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _material_goal_change(previous: Any, current: Any) -> bool:
    """Return True only when the active work topic materially changes."""
    old = _goal_signature(previous)
    new = _goal_signature(current)
    if not old or not new or old == new:
        return False
    if old in new or new in old:
        return False
    return SequenceMatcher(None, old, new).ratio() < 0.58


def _replace_unfinished_for_new_goal(
    fields: dict[str, Any],
    *,
    goal: str,
    first_move: str,
    source_reference: dict[str, Any],
) -> None:
    """Prevent an old candidate task from being presented as current work.

    User-confirmed unfinished items are deliberate controls and must survive a
    topic switch. Unconfirmed items are only a working projection, so replace
    them with the new goal's first executable move when available.
    """
    confirmed_unfinished = False
    for field_name in ("unfinished_items", "constraints", "confirmed_decisions"):
        current = fields.get(field_name)
        if isinstance(current, dict) and current.get("status") == "confirmed":
            if field_name == "unfinished_items":
                confirmed_unfinished = True
            continue
        fields.pop(field_name, None)
    if confirmed_unfinished:
        return
    replacement = _clean_text(first_move or goal, 320)
    if replacement:
        fields["unfinished_items"] = _field([replacement], "inferred", source_reference)


def _set_candidate(
    fields: dict[str, Any],
    name: str,
    value: Any,
    status: PerspectiveStatus,
    source_reference: dict[str, Any],
    *,
    explicit_change: bool = False,
) -> None:
    if name not in FIELD_NAMES or value in (None, "", []):
        return
    current = fields.get(name)
    if isinstance(current, dict) and current.get("status") == "confirmed" and not explicit_change:
        return
    if name in LIST_FIELDS and name != "current_artifacts" and current:
        value = _merge_list(current.get("value"), value)
    candidate = _field(value, status, source_reference)
    if _same_field(current, candidate):
        return
    fields[name] = candidate


def build_candidate_fields(
    *,
    existing_fields: dict[str, Any] | None,
    trusted_history: list[dict[str, Any]],
    current_message: str,
    message_id: str,
    artifact_context: dict[str, Any] | None = None,
    artifact_explicit: bool = False,
) -> dict[str, Any]:
    """Build a conservative candidate without turning assumptions into facts."""
    fields = copy.deepcopy(existing_fields or {})
    message = _clean_text(current_message, 2000)
    message_source = _source("message", message_id)

    existing_goal = _field_value(fields, "goal")
    trivial = _is_trivial_goal_text(message)

    # 证据完成的归档：用户表达"学会了/搞定了/完成"时，把当前目标移入归档目标，
    # 右栏活动路径随之消失；除非同一条消息还提出了新的实质目标。
    if existing_goal and _GOAL_COMPLETION_SIGNAL.search(message):
        _archive_goal(fields, _goal_entry(str(existing_goal)), "你确认完成了", message_source)
        fields.pop("goal", None)
        fields.pop("goal_kind", None)
        fields.pop("stage", None)
        fields.pop("next_action", None)
        for parallel in (_field_value(fields, "parallel_goals") or []):
            if isinstance(parallel, dict) and parallel.get("text"):
                _archive_goal(fields, _goal_entry(str(parallel["text"]), parallel.get("kind")), "随主目标完成归档", message_source)
        fields.pop("parallel_goals", None)

    goal = _extract(_EXPLICIT_GOAL_PATTERNS, message)
    if not trivial and goal and _is_trivial_goal_text(goal):
        goal = ""
    if not goal and existing_goal and _TASK_SHIFT_SIGNAL.search(message) and not trivial:
        # Some natural task turns ("I have an exam next week...") do not use
        # the explicit-goal grammar. Treat the turn as a candidate goal only
        # when it materially differs from the active thread focus.
        if _material_goal_change(existing_goal, message):
            goal = message[:240]
    if goal:
        if _material_goal_change(existing_goal, goal):
            # 转向新目标时，旧目标不是完成而是被替代：归档保存（理由：转向新目标），
            # 保持右栏只显示当前真实路径。
            _archive_goal(fields, _goal_entry(str(existing_goal)), "转向了新目标", message_source)
            _replace_unfinished_for_new_goal(
                fields,
                goal=goal,
                first_move=message[:320],
                source_reference=message_source,
            )
            # 新旧目标都真实存在且足够不同时，作为并行路径保留旧的（最多 2 条并行）。
            parallel = _field_value(fields, "parallel_goals") or []
            signature = _goal_signature(goal)
            if len(parallel) < 2 and all(_goal_signature(item.get("text")) != signature for item in parallel if isinstance(item, dict)):
                _set_candidate(
                    fields,
                    "parallel_goals",
                    [*parallel, _goal_entry(str(existing_goal))],
                    "observed",
                    message_source,
                )
        _set_candidate(fields, "goal", goal, "observed", message_source)
        _set_candidate(fields, "goal_kind", _goal_kind(goal), "inferred", message_source)
    elif not existing_goal and not trivial and len(message) >= 8:
        # 首条实质话语可以作为工作焦点候选；闲聊、语气词（哎/哈哈/在吗…）永远不行。
        _set_candidate(fields, "goal", message[:240], "observed", message_source)
        _set_candidate(fields, "goal_kind", _goal_kind(message), "inferred", message_source)

    # 并行目标声明（"另外我还想…"）：不替换主路径，加入并行路径（上限 2 条）。
    if existing_goal and not goal and not trivial:
        parallel_sentence = _sentence_with_signal(message, _PARALLEL_GOAL_SIGNAL)
        if parallel_sentence and len(parallel_sentence) >= 6:
            parallel = _field_value(fields, "parallel_goals") or []
            entry = _goal_entry(parallel_sentence)
            if (
                len(parallel) < 2
                and _goal_signature(entry["text"]) != _goal_signature(existing_goal)
                and all(_goal_signature(item.get("text")) != _goal_signature(entry["text"]) for item in parallel if isinstance(item, dict))
            ):
                _set_candidate(fields, "parallel_goals", [*parallel, entry], "observed", message_source)

    stage = _extract(_EXPLICIT_STAGE_PATTERNS, message)
    if stage:
        _set_candidate(fields, "stage", stage, "observed", message_source)
    elif not _field_value(fields, "stage") and goal:
        _set_candidate(fields, "stage", "准备与推进中", "inferred", message_source)

    constraint = _sentence_with_signal(message, _CONSTRAINT_SIGNAL)
    if constraint:
        _set_candidate(fields, "constraints", constraint, "observed", message_source)

    decision = _sentence_with_signal(message, _DECISION_SIGNAL)
    if decision:
        _set_candidate(fields, "confirmed_decisions", decision, "observed", message_source)

    unfinished = _sentence_with_signal(message, _UNFINISHED_SIGNAL)
    if unfinished:
        _set_candidate(fields, "unfinished_items", unfinished, "observed", message_source)
    elif _TASK_SHIFT_SIGNAL.search(message):
        current_unfinished = _field_value(fields, "unfinished_items")
        unfinished_text = " ".join(str(item) for item in current_unfinished) if isinstance(current_unfinished, list) else str(current_unfinished or "")
        if unfinished_text and _material_goal_change(unfinished_text, message):
            _replace_unfinished_for_new_goal(
                fields,
                goal=goal or message[:240],
                first_move=message[:320],
                source_reference=message_source,
            )

    if _ACTION_SIGNAL.search(message):
        _set_candidate(fields, "next_action", message[:320], "observed", message_source)

    if artifact_context:
        artifact_ref = {
            "artifact_id": artifact_context.get("artifact_id"),
            "version_id": artifact_context.get("version_id"),
            "version_number": artifact_context.get("version_number"),
            "kind": artifact_context.get("artifact_kind"),
            "title": artifact_context.get("title"),
        }
        _set_candidate(
            fields,
            "current_artifacts",
            [artifact_ref],
            "observed",
            _source("artifact", artifact_ref.get("artifact_id"), version_id=artifact_ref.get("version_id")),
            explicit_change=artifact_explicit,
        )

    # The input is intentionally part of the contract even when no extraction
    # is needed: server history proves this update belongs to the trusted thread.
    if trusted_history and not message:
        return fields
    return fields


async def get_latest_snapshot(
    db: AsyncSession, *, user_id: int, thread_id: str,
) -> TideSharedPerspectiveSnapshot | None:
    return (await db.execute(
        select(TideSharedPerspectiveSnapshot)
        .where(
            TideSharedPerspectiveSnapshot.user_id == user_id,
            TideSharedPerspectiveSnapshot.thread_id == thread_id,
        )
        .order_by(desc(TideSharedPerspectiveSnapshot.revision))
        .limit(1)
    )).scalar_one_or_none()


def public_snapshot(
    snapshot: TideSharedPerspectiveSnapshot | None, *, thread_id: str,
    workspace_object_id: int | None = None,
) -> dict[str, Any]:
    if snapshot is None:
        return {
            "thread_id": thread_id,
            "revision": 0,
            "workspace_object_id": workspace_object_id,
            "fields": {},
            "updated_at": None,
        }
    created_at = snapshot.created_at
    return {
        "thread_id": snapshot.thread_id,
        "revision": snapshot.revision,
        "workspace_object_id": workspace_object_id,
        "fields": copy.deepcopy(snapshot.fields or {}),
        "updated_at": created_at.isoformat() if created_at else None,
    }


async def persist_candidate_update(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    trusted_history: list[dict[str, Any]],
    current_message: str,
    message_id: str,
    artifact_context: dict[str, Any] | None = None,
    artifact_explicit: bool = False,
) -> TideSharedPerspectiveSnapshot | None:
    current = await get_latest_snapshot(db, user_id=user_id, thread_id=thread_id)
    fields = build_candidate_fields(
        existing_fields=current.fields if current else {},
        trusted_history=trusted_history,
        current_message=current_message,
        message_id=message_id,
        artifact_context=artifact_context,
        artifact_explicit=artifact_explicit,
    )
    if current and fields == (current.fields or {}):
        return current
    if not fields:
        return current
    row = TideSharedPerspectiveSnapshot(
        user_id=user_id,
        thread_id=thread_id,
        revision=(current.revision + 1) if current else 1,
        parent_snapshot_id=current.id if current else None,
        fields=fields,
        changed_by="candidate",
    )
    db.add(row)
    await db.flush()
    return row


async def persist_goal_collaboration_update(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    plan: dict[str, Any],
    message_id: str,
) -> TideSharedPerspectiveSnapshot | None:
    """Merge model-projected current-turn facts into the existing thread truth.

    This remains a candidate snapshot. Facts retain the exact message evidence
    span and can still be confirmed, corrected, or rejected through the normal
    Shared Perspective controls.
    """
    current = await get_latest_snapshot(db, user_id=user_id, thread_id=thread_id)
    fields = copy.deepcopy(current.fields or {}) if current else {}
    plan_source = _source("goal_collaboration", message_id)
    goal = _clean_text(plan.get("goal"), 300)
    lifecycle = str(plan.get("goal_lifecycle") or "").upper()
    if lifecycle == "COMPLETED":
        previous_goal = _field_value(fields, "goal")
        completed_goal = goal or _clean_text(previous_goal, 300)
        if not completed_goal or _is_trivial_goal_text(completed_goal):
            return current
        reason = _clean_text(plan.get("goal_completion_reason"), 300) or "DARYA 判断当前目标已收束"
        _archive_goal(fields, _goal_entry(completed_goal), reason, plan_source)
        for field_name in ("goal", "goal_kind", "stage", "next_action", "unfinished_items"):
            fields.pop(field_name, None)
        if current and fields == (current.fields or {}):
            return current
        row = TideSharedPerspectiveSnapshot(
            user_id=user_id,
            thread_id=thread_id,
            revision=(current.revision + 1) if current else 1,
            parent_snapshot_id=current.id if current else None,
            fields=fields,
            changed_by="goal_collaboration_completed",
        )
        db.add(row)
        await db.flush()
        return row
    if not plan.get("active"):
        return current
    previous_goal = _field_value(fields, "goal")
    first_move = _clean_text(plan.get("useful_first_move"), 320)
    if goal and _material_goal_change(previous_goal, goal):
        # 旧目标被新目标替代：归档保存而不是丢弃（理由：转向了新目标）。
        if previous_goal and not _is_trivial_goal_text(str(previous_goal)):
            _archive_goal(fields, _goal_entry(str(previous_goal)), "转向了新目标", plan_source)
        _replace_unfinished_for_new_goal(
            fields,
            goal=goal,
            first_move=first_move,
            source_reference=plan_source,
        )
    if goal:
        _set_candidate(fields, "goal", goal, "inferred", plan_source)
        _set_candidate(fields, "goal_kind", _goal_kind(goal), "inferred", plan_source)
    facts = []
    for row in list(plan.get("observed_user_facts") or [])[:8]:
        if not isinstance(row, dict):
            continue
        fact = _clean_text(row.get("fact"), 240)
        span = _clean_text(row.get("evidence_span"), 240)
        if fact and span:
            facts.append({"fact": fact, "evidence_span": span})
    if facts:
        _set_candidate(fields, "user_facts", facts, "observed", plan_source)
    constraints = [_clean_text(item, 160) for item in list(plan.get("constraints") or []) if _clean_text(item, 160)]
    if constraints:
        _set_candidate(fields, "constraints", constraints, "observed", plan_source)
    if first_move:
        _set_candidate(fields, "next_action", first_move, "inferred", plan_source)
    if current and fields == (current.fields or {}):
        return current
    row = TideSharedPerspectiveSnapshot(
        user_id=user_id,
        thread_id=thread_id,
        revision=(current.revision + 1) if current else 1,
        parent_snapshot_id=current.id if current else None,
        fields=fields,
        changed_by="goal_collaboration_candidate",
    )
    db.add(row)
    await db.flush()
    return row


async def apply_user_control(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    field_name: str,
    action: PerspectiveAction,
    expected_revision: int,
    value: Any = None,
) -> TideSharedPerspectiveSnapshot:
    if field_name not in FIELD_NAMES:
        raise SharedPerspectiveError("未知的共同视角字段。", code="UNKNOWN_FIELD")
    current = await get_latest_snapshot(db, user_id=user_id, thread_id=thread_id)
    current_revision = current.revision if current else 0
    if expected_revision != current_revision:
        raise SharedPerspectiveError(
            "共同视角已发生变化，请刷新后再修改。",
            code="REVISION_CONFLICT",
            status_code=409,
        )
    fields = copy.deepcopy(current.fields or {}) if current else {}
    existing = fields.get(field_name)
    source = _source("user_control", f"{action}:{field_name}")

    if action == "confirm":
        if not isinstance(existing, dict) or existing.get("value") in (None, "", []):
            raise SharedPerspectiveError("没有可确认的内容。", code="FIELD_EMPTY")
        fields[field_name] = _field(existing.get("value"), "confirmed", source)
    elif action == "correct":
        if value in (None, "", []):
            raise SharedPerspectiveError("修正内容不能为空。", code="VALUE_REQUIRED")
        normalized = value
        if field_name not in LIST_FIELDS:
            normalized = _clean_text(value, 500)
        elif not isinstance(value, list):
            normalized = [_clean_text(value, 500)]
        fields[field_name] = _field(normalized, "confirmed", source)
    elif action == "reject":
        # A confirmed null is a deliberate boundary: candidate extraction may
        # not silently recreate the rejected assumption on the next turn.
        fields[field_name] = _field(None, "confirmed", source)
    else:
        raise SharedPerspectiveError("未知的操作。", code="UNKNOWN_ACTION")

    row = TideSharedPerspectiveSnapshot(
        user_id=user_id,
        thread_id=thread_id,
        revision=current_revision + 1,
        parent_snapshot_id=current.id if current else None,
        fields=fields,
        changed_by=f"user_{action}",
    )
    db.add(row)
    await db.flush()
    return row


def decision_context(snapshot: TideSharedPerspectiveSnapshot | dict[str, Any] | None) -> dict[str, Any]:
    if snapshot is None:
        return {}
    fields = snapshot.fields if isinstance(snapshot, TideSharedPerspectiveSnapshot) else snapshot.get("fields", {})
    return {
        name: item.get("value")
        for name, item in (fields or {}).items()
        if name in FIELD_NAMES and isinstance(item, dict) and item.get("value") not in (None, "", [])
    }


def current_artifact_target(snapshot: TideSharedPerspectiveSnapshot | None) -> tuple[int | None, int | None]:
    if snapshot is None:
        return None, None
    refs = _field_value(snapshot.fields or {}, "current_artifacts")
    first = refs[0] if isinstance(refs, list) and refs else None
    if not isinstance(first, dict):
        return None, None
    try:
        artifact_id = int(first.get("artifact_id")) if first.get("artifact_id") else None
        version_id = int(first.get("version_id")) if first.get("version_id") else None
        return artifact_id, version_id
    except (TypeError, ValueError):
        return None, None
