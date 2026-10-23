"""Independent artifact review contract for DARYA's quality gate.

The reviewer receives product context and candidate content, never private
generator reasoning.  Its structured output is judgment, not chain-of-thought.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal, Mapping


ReviewVerdict = Literal["pass", "revise", "block"]
ClaimStatus = Literal["SUPPORTED", "USER_PROVIDED", "INFERRED", "UNVERIFIED"]

_IMPORTANT_CLAIM = re.compile(
    r"(?:\b\d+(?:\.\d+)?\s*(?:%|percent|倍|万人|人|项|次|小时|分钟)\b|"
    r"研究(?:表明|显示|证明)|数据显示|调研显示|竞赛(?:要求|规定)|比赛(?:要求|规定)|"
    r"自动(?:保存|保留|同步|生成|识别|追踪|恢复)|保证|确保|已经(?:实现|支持|获得|达到)|"
    r"提升了?|降低了?|增长了?|减少了?|覆盖(?:了)?|"
    r"research\s+(?:shows|proves)|studies\s+show|competition\s+requires|"
    r"automatically\s+(?:preserves?|saves?|syncs?|generates?|tracks?)|"
    r"\b(?:the\s+)?(?:system|platform|product|workspace|darya|this)\s+"
    r"(?:(?:directly|currently|seamlessly|reliably|automatically)\s+)?"
    r"(?:provides?|gives?|offers?|enables?|allows?|supports?|preserves?|tracks?|records?)\b|"
    r"\b(?:evaluators?|reviewers?|users?|learners?)\s+can\s+(?:inspect|trace|verify|review|see|follow)\b|"
    r"\b(?:every|each)\s+(?:output|artifact|decision)\s+(?:links?|maps?|traces?)\b|"
    r"\bthis\s+page\s+(?:shows?|demonstrates?|proves?)\b|"
    r"\b(?:trace|review|verify)\s+(?:every|each|alignment)\b|"
    r"(?:系统|平台|产品|工作区|DARYA)(?:可以|能够|会|提供|支持|实现|让)|"
    r"guarantees?|has\s+(?:achieved|supported)|increases?|reduces?)",
    re.I,
)
_UNCERTAINTY_LANGUAGE = re.compile(
    r"需要(?:验证|核查|提供|展示)|待(?:验证|核查|补充)|计划|目标|旨在|设计为|"
    r"可作为|可能|预计|假设|如果|需求|应当|"
    r"needs?\s+(?:verification|evidence)|requires?\s+evidence|to\s+be\s+verified|"
    r"to\s+(?:demonstrate|verify|validate)\s*:|"
    r"designed\s+to|aims?\s+to|planned|proposed|may|could|if\b",
    re.I,
)


def _text(value: Any, limit: int = 3000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _extract_json(raw: Any) -> dict[str, Any]:
    text = str(raw or "").replace("\ufeff", "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("artifact reviewer returned no JSON object")
    value = json.loads(text[start:end + 1])
    if not isinstance(value, dict):
        raise ValueError("artifact reviewer returned invalid JSON")
    return value


def _deck_text(deck: Mapping[str, Any]) -> str:
    parts = [_text(deck.get("title"), 500)]
    for slide in deck.get("slides") or []:
        if not isinstance(slide, Mapping):
            continue
        parts.extend(_text(slide.get(key), 5000) for key in ("title", "subtitle", "body", "narration"))
        parts.extend(_text(item, 1000) for item in (slide.get("items") or []))
    return "\n".join(part for part in parts if part)


def _claim_location(slide: Mapping[str, Any], index: int) -> str:
    raw = slide.get("index")
    try:
        return f"page {int(raw) + 1}"
    except (TypeError, ValueError):
        return f"page {index + 1}"


def classify_candidate_claims(
    *, candidate: Mapping[str, Any], source: Mapping[str, Any], user_request: str,
) -> list[dict[str, str]]:
    """Conservative guardrail for important claims that need explicit support."""
    source_text = _deck_text(source).lower()
    request_text = _text(user_request, 4000).lower()
    claims: list[dict[str, str]] = []
    for index, slide in enumerate(candidate.get("slides") or []):
        if not isinstance(slide, Mapping):
            continue
        fragments = [slide.get("title"), slide.get("subtitle"), slide.get("body"), *(slide.get("items") or [])]
        for fragment in fragments:
            for sentence in re.split(r"(?<=[。！？!?；;])\s*|\n+", _text(fragment, 5000)):
                statement = _text(sentence, 800)
                if not statement or not _IMPORTANT_CLAIM.search(statement) or _UNCERTAINTY_LANGUAGE.search(statement):
                    continue
                lowered = statement.lower()
                if lowered in source_text:
                    status: ClaimStatus = "SUPPORTED"
                elif lowered in request_text:
                    status = "USER_PROVIDED"
                else:
                    status = "UNVERIFIED"
                claims.append({
                    "text": statement,
                    "status": status,
                    "category": "important_factual_or_product_claim",
                    "location": _claim_location(slide, index),
                })
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for claim in claims:
        key = (claim["location"], claim["text"])
        if key not in seen:
            seen.add(key)
            unique.append(claim)
    return unique[:24]


def _score(value: Any) -> int:
    try:
        return max(0, min(5, int(value)))
    except (TypeError, ValueError):
        return 0


def normalize_review(
    value: Mapping[str, Any], *, round_number: int, detected_claims: list[dict[str, str]],
) -> dict[str, Any]:
    verdict = str(value.get("verdict") or "revise").lower()
    if verdict not in {"pass", "revise", "block"}:
        verdict = "revise"
    issues = []
    for raw in value.get("issues") or []:
        if not isinstance(raw, Mapping):
            continue
        category = _text(raw.get("category"), 80) or "clarity"
        severity = _text(raw.get("severity"), 30).lower() or "medium"
        if severity not in {"low", "medium", "high", "blocking"}:
            severity = "medium"
        issues.append({
            "category": category,
            "severity": severity,
            "location": _text(raw.get("location") or raw.get("page"), 100) or "artifact",
            "description": _text(raw.get("description"), 500) or "候选仍需进一步核查。",
            "why_it_matters": _text(raw.get("why_it_matters"), 500),
            "evidence_status": _text(raw.get("evidence_status"), 40).upper() or "UNVERIFIED",
            "recommended_constraint": _text(raw.get("recommended_constraint"), 600) or "保留原意，改成可核查且不过度断言的表达。",
        })

    claims = []
    for raw in value.get("claims") or []:
        if not isinstance(raw, Mapping):
            continue
        status = _text(raw.get("status"), 30).upper()
        if status not in {"SUPPORTED", "USER_PROVIDED", "INFERRED", "UNVERIFIED"}:
            status = "UNVERIFIED"
        claims.append({
            "text": _text(raw.get("text"), 800),
            "status": status,
            "category": _text(raw.get("category"), 100) or "important_claim",
            "location": _text(raw.get("location"), 100) or "artifact",
        })

    for claim in detected_claims:
        if claim["status"] != "UNVERIFIED":
            continue
        if not any(issue["location"] == claim["location"] and issue["category"] == "unsupported_claim" for issue in issues):
            issues.append({
                "category": "unsupported_claim",
                "severity": "high",
                "location": claim["location"],
                "description": f"这句话当前没有可用上下文支持：{claim['text']}",
                "why_it_matters": "把未核实的能力、指标或外部事实写成既成事实会削弱产物可信度。",
                "evidence_status": "UNVERIFIED",
                "recommended_constraint": "删除该断言，或改写为目标、设计意图、待验证要求；不得补造引用。",
            })
        if not any(item["text"] == claim["text"] and item["location"] == claim["location"] for item in claims):
            claims.append(claim)
    if any(issue["severity"] in {"high", "blocking"} for issue in issues) and verdict == "pass":
        verdict = "revise"
    return {
        "verdict": verdict,
        "round": round_number,
        "summary": _text(value.get("summary"), 600) or (
            "这份候选可以进入用户确认。" if verdict == "pass" else "这份候选在进入确认前仍需收紧。"
        ),
        "issues": issues[:24],
        "claims": claims[:24],
        "scores": {
            key: _score(value.get(key) if key in value else (value.get("scores") or {}).get(key))
            for key in (
                "goal_fit", "clarity", "internal_consistency",
                "unsupported_claim_risk", "artifact_integrity",
            )
        },
    }


async def review_artifact_candidate(
    *, review_input: Mapping[str, Any], round_number: int,
) -> dict[str, Any]:
    """Run the independent reviewer using a reviewer-only prompt and contract."""
    source = review_input.get("source_artifact") or {}
    candidate = review_input.get("proposed_candidate") or {}
    detected_claims = classify_candidate_claims(
        candidate=candidate,
        source=source,
        user_request=str(review_input.get("user_request") or ""),
    )
    prompt = f"""你是 DARYA 的独立 Artifact Reviewer，不负责生成候选，也不能假装证据存在。
你只审阅下面的候选是否值得交给用户确认。不要输出思维过程，只输出严格 JSON。

审阅输入：
{json.dumps(dict(review_input), ensure_ascii=False)[:30000]}

预扫描的重要声明：
{json.dumps(detected_claims, ensure_ascii=False)}

输出：
{{
  "verdict":"pass|revise|block",
  "summary":"面向用户的短摘要",
  "issues":[{{"category":"goal_fit|clarity|internal_consistency|unsupported_claim|artifact_integrity","severity":"low|medium|high|blocking","location":"page N 或 artifact","description":"具体问题","why_it_matters":"影响","evidence_status":"SUPPORTED|USER_PROVIDED|INFERRED|UNVERIFIED","recommended_constraint":"交给生成器的明确约束"}}],
  "claims":[{{"text":"重要声明","status":"SUPPORTED|USER_PROVIDED|INFERRED|UNVERIFIED","category":"product_capability|metric|research|competition|external_fact|user_achievement|organization","location":"page N"}}],
  "goal_fit":0,
  "clarity":0,
  "internal_consistency":0,
  "unsupported_claim_risk":0,
  "artifact_integrity":0
}}

规则：
- 结合用户当前请求、Shared Perspective、Workspace Object、源版本、候选、元数据和 evidence status 判断；
- 重要能力、指标、研究、竞赛要求、外部事实、用户成绩或组织信息没有支持时，不能按已验证事实通过；
- 可改成设计意图、需求、待验证项的给 revise；无法安全保留核心含义的给 block；
- 已清楚标为设计目标、待演示、待验证或核查要求，且后续没有继续断言现有能力的表达，不应被当作已验证事实；
- 检查候选是否变得空泛、削弱原内容、改错页面或破坏产物完整性；
- 不发明引用，不索取或输出任何私有推理；
- 分数 0 到 5，unsupported_claim_risk 的 5 表示风险最高；
- 只输出 JSON。"""
    from services.llm_service import get_llm_service
    llm = await get_llm_service()
    raw = await llm.generate(prompt, max_tokens=2600, response_format="json")
    return normalize_review(_extract_json(raw), round_number=round_number, detected_claims=detected_claims)
