"""General goal-aware collaboration for the single DARYA conversation.

This planner does not execute tools or create a second profile.  It projects
directly stated user facts and goals into the existing thread perspective,
decides whether current external facts are needed, and gives the response
layer one bounded next-step contract.  Search and artifacts remain owned by
their existing authorities.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping


CAPABILITIES = {
    "NONE", "SEARCH", "HANDOUT", "PRACTICAL_GUIDE", "ASSESSMENT",
    "CODE_LAB", "PRESENTATION", "MINDMAP", "VIDEO", "STORYBOOK",
    "DEMONSTRATE",
}
PRIORITIES = {"SAFETY", "WELLBEING", "DECISION", "LEARNING", "CREATION", "GENERAL"}
RISK_LEVELS = {"NONE", "CONCERN", "URGENT"}


@dataclass(frozen=True)
class GoalCollaborationPlan:
    active: bool = False
    # V2 cognition owns this semantic state.  It is intentionally optional so
    # the older standalone planner remains backwards compatible.
    goal_lifecycle: str = "ACTIVE"
    goal_completion_reason: str = ""
    goal: str = ""
    goal_evidence_span: str = ""
    observed_user_facts: tuple[dict[str, str], ...] = ()
    constraints: tuple[str, ...] = ()
    missing_information: tuple[str, ...] = ()
    clarification_question: str | None = None
    external_research_needed: bool = False
    research_query: str = ""
    recommended_capability: str = "NONE"
    useful_first_move: str = ""
    priority: str = "GENERAL"
    risk_level: str = "NONE"
    protected_need: str = ""
    avoid_actions: tuple[str, ...] = ()
    support_options: tuple[str, ...] = ()
    decision_criteria: tuple[str, ...] = ()
    assistant_commitments: tuple[str, ...] = ()
    confidence: float = 0.0
    source: str = "safe_fallback"

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _prompt(
    message: str,
    *,
    recent_history: list[dict[str, str]],
    thread_context: Mapping[str, Any],
    user_context: Iterable[Mapping[str, Any]],
    memories: Iterable[Mapping[str, Any]],
) -> str:
    return f"""你是 DARYA 的目标协作规划器，只做结构化判断，不回答用户，不执行搜索或生成。

目标：面对任何领域的自然对话，判断 DARYA 是否应站在用户处境中主动推进，而不是只回答字面问题。不要按固定场景或关键词套模板。

通用原则：
1. 先判断处境，再处理字面任务。优先级是：即时人身安全/重大健康危险 > 正在发生的伤害、胁迫或严重心理压力 > 高后果决策 > 学习/创作任务。设置 priority 和 risk_level，写明 protected_need。
2. 识别用户是否处于年龄、权力、健康、经济或信息不对称中；识别谁可能是伤害或胁迫来源。avoid_actions 列出会让用户更危险、失去主体性或被推回风险源的做法；不得建议用户只向潜在伤害者求助。
3. support_options 给出与处境匹配的可靠支持路径，并保留用户选择权。高风险时 useful_first_move 必须先降低伤害，不得先优化成绩、产出或效率。
4. 只把用户直接说过的身份、学校/组织、阶段、位置、经验、偏好、限制等列为 observed_user_facts；每项 evidence_span 必须逐字来自当前消息。
5. 识别用户想达到、避免或决定的结果。普通寒暄、单纯事实问答或没有行动目标时 active=false。
6. 若目标依赖会变化、地域性、机构性或现实世界信息，external_research_needed=true，并形成包含用户已知处境的具体 research_query。若存在即时安全风险，不得为等待搜索而延迟安全建议。
7. missing_information 只列会实质改变建议的变量，最多 3 项。clarification_question 最多一个问题；高风险时该问题必须先确认即时安全。
8. decision_criteria 列出真正会改变选择或解法的比较维度，最多 4 项。不能写“综合考虑”“根据情况”等空词。
9. assistant_commitments 列出 DARYA 应替用户承担的认知劳动，最多 3 项，例如核对官方事实、交叉比较、根据偏好生成可保存材料。不得把“搜索、整理、列清单、分析利弊”全部布置给用户自己做。
10. useful_first_move 必须是 DARYA 本轮可直接交付的一个小成果或安全动作，不是“建议用户先列出/先搜索/先想清楚”。信息不足时也应先给临时决策框架或明确自己将核对的对象，再只问一个高价值问题。
11. recommended_capability 只能从现有能力选一个：SEARCH、HANDOUT、PRACTICAL_GUIDE、ASSESSMENT、CODE_LAB、PRESENTATION、MINDMAP、VIDEO、STORYBOOK、DEMONSTRATE、NONE。它是下一步建议，不代表已经执行。
12. 焦虑、犹豫或压力要转化成可验证的小步骤，不写空泛安慰；高后果决定必须保留不确定性。性别等身份只有在确实改变风险、机会或用户偏好时才能影响建议，禁止刻板推断。
13. 历史和记忆只用于连续性，不能把推断写成用户事实；已确认的用户偏好应影响 research_query、useful_first_move 和能力选择。
14. 不得因为目标领域不在已安装学习包中而放弃；可用线程级搜索建立依据，但不能声称形成永久知识包。
15. goal 若来自当前消息，goal_evidence_span 填当前原文；若是承接历史或线程上下文，可留空。每个 observed_user_fact 仍必须有当前消息的原文证据。

只输出一个 JSON 对象，字段完整：
{{"active":true,"goal":"简洁目标","goal_evidence_span":"当前消息原文","observed_user_facts":[{{"fact":"事实","evidence_span":"当前消息原文"}}],"constraints":["限制"],"missing_information":["关键缺口"],"clarification_question":"至多一个自然问题或 null","external_research_needed":true,"research_query":"具体检索式","recommended_capability":"SEARCH|HANDOUT|PRACTICAL_GUIDE|ASSESSMENT|CODE_LAB|PRESENTATION|MINDMAP|VIDEO|STORYBOOK|DEMONSTRATE|NONE","useful_first_move":"DARYA 本轮直接交付的小成果或安全动作","priority":"SAFETY|WELLBEING|DECISION|LEARNING|CREATION|GENERAL","risk_level":"NONE|CONCERN|URGENT","protected_need":"当前最应优先保护的需要","avoid_actions":["不应建议的行动"],"support_options":["可靠支持路径"],"decision_criteria":["会改变选择的比较维度"],"assistant_commitments":["DARYA 应替用户承担的工作"],"confidence":0.0}}

当前消息：{json.dumps(message, ensure_ascii=False)}
最近对话：{json.dumps(recent_history[-6:], ensure_ascii=False)}
线程共同视角：{json.dumps(dict(thread_context), ensure_ascii=False)}
已确认用户上下文：{json.dumps(list(user_context)[:6], ensure_ascii=False)}
相关长期记忆：{json.dumps(list(memories)[:6], ensure_ascii=False)}
"""


def _parse(raw: Any, message: str) -> GoalCollaborationPlan | None:
    if isinstance(raw, Mapping) and "content" in raw:
        raw = raw["content"]
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(value, Mapping):
        return None
    required = {
        "active", "goal", "goal_evidence_span", "observed_user_facts",
        "constraints", "missing_information", "clarification_question",
        "external_research_needed", "research_query", "recommended_capability",
        "useful_first_move", "priority", "risk_level", "protected_need",
        "avoid_actions", "support_options", "decision_criteria",
        "assistant_commitments", "confidence",
    }
    if set(value) != required or value.get("active") not in {True, False}:
        return None
    capability = str(value.get("recommended_capability") or "NONE").upper()
    if capability not in CAPABILITIES:
        return None
    priority = str(value.get("priority") or "GENERAL").upper()
    risk_level = str(value.get("risk_level") or "NONE").upper()
    if priority not in PRIORITIES or risk_level not in RISK_LEVELS:
        return None
    goal = str(value.get("goal") or "").strip()[:300]
    goal_span = str(value.get("goal_evidence_span") or "").strip()[:240]
    if goal_span and _compact(goal_span) not in _compact(message):
        return None
    facts: list[dict[str, str]] = []
    for row in list(value.get("observed_user_facts") or [])[:8]:
        if not isinstance(row, Mapping):
            return None
        fact = str(row.get("fact") or "").strip()[:240]
        span = str(row.get("evidence_span") or "").strip()[:240]
        if not fact or not span or _compact(span) not in _compact(message):
            return None
        facts.append({"fact": fact, "evidence_span": span})
    try:
        confidence = max(0.0, min(1.0, float(value.get("confidence"))))
    except (TypeError, ValueError):
        return None
    question = str(value.get("clarification_question") or "").strip()[:240] or None
    if question:
        question = re.sub(r"[?？]+", "", question).rstrip("。.!！") + "？"
    external = value.get("external_research_needed") is True
    query = str(value.get("research_query") or "").strip()[:220]
    if external and not query:
        return None
    active = value.get("active") is True and bool(goal or facts)
    return GoalCollaborationPlan(
        active=active,
        goal=goal if active else "",
        goal_evidence_span=goal_span if active else "",
        observed_user_facts=tuple(facts) if active else (),
        constraints=tuple(str(item).strip()[:160] for item in list(value.get("constraints") or [])[:6] if str(item).strip()) if active else (),
        missing_information=tuple(str(item).strip()[:120] for item in list(value.get("missing_information") or [])[:3] if str(item).strip()) if active else (),
        clarification_question=question if active else None,
        external_research_needed=external if active else False,
        research_query=query if active and external else "",
        recommended_capability=capability if active else "NONE",
        useful_first_move=str(value.get("useful_first_move") or "").strip()[:500] if active else "",
        priority=priority if active else "GENERAL",
        risk_level=risk_level if active else "NONE",
        protected_need=str(value.get("protected_need") or "").strip()[:300] if active else "",
        avoid_actions=tuple(str(item).strip()[:240] for item in list(value.get("avoid_actions") or [])[:4] if str(item).strip()) if active else (),
        support_options=tuple(str(item).strip()[:240] for item in list(value.get("support_options") or [])[:4] if str(item).strip()) if active else (),
        decision_criteria=tuple(str(item).strip()[:180] for item in list(value.get("decision_criteria") or [])[:4] if str(item).strip()) if active else (),
        assistant_commitments=tuple(str(item).strip()[:240] for item in list(value.get("assistant_commitments") or [])[:3] if str(item).strip()) if active else (),
        confidence=confidence,
        source="structured_model",
    )


def _fallback(message: str) -> GoalCollaborationPlan:
    text = re.sub(r"\s+", " ", str(message or "")).strip()
    goal_signal = re.search(
        r"我想|我要|我准备|我打算|我的目标|希望|计划|需要|担心|焦虑|怎么才能|"
        r"i want|i need|my goal|i plan|i hope|i'm preparing",
        text,
        re.I,
    )
    if not goal_signal:
        return GoalCollaborationPlan()
    external = bool(re.search(
        r"学校|公司|岗位|就业|招聘|录取|考研|申请|政策|比赛|考试|医院|地区|当地|价格|市场|"
        r"school|company|job|admission|policy|competition|local|market|price",
        text,
        re.I,
    ))
    return GoalCollaborationPlan(
        active=True,
        goal=text[:300],
        goal_evidence_span=text[:240],
        external_research_needed=external,
        research_query=text[:220] if external else "",
        recommended_capability="SEARCH" if external else "PRACTICAL_GUIDE",
        useful_first_move="我先用现有信息搭一个可修改的比较框架，标出已知、未知和最值得验证的一项。",
        priority="GENERAL",
        risk_level="NONE",
        decision_criteria=("目标匹配度", "现实约束", "验证成本"),
        assistant_commitments=(
            "先根据现有信息建立可修改的比较框架",
            "核对会改变决定的外部事实并标明依据",
        ),
        confidence=.55,
        source="safe_fallback",
    )


async def plan_goal_collaboration(
    message: str,
    *,
    recent_history: Iterable[Mapping[str, Any]] = (),
    thread_context: Mapping[str, Any] | None = None,
    user_context: Iterable[Mapping[str, Any]] = (),
    memories: Iterable[Mapping[str, Any]] = (),
    llm: Any | None = None,
) -> GoalCollaborationPlan:
    history = [
        {"role": str(row.get("role") or ""), "content": str(row.get("content") or "")[:800]}
        for row in list(recent_history)[-6:]
        if isinstance(row, Mapping)
    ]
    if llm is None:
        try:
            from services.llm_service_real import get_llm_service
            llm = await get_llm_service()
        except Exception:
            llm = None
    if llm is not None:
        try:
            raw = await llm.chat(
                messages=[{"role": "user", "content": _prompt(
                    message,
                    recent_history=history,
                    thread_context=thread_context or {},
                    user_context=user_context,
                    memories=memories,
                )}],
                temperature=0.0,
                max_tokens=950,
                response_format={"type": "json_object"},
                use_cache=False,
                write_cache=False,
            )
            parsed = _parse(raw, message)
            if parsed is not None:
                return parsed
        except Exception:
            pass
    return _fallback(message)


def build_goal_collaboration_directive(plan: GoalCollaborationPlan) -> str:
    if not plan.active:
        return ""
    return (
        "DARYA 目标协作契约（只用于本轮，不展示字段或内部推理）：\n"
        + json.dumps(plan.public_dict(), ensure_ascii=False)
        + "\n站在用户已明确提供的处境中回答。把 useful_first_move 做成用户此刻就能使用的小成果，而不是原样复述成一句命令；若本轮已完成检索，再结合证据给针对性判断。"
        "missing_information 只用于一个最有价值的自然追问，不能连环盘问，也不能因缺信息而把整轮退化成追问。"
        "必须遵守 priority、risk_level、protected_need 和 avoid_actions；风险高于任务时先降低风险，不得把用户推回潜在伤害源。support_options 应作为可选的支持路径而不是命令。"
        "使用 decision_criteria 给出临时判断框架；明确说明 assistant_commitments 中哪些分析、核对或材料工作由 DARYA 承担，不能把这些认知劳动重新布置给用户。"
        "明确说明推荐的具体下一步和为什么适合这个用户；焦虑场景用低负担、可回退的试跑降低不确定性，不写通用安慰，也不因性别做无依据推断。"
        "recommended_capability 只是可调用能力建议；除非本轮已有真实 action/artifact 回执，不得声称已经生成产物。"
    )
