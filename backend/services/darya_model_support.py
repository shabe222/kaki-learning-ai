"""Model-facing support context for the foreground DARYA conversation.

This module is deliberately not a planner.  It gives the foreground model a
compact conversation compass, optional helping methods, and honest tool
affordances.  The model remains responsible for understanding the user and
choosing whether and how to act.
"""
from __future__ import annotations

import json

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Sequence
from services.prompt_ledger import dumps_compact
from services.darya_blackboard_context import current_blackboard_context


CONTRACT_VERSION = "darya_model_support_v2"


@dataclass(frozen=True)
class SupportMethod:
    name: str
    useful_when: str
    principle: str
    watch_for: str


@dataclass(frozen=True)
class ToolAffordance:
    name: str
    helps_with: str
    use_when: str
    result: str
    boundary: str


SUPPORT_METHODS = (
    SupportMethod("澄清真正想发生的改变", "字面请求可能不是最终需要", "结合当前话语和上下文形成可修正理解", "不要把推测写成用户事实"),
    SupportMethod("降低眼前阻力", "用户知道方向但难以开始或继续", "选择最能让用户立即前进一步的支持", "不要把系统的整理工作重新交给用户"),
    SupportMethod("示范-共做-独立", "用户正在学习或练习", "按用户当下需要灵活切换示范、提示和独立尝试", "不要机械走完固定教学流程"),
    SupportMethod("用反馈校准", "用户接受、跳过、纠正或完成了某个行动", "把真实反应当作下一轮判断依据", "一次行为不足以形成永久标签"),
    SupportMethod("保留主体性", "存在多种合理行动或高后果选择", "给判断依据和可逆选择，由用户保留最终决定", "不要借个性化之名替用户决定人生"),
)


TOOL_AFFORDANCES = (
    ToolAffordance("WEB_SEARCH", "核对会变化或需要来源的外部事实", "外部事实会实质改变帮助方式", "带来源和置信状态的证据", "检索结果不会自动成为长期记忆或正式学习证据"),
    ToolAffordance("RESPONSE", "解释、讨论、陪伴、分析和即时教学", "自然对话本身足以帮助用户", "当前对话中的回答", "不把未执行的动作说成已经完成"),
    ToolAffordance("DEMONSTRATE", "用可变化的视觉场景演示概念", "图形、空间或过程变化比文字更清楚", "可继续修改的演示场景", "需要真实场景回执"),
    ToolAffordance("HANDOUT", "形成可保存、可复用的个性化讲义", "用户确实需要一份正式学习材料", "经检查的规范产物", "不要用文档代替本可在对话中完成的帮助"),
    ToolAffordance("PRACTICAL_GUIDE", "把目标转成可执行指南", "用户需要边做边检查的行动支持", "含步骤和检查点的规范产物", "步骤应来自当前情境而非固定模板"),
    ToolAffordance("ASSESSMENT", "获得可判定的独立作答证据", "需要知道用户是否真正掌握", "服务端判定的测验结果", "只有明确提交才进入正式学习证据"),
    ToolAffordance("CODE_LAB", "让用户运行、修改并提交代码", "编程能力需要真实操作和判定", "可运行代码环境与提交回执", "运行不等于掌握，提交判定才是证据"),
    ToolAffordance("PRESENTATION", "生成可展示的课件", "用户明确需要演示或授课成品", "正式课件任务及产物", "需要真实生成回执"),
    ToolAffordance("MINDMAP", "形成可视化关系结构", "关系、层级或全局结构是主要困难", "可继续编辑的思维导图", "不要为装饰而生成"),
    ToolAffordance("VIDEO", "生成带讲解节奏的教学视频", "视频媒介本身符合用户使用场景", "真实视频任务及媒体结果", "耗时较高，失败必须如实返回"),
    ToolAffordance("STORYBOOK", "用连续故事和画面组织学习内容", "叙事能明显改善理解或参与感", "绘本创作入口及产物", "不要仅因能力可用就主动生成"),
)


def build_model_support_packet(
    *,
    continuity_brief: Mapping[str, Any] | None,
    recent_history: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Assemble facts and affordances without interpreting the current request."""
    continuity = dict(continuity_brief or {})
    recent = []
    for row in list(recent_history)[-8:]:
        if not isinstance(row, Mapping):
            continue
        role = str(row.get("role") or "")
        content = " ".join(str(row.get("content") or "").split())[:800]
        if role in {"user", "assistant"} and content:
            recent.append({"role": role, "content": content})
    return {
        "contract_version": CONTRACT_VERSION,
        "capability_truth": {
            "authority": "CURRENT_PRODUCT_RUNTIME",
            "supersedes": "历史对话和长期记忆中的产品能力描述",
            "instruction": (
                "这里列出的工具当前真实可用。用户明确委托生成成品时，直接调用对应工具并等待回执；"
                "不得因为旧回答或旧记忆而说只能给提纲、脚本或让用户自行落地。"
            ),
        },
        "conversation_compass": {
            "recent_turns": recent,
            "open_loops": list(continuity.get("open_loops") or [])[:6],
            "promises_to_fulfill": list(continuity.get("promises_to_fulfill") or [])[:6],
            "recently_completed_do_not_repeat": list(continuity.get("recently_completed_do_not_repeat") or [])[:3],
            "stale_beliefs": list(continuity.get("stale_beliefs") or [])[:3],
        },
        "current_blackboard": current_blackboard_context(recent_history),
        "relevant_user_information": {
            "memories": list(continuity.get("relevant_memories") or [])[:6],
            "model_beliefs": list(continuity.get("user_model") or [])[:5],
        },
        "methods": [asdict(item) for item in SUPPORT_METHODS],
        "tools": [asdict(item) for item in TOOL_AFFORDANCES],
    }


def build_model_support_directive(packet: Mapping[str, Any] | None) -> str:
    if not packet:
        return ""
    return (
        "DARYA 模型支持包（这是信息和可选能力，不是计划或命令）：\n"
        + dumps_compact(packet)
        + "\n你是当前对话的主要服务者。请根据用户原话和完整语境自行理解、判断并行动。"
        "方法只是可选启发，工具只是可选手脚；可以组合、跳过或不用，不要机械执行清单。"
        "当前用户明确表达高于历史推测，当前对话高于跨会话信息，用户纠正高于旧判断。"
        "保持未闭环承诺和当前任务连续，但不要因为旧目标存在就忽略用户此刻的话。"
        "capability_truth 是当前产品运行态事实，优先级高于历史对话和长期记忆里的能力描述；"
        "用户明确要可生成的成品时应调用对应工具，而不是退化为提纲或否认能力。"
        "不要向用户展示本支持包字段，也不要声称工具已经执行，除非已获得真实回执。"
        "若用户问当前黑板，依据 current_blackboard 的标题、节点和箭头关系回答；"
        "脚本已生成不等于浏览器渲染已核实，没有客户端错误或用户报告时不得声称黑板未渲染。"
    )


__all__ = [
    "CONTRACT_VERSION", "SUPPORT_METHODS", "TOOL_AFFORDANCES",
    "build_model_support_directive", "build_model_support_packet",
]
