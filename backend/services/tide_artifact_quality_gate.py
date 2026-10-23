"""Bounded generator-to-reviewer orchestration for artifact candidates."""
from __future__ import annotations

import copy
import json
from typing import Any, Awaitable, Callable, Mapping

from services.tide_artifact_review import review_artifact_candidate


ArtifactReviewer = Callable[..., Awaitable[dict[str, Any]]]
CandidateReviser = Callable[..., Awaitable[dict[str, Any]]]
CandidateNormalizer = Callable[[Mapping[str, Any]], dict[str, Any]]
CandidateChanged = Callable[[Mapping[str, Any], Mapping[str, Any]], bool]


def _constraints(review: Mapping[str, Any]) -> list[str]:
    result = []
    for issue in review.get("issues") or []:
        if not isinstance(issue, Mapping):
            continue
        value = str(issue.get("recommended_constraint") or "").strip()
        if value and value not in result:
            result.append(value[:600])
    return result[:12]


def public_quality_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    reviews = list(result.get("reviews") or [])
    final = reviews[-1] if reviews else {}
    issues = list(final.get("issues") or [])
    needs_evidence = [
        {
            "location": issue.get("location"),
            "description": issue.get("description"),
            "evidence_status": issue.get("evidence_status"),
        }
        for issue in issues
        if isinstance(issue, Mapping) and issue.get("evidence_status") == "UNVERIFIED"
    ][:6]
    strengthened = []
    if int(result.get("revision_rounds") or 0) > 0:
        for review in reviews[:-1]:
            for issue in review.get("issues") or []:
                if not isinstance(issue, Mapping):
                    continue
                value = str(issue.get("recommended_constraint") or "").strip()
                if value and value not in strengthened:
                    strengthened.append(value[:240])
    return {
        "status": result.get("status"),
        "final_verdict": final.get("verdict", "block"),
        "review_rounds": len(reviews),
        "max_review_rounds": result.get("max_review_rounds"),
        "revision_rounds": result.get("revision_rounds", 0),
        "summary": final.get("summary") or "质量审阅未完成。",
        "issue_count": len(issues),
        "needs_evidence": needs_evidence,
        "materially_changed": bool(result.get("materially_changed")),
        "strengthened": strengthened[:4],
    }


async def run_artifact_quality_gate(
    *,
    source_candidate: Mapping[str, Any],
    initial_candidate: Mapping[str, Any],
    review_context: Mapping[str, Any],
    revise_candidate: CandidateReviser,
    normalize_candidate: CandidateNormalizer,
    candidate_changed: CandidateChanged,
    reviewer: ArtifactReviewer = review_artifact_candidate,
    max_review_rounds: int = 2,
) -> dict[str, Any]:
    """Review, revise from structured feedback, and stop after a small fixed bound."""
    maximum = max(1, min(int(max_review_rounds), 2))
    original = copy.deepcopy(dict(initial_candidate))
    candidate = copy.deepcopy(original)
    reviews: list[dict[str, Any]] = []
    revision_rounds = 0
    materially_changed = False

    for round_number in range(1, maximum + 1):
        review_input = dict(review_context)
        review_input["source_artifact"] = copy.deepcopy(dict(source_candidate))
        review_input["proposed_candidate"] = copy.deepcopy(candidate)
        review = await reviewer(review_input=review_input, round_number=round_number)
        reviews.append(copy.deepcopy(review))
        verdict = str(review.get("verdict") or "block")
        if verdict == "pass":
            return {
                "status": "passed",
                "candidate": candidate,
                "original_candidate": original,
                "reviews": reviews,
                "revision_rounds": revision_rounds,
                "materially_changed": materially_changed,
                "max_review_rounds": maximum,
            }
        if verdict == "block" or round_number >= maximum:
            break
        constraints = _constraints(review)
        if not constraints:
            break
        revised = await revise_candidate(
            existing_candidate=copy.deepcopy(candidate),
            constraints=constraints,
            prior_review=copy.deepcopy(review),
            round_number=round_number,
        )
        revised = normalize_candidate(revised)
        if not candidate_changed(candidate, revised):
            break
        candidate = revised
        revision_rounds += 1
        materially_changed = materially_changed or (
            json.dumps(original, ensure_ascii=False, sort_keys=True)
            != json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        )

    return {
        "status": "blocked",
        "candidate": candidate,
        "original_candidate": original,
        "reviews": reviews,
        "revision_rounds": revision_rounds,
        "materially_changed": materially_changed,
        "max_review_rounds": maximum,
    }
