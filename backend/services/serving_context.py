"""One place that decides what the model knows when it serves a learner.

Before this, every serving path picked its own subset out of ~52 context
gathering calls and glued the pieces together with string concatenation in the
router. That had two consequences worth naming:

* a path could quietly be blind. The blackboard planner was asked to teach from
  a board while seeing three lines about the learner -- no conversation, no
  level, no confirmed facts -- even though the orchestrator had read the whole
  picture seconds earlier in the same turn.
* adding a channel meant remembering every path that might need it.

So the context is assembled once, in named layers, and each capability asks for
the layers it actually needs. Layers are plain text with a character budget, so
what the model receives is greppable and its size is bounded by construction.

The layers, in order:

    identity      who DARYA is and what it may never claim
    learner       this person: profile, level, what they have confirmed
    situation     what we are doing right now: goal, route, open artefacts
    conversation  what was actually said, most recent last
    channels      every production channel, with its real contract
    evidence      the facts verified for this turn

Nothing here talks to a database or a model; callers hand in what they already
gathered. That keeps it cheap to test and safe to call from any path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

LAYER_ORDER = ("identity", "learner", "situation", "conversation", "channels", "evidence")

LAYER_TITLES = {
    "identity": "你的身份与边界",
    "learner": "这个学习者",
    "situation": "此刻在做的事",
    "conversation": "刚才说过的话",
    "channels": "你能产出的东西（真实通道，不是清单）",
    "evidence": "本轮已核对的事实",
}

# How much room each layer may take. Bounded by construction: a layer that
# grows with the conversation cannot be allowed to push the rules out.
LAYER_BUDGETS = {
    "identity": 1400,
    "learner": 1600,
    "situation": 1800,
    "conversation": 2600,
    "channels": 2200,
    "evidence": 1800,
}

# Which layers a capability needs to do its job well.
#
# The board is the one that used to be starved: teaching from a blackboard
# without the conversation is guesswork, so it asks for conversation and
# learner, not just situation.
CAPABILITY_LAYERS: Mapping[str, tuple[str, ...]] = {
    "CONVERSATION": ("identity", "learner", "situation", "evidence"),
    "DEMONSTRATE": ("identity", "learner", "situation", "conversation"),
    "HANDOUT": ("identity", "learner", "situation", "evidence"),
    "PRACTICAL_GUIDE": ("identity", "learner", "situation", "evidence"),
    "ASSESSMENT": ("identity", "learner", "situation"),
    "CODE_LAB": ("identity", "learner", "situation", "conversation"),
    "PRESENTATION": ("identity", "learner", "situation", "evidence"),
    "MINDMAP": ("identity", "learner", "situation"),
    "VIDEO": ("identity", "learner", "situation"),
    "STORYBOOK": ("identity", "learner", "situation"),
    "WEB_SEARCH": ("identity", "learner", "situation"),
}
DEFAULT_LAYERS = ("identity", "learner", "situation")

IDENTITY_BODY = (
    "你是 DARYA，陪一个具体的人把一件事弄明白。你只做两件事：把话说清楚，把东西做出来。\n"
    "不可逾越的边界：\n"
    "- 没有真实回执，就不说「已经完成/已经生成/已经掌握」。计划不是完成。\n"
    "- 不把推测写成事实；不确定就说不确定，并说明需要什么才能确定。\n"
    "- 不把检索、整理、比较这些认知劳动整个推回给用户；先交付现有信息支持的成果。\n"
    "- 用户明确要产出东西时，调用对应通道并等真实回执，不要用提纲或一段描述冒充成品。\n"
    "- 用户说不的时候就不再提那件事。"
)


@dataclass(frozen=True)
class Layer:
    key: str
    body: str

    @property
    def title(self) -> str:
        return LAYER_TITLES.get(self.key, self.key)

    def render(self, budget: int | None = None) -> str:
        body = self.body.strip()
        if not body:
            return ""
        limit = LAYER_BUDGETS.get(self.key, 1600) if budget is None else budget
        if len(body) > limit:
            body = body[: max(0, limit - 1)].rstrip() + "…"
        return f"【{self.title}】\n{body}"


@dataclass(frozen=True)
class ServingContext:
    """The layered picture of one turn, assembled once and read by any path."""

    layers: tuple[Layer, ...] = ()
    meta: Mapping[str, Any] = field(default_factory=dict)

    def layer(self, key: str) -> Layer | None:
        for item in self.layers:
            if item.key == key:
                return item
        return None

    def has(self, key: str) -> bool:
        item = self.layer(key)
        return bool(item and item.body.strip())

    def render(self, keys: Iterable[str] | None = None, *, budget: int | None = None) -> str:
        wanted = tuple(keys or LAYER_ORDER)
        blocks = []
        for key in wanted:
            item = self.layer(key)
            if item is None:
                continue
            block = item.render(budget)
            if block:
                blocks.append(block)
        return "\n\n".join(blocks)

    def for_capability(self, capability: str) -> str:
        """The layers this capability needs, in canonical order."""
        keys = CAPABILITY_LAYERS.get(str(capability or "").upper(), DEFAULT_LAYERS)
        return self.render(keys)

    def summary(self) -> dict[str, int]:
        """Layer sizes, for tests and for the turn probe."""
        return {item.key: len(item.body) for item in self.layers}


def _lines(values: Iterable[Any], *, limit: int, width: int) -> list[str]:
    out: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split())
        if not text:
            continue
        out.append(text[:width])
        if len(out) >= limit:
            break
    return out


def _bullets(title: str, values: Iterable[Any], *, limit: int, width: int = 160) -> str:
    rows = _lines(values, limit=limit, width=width)
    if not rows:
        return ""
    body = "\n".join(f"- {row}" for row in rows)
    return f"{title}\n{body}" if title else body


def build_serving_context(
    *,
    message: str,
    profile: Mapping[str, Any] | None = None,
    learner_state: Any | None = None,
    confirmed_facts: Sequence[str] = (),
    memories: Sequence[Any] = (),
    impressions: Sequence[Any] = (),
    route: Any | None = None,
    work_brief: Mapping[str, Any] | None = None,
    open_scene: Mapping[str, Any] | None = None,
    recent_history: Sequence[Mapping[str, Any]] = (),
    evidence: Mapping[str, Any] | None = None,
    capabilities: Sequence[Mapping[str, Any]] = (),
    extra_learner_lines: Sequence[str] = (),
    ledger_memories: Sequence[Mapping[str, Any]] = (),
    guard_reminder: str = "",
) -> ServingContext:
    """Assemble the layers. Pure: no db, no model, no network."""
    layers: list[Layer] = [Layer("identity", IDENTITY_BODY)]

    layers.append(Layer("learner", _learner_body(
        profile=profile,
        learner_state=learner_state,
        confirmed_facts=confirmed_facts,
        memories=memories,
        impressions=impressions,
        extra=extra_learner_lines,
        ledger=ledger_memories,
    )))
    layers.append(Layer("situation", _situation_body(
        message=message, route=route, work_brief=work_brief, open_scene=open_scene,
        guard_reminder=guard_reminder,
    )))
    layers.append(Layer("conversation", _conversation_body(recent_history, message=message)))
    layers.append(Layer("channels", _channels_body(capabilities)))
    layers.append(Layer("evidence", _evidence_body(evidence)))
    return ServingContext(layers=tuple(layers), meta={"message": str(message or "")[:200]})


def _learner_body(
    *,
    profile: Mapping[str, Any] | None,
    learner_state: Any | None,
    confirmed_facts: Sequence[str],
    memories: Sequence[Any],
    impressions: Sequence[Any],
    extra: Sequence[str],
    ledger: Sequence[Mapping[str, Any]] = (),
) -> str:
    parts: list[str] = []

    if isinstance(profile, Mapping) and profile:
        identity = [
            f"{key}：{value}" for key, value in profile.items()
            if value not in (None, "", [], {}) and not str(key).startswith("_")
        ]
        parts.append(_bullets("他是谁：", identity, limit=8))

    level = getattr(learner_state, "support_level", None) or getattr(learner_state, "knowledge_level", None)
    concept = getattr(learner_state, "concept_key", None)
    state_rows = []
    if level:
        state_rows.append(f"当前支撑水平 {level}（决定你讲多细、举什么例子，不是对他能力的评语）")
    if concept:
        state_rows.append(f"当前概念：{concept}")
    parts.append(_bullets("他的状态：", state_rows, limit=4))

    parts.append(_bullets("他明确告诉过你的事实（可以直接用）：", confirmed_facts, limit=8))
    parts.append(_bullets("你记得的长期信息（只在与本轮相关时使用）：", memories, limit=6))
    parts.append(_bullets("你观察到的（只能当线索，不能当结论）：", impressions, limit=6))
    parts.append(_bullets("", extra, limit=6))
    return "\n".join(part for part in parts if part)


def _situation_body(
    *,
    message: str,
    route: Any | None,
    work_brief: Mapping[str, Any] | None,
    open_scene: Mapping[str, Any] | None,
    guard_reminder: str = "",
) -> str:
    parts: list[str] = [_bullets("用户这一轮的原话：", [message], limit=1, width=600)]
    if str(guard_reminder or "").strip():
        # The reminder is about the model's own previous wording, so it belongs
        # next to what is being asked now, not buried in a log.
        parts.append(str(guard_reminder).strip())

    route_rows = []
    for attr, label in (
        ("learner_goal", "学习目标"),
        ("domain_id", "领域"),
        ("current_node_id", "当前节点"),
        ("target_node_id", "目标节点"),
    ):
        value = getattr(route, attr, None) if route is not None else None
        if value:
            route_rows.append(f"{label}：{value}")
    parts.append(_bullets("学习路线：", route_rows, limit=6))

    if isinstance(work_brief, Mapping) and work_brief:
        brief_rows = []
        for key, label in (
            ("goal", "目标"), ("stage", "阶段"), ("strategy", "策略"),
        ):
            value = work_brief.get(key)
            if value:
                brief_rows.append(f"{label}：{value}")
        parts.append(_bullets("本轮工作简报：", brief_rows, limit=4))
        for key, label in (
            ("confirmed_facts", "已确认"), ("unknowns", "还不确定"),
            ("deliverables", "要交付"), ("success_checks", "算成功的标志"),
        ):
            parts.append(_bullets(f"{label}：", work_brief.get(key) or (), limit=6))

    if isinstance(open_scene, Mapping) and open_scene:
        title = str(open_scene.get("title") or "").strip()
        items = [
            str(item.get("label") or "").strip()
            for item in (open_scene.get("items") or [])
            if isinstance(item, Mapping)
        ]
        parts.append(_bullets(
            "黑板上已经有的（继续讲同一件事时接着它讲，别重画）：",
            [f"标题「{title}」"] + [f"第 {i} 项：{label}" for i, label in enumerate(items, 1) if label],
            limit=12,
            width=80,
        ))
    return "\n".join(part for part in parts if part)


def _conversation_body(recent_history: Sequence[Mapping[str, Any]], *, message: str) -> str:
    current = " ".join(str(message or "").split())
    rows = []
    for row in list(recent_history)[-12:]:
        if not isinstance(row, Mapping):
            continue
        content = " ".join(str(row.get("content") or "").split())
        # The current turn arrives both as `message` and as the tail of history;
        # repeating it in two layers invites the model to answer it twice.
        if not content or content == current:
            continue
        speaker = "用户" if str(row.get("role")) == "user" else "DARYA"
        rows.append(f"{speaker}：{content[:220]}")
    if not rows:
        return "（这是这段对话的开始，之前没有别的话。）"
    return "\n".join(rows[-12:])


def _channels_body(capabilities: Sequence[Mapping[str, Any]]) -> str:
    if not capabilities:
        return ""
    rows = []
    for item in capabilities:
        name = str(item.get("name") or "")
        output = str(item.get("real_output") or "")
        fallback = str(item.get("fallback") or "")
        if not name:
            continue
        line = f"{name} → {output}"
        if fallback:
            line += f"（拿不到时退到：{fallback}）"
        rows.append(line)
    return _bullets(
        "用户明确要下面某样东西时，直接调那个通道并等真实回执；说过的话要兑现，不要用描述代替成品。",
        rows,
        limit=16,
        width=180,
    )


def _evidence_body(evidence: Mapping[str, Any] | None) -> str:
    if not isinstance(evidence, Mapping) or not evidence:
        return ""
    decision = str(evidence.get("decision") or "")
    if decision == "SEARCH_NOT_NEEDED":
        return ""
    status = str(evidence.get("evidence_status") or "")
    rows = [f"核对结论：{status or '未标注'}"]
    for source in list(evidence.get("sources") or [])[:4]:
        if not isinstance(source, Mapping):
            continue
        rows.append(
            f"{source.get('title')}（{source.get('publisher')}）"
            f" — {str(source.get('claim_provenance', {}).get('evidence_span') or '')[:120]}"
        )
    conflict = evidence.get("conflict")
    if isinstance(conflict, Mapping) and str(conflict.get("status") or "") == "UNRESOLVED":
        rows.append("来源之间仍有冲突，不要替用户随机选边。")
    if not rows:
        return ""
    return _bullets("只用这些讲事实；没有来源的说法就不要写成确定的。", rows, limit=8, width=200)
