"""A second pair of eyes on a draft answer, before the learner reads it.

This is deliberately *not* a censor. It does not rewrite, block, or score the
answer. It looks for the specific ways a helpful-sounding answer goes wrong,
and hands back short, polite reminders that the caller can append to the model's
next step or to the reply itself:

    numbers and dates with nothing behind them
    named sources that were never retrieved
    "already done / already generated" with no receipt
    "you now understand this" with no judged attempt
    a promised channel that this build cannot actually run

Everything here is deterministic -- pattern and record matching, no model call.
A guard that costs a model call on every turn would be turned off, and a guard
that is off protects nobody. It is also intentionally conservative: it prefers
saying nothing to crying wolf, because a reminder that fires on healthy answers
trains everyone to ignore it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

# "已经完成" family -- only a real receipt may support these.
COMPLETION_CLAIM = re.compile(
    r"(?:已经|已)(?:经)?(?:生成|完成|做好|画好|出好|建好|跑通|执行|提交|保存|写好)"
    r"|(?:生成|做|画|写)(?:好|完了)了"
)

# Claims about the learner's mind. Only a judged attempt supports these.
MASTERY_CLAIM = re.compile(
    r"(?:你|您)(?:现在)?(?:已经)?(?:完全)?(?:掌握|学会|弄懂|搞懂|理解了|懂了)"
    r"|(?:已经|已)(?:掌握|学会)"
)

# A number or date stated as fact. Loose on purpose: a false positive here is
# cheap (the reminder is soft), a miss is what we are trying to prevent.
NUMBER_FACT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|年|月|日|岁|万|亿|人|次|倍|分|名|所|个)")

SOURCE_CLAIM = re.compile(
    r"(?:据|根据|按照|引用)[^，。；\n]{0,18}(?:报告|研究|统计|数据|官方|论文|调查|白皮书)"
    r"|(?:研究|报告|统计)(?:表明|显示|指出)"
)

CAPABILITY_PROMISE = re.compile(
    r"(?:我(?:会|来|马上|现在)?(?:给你|帮你|为你)?(?:生成|做|画|出|准备|整理|制作))"
)

POLITE_TAIL = "以上如有与实际不符的地方，以你看到的结果为准。"


@dataclass(frozen=True)
class GuardFinding:
    kind: str
    detail: str
    reminder: str


@dataclass(frozen=True)
class GuardReport:
    findings: tuple[GuardFinding, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.findings

    def reminders(self) -> tuple[str, ...]:
        return tuple(finding.reminder for finding in self.findings)

    def render(self) -> str:
        """The polite note to hand back, or an empty string when there is none."""
        if not self.findings:
            return ""
        lines = "\n".join(f"- {finding.reminder}" for finding in self.findings)
        return (
            "写之前先对一下这几件事（不是批评，是提醒）：\n"
            f"{lines}\n"
            f"{POLITE_TAIL}"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "findings": [f.kind for f in self.findings],
            "reminders": list(self.reminders()),
        }


def _norm_number(value: str) -> str:
    """Compare numbers without the spacing a writer may or may not use.

    "2026 年" and "2026年" are the same fact; treating them as different made
    the guard flag a year the learner had just typed.
    """
    return re.sub(r"\s+", "", str(value or "")).lower()


def _supported_numbers(sources: Iterable[str]) -> set[str]:
    haystack = " ".join(sources)
    return {_norm_number(match.group(0)) for match in NUMBER_FACT.finditer(haystack)}


def review(
    draft: str,
    *,
    evidence: Mapping[str, Any] | None = None,
    receipts: Sequence[Mapping[str, Any]] = (),
    available_capabilities: Sequence[str] = (),
    judged_attempts: int = 0,
    confirmed_facts: Sequence[str] = (),
    learner_message: str = "",
) -> GuardReport:
    """Look over a draft answer. Returns reminders; never changes the draft."""
    text = str(draft or "")
    if not text.strip():
        return GuardReport()

    source_texts = [
        str(source.get("claim_provenance", {}).get("evidence_span") or "")
        + " " + str(source.get("title") or "")
        for source in ((evidence or {}).get("sources") or [])
        if isinstance(source, Mapping)
    ] + [str(fact) for fact in confirmed_facts]
    # A number the learner typed is not a claim the model made. Measured: the
    # board line "我在黑板上讲「2026 年高考报名时间」" was flagged for the year
    # the learner themselves had just written, which is how a guard teaches
    # everyone to ignore it.
    if str(learner_message or "").strip():
        source_texts.append(str(learner_message))

    findings: list[GuardFinding] = []

    # 1. "it is done" with nothing that says so.
    if COMPLETION_CLAIM.search(text) and not receipts:
        findings.append(GuardFinding(
            kind="completion_without_receipt",
            detail="draft claims something was completed but this turn has no receipt",
            reminder="还没拿到真实回执，就不要写「已经生成/已经完成」；说清楚现在做到哪一步了。",
        ))

    # 2. "you understand it now" with no judged attempt.
    if MASTERY_CLAIM.search(text) and judged_attempts <= 0:
        findings.append(GuardFinding(
            kind="mastery_without_attempt",
            detail="draft claims the learner has understood, with no judged attempt",
            reminder="说「你掌握了」要有独立作答的判定；没有的话就说「这一段我们刚讲完」，不要说掌握。",
        ))

    # 3. Numbers asserted as fact that no source carries.
    supported = _supported_numbers(source_texts)
    naked = [
        match.group(0) for match in NUMBER_FACT.finditer(text)
        if _norm_number(match.group(0)) not in supported
    ]
    if naked:
        sample = "、".join(dict.fromkeys(naked))[:60]
        findings.append(GuardFinding(
            kind="unsourced_number",
            detail=f"numbers with no source: {sample}",
            reminder=f"这些数字（{sample}）没有来源撑着，要么给出出处，要么改成「我不确定」。",
        ))

    # 4. A study/report cited that was never retrieved.
    if SOURCE_CLAIM.search(text) and not [s for s in source_texts if s.strip()]:
        findings.append(GuardFinding(
            kind="unsourced_citation",
            detail="draft cites a study/report but no source was retrieved",
            reminder="引用了研究报告但本轮没有真的查到，不要写成有出处；要就现在去查。",
        ))

    # 5. Promising something this build cannot produce.
    if CAPABILITY_PROMISE.search(text) and available_capabilities:
        known = {str(name).upper() for name in available_capabilities}
        if not known:
            findings.append(GuardFinding(
                kind="capability_unavailable",
                detail="draft promises to produce something but no channel is available",
                reminder="这一轮没有可用的产出通道，别承诺「我给你生成」，先交付对话里能给出的东西。",
            ))

    return GuardReport(findings=tuple(findings))


def available_channel_names(registry: Mapping[str, Any] | None = None) -> tuple[str, ...]:
    """Names of the channels this build can actually run."""
    if registry is None:
        try:
            from services.darya_cognition_orchestrator_v2 import capability_registry
            registry = capability_registry()
        except Exception:
            return ()
    return tuple(str(name) for name in (registry or {}))


REMINDER_PREFIX = "上一轮你自己说过的话里，有几处我没能在任何来源里找到依据。"


def reminder_from_history(recent_history: Sequence[Mapping[str, Any]] | None) -> str:
    """Bring the last guard finding forward as a reminder to the model.

    The guard records what it found, but a record nobody reads changes nothing:
    the same unbacked claim can be repeated next turn. This reads the most recent
    reviewed turn and hands the reminders back, so the model is told rather than
    quietly filed.
    """
    for row in reversed(list(recent_history or [])):
        if not isinstance(row, Mapping) or str(row.get("role")) != "assistant":
            continue
        review = row.get("hallucination_review")
        if not isinstance(review, Mapping):
            continue
        reminders = [str(item) for item in (review.get("reminders") or []) if str(item).strip()]
        if not reminders:
            return ""
        body = chr(10).join("- " + item for item in reminders[:4])
        return REMINDER_PREFIX + chr(10) + body
    return ""
