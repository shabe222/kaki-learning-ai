"""Small, inspectable resolver for installed DARYA learning domains."""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from services.darya_domain_knowledge import DomainKnowledgePack
from services.darya_domain_registry import get_domain_pack, installed_packs


RESOLVER_CONTRACT_VERSION = "darya_learning_domain_resolver_v1"
_LEARNING_INTENT = re.compile(
    r"学习|在学|掌握|理解|弄懂|搞懂|学明白|不明白|卡在|懵|解释|为什么|为啥|怎么|到底|我觉得|我认为|是不是|哪里.*问题|说不清|说不上来|[?？]|"
    r"教我|带我学|练习|检验|learn|understand|explain|teach|practice|why|I think",
    re.I,
)


@dataclass(frozen=True)
class DomainResolution:
    status: str
    pack: DomainKnowledgePack | None
    confidence: float
    basis: tuple[str, ...]
    alternatives: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {
            "contract_version": RESOLVER_CONTRACT_VERSION,
            "status": self.status,
            "domain_id": self.pack.domain_id if self.pack else None,
            "domain_title": self.pack.title if self.pack else None,
            "confidence": round(self.confidence, 2),
            "basis": list(self.basis),
            "alternatives": list(self.alternatives),
            "ordinary_interaction_fallback": self.pack is None,
        }


def _signal_score(pack: DomainKnowledgePack, text: str) -> tuple[float, list[str]]:
    normalized = text.lower()
    matched: list[str] = []
    score = 0.0
    for signal, weight in pack.resolver_signals.items():
        if signal.lower() in normalized:
            score += float(weight)
            matched.append(signal)
    return score, matched


def resolve_learning_domain(
    *, message: str,
    explicit_domain_id: str | None = None,
    current_domain_id: str | None = None,
    learner_goal: str = "",
    workspace_context: str = "",
    require_learning_intent: bool = True,
) -> DomainResolution:
    """Resolve one installed pack or return an honest non-match.

    Explicit selection and active thread state are authoritative.  New-thread
    inference combines the user message, explicit goal, and workspace context,
    requires learning intent, rejects weak/ambiguous matches, and exposes its
    basis.  It is not a second agent or a keyword-only one-shot router.
    """
    explicit = get_domain_pack(explicit_domain_id)
    if explicit_domain_id:
        if explicit:
            return DomainResolution("MATCHED", explicit, 1.0, ("explicit_domain",))
        return DomainResolution("UNSUPPORTED", None, 1.0, ("explicit_domain_not_installed",))

    current = get_domain_pack(current_domain_id)
    if current_domain_id:
        if current:
            return DomainResolution("MATCHED", current, 0.99, ("active_thread_domain",))
        return DomainResolution("UNSUPPORTED", None, 1.0, ("active_thread_domain_not_installed",))

    combined = " ".join(part for part in (message, learner_goal, workspace_context) if part).strip()
    if not combined:
        return DomainResolution("UNSUPPORTED", None, 0.0, ("empty_need",))
    if require_learning_intent and not _LEARNING_INTENT.search(combined):
        return DomainResolution("NOT_LEARNING", None, 0.9, ("no_learning_intent",))

    ranked: list[tuple[float, DomainKnowledgePack, list[str]]] = []
    for pack in installed_packs():
        message_score, message_hits = _signal_score(pack, message)
        goal_score, goal_hits = _signal_score(pack, learner_goal)
        workspace_score, workspace_hits = _signal_score(pack, workspace_context)
        score = message_score + goal_score * 1.25 + workspace_score * 0.5
        hits = list(dict.fromkeys(message_hits + goal_hits + workspace_hits))
        ranked.append((score, pack, hits))
    ranked.sort(key=lambda item: (item[0], item[1].domain_id), reverse=True)
    top_score, top_pack, top_hits = ranked[0]
    next_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if top_score < 4.0 or not top_hits:
        return DomainResolution("UNSUPPORTED", None, 0.78, ("no_installed_domain_fit",))
    if next_score >= 4.0 and abs(top_score - next_score) < 2.0:
        alternatives = tuple(item[1].domain_id for item in ranked[:2])
        return DomainResolution("AMBIGUOUS", None, 0.45, ("multiple_installed_domains_fit",), alternatives)
    confidence = min(0.96, 0.58 + top_score / 30.0)
    return DomainResolution(
        "MATCHED",
        top_pack,
        confidence,
        tuple(["multi_context_domain_fit", *[f"signal:{item}" for item in top_hits[:4]]]),
        tuple(item[1].domain_id for item in ranked[1:] if item[0] >= 4.0),
    )


def build_domain_disambiguation_question(resolution: DomainResolution) -> str:
    """Build one observable question when multiple installed domains fit.

    The wording is derived from the installed-pack registry, not from a test
    phrase or a hidden model judgment.  Callers must not create learning state
    until the learner chooses one of the alternatives.
    """
    if resolution.status != "AMBIGUOUS" or len(resolution.alternatives) < 2:
        raise ValueError("domain disambiguation requires at least two alternatives")
    packs = [get_domain_pack(domain_id) for domain_id in resolution.alternatives]
    titles = [pack.title for pack in packs if pack is not None]
    if len(titles) < 2:
        raise ValueError("domain disambiguation alternatives must be installed")
    return f"你这里同时可能指向两个学习领域。你想问的是“{titles[0]}”，还是“{titles[1]}”？"
