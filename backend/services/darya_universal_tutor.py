"""Pack-independent verify + method directive for non-TutorSession turns.

DARYA installs per-subject knowledge packs (Python, Physics). For turns where
no pack matches — life / competition / soft-skill / any other non-pack topic —
the chat endpoint used to fall through to a generic ``tutor_response`` agent
with no核查 (verification) or 教法 (teaching method) scaffolding. This module
provides that scaffolding in a pack-independent way:

- ``CheckableClaim`` is keyed on a meta-level ``kind`` (definition / boundary /
  responsibility / trade_off / common_error), so the same shape applies to
  any topic the student brings up.
- ``select_universal_strategy`` picks one of the six pack-independent
  pedagogical strategies that ``services.tutor_core`` already exposes
  (``_PEDAGOGY_LABELS``). The behavior map is preserved verbatim.
- ``extract_checkable_claims`` asks the model once per thread for up to 5
  meta-level claims; the output is deterministically post-processed so the
  downstream directive can rely on it.
- ``build_universal_directive`` returns the system-prompt directive that
  ``routers/chat.py`` appends in the no-pack branch. It may also be empty
  (the chat endpoint must accept an empty string silently).

The module is deliberately small and has zero coupling to ``TutorSession`` /
``LearningGraph`` / domain packs; this is the contract the new piece must
honor.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from services.extract_llm_json import extract_json_object
from services.llm_service_real import get_llm_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — must match services/tutor_core.py pedagogy surface verbatim.
# ---------------------------------------------------------------------------

#: Meta-level claim kinds. These never reference a specific subject, which is
#: the whole point: the same shape classifies a debate topic, a life-choice
#: topic, or a competition topic.
CLAIM_KINDS: tuple[str, ...] = (
    "definition",
    "boundary",
    "responsibility",
    "trade_off",
    "common_error",
)

#: Pedagogical strategies exposed by services.tutor_core._PEDAGOGY_LABELS.
#: We mirror the labels and the behavior_map locally so this module stays
#: importable without dragging in TutorContext. Tests pin both.
UNIVERSAL_STRATEGIES: tuple[str, ...] = (
    "socratic_questioning",
    "feynman_teach_back",
    "worked_example",
    "scaffolding",
    "retrieval_practice",
    "transfer_challenge",
)

STRATEGY_LABELS: dict[str, str] = {
    "socratic_questioning": "苏格拉底式追问",
    "feynman_teach_back": "费曼复述法",
    "worked_example": "例题示范",
    "scaffolding": "支架式教学",
    "retrieval_practice": "检索练习",
    "transfer_challenge": "迁移挑战",
}

#: Behavior sets, verbatim from services.tutor_core.build_pedagogical_strategy.
#: Re-declared (not imported) because the original lives inside a function.
STRATEGY_BEHAVIORS: dict[str, tuple[str, ...]] = {
    "socratic_questioning": ("先追问理由", "用对比问题暴露混淆", "不给出替代性自报掌握结论"),
    "feynman_teach_back": ("让学习者用自己的话复述", "从解释缺口继续追问", "把复述作为新证据而非掌握结论"),
    "worked_example": ("先展示完整例题", "再标出关键步骤", "最后换一个近似例子检查理解"),
    "scaffolding": ("拆成小步骤", "一次推进一个关键点", "逐步减少提示"),
    "retrieval_practice": ("先独立回忆或作答", "延后提示", "用结果决定是否继续练习"),
    "transfer_challenge": ("使用新情境", "要求解释迁移理由", "避免只复述原题"),
}

#: Hard cap on how many claims a single directive carries. Five is the
#: agreed surface; tests pin it.
MAX_CLAIMS = 5

#: Markers that a claim is asking for verification, not assertion. Every
#: extracted claim must contain at least one of these — that is the
#: invariant tests check.
META_VERIFICATION_MARKERS: tuple[str, ...] = (
    "待验证",
    "需先界定",
    "需先确认",
    "待核查",
    "需要先",
    "要界定",
    "先说清",
    "要分清",
    "要区分",
    "先确认",
)

#: Phrases that signal the student is volunteering a self-report ("I think…",
#: "I believe…") without evidence. When present we bias toward
#: feynman_teach_back — letting them explain back is the only way to verify
#: the claim.
SELF_REPORT_TRIGGERS: tuple[str, ...] = (
    "我觉得",
    "我认为",
    "我以为",
    "我相信",
    "我觉得…",
    "应该是",
    "大概是",
    "可能",
    "应该是吧",
)

#: Phrases that suggest the student has made a prior factual claim that we
#: should re-examine with socratic questioning (common_error).
ERROR_HISTORY_TRIGGERS: tuple[str, ...] = (
    "我说错了",
    "我又错了",
    "不对",
    "反了",
    "我搞反",
    "我刚才说",
)


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckableClaim:
    """One checkable assertion a topic needs to be settled on.

    ``text`` is the assertion, ``kind`` is the meta-level category, and
    ``evidence_hint`` tells the tutor what would count as the assertion being
    true. ``evidence_hint`` must be non-empty and must not be byte-identical
    to ``text``; tests pin both.
    """

    text: str
    kind: str
    evidence_hint: str

    def is_well_formed(self) -> bool:
        text = (self.text or "").strip()
        hint = (self.evidence_hint or "").strip()
        if not text or not hint:
            return False
        if text == hint:
            return False
        if self.kind not in CLAIM_KINDS:
            return False
        if not any(marker in text for marker in META_VERIFICATION_MARKERS):
            return False
        return True


# ---------------------------------------------------------------------------
# Strategy selection — purely deterministic
# ---------------------------------------------------------------------------


def select_universal_strategy(
    *,
    claim: CheckableClaim,
    learner_state: dict[str, Any] | None,
    history: Sequence[dict[str, Any]],
) -> str:
    """Pick one of :data:`UNIVERSAL_STRATEGIES` for the next directive.

    Selection is keyed on ``claim.kind`` plus the recent ``history`` shape;
    it never inspects the topic text itself, which is what keeps this path
    pack-independent.
    """
    last_student = _last_student_text(history)
    has_error_history = any(
        trigger in (text or "")
        for trigger in ERROR_HISTORY_TRIGGERS
        for text in _history_texts(history)
    )
    if last_student and any(t in last_student for t in SELF_REPORT_TRIGGERS):
        return "feynman_teach_back"
    if claim.kind == "common_error" and has_error_history:
        return "socratic_questioning"
    if claim.kind in {"responsibility", "trade_off"}:
        return "socratic_questioning"
    if claim.kind in {"definition", "boundary"} and _learner_weak(learner_state):
        return "worked_example"
    if claim.kind == "definition" and not _learner_weak(learner_state):
        return "scaffolding"
    return "scaffolding"


def _last_student_text(history: Sequence[dict[str, Any]]) -> str:
    for message in reversed(list(history or [])):
        role = str(message.get("role") or "").lower()
        if role in {"user", "student", "learner"}:
            return str(message.get("content") or message.get("text") or "")
    return ""


def _history_texts(history: Sequence[dict[str, Any]]) -> Iterable[str]:
    for message in history or []:
        yield str(message.get("content") or message.get("text") or "")


def _learner_weak(learner_state: dict[str, Any] | None) -> bool:
    if not learner_state:
        return True
    if learner_state.get("topic_mastery") in {"weak", "low", "unknown", None, ""}:
        return True
    mastery = learner_state.get("mastery_signal")
    if isinstance(mastery, (int, float)) and mastery < 0.55:
        return True
    return False


# ---------------------------------------------------------------------------
# Claim extraction — one LLM call per thread, deterministic post-processing
# ---------------------------------------------------------------------------


_EXTRACTION_SYSTEM_PROMPT = """你是 DARYA 的通用核查助手，请严格按下列要求输出：
1. 用户的话题不属于已装包领域（Python / Physics）。你要列出最多 5 条**元层级**的可核查命题——
   即用「待验证 / 需先界定 / 待核查 / 要分清 / 先确认」一类词开头，而不是直接给答案。
2. 每条命题的 kind 必须是以下五个之一：definition / boundary / responsibility / trade_off / common_error。
3. 每条命题附 evidence_hint：什么算这条命题成立。不能与命题文本完全相同。
4. 只输出 JSON，不要任何解释或前后缀文字。形如：
   {"claims": [{"text": "...", "kind": "definition", "evidence_hint": "..."}, ...]}
"""

_EXTRACTION_USER_TEMPLATE = """话题：{topic}

最近对话（最多 5 条，最近的在后）：
{history}

请输出至多 {max_claims} 条元层级可核查命题的 JSON。"""


def _format_history(history: Sequence[dict[str, Any]] | None, limit: int = 5) -> str:
    parts: list[str] = []
    for message in list(history or [])[-limit:]:
        role = str(message.get("role") or "user")
        content = str(message.get("content") or message.get("text") or "").strip()
        if not content:
            continue
        parts.append(f"[{role}] {content[:240]}")
    return "\n".join(parts) or "（无最近对话）"


async def extract_checkable_claims(
    *,
    topic: str,
    history: Sequence[dict[str, Any]] | None,
    max_claims: int = MAX_CLAIMS,
) -> list[CheckableClaim]:
    """Return up to ``max_claims`` meta-level checkable claims for ``topic``.

    The function never raises: a model failure, empty response, or post-process
    rejection all degrade to an empty list. The caller decides what to do with
    that. This is the contract the directive builder relies on.
    """
    clean_topic = (topic or "").strip()
    if not clean_topic:
        return []
    cap = max(1, min(int(max_claims or MAX_CLAIMS), MAX_CLAIMS))
    try:
        llm = await get_llm_service()
    except Exception as exc:  # noqa: BLE001 — degrade silently, by contract.
        logger.warning("universal_tutor: get_llm_service failed: %s", type(exc).__name__)
        return []
    messages = [
        {
            "role": "user",
            "content": _EXTRACTION_USER_TEMPLATE.format(
                topic=clean_topic[:800],
                history=_format_history(history),
                max_claims=cap,
            ),
        }
    ]
    try:
        response = await llm.chat(
            messages=messages,
            system_prompt=_EXTRACTION_SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=800,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("universal_tutor: llm.chat failed: %s", type(exc).__name__)
        return []
    text = str(response.get("content") or response.get("text") or "")
    parsed = extract_json_object(text) or {}
    raw_claims = parsed.get("claims") if isinstance(parsed, dict) else None
    if not isinstance(raw_claims, list):
        return []
    return _post_process_claims(raw_claims, cap=cap)


def _post_process_claims(raw_claims: Sequence[Any], *, cap: int) -> list[CheckableClaim]:
    out: list[CheckableClaim] = []
    seen: set[str] = set()
    for entry in raw_claims:
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text") or "").strip()
        kind = str(entry.get("kind") or "").strip().lower()
        hint = str(entry.get("evidence_hint") or "").strip()
        if kind not in CLAIM_KINDS:
            kind = _fallback_kind(text)
        if not text:
            continue
        if not hint:
            hint = _fallback_hint(kind)
        if not any(marker in text for marker in META_VERIFICATION_MARKERS):
            text = f"待核查：{text}".strip()
        claim = CheckableClaim(text=text[:240], kind=kind, evidence_hint=hint[:240])
        if not claim.is_well_formed():
            continue
        key = (claim.text, claim.kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(claim)
        if len(out) >= cap:
            break
    return out


_KIND_HINT_FALLBACKS: dict[str, str] = {
    "definition": "看概念是否被双方共同接受；接受方给出可观察的判别标准。",
    "boundary": "看是否给出了边界条件的具体例子；不接受则标出冲突处。",
    "responsibility": "看是否指明主体和可追责的失败模式；不接受「大家都有责任」。",
    "trade_off": "看是否说清楚放弃什么、保留什么、衡量尺度；不接受只列优点。",
    "common_error": "看是否能用一个反例推翻「这总是 X」的表述；接受则需找反例。",
}


def _fallback_kind(text: str) -> str:
    lowered = text.lower()
    if "定义" in text or "是什么" in text:
        return "definition"
    if "边界" in text or "区分" in text or " vs " in lowered or "区别" in text:
        return "boundary"
    if "责任" in text or "谁负责" in text or "归谁" in text:
        return "responsibility"
    if "取舍" in text or "权衡" in text or "代价" in text:
        return "trade_off"
    return "common_error"


def _fallback_hint(kind: str) -> str:
    return _KIND_HINT_FALLBACKS.get(kind, _KIND_HINT_FALLBACKS["common_error"])


# ---------------------------------------------------------------------------
# Directive assembly — no LLM call
# ---------------------------------------------------------------------------


def build_universal_directive(
    *,
    claims: Sequence[CheckableClaim],
    strategy: str,
    topic: str,
) -> str:
    """Build the system-prompt directive from pre-extracted claims + a strategy.

    Returns an empty string when there is nothing worth telling the model.
    The chat endpoint must treat empty strings as a no-op.
    """
    clean_claims = [claim for claim in claims if claim.is_well_formed()]
    if not clean_claims or strategy not in UNIVERSAL_STRATEGIES:
        return ""
    clean_topic = (topic or "").strip() or "当前话题"
    behaviors = STRATEGY_BEHAVIORS.get(strategy, ())
    strategy_label = STRATEGY_LABELS.get(strategy, strategy)

    lines: list[str] = []
    lines.append("你现在处于 DARYA 通用辅导模式（无学科领域包）。")
    lines.append(f"用户当前话题：「{clean_topic[:80]}」。")
    lines.append("")
    lines.append(f"本轮需核查的命题（最多 {MAX_CLAIMS} 条，按元层级分类）：")
    for index, claim in enumerate(clean_claims[:MAX_CLAIMS], start=1):
        lines.append(
            f"{index}. {claim.text}（{claim.kind}）——成立条件：{claim.evidence_hint}"
        )
    lines.append("")
    lines.append(f"采用的教法：{strategy_label}（来自通用教法库，包无关）。")
    lines.append("执行要点：")
    for behavior in behaviors:
        lines.append(f"  - {behavior}")
    lines.append("  - 结尾必须有一个**可独立判断**的小问题（不是「懂了吗」）。")
    lines.append("  - 要求学习者独立作答的题目，在他提交答案前只给题目和作答要求；不要同时泄露该题答案、标准分类或可直接推出答案的提示。")
    lines.append("  - 不得声称已掌握；不得越过 evidence_hint 凭印象断言。")
    lines.append(
        "其余 DARYA 通用底线（不编造来源、不替用户下结论、遇到争议先说「这一点要看…」）继续生效。"
    )
    return "\n".join(lines).strip()


async def build_universal_directive_from_message(
    *,
    user_message: str,
    history: Sequence[dict[str, Any]] | None,
    profile: dict[str, Any] | None,
    learner_state: dict[str, Any] | None,
) -> str:
    """High-level entry point used by ``routers/chat.py``.

    Returns ``""`` on any internal failure (model timeout, parse error, no
    well-formed claims). The directive builder is purely additive; an empty
    result means the chat endpoint should fall through with no directive.
    """
    topic = _extract_topic(user_message, history)
    try:
        claims = await extract_checkable_claims(
            topic=topic,
            history=history,
            max_claims=MAX_CLAIMS,
        )
    except Exception as exc:  # noqa: BLE001 — contract: never raise.
        logger.warning("universal_tutor: extract_checkable_claims crashed: %s", type(exc).__name__)
        return ""
    if not claims:
        return ""
    focus = claims[0]
    strategy = select_universal_strategy(
        claim=focus,
        learner_state=learner_state,
        history=history or [],
    )
    try:
        return build_universal_directive(
            claims=claims,
            strategy=strategy,
            topic=topic,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("universal_tutor: build_universal_directive crashed: %s", type(exc).__name__)
        return ""


_TOPIC_HINT_RE = re.compile(
    r"(?:关于|讨论|辩题|准备|我想|我想弄清|我在准备|我打算|下周|明天|最近|马上|当前)"
    r"[^。！？\n]{0,80}",
    re.UNICODE,
)


def _extract_topic(user_message: str, history: Sequence[dict[str, Any]] | None) -> str:
    """Return the best topic sentence we can recover from the message or history.

    Heuristic, not LLM-based: prefer the user message; if too short or empty,
    fall back to the last student turn in history. Stripped to 240 chars.
    """
    msg = (user_message or "").strip()
    if msg:
        return msg[:240]
    for message in reversed(list(history or [])):
        role = str(message.get("role") or "").lower()
        if role in {"user", "student", "learner"}:
            text = str(message.get("content") or message.get("text") or "").strip()
            if text:
                return text[:240]
    return ""


# Re-export commonly imported names for tests.
__all__ = [
    "CLAIM_KINDS",
    "CheckableClaim",
    "MAX_CLAIMS",
    "META_VERIFICATION_MARKERS",
    "STRATEGY_BEHAVIORS",
    "STRATEGY_LABELS",
    "UNIVERSAL_STRATEGIES",
    "build_universal_directive",
    "build_universal_directive_from_message",
    "extract_checkable_claims",
    "select_universal_strategy",
]
