# -*- coding: utf-8 -*-
"""拾光漫游 · 游戏智能体答题判定（judge）

独立实现判定逻辑：拒绝"含主题词即判对"，答错时组装教学反馈。
"""
from __future__ import annotations

from typing import Any


def _normalise(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split())


def _group_hit(group: list[str], normal: str) -> bool:
    """长词组按 2 字滑动片段匹配，短词整词匹配。"""
    for term in group:
        key = _normalise(term)
        if not key:
            continue
        if key in normal:
            return True
        if len(key) >= 5:
            for start in range(0, len(key) - 1):
                if key[start:start + 2] in normal:
                    return True
    return False


def evaluate(answer: Any, task: dict[str, Any]) -> tuple[bool, float]:
    """判定一次作答是否构成有效学习证据。"""
    normal = _normalise(answer)
    if task.get("type") == "multiple_choice":
        options = list(task.get("options") or [])
        expected = options[int(task.get("correct_option", 0))] if options else ""
        correct = normal in {_normalise(expected), _normalise(task.get("correct_option"))}
        return correct, 1.0 if correct else 0.0
    groups = list(task.get("accepted_groups") or [])
    if len(normal) < 4:
        return False, 0.0
    if not groups:
        return True, 1.0
    matched = sum(1 for group in groups if _group_hit(group, normal))
    if len(groups) == 1:
        is_echo = any(normal == _normalise(term) for term in groups[0])
        if matched < 1 or is_echo:
            return False, 0.0
        return True, 1.0
    threshold = min(2, len(groups))
    return matched >= threshold, matched / max(1, len(groups))


def teaching_feedback(current_task: dict[str, Any], content: dict[str, Any], message: str, attempt: int = 1) -> dict[str, Any]:
    """组装教学化答错反馈：三问阶梯，越错教得越透。

    attempt=1  -> 给方向（wrong_guidance）
    attempt=2  -> 拆步骤讲解（scaffold / second_attempt）
    attempt>=3 -> 完整答案 + 知识点（answer_explanation + knowledge + correction）
    """
    concept = content.get("concept") or {}
    misconception = content.get("misconception") or {}
    hint = current_task.get("hint") or ""
    wrong_guidance = current_task.get("wrong_guidance") or ""
    rationale = current_task.get("rationale") or ""
    explanation = current_task.get("answer_explanation") or ""
    second_attempt = current_task.get("second_attempt") or ""
    scaffold = list(current_task.get("scaffold") or [])
    knowledge = concept.get("explanation") or ""
    correction = misconception.get("correction") or ""

    ladder = min(max(int(attempt or 1), 1), 3)
    guidance = hint or wrong_guidance
    if ladder >= 2 and wrong_guidance:
        guidance = wrong_guidance
    if ladder >= 2 and second_attempt:
        guidance = second_attempt
    steps = scaffold if ladder >= 2 else []

    return {
        "kind": "needs_review",
        "message": message,
        "wrong_guidance": guidance,
        "rationale": rationale,
        "explanation": explanation if ladder >= 3 else "",
        "second_attempt": second_attempt if ladder >= 2 else "",
        "scaffold": steps,
        "knowledge": knowledge if ladder >= 3 else "",
        "correction": correction if ladder >= 3 else "",
        "ladder": ladder,
    }
