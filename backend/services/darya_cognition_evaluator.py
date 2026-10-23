"""Bounded post-response checks for DARYA's cognition contract.

This evaluator is telemetry and control-plane only. It never exposes hidden
reasoning and never replaces a useful answer with a generic fallback. It makes
the common failure modes inspectable: losing the user's goal, surfacing an
internal downgrade, skipping an urgent safety response, or claiming a
capability result without an execution receipt.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

from services.darya_cognition_frame import CognitionFrame


_DOWNGRADE_MARKER = re.compile(
    r"(?:internal fallback|degrad(?:e|ed|ation)|unable to (?:call|generate|create)|"
    r"unsupported capability|capability (?:is )?not available|"
    r"我做不到|无法(?:调用|生成|创建|制作)|不能(?:调用|生成|创建|制作)|"
    r"不支持(?:这项|该项|当前)?(?:能力|功能)|内部降级|能力有限|"
    r"(?:生成|制作).{0,8}(?:失败|没有成功落地)|"
    r"前面.{0,16}(?:没有成功|失败)|不再走.{0,12}(?:流程|卡片))",
    re.I,
)
_SAFETY_MARKER = re.compile(
    r"(?:safe|safety|trusted|adult|teacher|guardian|emergency|"
    r"安全|求助|老师|监护|紧急|可靠)",
    re.I,
)
_COMPLETION_MARKER = re.compile(
    r"(?:done|completed|next|step|deliver|created|saved|"
    r"完成|已生成|下一步|还需|可保存)",
    re.I,
)


def _public_execution(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "public_dict"):
        result = value.public_dict()
        return result if isinstance(result, Mapping) else {}
    return {}


def evaluate_cognition_result(
    frame: CognitionFrame,
    response: str,
    *,
    action_execution: Any | None = None,
    evidence_bundle: Any | None = None,
) -> dict[str, Any]:
    """Return safe, inspectable checks without rewriting the response."""
    text = str(response or "").strip()
    execution = _public_execution(action_execution)
    checks: list[dict[str, Any]] = []
    critical: list[str] = []
    warnings: list[str] = []

    if frame.risk_level == "URGENT":
        safety_ok = bool(_SAFETY_MARKER.search(text))
        checks.append({"name": "urgent_safety", "passed": safety_ok})
        if not safety_ok:
            critical.append("urgent_safety_missing")
    else:
        checks.append({"name": "urgent_safety", "passed": True, "skipped": True})

    visible_downgrade = bool(_DOWNGRADE_MARKER.search(text))
    checks.append({"name": "visible_internal_downgrade", "passed": not visible_downgrade})
    if visible_downgrade and frame.selected_capability not in {"CONVERSATION_ONLY", "NONE"}:
        warnings.append("visible_internal_downgrade")

    capability_receipt = bool(execution) or frame.selected_capability in {"CONVERSATION_ONLY", "NONE"}
    checks.append({"name": "capability_receipt", "passed": capability_receipt})
    if not capability_receipt:
        warnings.append("capability_without_execution_receipt")

    completion_ok = bool(_COMPLETION_MARKER.search(text)) or len(text) >= 120
    checks.append({"name": "completion_signal", "passed": completion_ok})
    if not completion_ok:
        warnings.append("weak_completion_signal")

    if frame.search_required:
        evidence = _public_execution(evidence_bundle)
        evidence_status = str(evidence.get("status") or frame.evidence_status or "").upper()
        evidence_boundary_ok = evidence_status in {"UNVERIFIED", "NOT_REQUIRED", "VERIFIED", "SUPPORTED", "CALIBRATED_FALLBACK", ""} or bool(evidence)
        checks.append({"name": "evidence_boundary", "passed": evidence_boundary_ok, "status": evidence_status or "UNKNOWN"})
        if not evidence_boundary_ok:
            warnings.append("evidence_boundary_unclear")

    status = "FAIL" if critical else ("WARN" if warnings else "PASS")
    return {
        "contract_version": frame.contract_version,
        "status": status,
        "critical": critical,
        "warnings": warnings,
        "checks": checks,
        "selected_capability": frame.selected_capability,
        "next_action": frame.next_action,
        "response_length": len(text),
    }


def needs_cognition_repair(frame: CognitionFrame, response: str) -> bool:
    """Detect a goal response that is fluent but has not actually done the work."""
    text = str(response or "").strip()
    goal = str(frame.goal or "")
    plan_goal = bool(re.search(
        r"(?:计划|规划|选择|怎么开始|从哪里开始|做出来|生成|可保存|想红|有人看|涨粉|"
        r"怎么教|课程|视频|UP主|内容创作|plan|choose|start)",
        goal,
        re.I,
    ))
    if not plan_goal:
        return False
    timeboxed = len(re.findall(r"(?:第\s*\d+\s*[天周步阶段]|前\s*\d+\s*周|今天|明天|本周|下周|week\s*\d|day\s*\d)", text, re.I))
    completion = len(re.findall(r"(?:完成信号|完成标准|验收|记录|产出|测出|检验|复盘|调整|可回退)", text, re.I))
    unsupported_certainty = bool(re.search(
        r"(?:首选|次选|别碰|最大(?:的)?杠杆|最值钱的一张牌|否则.*白复习|"
        r"需求量很大|远比你想象的多|招.{0,8}大户|对口度极高|最接近|高度匹配|"
        r"竞争相对激烈|提分最快|岗位不少|门槛较高|"
        r"完播和涨粉会自然|稳定规律|多数时候是|爆款公式|一定会涨粉)",
        text,
    ))
    imposed_unknown_capacity = bool(re.search(
        r"(?:每天|每日|每周).{0,6}(?:1\.5|2|3|4|5|6|7|8|9|10)(?:小时|h)",
        text,
        re.I,
    )) and not re.search(r"(?:用户|你).{0,12}(?:说|提供|确认).{0,10}(?:每天|每日|每周)", text)
    visible_process_failure = bool(_DOWNGRADE_MARKER.search(text))
    return (
        len(text) < 60
        or timeboxed < 3
        or completion < 2
        or unsupported_certainty
        or imposed_unknown_capacity
        or visible_process_failure
    )


__all__ = ["evaluate_cognition_result", "needs_cognition_repair"]
