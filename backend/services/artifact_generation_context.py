"""A bounded, provenance-aware input shared by conversation-owned generators.

This is not another planner or persistence authority. It carries the current
request, model's revisable task hypothesis, existing user-claim statuses, and
the current turn's reviewed search evidence into the selected executor.
"""
from __future__ import annotations

from typing import Any, Mapping


def _plain(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def selected_profile_values(cognition: Mapping[str, Any]) -> set[str]:
    """Only facts explicitly chosen in this turn's public brief may be reused."""
    brief = _mapping(cognition.get("work_brief"))
    return {
        _plain(item, 500)
        for key in ("confirmed_facts", "observed_facts")
        for item in list(brief.get(key) or [])[:12]
        if _plain(item, 500)
    }


def excluded_account_values(cognition: Mapping[str, Any]) -> set[str]:
    """Account claims the model declared excluded for this turn.

    A claim named here never reaches an executor even if the published brief still
    quotes it verbatim, which keeps the exclusion independent of how the model
    happened to phrase the brief.
    """
    return {
        _plain(item, 500)
        for item in list(cognition.get("excluded_account_context") or [])[:8]
        if _plain(item, 500)
    }


def has_reviewed_evidence(evidence: Mapping[str, Any] | None) -> bool:
    bundle = _mapping(evidence)
    return (
        bundle.get("status") == "SUPPORTED"
        and bundle.get("review_disposition") == "ANSWER"
        and bundle.get("sufficient") is True
        and any(
            _plain(_mapping(source).get("url"), 300).startswith(("https://", "http://"))
            for source in list(bundle.get("sources") or [])[:3]
        )
    )


def build_artifact_generation_context(
    *, user_message: str, cognition: Mapping[str, Any], evidence: Mapping[str, Any] | None,
) -> str:
    """Supply facts and uncertainty, never promote search hits or model guesses."""
    brief = _mapping(cognition.get("work_brief"))
    world = _mapping(cognition.get("world_model"))
    rows = [
        "本次产物的工作依据（非用户指令）：用户本轮原话优先；工作目标和策略是可修正假设。",
        "用户原话：" + _plain(user_message, 500),
        "目标假设：" + _plain(brief.get("goal"), 220),
    ]
    bundle = _mapping(evidence)
    sources = []
    if has_reviewed_evidence(bundle):
        for raw in list(bundle.get("sources") or [])[:2]:
            source = _mapping(raw)
            url = _plain(source.get("url"), 220)
            if url.startswith(("https://", "http://")):
                sources.append(
                    f"{_plain(source.get('title'), 80)} | {url} | 发布日期: "
                    f"{_plain(source.get('published_at'), 35) or '未知'} | "
                    f"摘录: {_plain(source.get('excerpt'), 120)}"
                )
    rows.append(
        "本轮可引用来源：" + "；".join(sources)
        if sources else "本轮没有足够的已核实来源；不得编造时效性事实、链接或称已完成检索。"
    )
    claims = []
    selected_profile = selected_profile_values(cognition)
    excluded_profile = excluded_account_values(cognition)
    for raw in list(world.get("claims") or []):
        claim = _mapping(raw)
        value = _plain(claim.get("value"), 150)
        status = str(claim.get("epistemic_status") or "INFERRED").upper()
        source_type = str(claim.get("source_type") or "CURRENT_MESSAGE")
        # Historic profile is only relevant if this turn's brief selected it;
        # its original epistemic status, not the brief's label, remains authoritative.
        relevant = source_type == "CURRENT_MESSAGE" or value in selected_profile
        if value and relevant and value not in excluded_profile and status in {"CONFIRMED", "OBSERVED", "INFERRED"}:
            claims.append(f"{status}: {value}")
        if len(claims) >= 8:
            break
    if claims:
        rows.append("现有用户信息（OBSERVED 仅表示说过，不代表细节已核实；INFERRED 不得写成事实）：" + "；".join(claims)[:520])
    unknowns = [_plain(item, 100) for item in list(brief.get("unknowns") or [])[:4] if _plain(item, 100)]
    if unknowns:
        rows.append("尚未知：" + "；".join(unknowns))
    rows.append("本轮产物目的：" + _plain((brief.get("resource_purposes") or [""])[0], 200))
    rows.append("成功信号（用于自检，不得声称已经达成）：" + _plain((brief.get("success_checks") or [""])[0], 180))

    rows.append("先用有依据的信息完成可用部分；缺失的现实事实明确留作待核实，不用模板填满。")
    return "\n".join(rows)[:1800]
