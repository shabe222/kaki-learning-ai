from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import Domain, KnowledgePlan, PlannerRequest, QuestionKind


@dataclass(frozen=True)
class AcademicFinding:
    code: str
    message: str


class AcademicVerifier(Protocol):
    def verify(self, plan: KnowledgePlan, request: PlannerRequest) -> list[AcademicFinding]: ...


class DefaultAcademicVerifier:
    """Dependency-free checks plus an extension point for stronger domain validators.

    This verifier deliberately does not pretend to solve arbitrary mathematics or execute
    arbitrary code. Integrations may wrap it with a symbolic-math, test-runner, or
    source-grounding verifier; any returned finding blocks the model output and triggers
    the normal one-repair-then-fallback path.
    """

    def verify(self, plan: KnowledgePlan, request: PlannerRequest) -> list[AcademicFinding]:
        findings: list[AcademicFinding] = []
        if plan.misconceptions[0].mistaken_claim.strip() == plan.misconceptions[0].correction.strip():
            findings.append(AcademicFinding("misconception_not_corrected", "误区陈述与纠正内容相同"))

        questions = [
            plan.challenges.understanding_trial,
            plan.challenges.practice_trial,
            plan.challenges.remedy,
            plan.challenges.understanding_interleave,
            plan.challenges.practice_interleave,
            *(stage.task for stage in plan.challenges.boss),
        ]
        for index, question in enumerate(questions):
            if question.kind == QuestionKind.MULTIPLE_CHOICE and question.options:
                correct = question.options[question.correct_option or 0].strip()
                if not correct:
                    findings.append(AcademicFinding("empty_correct_option", f"第 {index + 1} 题正确选项为空"))
            if question.remediation and len(question.remediation.third_attempt_full_solution) <= len(question.hint):
                findings.append(
                    AcademicFinding(
                        "full_solution_not_explanatory",
                        f"第 {index + 1} 题第三次完整解法没有比最小提示提供更多解释",
                    )
                )

        if plan.domain == Domain.PROGRAMMING:
            text = " ".join(question.verification_method or "" for question in questions).lower()
            if not any(token in text for token in ("测试", "断言", "输入", "输出", "test", "assert", "output")):
                findings.append(AcademicFinding("programming_not_testable", "编程计划缺少可执行测试或输入输出依据"))
        elif plan.domain == Domain.MATH:
            text = " ".join(question.verification_method or "" for question in questions)
            if not any(token in text for token in ("推导", "计算", "代入", "公式", "图", "反例", "证明", "方向导数")):
                findings.append(AcademicFinding("math_not_verifiable", "数学计划缺少推导、计算、图形或反例验证方式"))
        elif plan.domain == Domain.LANGUAGE:
            text = " ".join(question.answer_boundary or "" for question in questions)
            if not any(token in text for token in ("语义", "语气", "自然", "语境", "措辞")):
                findings.append(AcademicFinding("language_boundary_missing", "语言计划缺少语义、语气或语境边界"))
        elif plan.domain == Domain.OPEN:
            text = " ".join(question.answer_boundary or "" for question in questions)
            if not any(token in text for token in ("主张", "理由", "证据", "边界", "反例", "逻辑")):
                findings.append(AcademicFinding("open_rubric_missing", "开放问题缺少论点、证据或边界评分依据"))
        return findings


class CompositeAcademicVerifier:
    def __init__(self, *verifiers: AcademicVerifier):
        self.verifiers = verifiers or (DefaultAcademicVerifier(),)

    def verify(self, plan: KnowledgePlan, request: PlannerRequest) -> list[AcademicFinding]:
        findings: list[AcademicFinding] = []
        for verifier in self.verifiers:
            findings.extend(verifier.verify(plan, request))
        return findings
