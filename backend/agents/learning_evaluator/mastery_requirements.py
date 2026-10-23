from __future__ import annotations

import hashlib
import re

from .enums import *
from .execution_codes import EvaluationIssueCode
from .models import (
    MasteryRequirementAssessment,
    MasteryRequirementParseResult,
    NodeEvaluationContract,
    NormalizedEvidence,
    NormalizedMasteryRequirement,
    ObjectiveAssessment,
    ValidationIssue,
    ValidationReport,
)

TYPE_RULES = [
    (MasteryRequirementType.TRANSFER_PERFORMANCE, ("迁移", "新情境", "综合应用", "跨段推断", "推断说明")),
    (MasteryRequirementType.RETENTION_RECHECK, ("间隔复测", "间隔", "复测", "保持", "保留")),
    (MasteryRequirementType.CODE_TEST_PASS, ("代码测试通过", "代码测试", "测试通过", "运行通过")),
    (MasteryRequirementType.UNASSISTED_EXPLANATION, ("独立解释", "独立讲解", "独立复述", "讲解", "复述")),
    (MasteryRequirementType.ERROR_RECOVERY, ("纠错", "错误恢复", "改错")),
    (MasteryRequirementType.VERIFIED_ASSESSMENT, ("可信测评", "验证测评", "测评", "考试", "评分")),
    (MasteryRequirementType.MULTI_ACTIVITY_EVIDENCE, ("不同活动", "多种活动", "多活动", "两种活动")),
    (MasteryRequirementType.MANUAL_REVIEW, ("人工复核", "教师复核", "手工审核")),
    (MasteryRequirementType.OBJECTIVE_COVERAGE, ("目标覆盖", "全部目标", "所有目标")),
    (MasteryRequirementType.INDEPENDENT_ATTEMPT, ("独立作答", "独立完成", "独立尝试", "独立答题")),
]

CHINESE_NUMBERS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "十三": 13, "十四": 14, "十五": 15, "十六": 16, "十七": 17, "十八": 18, "十九": 19, "二十": 20,
}

TRUST_RANK = {
    TrustLevel.INVALID: 0,
    TrustLevel.INFERRED: 1,
    TrustLevel.SELF_REPORTED: 2,
    TrustLevel.STRUCTURED_HUMAN: 3,
    TrustLevel.SYSTEM_OBSERVED: 4,
    TrustLevel.VERIFIED: 5,
}


def _rid(node_id: str, index: int, kind: MasteryRequirementType, ordinal: int, text: str) -> str:
    raw = f"{node_id}|{index}|{kind.value}|{ordinal}|{text}"
    return f"mr-{hashlib.sha256(raw.encode()).hexdigest()[:16]}"


def _parse_int(token: str) -> int | None:
    if token.isdigit():
        value = int(token)
        return value if 0 <= value <= 20 else None
    return CHINESE_NUMBERS.get(token)


def _parse_count(text: str) -> tuple[int | None, bool]:
    patterns = [
        r"至少\s*([0-9]{1,2}|二十|十[一二三四五六七八九]?|[一二两三四五六七八九])\s*(?:次|个|种|条)",
        r"([0-9]{1,2}|二十|十[一二三四五六七八九]?|[一二两三四五六七八九])\s*种不同活动",
        r"([0-9]{1,2}|二十|十[一二三四五六七八九]?|[一二两三四五六七八九])\s*个独立活动",
    ]
    hits = []
    for pattern in patterns:
        hits.extend(re.findall(pattern, text))
    values = [_parse_int(x) for x in hits]
    values = [x for x in values if x is not None]
    if not hits:
        if any(term in text for term in ("若干次", "多次", "足够多", "数次")):
            return None, True
        return None, False
    if not values or len(set(values)) > 1:
        return None, True
    return values[0], False


def _parse_score(text: str) -> tuple[float | None, bool]:
    values: list[float] = []
    for raw in re.findall(r"(?:达到|至少|不低于)\s*([0-9]{1,3}(?:\.[0-9]+)?)\s*%", text):
        values.append(float(raw) / 100)
    for raw in re.findall(r"(?:达到|至少|不低于)\s*(0(?:\.\d+)?|1(?:\.0+)?)\b", text):
        values.append(float(raw))
    for left, right in re.findall(r"(?:达到|至少|不低于)?\s*([0-9]+)\s*/\s*([0-9]+)", text):
        if int(right) <= 0 or int(left) > int(right):
            return None, True
        values.append(int(left) / int(right))
    if not values:
        if any(term in text for term in ("高分", "足够分数", "较高分")):
            return None, True
        return None, False
    rounded = {round(v, 6) for v in values}
    if len(rounded) > 1 or any(v < 0 or v > 1 for v in values):
        return None, True
    return values[0], False


def _requirement_shape(kind: MasteryRequirementType):
    dims: list[EvaluationDimension] = []
    types: list[EvidenceType] = []
    min_count = 1
    min_trust = TrustLevel.SYSTEM_OBSERVED
    min_score = 0.6
    transfer = kind == MasteryRequirementType.TRANSFER_PERFORMANCE
    retention = kind == MasteryRequirementType.RETENTION_RECHECK
    unassisted = kind in {MasteryRequirementType.INDEPENDENT_ATTEMPT, MasteryRequirementType.UNASSISTED_EXPLANATION}
    if kind == MasteryRequirementType.VERIFIED_ASSESSMENT:
        types = [EvidenceType.ASSESSMENT_RESULT]
        min_trust = TrustLevel.VERIFIED
    elif kind == MasteryRequirementType.TRANSFER_PERFORMANCE:
        dims = [EvaluationDimension.TRANSFER]
        types = [EvidenceType.ADVENTURE_TRANSFER, EvidenceType.TEACH_BACK, EvidenceType.ASSESSMENT_RESULT]
    elif kind == MasteryRequirementType.RETENTION_RECHECK:
        dims = [EvaluationDimension.RETENTION]
    elif kind == MasteryRequirementType.UNASSISTED_EXPLANATION:
        types = [EvidenceType.DIALOGUE_EXPLANATION, EvidenceType.TEACH_BACK, EvidenceType.ADVENTURE_EXPLANATION]
        dims = [EvaluationDimension.CONCEPTUAL_UNDERSTANDING]
    elif kind == MasteryRequirementType.CODE_TEST_PASS:
        types = [EvidenceType.CODE_EXECUTION]
        min_score = 0.8
    elif kind == MasteryRequirementType.ERROR_RECOVERY:
        dims = [EvaluationDimension.ERROR_RECOVERY]
    elif kind == MasteryRequirementType.MULTI_ACTIVITY_EVIDENCE:
        min_count = 2
    elif kind == MasteryRequirementType.MANUAL_REVIEW:
        types = [EvidenceType.MANUAL_TEACHER_EVIDENCE]
        min_trust = TrustLevel.STRUCTURED_HUMAN
    elif kind == MasteryRequirementType.INDEPENDENT_ATTEMPT:
        # Independence is an evidence authority property, not a dimension requirement.
        min_count = 2
    return dims, types, min_count, min_trust, transfer, retention, unassisted, min_score




def _unparsed_fragments(text: str, kinds: list[MasteryRequirementType]) -> list[str]:
    """Conservative finite-grammar residual detector.

    Known requirement phrases, numeric thresholds, connectors and harmless modal
    words are removed. Any remaining semantic text is unresolved rather than
    silently guessed.
    """
    residual = text
    for kind, words in TYPE_RULES:
        if kind in kinds:
            for word in sorted(words, key=len, reverse=True):
                residual = residual.replace(word, " ")
    residual = re.sub(r"(?:达到|至少|不低于)\s*[0-9]{1,3}(?:\.[0-9]+)?\s*%", " ", residual)
    residual = re.sub(r"(?:达到|至少|不低于)\s*(?:0(?:\.\d+)?|1(?:\.0+)?)\b", " ", residual)
    residual = re.sub(r"(?:达到|至少|不低于)?\s*[0-9]+\s*/\s*[0-9]+", " ", residual)
    residual = re.sub(r"至少\s*(?:[0-9]{1,2}|二十|十[一二三四五六七八九]?|[一二两三四五六七八九])\s*(?:次|个|种|条)", " ", residual)
    residual = re.sub(r"(?:[0-9]{1,2}|二十|十[一二三四五六七八九]?|[一二两三四五六七八九])\s*(?:种不同活动|个独立活动)", " ", residual)
    for token in ("必须", "需要", "要求", "完成", "通过", "达到", "至少", "不低于", "并且", "并", "和", "与", "且", "同时", "的", "证据", "任务", "表现", "结果", "一次", "次", "个", "种", "条"):
        residual = residual.replace(token, " ")
    residual = re.sub(r"[，,。；;：:、（）()\[\]{}\s]+", " ", residual).strip()
    return [residual] if residual else []

def parse_mastery_requirement(node: NodeEvaluationContract, index: int, text: str) -> MasteryRequirementParseResult:
    kinds: list[MasteryRequirementType] = []
    for kind, words in TYPE_RULES:
        if any(word in text for word in words):
            kinds.append(kind)
    count, count_ambiguous = _parse_count(text)
    score, score_ambiguous = _parse_score(text)
    residual_fragments = _unparsed_fragments(text, kinds) if kinds else [text]
    ambiguous = count_ambiguous or score_ambiguous or bool(residual_fragments)
    parsed: list[NormalizedMasteryRequirement] = []
    if not kinds or count_ambiguous or score_ambiguous:
        unresolved = NormalizedMasteryRequirement(
            requirement_id=_rid(node.node_id, index, MasteryRequirementType.UNRESOLVED, 0, text),
            source_requirement_index=index,
            source_text=text,
            requirement_type=MasteryRequirementType.UNRESOLVED,
            minimum_independent_count=0,
            minimum_trust_level=TrustLevel.INFERRED,
            minimum_score=0,
            resolved=False,
        )
        return MasteryRequirementParseResult(
            source_requirement_index=index,
            source_text=text,
            parsed_requirements=[unresolved],
            unparsed_fragments=[text],
            ambiguous=ambiguous or not kinds,
        )
    for ordinal, kind in enumerate(dict.fromkeys(kinds)):
        dims, types, min_count, min_trust, transfer, retention, unassisted, min_score = _requirement_shape(kind)
        if count is not None:
            if kind in {
                MasteryRequirementType.INDEPENDENT_ATTEMPT,
                MasteryRequirementType.MULTI_ACTIVITY_EVIDENCE,
                MasteryRequirementType.VERIFIED_ASSESSMENT,
                MasteryRequirementType.TRANSFER_PERFORMANCE,
                MasteryRequirementType.RETENTION_RECHECK,
                MasteryRequirementType.CODE_TEST_PASS,
                MasteryRequirementType.UNASSISTED_EXPLANATION,
            }:
                min_count = count
        if score is not None:
            min_score = score
        parsed.append(
            NormalizedMasteryRequirement(
                requirement_id=_rid(node.node_id, index, kind, ordinal, text),
                source_requirement_index=index,
                source_text=text,
                requirement_type=kind,
                required_dimensions=dims,
                accepted_evidence_types=types,
                minimum_independent_count=min_count,
                minimum_trust_level=min_trust,
                requires_transfer=transfer,
                requires_retention=retention,
                requires_unassisted=unassisted,
                minimum_score=min_score,
                resolved=True,
            )
        )
    if residual_fragments:
        parsed.append(
            NormalizedMasteryRequirement(
                requirement_id=_rid(node.node_id, index, MasteryRequirementType.UNRESOLVED, len(parsed), text),
                source_requirement_index=index,
                source_text=text,
                requirement_type=MasteryRequirementType.UNRESOLVED,
                minimum_independent_count=0,
                minimum_trust_level=TrustLevel.INFERRED,
                minimum_score=0,
                resolved=False,
            )
        )
    return MasteryRequirementParseResult(
        source_requirement_index=index,
        source_text=text,
        parsed_requirements=parsed,
        unparsed_fragments=residual_fragments,
        ambiguous=bool(residual_fragments),
    )


def normalize_mastery_requirements(node: NodeEvaluationContract) -> tuple[list[NormalizedMasteryRequirement], bool]:
    results = [parse_mastery_requirement(node, index, text) for index, text in enumerate(node.mastery_evidence_requirements)]
    requirements = [requirement for result in results for requirement in result.parsed_requirements]
    unresolved = any(result.ambiguous or result.unparsed_fragments or any(not r.resolved for r in result.parsed_requirements) for result in results)
    return requirements, unresolved


def assess_mastery_requirements(requirements: list[NormalizedMasteryRequirement], evidence: list[NormalizedEvidence], objectives: list[ObjectiveAssessment]) -> list[MasteryRequirementAssessment]:
    valid = [e for e in evidence if e.state == EvidenceState.VALID]
    out: list[MasteryRequirementAssessment] = []
    for requirement in requirements:
        matched: list[str] = []
        reason = "MASTERY_REQUIREMENT_UNSATISFIED"
        if not requirement.resolved:
            out.append(
                MasteryRequirementAssessment(
                    requirement_id=requirement.requirement_id,
                    requirement_type=requirement.requirement_type,
                    satisfied=False,
                    evidence_ids=[],
                    reason_code="UNRESOLVED_MASTERY_REQUIREMENT",
                )
            )
            continue
        if requirement.requirement_type == MasteryRequirementType.OBJECTIVE_COVERAGE:
            ok = bool(objectives) and all(o.achieved for o in objectives)
            reason = "OBJECTIVE_ACHIEVEMENT_VALID" if ok else reason
        else:
            candidates: list[NormalizedEvidence] = []
            for item in valid:
                if item.weak or item.effective_weight < 0.25 or item.normalized_score < requirement.minimum_score:
                    continue
                if requirement.accepted_evidence_types and item.evidence_type not in requirement.accepted_evidence_types:
                    continue
                if requirement.required_dimensions and not set(requirement.required_dimensions).intersection(item.dimensions):
                    continue
                if TRUST_RANK[item.trust_level] < TRUST_RANK[requirement.minimum_trust_level]:
                    continue
                if requirement.requires_unassisted and not item.independent:
                    continue
                if requirement.requires_transfer and not item.transfer:
                    continue
                if requirement.requires_retention and EvaluationDimension.RETENTION not in item.dimensions:
                    continue
                candidates.append(item)
            if requirement.requirement_type == MasteryRequirementType.MULTI_ACTIVITY_EVIDENCE:
                identities = {(e.evidence_family_key, e.canonical_activity_key) for e in candidates if e.independent}
            else:
                identities = {e.canonical_activity_key for e in candidates if e.independent}
            ok = len(identities) >= requirement.minimum_independent_count
            matched = [e.evidence_id for e in candidates]
            if ok:
                reason = "MASTERY_REQUIREMENT_SATISFIED"
        out.append(
            MasteryRequirementAssessment(
                requirement_id=requirement.requirement_id,
                requirement_type=requirement.requirement_type,
                satisfied=ok,
                evidence_ids=matched,
                reason_code=reason,
            )
        )
    return out


def validate_mastery_requirement_coverage(requirements: list[NormalizedMasteryRequirement], assessments: list[MasteryRequirementAssessment]):
    by_id = {a.requirement_id: a for a in assessments}
    issues: list[ValidationIssue] = []
    for requirement in requirements:
        assessment = by_id.get(requirement.requirement_id)
        if not requirement.resolved:
            issues.append(ValidationIssue(code=EvaluationIssueCode.UNRESOLVED_MASTERY_REQUIREMENT, severity=Severity.REVIEW_REQUIRED, message="mastery requirement could not be normalized"))
        elif assessment is None or not assessment.satisfied:
            issues.append(ValidationIssue(code=EvaluationIssueCode.MASTERY_REQUIREMENT_UNSATISFIED, severity=Severity.WARNING, message="mastery requirement not satisfied"))
    return ValidationReport.from_issues(issues)
