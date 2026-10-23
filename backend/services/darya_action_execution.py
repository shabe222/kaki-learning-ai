"""Phase 3.0 action execution bridge for DARYA Current Perspective.

Policy selection is not execution.  This module records the selected action,
maps it to a callable contract, and guarantees a useful reversible result for
PREPARE before the conversation asks the user for more information.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Literal


PerspectiveAction = Literal["ANSWER", "PREPARE", "SEARCH", "CLARIFY", "WARN"]
ExecutionStatus = Literal["READY", "COMPLETED", "DOWNGRADED"]


@dataclass
class ActionExecution:
    selected_action: PerspectiveAction
    executed_action: PerspectiveAction
    capability: str
    contract: str | None
    status: ExecutionStatus
    downgrade_reason: str | None
    result_requirement: str
    prepared_result: str | None = None
    artifact_action_id: int | None = None

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _available(capabilities: dict[str, Any], name: str) -> tuple[bool, str | None]:
    row = capabilities.get(name) or {}
    contract = row.get("contract")
    return bool(row.get("available") and contract), contract


def _prepare_v0(state: dict[str, Any], artifact_action: dict[str, Any] | None) -> str:
    explicit = str(((state.get("explicit_user_need") or {}).get("value") or "")).strip()
    bottleneck = str(((state.get("primary_bottleneck") or {}).get("value") or "")).strip()
    work_object = state.get("current_work_object") or {}
    artifact = work_object.get("artifact") or {}
    title = artifact.get("title") or work_object.get("title") or "当前内容"
    goal_plan = state.get("goal_collaboration") or {}

    if goal_plan.get("active") and not artifact_action:
        goal = str(goal_plan.get("goal") or explicit or "当前目标").strip()
        first_move = str(goal_plan.get("useful_first_move") or state.get("recommended_next_move") or "先推进最小可验证的一步").strip()
        missing = [str(item) for item in list(goal_plan.get("missing_information") or [])[:3] if str(item).strip()]
        capability = str(goal_plan.get("recommended_capability") or "NONE")
        return (
            f"我先按你现在的情况整理一个第一版（可修改）：\n"
            f"1. 目标：{goal}\n"
            f"2. 先做：{first_move}\n"
            f"3. 还需确认：{'、'.join(missing) if missing else '先用现有信息执行，再根据结果修正'}\n"
            f"4. 下一种合适支持：{capability if capability != 'NONE' else '继续在当前对话中细化'}"
        )

    if artifact_action:
        proposal = artifact_action.get("proposal") or {}
        page = proposal.get("page_number")
        reason = artifact_action.get("reason") or bottleneck or "先处理最影响理解的一处"
        target = f"第 {page} 页" if page else str(title)
        return (
            f"我先对「{target}」做一个可回退的第一版：\n"
            f"1. 判断：{reason}\n"
            "2. 处理：保留原版，只改当前最影响理解的页面，不扩大修改范围。\n"
            "3. 验收：读者能在数秒内说出这页的唯一重点，且没有新增未核实事实。"
        )

    presentation = bool(re.search(r"PPT|幻灯片|演示|slide|presentation", explicit, re.I)) or (
        str(artifact.get("artifact_kind") or "").lower() == "presentation"
    )
    if presentation:
        diagnosis = bottleneck or "信息主次还没有立起来，读者不知道先看哪里"
        return (
            "我先给你一版可修改的演示稿：\n"
            f"1. 先定一个判断：{diagnosis}\n"
            "2. 首页只留“对谁、解决什么、为什么现在值得看”，其他功能下移。\n"
            "3. 中段改成“问题 → 做法 → 可见证据”，每页只承担一个结论。\n"
            "4. 先不补新数字；把未核实结果标成占位，避免把示例写成事实。"
        )

    next_move = str(state.get("recommended_next_move") or "先做一个最小可验证版本").strip()
    return (
        "我先把它落成一个可修改的初稿：\n"
        f"1. 当前主判断：{bottleneck or explicit or '先找到最影响结果的一处'}\n"
        f"2. 最小动作：{next_move}\n"
        "3. 验收方式：只检查这个改动是否让下一步更清楚，不同时扩大范围。"
    )


def plan_action_execution(
    state: dict[str, Any],
    *,
    artifact_action: dict[str, Any] | None = None,
) -> ActionExecution:
    selected: PerspectiveAction = state.get("action") or "ANSWER"
    capabilities = state.get("capabilities") or {}

    if selected == "SEARCH":
        ok, contract = _available(capabilities, "web_search")
        if ok:
            return ActionExecution(selected, "SEARCH", "web_search", contract, "READY", None, "EVIDENCE_BUNDLE")
        return ActionExecution(selected, "CLARIFY", "conversation", "single_material_question", "DOWNGRADED", "web_search unavailable", "ONE_QUESTION")

    if selected == "PREPARE":
        artifact_ok, artifact_contract = _available(capabilities, "artifact_edit")
        has_artifact = bool((state.get("current_work_object") or {}).get("artifact"))
        if artifact_ok and has_artifact and artifact_action:
            return ActionExecution(
                selected, "PREPARE", "artifact_edit", artifact_contract, "READY", None,
                "ARTIFACT_PROPOSAL_AND_V0", _prepare_v0(state, artifact_action), artifact_action.get("id"),
            )
        response_ok, response_contract = _available(capabilities, "response_generation")
        if response_ok:
            return ActionExecution(
                selected, "PREPARE", "response_generation", response_contract, "READY", None,
                "USER_VISIBLE_V0", _prepare_v0(state, None),
            )
        return ActionExecution(selected, "CLARIFY", "conversation", "single_material_question", "DOWNGRADED", "no callable preparation contract", "ONE_QUESTION")

    requirement = {
        "ANSWER": "DIRECT_ANSWER",
        "CLARIFY": "ONE_MATERIAL_QUESTION",
        "WARN": "RISK_AND_SAFE_NEXT_MOVE",
    }[selected]
    return ActionExecution(selected, selected, "conversation", "tide_response_contract", "READY", None, requirement)


def build_action_execution_directive(execution: ActionExecution) -> str:
    return (
        "DARYA 动作执行契约（不向用户暴露字段名）：\n"
        + json.dumps(execution.public_dict(), ensure_ascii=False)
        + "\nselected_action 是策略选择，executed_action 是本轮真正可执行的动作。"
        "若 executed_action=PREPARE，必须先给出 prepared_result 所要求的有用 V0，再允许邀请用户纠偏；不得只追问。"
        "不得声称调用了 capability/contract 之外的工具。"
    )


def enforce_action_execution_response(raw: str, execution: ActionExecution) -> tuple[str, dict[str, Any]]:
    result = str(raw or "")
    useful = bool(result.strip())
    if execution.executed_action == "PREPARE":
        # Keep a substantive provider answer. Replace only empty/question-only
        # or visibly degraded output so PREPARE never collapses to an interview.
        v0 = execution.prepared_result or "我先给你一个可修改的初稿：先收窄到一个主判断，再验证最小改动。"
        visible = re.sub(r"\[/?tide_(?:center|sidecar)[^\]]*\]", "", result, flags=re.I).strip()
        provider_useful = (
            len(visible) >= 48
            and not re.fullmatch(r"[^?？]{0,260}[?？]", visible, re.S)
            and not re.search(r"(?:我做不到|无法生成|能力有限|只能给你|暂时不能)", visible)
        )
        if not provider_useful:
            result = (
                "[tide_center]\n" + v0 + "\n\n"
                "这版先让事情往前走；你只要指出最不对的一处，我就在这版初稿上继续改。\n"
                "[/tide_center]"
            )
            useful = len(v0) >= 80 and bool(re.search(r"(?:^|\n)[1-4][.\u3001]", v0))
    execution.status = "COMPLETED" if execution.status != "DOWNGRADED" else "DOWNGRADED"
    telemetry = execution.public_dict() | {
        "result_status": execution.status,
        "useful_result_present": useful,
    }
    return result, telemetry
