"""Model-primary cognition orchestration for one DARYA turn.

V2 makes the help decision before search and artifact routing.  It builds a
read-only account world-model projection, a research task, a validated action
graph and one work brief.  Execution remains owned by the existing capability
authorities; planner nodes are never marked completed without their receipts.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable, Mapping, Sequence
from services.prompt_ledger import dumps_compact
from services.darya_blackboard_context import blackboard_context_from_action, current_blackboard_context


CONTRACT_VERSION = "darya_cognition_orchestration_v2"
CLAIM_KINDS = {"IDENTITY", "SITUATION", "GOAL", "CONSTRAINT", "PREFERENCE", "RESULT"}
EPISTEMIC_STATUSES = {"OBSERVED", "CONFIRMED", "INFERRED"}
NODE_KINDS = {"UNDERSTAND", "RESEARCH", "ANALYZE", "CREATE", "DELIVER", "PRACTICE", "FOLLOW_UP"}
PLANNER_STATES = {"PLANNED", "BLOCKED"}
CAPABILITIES = {
    "NONE", "WEB_SEARCH", "ANALYZE", "RESPONSE", "HANDOUT", "PRACTICAL_GUIDE",
    "ASSESSMENT", "CODE_LAB", "PRESENTATION", "MINDMAP", "VIDEO", "STORYBOOK",
    "DEMONSTRATE", "FOLLOW_UP",
}
LOW_RISK_PROACTIVE_CAPABILITIES = {"HANDOUT", "PRACTICAL_GUIDE"}
PLANNER_TIMEOUT_SECONDS = 35.0
ARTIFACT_CAPABILITIES = {
    "HANDOUT", "PRACTICAL_GUIDE", "ASSESSMENT", "CODE_LAB", "PRESENTATION",
    "MINDMAP", "VIDEO", "STORYBOOK", "DEMONSTRATE",
}
# Width of the fragment used to recognise a brief fact that restates a claim the
# model declared excluded. Long enough to be distinctive, short enough to survive
# light paraphrasing such as 2027届毕业 vs 2027届学生.
_EXCLUSION_SHINGLE_WIDTH = 6
INTERACTION_MODES = {"EXECUTE", "ORIENT", "EXPLAIN", "CLARIFY"}
GOAL_LIFECYCLES = {"ACTIVE", "PAUSED", "COMPLETED", "BACKGROUND"}
_SENSITIVE = re.compile(
    r"身份证|精确住址|家庭住址|宗教|政治倾向|政治立场|种族|民族身份|性取向|"
    r"性别认同|病史|诊断|犯罪|前科|银行卡|密码|手机号|\b(?:HIV|AIDS)\b",
    re.I,
)
logger = logging.getLogger(__name__)


def _plain(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "public_dict"):
        candidate = value.public_dict()
        if isinstance(candidate, Mapping):
            return candidate
    if hasattr(value, "evidence"):
        candidate = value.evidence()
        if isinstance(candidate, Mapping):
            return candidate
    return {}


def _unique(values: Iterable[str], *, limit: int, item_limit: int) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = _plain(value, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return tuple(result)


@dataclass(frozen=True)
class WorldModelClaim:
    kind: str
    value: str
    epistemic_status: str
    confidence: float
    freshness: str
    source_type: str
    source_ref: str
    scope: str
    serving_state: str = "ACTIVE"


@dataclass(frozen=True)
class UserWorldModel:
    claims: tuple[WorldModelClaim, ...] = ()
    current_situation: str = ""
    goals: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    preferences: tuple[str, ...] = ()
    recent_results: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResearchQuestion:
    claim: str
    query: str
    why_it_changes_the_plan: str
    expected_source_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResearchTask:
    needed: bool = False
    questions: tuple[ResearchQuestion, ...] = ()
    source_strategy: tuple[str, ...] = ()
    budget: int = 0
    stop_condition: str = ""
    fallback: str = ""


@dataclass(frozen=True)
class HelpOpportunity:
    true_goal: str
    main_obstacle: str
    highest_value_unknown: str
    why_it_changes_the_plan: str
    public_research_available: bool
    private_input_needed: str | None
    best_immediate_deliverable: str
    success_signal: str


@dataclass(frozen=True)
class ActionNode:
    node_id: str
    kind: str
    objective: str
    capability: str
    depends_on: tuple[str, ...]
    state: str
    required_input: str
    completion_signal: str
    fallback: str


@dataclass(frozen=True)
class WorkBrief:
    user: str
    goal: str
    stage: str
    confirmed_facts: tuple[str, ...]
    observed_facts: tuple[str, ...]
    unknowns: tuple[str, ...]
    strategy: str
    deliverables: tuple[str, ...]
    resource_purposes: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    success_checks: tuple[str, ...]


@dataclass(frozen=True)
class InteractionSupport:
    """Model-selected presentation support, not a keyword-driven UI route."""

    mode: str = "ORIENT"
    sidecar: bool = False
    sidecar_type: str | None = None
    sidecar_title: str | None = None
    urgency: str = "normal"
    risk_level: str = "NONE"
    risk: str = ""
    reason: str = "model_selected"


@dataclass(frozen=True)
class CognitionOrchestrationV2:
    contract_version: str
    source: str
    confidence: float
    world_model: UserWorldModel
    help_opportunity: HelpOpportunity
    research_task: ResearchTask
    action_graph: tuple[ActionNode, ...]
    work_brief: WorkBrief
    recommended_capability: str
    turn_mode: str = "TASK"
    goal_lifecycle: str = "ACTIVE"
    goal_completion_reason: str = ""
    interaction: InteractionSupport = InteractionSupport()
    # Account claims the model itself declared excluded for this turn, validated
    # against the claims it was given. Empty means nothing was excluded.
    excluded_account_context: tuple[str, ...] = ()
    # Things worth carrying to the next conversation. The model may propose
    # them, but the ledger checks each one: a quote from the learner's own
    # words, nothing sensitive or identifying, and OBSERVED rather than stated.
    memories_to_keep: tuple[dict[str, Any], ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return asdict(self)

    def primary_research_query(self) -> str:
        return self.research_task.questions[0].query if self.research_task.questions else ""

    def goal_plan_payload(self) -> dict[str, Any]:
        observed = [
            {"fact": claim.value, "evidence_span": claim.source_ref}
            for claim in self.world_model.claims
            if claim.epistemic_status == "OBSERVED" and claim.source_type == "CURRENT_MESSAGE"
        ]
        return {
            "active": self.turn_mode in {"TASK", "LEARNING"} and self.goal_lifecycle == "ACTIVE" and bool(self.help_opportunity.true_goal),
            "goal_lifecycle": self.goal_lifecycle,
            "goal_completion_reason": self.goal_completion_reason,
            "goal": self.help_opportunity.true_goal,
            "goal_evidence_span": self.help_opportunity.true_goal,
            "observed_user_facts": observed[:8],
            "constraints": list(self.world_model.constraints[:8]),
            "missing_information": [self.help_opportunity.highest_value_unknown]
            if self.help_opportunity.highest_value_unknown else [],
            "clarification_question": self.help_opportunity.private_input_needed,
            "external_research_needed": self.research_task.needed,
            "research_query": self.primary_research_query(),
            "recommended_capability": self.recommended_capability,
            "useful_first_move": self.help_opportunity.best_immediate_deliverable,
            "priority": "GENERAL",
            "risk_level": "NONE",
            "protected_need": "",
            "avoid_actions": [],
            "support_options": [],
            "decision_criteria": [self.help_opportunity.why_it_changes_the_plan]
            if self.help_opportunity.why_it_changes_the_plan else [],
            "assistant_commitments": [
                "主动核对可公开获得且会改变方案的信息",
                "先交付现有信息足以支持的可用成果",
            ],
            "confidence": self.confidence,
            "source": self.source,
        }


@dataclass(frozen=True)
class CapabilityContract:
    capability: str
    real_output: str
    required_inputs: tuple[str, ...]
    recoverable_inputs: tuple[str, ...]
    authority: str
    receipt_type: str
    persistence: str
    fallback: str


def capability_registry() -> dict[str, CapabilityContract]:
    """Queryable execution contracts; availability is not an execution claim."""
    rows = (
        CapabilityContract("WEB_SEARCH", "reviewed evidence bundle", ("research query",), ("world model", "work brief"), "tide_search_evidence", "EvidenceBundle", "assistant metadata", "calibrated unverified answer"),
        CapabilityContract("HANDOUT", "reviewed canonical handout", ("work brief",), ("account context", "conversation"), "conversation_capability_actions", "GeneratedItemVersion", "GeneratedItem", "complete first draft"),
        CapabilityContract("PRACTICAL_GUIDE", "reviewed practical guide", ("work brief",), ("account context", "conversation"), "conversation_capability_actions", "GeneratedItemVersion", "GeneratedItem", "complete first draft"),
        CapabilityContract("ASSESSMENT", "server-judged assessment", ("learning state", "work brief"), ("active route",), "tiered_assessments", "GeneratedItemVersion", "GeneratedItem", "practice in conversation"),
        CapabilityContract("CODE_LAB", "reviewed runnable code lab", ("learning state", "work brief"), ("active route",), "conversation_capability_actions", "GeneratedItemVersion", "GeneratedItem", "bounded code task in conversation"),
        CapabilityContract("PRESENTATION", "presentation job", ("work brief",), ("conversation",), "conversation_capability_actions", "presentation action receipt", "workspace runtime", "slide-ready outline"),
        CapabilityContract("MINDMAP", "mindmap job", ("work brief",), ("conversation",), "conversation_capability_actions", "mindmap action receipt", "workspace runtime", "structured hierarchy"),
        CapabilityContract("VIDEO", "video generation job", ("work brief",), ("conversation",), "conversation_capability_actions", "video action receipt", "workspace runtime", "shoot-ready script"),
        CapabilityContract("STORYBOOK", "storybook creator entry", ("work brief",), ("conversation",), "conversation_capability_actions", "storybook action receipt", "workspace runtime", "page-by-page brief"),
        CapabilityContract("DEMONSTRATE", "blackboard the user watched being written", ("scene intent",), ("conversation", "active route"), "blackboard", "scene revision", "scene authority", "text explanation"),
        CapabilityContract("RESPONSE", "visible answer", ("work brief",), ("all selected context",), "chat_quick", "assistant message", "ChatSession", "minimal useful answer"),
        CapabilityContract("ANALYZE", "decision synthesis", ("work brief",), ("world model", "evidence"), "chat_quick", "assistant message metadata", "ChatSession", "conditional analysis"),
        CapabilityContract("FOLLOW_UP", "next-turn continuation", ("unfinished action graph",), ("assistant metadata",), "chat_quick", "assistant message", "ChatSession", "resume from recent history"),
    )
    return {row.capability: row for row in rows}


def _claim_from_memory(item: Mapping[str, Any]) -> WorldModelClaim | None:
    value = _plain(item.get("content"), 500)
    if not value or _SENSITIVE.search(value) or str(item.get("state") or "ACTIVE") != "ACTIVE":
        return None
    status = str(item.get("epistemic_status") or "CONFIRMED").upper()
    if status not in EPISTEMIC_STATUSES:
        status = "CONFIRMED" if status in {"USER_CONFIRMED", "USER_PROVIDED"} else "INFERRED"
    topic = str(item.get("topic") or "")
    kind = "PREFERENCE" if "preference" in topic or item.get("type") == "PREFERENCE" else "IDENTITY"
    if "goal" in topic:
        kind = "GOAL"
    if topic.startswith("observed:constraint:"):
        kind = "CONSTRAINT"
    return WorldModelClaim(
        kind=kind,
        value=value,
        epistemic_status=status,
        confidence=max(0.0, min(1.0, float(item.get("confidence") or 0.0))),
        freshness=str(item.get("freshness") or "STABLE"),
        source_type="LONG_TERM_MEMORY",
        source_ref=str(item.get("id") or item.get("source_message_id") or ""),
        scope="ACCOUNT",
    )


def _claim_from_context(item: Mapping[str, Any]) -> WorldModelClaim | None:
    value = _plain(item.get("value"), 500)
    if not value or _SENSITIVE.search(value):
        return None
    raw_kind = str(item.get("kind") or "").lower()
    kind = {
        "goal": "GOAL", "constraint": "CONSTRAINT", "preference": "PREFERENCE",
        "current_situation": "SITUATION", "result": "RESULT",
    }.get(raw_kind, "IDENTITY")
    status = "CONFIRMED" if str(item.get("status") or "").upper() in {"CONFIRMED", "USER_CONFIRMED"} else "OBSERVED"
    return WorldModelClaim(
        kind=kind,
        value=value,
        epistemic_status=status,
        confidence=max(0.0, min(1.0, float(item.get("confidence") or 0.0))),
        freshness="STABLE",
        source_type="USER_CONTEXT",
        source_ref=str(item.get("id") or ""),
        scope=str(item.get("scope") or "THREAD").upper(),
    )


def _claim_from_impression(item: Mapping[str, Any]) -> WorldModelClaim | None:
    value = _plain(item.get("summary"), 240)
    if not value or _SENSITIVE.search(value) or str(item.get("status") or "active") in {"rejected", "forgotten"}:
        return None
    return WorldModelClaim(
        kind="PREFERENCE",
        value=value,
        epistemic_status="CONFIRMED" if item.get("confirmed_by_user") is True else "INFERRED",
        confidence=max(0.0, min(1.0, float(item.get("confidence") or 0.0))),
        freshness="RECENT",
        source_type="AI_IMPRESSION",
        source_ref=str(item.get("id") or item.get("impression_key") or ""),
        scope="ACCOUNT",
    )


def build_world_model(
    *,
    memories: Iterable[Mapping[str, Any]] = (),
    user_context: Iterable[Mapping[str, Any]] = (),
    impressions: Iterable[Mapping[str, Any]] = (),
    observed_candidates: Iterable[Mapping[str, Any]] = (),
    current_message: str = "",
) -> UserWorldModel:
    claims: list[WorldModelClaim] = []
    for item in memories:
        claim = _claim_from_memory(item) if isinstance(item, Mapping) else None
        if claim:
            claims.append(claim)
    for item in user_context:
        claim = _claim_from_context(item) if isinstance(item, Mapping) else None
        if claim:
            claims.append(claim)
    for item in impressions:
        claim = _claim_from_impression(item) if isinstance(item, Mapping) else None
        if claim:
            claims.append(claim)
    compact_message = "".join(str(current_message or "").split())
    for item in observed_candidates:
        if not isinstance(item, Mapping):
            continue
        value = _plain(item.get("value"), 300)
        span = _plain(item.get("evidence_span"), 300)
        kind = str(item.get("kind") or "SITUATION").upper()
        if kind not in CLAIM_KINDS or not value or not span or _SENSITIVE.search(value):
            continue
        if "".join(span.split()) not in compact_message:
            continue
        claims.append(WorldModelClaim(
            kind=kind,
            value=value,
            epistemic_status="OBSERVED",
            confidence=max(0.0, min(1.0, float(item.get("confidence") or 0.8))),
            freshness=str(item.get("freshness") or "CURRENT"),
            source_type="CURRENT_MESSAGE",
            source_ref=span,
            scope="TURN",
        ))
    deduped: list[WorldModelClaim] = []
    for claim in claims:
        if not any(existing.kind == claim.kind and existing.value == claim.value for existing in deduped):
            deduped.append(claim)
    return _world_model_from_claims(deduped, current_message=current_message)


def _world_model_from_claims(
    claims: Sequence[WorldModelClaim], *, current_message: str,
) -> UserWorldModel:
    return UserWorldModel(
        claims=tuple(claims[:32]),
        current_situation=_plain(current_message, 500),
        goals=_unique((c.value for c in claims if c.kind == "GOAL"), limit=8, item_limit=300),
        constraints=_unique((c.value for c in claims if c.kind == "CONSTRAINT"), limit=8, item_limit=240),
        preferences=_unique((c.value for c in claims if c.kind == "PREFERENCE"), limit=8, item_limit=240),
        recent_results=_unique((c.value for c in claims if c.kind == "RESULT"), limit=8, item_limit=300),
        conflicts=(),
    )


def _json_object(raw: Any) -> Mapping[str, Any] | None:
    if isinstance(raw, Mapping) and "content" in raw:
        raw = raw["content"]
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        value = json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def compact_capability_table() -> str:
    """Render the capability contracts as one line each.

    Serialising them as nested JSON repeats every field name 13 times and
    repeats each capability's own name as its first value, which together were
    half of this prompt. The field order is stated once in the legend the
    caller prints above this table, so nothing is lost.
    """
    lines = []
    for name, contract in capability_registry().items():
        lines.append(
            f"- {name}：产出 {contract.real_output}；"
            f"必需 {', '.join(contract.required_inputs) or '无'}；"
            f"可替代 {', '.join(contract.recoverable_inputs) or '无'}；"
            f"权威 {contract.authority}；回执 {contract.receipt_type}；"
            f"落库 {contract.persistence}；兜底 {contract.fallback}"
        )
    return "\n".join(lines)


def _prompt(
    message: str,
    *,
    recent_history: Sequence[Mapping[str, Any]],
    world_model: UserWorldModel,
    continuity_brief: Mapping[str, Any] | None = None,
    current_blackboard: Mapping[str, Any] | None = None,
) -> str:
    public_world = asdict(world_model)
    return f"""你是 DARYA 的 Cognition Orchestration V2。你先决定怎样真正帮助用户，再规划搜索和能力；不要回答用户，不输出隐藏推理。

目标：恢复用户处境，找出最大障碍和最高信息价值未知，自动研究公开信息，最多询问一个必须由用户提供的私人信息，同时先交付一版可用成果。

规则：
0. 先确定 turn_mode：CONVERSATION（闲聊/情绪/插话），LEARNING（当前主动学习或作答），TASK（委托办事），PAUSED（暂停），STOPPED（用户要求结束）。同时输出 goal_lifecycle：ACTIVE（正在推进）、PAUSED（暂缓）、BACKGROUND（闲聊，不改变此前目标）、COMPLETED（当前目标的约定交付已完成或用户明确确认结束）。这是同一编排决定，不是新增分类流程。STOPPED只是停止，不代表学会；只有独立作答的真实判定能支持掌握。复述已展示答案或“我会了”不能支持掌握。稳定数学知识不需要时效搜索。普通身份/长期目标与本轮“出两题”等临时请求必须区分，后者不要提取为账号GOAL。
1. 不得按“考研/考公/UP主”等身份套模板。只使用提供的证据。
2. `world_model_candidates` 只允许当前消息逐字支持的普通事实；`evidence_span` 必须是当前消息原文。敏感信息不得输出。
3. 搜索要拆成会改变方案的具体事实，每项给独立 query 和期望来源；最多 3 项。
4. actions 是可选的 0-8 个行动建议。只有确实需要外部工具或后续动作时才填写；不要为了满足结构而编造步骤。
5. capability 必须来自 {sorted(CAPABILITIES)}。是否使用能力由你根据完整语境判断，不使用工具也是有效决定。
6. 不得声称任何动作已执行。完成必须等待外部真实回执。
7. 所有资源必须服务同一个策略。公开信息能查则不问用户；私人问题至多一个，且不能阻止先交付第一版成果。
8. “可用的第一版”必须包含已经替用户完成的具体正文、脚本、比较或练习；不得用多个待填空字段、空白表格或通用模板冒充成果。缺少私人选择时，选择一个低风险、可替换的工作假设并明确标注，再把唯一最高价值问题留到最后。给交付物命名时用“第一版（可修改）”，不要写 V0。
9. turn_mode=LEARNING 时先在对话中完成教学、提问或示范。若你判断一份可保存讲义或实操指南会显著提升本人的理解、连贯性或巩固，可主动安排一个低风险 HANDOUT 或 PRACTICAL_GUIDE 的 CREATE 节点，并说明资源目的；不要无依据地自动生成高成本视频、PPT 或正式测验。需要摸底时，先给简短诊断题；只有用户明确需要或同意时才安排正式测验。
10. 不得声称用户已算出、已提交或已完成某道题或某件事，除非有该题/该事的真实服务端回执；对话里提出但用户未提交作答的练习，必须如实说明还没有做。
11. interaction 由你根据完整对话选择。Sidecar 只在独立工作对象、证据或较深结构确实能帮助用户继续工作时启用；简单回应、等待用户、只核对推理时不要启用，也不要复制 Center 内容。
12. 能力表是当前运行态的真实手脚，不是展示清单。用户明确委托生成某种现有成品时，turn_mode=TASK，recommended_capability 必须选择对应能力，并安排一个相同 capability 的 CREATE 节点；不得只交付提纲、脚本或教程，也不得声称系统不能生成。只有用户明确拒绝生成、仅询问能力，或请求不在能力表中时才可以不调用。
13. 生成稳定知识主题的课件、讲义、思维导图或视频，通常不需要外部搜索；主题名称本身不是检索理由。只有用户要求最新事实、指定来源、引用依据，或某个会变化的外部事实会实质改变成品时，research_task.needed 才为 true。生成动作不能被非必要检索取代。
13b. questions[].query 的写法直接决定检索能不能用，按实测结论写：
   - 年份和数字要和它修饰的词连写成一个词：写「2025年个体工商户免税政策」，不要写「2025 个体工商户 免税政策」。
     2026-09-23 实测：后者被搜索引擎切成「2025 年_百度百科」和「电子 科技大学」，整轮 0 来源；
     前者取回了国家税务总局的官方通知。
   - 一个查询只问一件事；主体用完整术语（「电子税务局」「定期定额户」「小规模纳税人」），
     不要把主体拆成会被切碎的短词。
   - 查询词不是用户的整句话：不要把他的年龄、地段、流水塞进去，只写「要核对的那件事 + 它的正式名称」。
14. 账号世界模型中的历史信息只是候选，不代表本轮采用。用户说“不要使用某背景”时，提到该背景是在排除它，不是要求使用它。只有确实会改变本轮产物且未被排除的事实才能逐字列入 work_brief 的 confirmed_facts/observed_facts；未列入的历史信息不进入本轮回答或资料。被用户排除的账号信息必须逐字填入 excluded_account_context（取值必须与下方账号候选世界模型中的 value 逐字一致，不得改写、不得编造），它在任何情况下都不得写入 work_brief 的任何字段。excluded_account_context 为空表示本轮没有排除任何账号信息。
15. DEMONSTRATE 是黑板：DARYA 会在板上一边说一边写、一边画。用户要求新画、修改、强调板书，或空间关系需要新图解时选它。用户只问现有黑板上的内容或箭头时，先依据当前黑板摘要解释；不必为解释而重画。当前黑板摘要只证明板书脚本已生成，不证明浏览器渲染成功；没有用户报告或客户端回执，不得声称黑板未渲染。不要把另一话题的图画进当前板书。
16. memories_to_keep 是留到以后用的东西，不是本轮任务：用户自己的偏好、他明确说过的稳定事实、你这次真正摸清的"他怎么才学得会"、以及他给过的长期要求。evidence_span 必须逐字来自当前消息，否则这一条会被丢掉。敏感信息（健康、宗教、政治、取向）和身份类信息（证件号、电话、邮箱、住址）一律不要写。没有值得留的就写 []，不要为填而填。

只输出 JSON（单行紧凑格式：不要缩进、不要换行、不要注释，空值就写空字符串/空数组）。字段很多，缩进会成倍放大输出长度，而长度直接等于这一轮用户等待的时间：
{{
 "turn_mode":"CONVERSATION|LEARNING|TASK|PAUSED|STOPPED", "goal_lifecycle":"ACTIVE|PAUSED|COMPLETED|BACKGROUND", "goal_completion_reason":"", "goal":"", "situation":"", "confidence":0.0,
 "world_model_candidates":[{{"kind":"IDENTITY|SITUATION|GOAL|CONSTRAINT|PREFERENCE|RESULT","value":"","evidence_span":"","confidence":0.0,"freshness":"CURRENT|STABLE|TIME_BOUND"}}],
 "help_opportunity":{{"true_goal":"","main_obstacle":"","highest_value_unknown":"","why_it_changes_the_plan":"","public_research_available":false,"private_input_needed":null,"best_immediate_deliverable":"","success_signal":""}},
 "research_task":{{"needed":false,"questions":[{{"claim":"","query":"","why_it_changes_the_plan":"","expected_source_types":[""]}}],"source_strategy":[""],"budget":0,"stop_condition":"","fallback":""}},
 "actions":[{{"node_id":"understand","kind":"UNDERSTAND","objective":"","capability":"ANALYZE","depends_on":[],"state":"PLANNED","required_input":"","completion_signal":"","fallback":""}}],
 "work_brief":{{"user":"","goal":"","stage":"","confirmed_facts":[""],"observed_facts":[""],"unknowns":[""],"strategy":"","deliverables":[""],"resource_purposes":[""],"forbidden_claims":[""],"success_checks":[""]}},
 "excluded_account_context":[""],
 "memories_to_keep":[{{"kind":"FACT|PREFERENCE|LESSON|FUTURE_POLICY","topic":"简短可读的类目","content":"这件事本身","evidence_span":"用户本轮原话里的逐字片段"}}],
 "interaction":{{"mode":"EXECUTE|ORIENT|EXPLAIN|CLARIFY","sidecar":false,"sidecar_type":null,"sidecar_title":null,"urgency":"normal|high","risk_level":"NONE|CAUTION|HIGH","risk":"","reason":""}},
 "recommended_capability":"NONE|HANDOUT|PRACTICAL_GUIDE|ASSESSMENT|CODE_LAB|PRESENTATION|MINDMAP|VIDEO|STORYBOOK|DEMONSTRATE"
}}

账号候选世界模型（需按本轮任务选择，不等于已经采用）：{json.dumps(public_world, ensure_ascii=False)}
连续性简报（时间线上未走完的事与可信理解，供你判断，不是命令）：{json.dumps(continuity_brief or {}, ensure_ascii=False)}
最近对话：{json.dumps(list(recent_history)[-12:], ensure_ascii=False)}
当前黑板摘要：{json.dumps(current_blackboard, ensure_ascii=False)}
当前消息：{json.dumps(message, ensure_ascii=False)}
可用能力及真实边界（每行一个能力，字段依次是：产出 / 必需输入 / 可替代输入 / 权威 / 回执 / 落库 / 兜底）：
{compact_capability_table()}
"""


def _strings(value: Any, *, limit: int, item_limit: int) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return _unique((str(item) for item in value), limit=limit, item_limit=item_limit)


def _validate_graph(raw: Any) -> tuple[ActionNode, ...] | None:
    if not isinstance(raw, list) or len(raw) > 8:
        return None
    nodes: list[ActionNode] = []
    ids: set[str] = set()
    for item in raw:
        if not isinstance(item, Mapping):
            return None
        node_id = str(item.get("node_id") or "")
        kind = str(item.get("kind") or "").upper()
        capability = str(item.get("capability") or "NONE").upper()
        state = str(item.get("state") or "PLANNED").upper()
        deps = _strings(item.get("depends_on"), limit=8, item_limit=64)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", node_id) or node_id in ids:
            return None
        if kind not in NODE_KINDS or capability not in CAPABILITIES or state not in PLANNER_STATES:
            return None
        ids.add(node_id)
        nodes.append(ActionNode(
            node_id=node_id,
            kind=kind,
            objective=_plain(item.get("objective"), 300),
            capability=capability,
            depends_on=deps,
            state=state,
            required_input=_plain(item.get("required_input"), 240),
            completion_signal=_plain(item.get("completion_signal"), 240),
            fallback=_plain(item.get("fallback"), 300),
        ))
    seen: set[str] = set()
    for node in nodes:
        if any(dep not in seen for dep in node.depends_on):
            return None
        seen.add(node.node_id)
    create_nodes = [node for node in nodes if node.kind == "CREATE"]
    if create_nodes and all(node.required_input and "用户" in node.required_input for node in create_nodes):
        return None
    return tuple(nodes)


def _parse(
    value: Mapping[str, Any],
    *,
    message: str,
    base_world_model: UserWorldModel,
) -> CognitionOrchestrationV2 | None:
    help_raw = value.get("help_opportunity")
    research_raw = value.get("research_task")
    brief_raw = value.get("work_brief")
    if not all(isinstance(item, Mapping) for item in (help_raw, research_raw, brief_raw)):
        return None
    # The public work brief is useful even when a model expresses a graph in a
    # shape our executor cannot safely run.  Do not discard the model's
    # understanding and fall back to a keyword route; keep an empty graph so
    # no capability can execute without a separately valid receipt-bound node.
    validated_graph = _validate_graph(value.get("actions"))
    graph = validated_graph or ()
    capability = str(value.get("recommended_capability") or "NONE").upper()
    turn_mode = str(value.get("turn_mode") or "TASK").upper()
    if turn_mode not in {"CONVERSATION", "LEARNING", "TASK", "PAUSED", "STOPPED"}:
        return None
    if turn_mode in {"CONVERSATION", "PAUSED", "STOPPED"}:
        capability = "NONE"
        graph = tuple(node for node in graph if node.kind in {"UNDERSTAND", "DELIVER"})
        graph = tuple(replace(node, depends_on=()) for node in graph)
    default_lifecycle = "ACTIVE" if turn_mode in {"TASK", "LEARNING"} else ("PAUSED" if turn_mode == "PAUSED" else "BACKGROUND")
    lifecycle = str(value.get("goal_lifecycle") or default_lifecycle).upper()
    if lifecycle not in GOAL_LIFECYCLES:
        return None
    if turn_mode in {"CONVERSATION", "PAUSED", "STOPPED"} and lifecycle == "ACTIVE":
        lifecycle = default_lifecycle
    if lifecycle == "COMPLETED":
        capability = "NONE"
        graph = tuple(node for node in graph if node.kind in {"UNDERSTAND", "DELIVER"})
        graph = tuple(replace(node, depends_on=()) for node in graph)
    # A presentation label such as RESPONSE/ANALYZE is not an executable
    # artifact request.  It remains visible in the work brief but cannot
    # acquire an artifact authority by accident.
    if capability not in ARTIFACT_CAPABILITIES | {"NONE"}:
        capability = "NONE"
    if validated_graph is None:
        capability = "NONE"
    candidates = value.get("world_model_candidates") if isinstance(value.get("world_model_candidates"), list) else []
    candidate_world = build_world_model(
        observed_candidates=candidates,
        current_message=message,
    )
    merged_claims = list(base_world_model.claims)
    for claim in candidate_world.claims:
        if not any(existing.kind == claim.kind and existing.value == claim.value for existing in merged_claims):
            merged_claims.append(claim)
    world_model = _world_model_from_claims(merged_claims, current_message=message)
    private_question = _plain(help_raw.get("private_input_needed"), 240) or None
    help_opportunity = HelpOpportunity(
        true_goal=_plain(help_raw.get("true_goal") or value.get("goal") or message, 300),
        main_obstacle=_plain(help_raw.get("main_obstacle"), 300),
        highest_value_unknown=_plain(help_raw.get("highest_value_unknown"), 240),
        why_it_changes_the_plan=_plain(help_raw.get("why_it_changes_the_plan"), 300),
        public_research_available=help_raw.get("public_research_available") is True,
        private_input_needed=private_question,
        best_immediate_deliverable=_plain(help_raw.get("best_immediate_deliverable"), 500),
        success_signal=_plain(help_raw.get("success_signal"), 300),
    )
    questions: list[ResearchQuestion] = []
    for item in list(research_raw.get("questions") or [])[:3]:
        if not isinstance(item, Mapping):
            continue
        query = _plain(item.get("query"), 220)
        claim = _plain(item.get("claim"), 240)
        if not query or not claim or _SENSITIVE.search(query):
            continue
        questions.append(ResearchQuestion(
            claim=claim,
            query=query,
            why_it_changes_the_plan=_plain(item.get("why_it_changes_the_plan"), 300),
            expected_source_types=_strings(item.get("expected_source_types"), limit=4, item_limit=80),
        ))
    needed = turn_mode in {"TASK", "LEARNING"} and research_raw.get("needed") is True and bool(questions)
    research_task = ResearchTask(
        needed=needed,
        questions=tuple(questions) if needed else (),
        source_strategy=_strings(research_raw.get("source_strategy"), limit=5, item_limit=120),
        budget=max(0, min(8, int(research_raw.get("budget") or 0))),
        stop_condition=_plain(research_raw.get("stop_condition"), 300),
        fallback=_plain(research_raw.get("fallback"), 300),
    )
    brief = WorkBrief(
        user=_plain(brief_raw.get("user"), 300),
        goal=_plain(brief_raw.get("goal") or help_opportunity.true_goal, 300),
        stage=_plain(brief_raw.get("stage"), 160),
        confirmed_facts=_strings(brief_raw.get("confirmed_facts"), limit=12, item_limit=300),
        observed_facts=_strings(brief_raw.get("observed_facts"), limit=12, item_limit=300),
        unknowns=_strings(brief_raw.get("unknowns"), limit=8, item_limit=240),
        strategy=_plain(brief_raw.get("strategy"), 500),
        deliverables=_strings(brief_raw.get("deliverables"), limit=6, item_limit=240),
        resource_purposes=_strings(brief_raw.get("resource_purposes"), limit=6, item_limit=300),
        forbidden_claims=_strings(brief_raw.get("forbidden_claims"), limit=8, item_limit=240),
        success_checks=_strings(brief_raw.get("success_checks"), limit=8, item_limit=240),
    )
    interaction_raw = value.get("interaction")
    if isinstance(interaction_raw, Mapping):
        interaction_mode = str(interaction_raw.get("mode") or "ORIENT").upper()
        if interaction_mode not in INTERACTION_MODES:
            return None
        sidecar = interaction_raw.get("sidecar") is True
        sidecar_type = _plain(interaction_raw.get("sidecar_type"), 80) or None
        sidecar_title = _plain(interaction_raw.get("sidecar_title"), 80) or None
        interaction = InteractionSupport(
            mode=interaction_mode,
            sidecar=sidecar,
            sidecar_type=sidecar_type if sidecar else None,
            sidecar_title=sidecar_title if sidecar else None,
            urgency="high" if str(interaction_raw.get("urgency") or "").lower() == "high" else "normal",
            risk_level=(
                str(interaction_raw.get("risk_level") or "NONE").upper()
                if str(interaction_raw.get("risk_level") or "NONE").upper() in {"NONE", "CAUTION", "HIGH"}
                else "NONE"
            ),
            risk=_plain(interaction_raw.get("risk"), 240),
            reason=_plain(interaction_raw.get("reason"), 160) or "model_selected",
        )
    else:
        # Backward-compatible neutral projection for older model/test payloads.
        # It deliberately does not infer presentation from words in the message.
        interaction = InteractionSupport()
    try:
        confidence = max(0.0, min(1.0, float(value.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        return None
    return CognitionOrchestrationV2(
        contract_version=CONTRACT_VERSION,
        source="structured_model",
        confidence=confidence,
        world_model=world_model,
        help_opportunity=help_opportunity,
        research_task=research_task,
        action_graph=graph,
        work_brief=brief,
        recommended_capability=capability,
        turn_mode=turn_mode,
        goal_lifecycle=lifecycle,
        goal_completion_reason=_plain(value.get("goal_completion_reason"), 300) if lifecycle == "COMPLETED" else "",
        interaction=interaction,
    )


def _fallback(message: str, world_model: UserWorldModel) -> CognitionOrchestrationV2:
    # If the support pass fails, deterministic code must not replace the
    # model's semantic judgment with keyword routing.  The foreground model
    # still receives the real conversation and model-support packet.
    goal = next((goal for goal in world_model.goals if _plain(goal, 300)), "")
    help_opportunity = HelpOpportunity(
        true_goal=goal,
        main_obstacle="还没有足够证据确定最大障碍",
        highest_value_unknown="",
        why_it_changes_the_plan="",
        public_research_available=False,
        private_input_needed=None,
        best_immediate_deliverable="",
        success_signal="",
    )
    confirmed = tuple(c.value for c in world_model.claims if c.epistemic_status == "CONFIRMED")[:12]
    observed = tuple(c.value for c in world_model.claims if c.epistemic_status == "OBSERVED")[:12]
    return CognitionOrchestrationV2(
        contract_version=CONTRACT_VERSION, source="context_only_fallback", confidence=0.0,
        world_model=world_model, help_opportunity=help_opportunity, research_task=ResearchTask(), action_graph=(),
        work_brief=WorkBrief(user="当前账号用户", goal=goal, stage="当前轮", confirmed_facts=confirmed,
            observed_facts=observed, unknowns=(), strategy="由前台模型根据当前对话自主判断。", deliverables=(),
            resource_purposes=(), forbidden_claims=("不得编造个人事实或外部事实",), success_checks=()),
        recommended_capability="NONE", turn_mode="TASK", goal_lifecycle="BACKGROUND",
        interaction=InteractionSupport(reason="context_only_fallback"),
        excluded_account_context=(),
    )


def _shingles(value: Any) -> set[str]:
    """Distinctive fixed-width fragments of a claim, whitespace removed."""
    text = "".join(str(value or "").split())
    if not text:
        return set()
    if len(text) < _EXCLUSION_SHINGLE_WIDTH:
        return {text}
    return {
        text[index:index + _EXCLUSION_SHINGLE_WIDTH]
        for index in range(len(text) - _EXCLUSION_SHINGLE_WIDTH + 1)
    }


def _declared_exclusions(value: Mapping[str, Any], account_values: Sequence[str]) -> tuple[str, ...]:
    """Validate the model's excluded-context names against the claims it was given.

    The model owns the semantic judgement about what the user asked to set aside;
    deterministic code only checks identity here, so a name that matches nothing
    is dropped rather than acted on.
    """
    raw = value.get("excluded_account_context")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    known = {_plain(item, 500): item for item in account_values if _plain(item, 500)}
    declared: list[str] = []
    for entry in list(raw)[:8]:
        text = _plain(entry, 500)
        if text and text in known and known[text] not in declared:
            declared.append(known[text])
    return tuple(declared)


def _without_excluded_facts(facts: Sequence[str], excluded: Sequence[str]) -> tuple[str, ...]:
    """Drop brief facts that restate a claim the model declared excluded.

    The match is textual overlap against claims we already hold, never a reading
    of the user's wording: the exclusion itself is the model's declaration. Short
    facts carry no distinctive fragment and are left alone on purpose, so a
    preference the user did want to apply cannot be removed by accident.
    """
    if not excluded:
        return tuple(facts)
    excluded_shingles = [_shingles(item) for item in excluded]
    kept: list[str] = []
    for fact in facts:
        fact_shingles = _shingles(fact)
        if fact_shingles and any(fact_shingles & item for item in excluded_shingles):
            continue
        kept.append(fact)
    return tuple(kept)


def _without_excluded_text(value: str, excluded: Sequence[str]) -> str:
    """Same rule for a single free-text brief field; it is dropped, not edited."""
    if not value:
        return value
    kept = _without_excluded_facts((value,), excluded)
    return kept[0] if kept else ""


def _project_interaction(value: Mapping[str, Any]) -> InteractionSupport:
    """Keep the model's own presentation support; fall back to a neutral shape.

    This deliberately does not infer presentation from words in the message.
    """
    raw = _mapping(value.get("interaction"))
    if not raw:
        return InteractionSupport(reason="model_public_work_brief")
    sidecar = raw.get("sidecar") is True
    mode = str(raw.get("mode") or "ORIENT").upper()
    risk_level = str(raw.get("risk_level") or "NONE").upper()
    return InteractionSupport(
        mode=mode if mode in INTERACTION_MODES else "ORIENT",
        sidecar=sidecar,
        sidecar_type=(_plain(raw.get("sidecar_type"), 80) or None) if sidecar else None,
        sidecar_title=(_plain(raw.get("sidecar_title"), 80) or None) if sidecar else None,
        urgency="high" if str(raw.get("urgency") or "").lower() == "high" else "normal",
        risk_level=risk_level if risk_level in {"NONE", "CAUTION", "HIGH"} else "NONE",
        risk=_plain(raw.get("risk"), 240),
        reason=_plain(raw.get("reason"), 160) or "model_public_work_brief",
    )


def _memory_proposals(raw: Any) -> tuple[dict[str, Any], ...]:
    """Loose parse; the ledger validates each entry before anything is stored."""
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    out: list[dict[str, Any]] = []
    for entry in list(raw)[:6]:
        if not isinstance(entry, Mapping):
            continue
        out.append({
            "kind": _plain(entry.get("kind"), 24),
            "topic": _plain(entry.get("topic"), 120),
            "content": _plain(entry.get("content"), 500),
            "evidence_span": _plain(entry.get("evidence_span"), 200),
        })
    return tuple(out)


def _project_model_understanding(
    value: Mapping[str, Any], *, message: str, base_world_model: UserWorldModel,
) -> CognitionOrchestrationV2 | None:
    """Keep the model's public work brief without a schema gate deciding its route.

    This is deliberately a display/context projection, not an executor parser.
    Artifact executors still independently check their inputs and receipts.
    """
    help_raw = _mapping(value.get("help_opportunity"))
    brief_raw = _mapping(value.get("work_brief"))
    if not help_raw and not brief_raw:
        return None
    turn_mode = str(value.get("turn_mode") or "TASK").upper()
    if turn_mode not in {"CONVERSATION", "LEARNING", "TASK", "PAUSED", "STOPPED"}:
        turn_mode = "TASK"
    default_lifecycle = "ACTIVE" if turn_mode in {"TASK", "LEARNING"} else ("PAUSED" if turn_mode == "PAUSED" else "BACKGROUND")
    lifecycle = str(value.get("goal_lifecycle") or default_lifecycle).upper()
    if lifecycle not in GOAL_LIFECYCLES:
        lifecycle = default_lifecycle
    candidates = value.get("world_model_candidates") if isinstance(value.get("world_model_candidates"), list) else []
    candidate_world = build_world_model(observed_candidates=candidates, current_message=message)
    claims = list(base_world_model.claims)
    claims.extend(claim for claim in candidate_world.claims if not any(old.kind == claim.kind and old.value == claim.value for old in claims))
    world_model = _world_model_from_claims(claims, current_message=message)
    # The model's own plan is projected whenever it is internally consistent.
    # An absent list means no plan was offered, which the schema allows; a
    # present but invalid list means the model contradicted itself (for example
    # by claiming a node already completed) and must not drive execution.
    raw_actions = value.get("actions")
    graph = _validate_graph(raw_actions) if isinstance(raw_actions, list) else ()
    plan_consistent = not isinstance(raw_actions, list) or graph is not None
    questions = tuple(
        ResearchQuestion(
            claim=_plain(_mapping(item).get("claim"), 240),
            query=_plain(_mapping(item).get("query"), 220),
            why_it_changes_the_plan=_plain(_mapping(item).get("why_it_changes_the_plan"), 300),
            expected_source_types=_strings(_mapping(item).get("expected_source_types"), limit=4, item_limit=80),
        ) for item in list(_mapping(value.get("research_task")).get("questions") or [])[:3]
        if _plain(_mapping(item).get("claim"), 240) and _plain(_mapping(item).get("query"), 220)
    )
    raw_research = _mapping(value.get("research_task"))
    try:
        confidence = max(0.0, min(1.0, float(value.get("confidence") or 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    requested_capability = str(value.get("recommended_capability") or "NONE").upper()
    capability = requested_capability if requested_capability in ARTIFACT_CAPABILITIES else "NONE"
    if turn_mode in {"CONVERSATION", "PAUSED", "STOPPED"} and lifecycle == "ACTIVE":
        # A non-work turn cannot report an advancing goal.
        lifecycle = default_lifecycle
    # New work may only be owned by an active work turn whose own plan is
    # consistent. This is the same gate the public rail already applies; without
    # it a chat turn or an already-closed goal could still acquire an artifact.
    # Turns that do not qualify keep the model's understanding but lose every
    # step that could execute.
    if not plan_consistent or turn_mode not in {"TASK", "LEARNING"} or lifecycle != "ACTIVE":
        capability = "NONE"
        graph = tuple(
            replace(node, depends_on=())
            for node in (graph or ())
            if node.kind in {"UNDERSTAND", "DELIVER"}
        )
    # Account history the model itself set aside this turn. Validated against the
    # claims it was given, then removed from the published brief so the learning
    # rail cannot present an excluded background as something DARYA will use.
    excluded = _declared_exclusions(
        value,
        tuple(
            claim.value
            for claim in base_world_model.claims
            if claim.source_type != "CURRENT_MESSAGE"
        ),
    )
    return CognitionOrchestrationV2(
        contract_version=CONTRACT_VERSION,
        source="structured_model",
        confidence=confidence,
        world_model=world_model,
        help_opportunity=HelpOpportunity(
            true_goal=_plain(help_raw.get("true_goal") or value.get("goal") or brief_raw.get("goal"), 300),
            main_obstacle=_plain(help_raw.get("main_obstacle"), 300),
            highest_value_unknown=_plain(help_raw.get("highest_value_unknown"), 240),
            why_it_changes_the_plan=_plain(help_raw.get("why_it_changes_the_plan"), 300),
            public_research_available=help_raw.get("public_research_available") is True,
            private_input_needed=_plain(help_raw.get("private_input_needed"), 240) or None,
            best_immediate_deliverable=_plain(help_raw.get("best_immediate_deliverable"), 500),
            success_signal=_plain(help_raw.get("success_signal"), 300),
        ),
        research_task=ResearchTask(
            needed=turn_mode in {"TASK", "LEARNING"} and raw_research.get("needed") is True and bool(questions),
            questions=questions,
            source_strategy=_strings(raw_research.get("source_strategy"), limit=5, item_limit=120),
            budget=max(0, min(8, int(raw_research.get("budget") or 0))),
            stop_condition=_plain(raw_research.get("stop_condition"), 300),
            fallback=_plain(raw_research.get("fallback"), 300),
        ),
        # The plan is public work posture, never an execution authority:
        # existing executors still decide their own inputs and receipts.
        action_graph=graph or (),
        # Every learner-visible brief field is stripped, not only the fact lists:
        # the rail prints these directly, so an excluded background echoed into any
        # of them would be presented as something DARYA will use. user/goal/stage
        # stay as the turn's own identity.
        work_brief=WorkBrief(
            user=_plain(brief_raw.get("user"), 300), goal=_plain(brief_raw.get("goal") or help_raw.get("true_goal") or value.get("goal"), 300),
            stage=_plain(brief_raw.get("stage"), 160),
            confirmed_facts=_without_excluded_facts(
                _strings(brief_raw.get("confirmed_facts"), limit=12, item_limit=300), excluded,
            ),
            observed_facts=_without_excluded_facts(
                _strings(brief_raw.get("observed_facts"), limit=12, item_limit=300), excluded,
            ),
            unknowns=_without_excluded_facts(
                _strings(brief_raw.get("unknowns"), limit=8, item_limit=240), excluded,
            ),
            strategy=_without_excluded_text(_plain(brief_raw.get("strategy"), 500), excluded),
            deliverables=_without_excluded_facts(
                _strings(brief_raw.get("deliverables"), limit=6, item_limit=240), excluded,
            ),
            resource_purposes=_without_excluded_facts(
                _strings(brief_raw.get("resource_purposes"), limit=6, item_limit=300), excluded,
            ),
            forbidden_claims=_without_excluded_facts(
                _strings(brief_raw.get("forbidden_claims"), limit=8, item_limit=240), excluded,
            ),
            success_checks=_without_excluded_facts(
                _strings(brief_raw.get("success_checks"), limit=8, item_limit=240), excluded,
            ),
        ),
        recommended_capability=capability,
        turn_mode=turn_mode,
        goal_lifecycle=lifecycle,
        goal_completion_reason=_plain(value.get("goal_completion_reason"), 300) if lifecycle == "COMPLETED" else "",
        interaction=_project_interaction(value),
        excluded_account_context=excluded,
        memories_to_keep=_memory_proposals(value.get("memories_to_keep")),
    )
async def _stream_planner(llm: Any, prompt: str, *, max_tokens: int, on_token: Any | None) -> str:
    """Run the planner as a stream and hand back the whole reply.

    Streaming buys one thing: while the model is writing, the caller knows it is
    writing. The reply is JSON, so none of it is worth showing -- but its arrival
    is, and that is what `on_token` reports. JSON mode is preserved, because a
    planner that streams but no longer parses would be a bad trade.

    Falls back to the non-streaming call if this provider cannot stream.
    """
    if not hasattr(llm, "chat_stream"):
        return await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            use_cache=False,
            write_cache=False,
        )
    buffer: list[str] = []
    try:
        async for chunk in llm.chat_stream(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        ):
            buffer.append(chunk)
            if on_token is not None:
                on_token(sum(len(part) for part in buffer))
    except TypeError:
        # A provider whose chat_stream predates the response_format argument.
        buffer = []
        async for chunk in llm.chat_stream(messages=[{"role": "user", "content": prompt}]):
            buffer.append(chunk)
            if on_token is not None:
                on_token(sum(len(part) for part in buffer))
    text = "".join(buffer).strip()
    if not text:
        raise RuntimeError("PLANNER_STREAM_EMPTY")
    return text

async def plan_cognition_orchestration_v2(
    message: str,
    *,
    recent_history: Iterable[Mapping[str, Any]] = (),
    memories: Iterable[Mapping[str, Any]] = (),
    user_context: Iterable[Mapping[str, Any]] = (),
    impressions: Iterable[Mapping[str, Any]] = (),
    continuity_brief: Mapping[str, Any] | None = None,
    llm: Any | None = None,
    on_token: Any | None = None,
) -> CognitionOrchestrationV2:
    history: list[dict[str, Any]] = []
    source_history = list(recent_history)
    board_context = current_blackboard_context(source_history)
    for row in source_history[-12:]:
        if not isinstance(row, Mapping):
            continue
        projected: dict[str, Any] = {
            "role": str(row.get("role") or ""),
            "content": _plain(row.get("content"), 800),
        }
        previous = row.get("cognition_orchestration")
        if isinstance(previous, Mapping):
            previous_brief = previous.get("work_brief") or {}
            previous_nodes = previous.get("action_graph") or []
            projected["previous_work"] = {
                "goal": _plain(previous_brief.get("goal"), 300) if isinstance(previous_brief, Mapping) else "",
                "strategy": _plain(previous_brief.get("strategy"), 400) if isinstance(previous_brief, Mapping) else "",
                "deliverables": list(previous_brief.get("deliverables") or [])[:6] if isinstance(previous_brief, Mapping) else [],
                "nodes": [
                    {
                        "node_id": _plain(node.get("node_id"), 64),
                        "kind": _plain(node.get("kind"), 40),
                        "state": _plain(node.get("state"), 40),
                        "completion_signal": _plain(node.get("completion_signal"), 160),
                    }
                    for node in list(previous_nodes)[:8]
                    if isinstance(node, Mapping)
                ],
            }
        previous_action = row.get("conversation_action")
        if isinstance(previous_action, Mapping):
            action_board = blackboard_context_from_action(previous_action)
            board_script_present = action_board is not None
            projected["previous_capability_receipt"] = {
                "kind": _plain(previous_action.get("kind"), 40),
                "status": _plain(previous_action.get("status"), 40),
                "artifact_present": bool(previous_action.get("artifact")) or board_script_present,
                "blackboard_script_present": board_script_present,
            }
            if action_board is not None:
                projected["previous_capability_receipt"]["blackboard_title"] = action_board["title"]
        history.append(projected)
    world_model = build_world_model(
        memories=memories,
        user_context=user_context,
        impressions=impressions,
        current_message=message,
    )
    if llm is None:
        try:
            from services.llm_service_real import get_llm_service
            llm = await get_llm_service()
        except Exception:
            logger.exception("cognition_v2 could not obtain a model service")
            llm = None
    if llm is not None:
        last_failure = "unknown"
        raw_snippet = ""
        for attempt, max_tokens in enumerate((3200, 2400), start=1):
            try:
                raw = await asyncio.wait_for(
                    _stream_planner(
                        llm,
                        _prompt(
                            message,
                            recent_history=history,
                            world_model=world_model,
                            continuity_brief=continuity_brief,
                            current_blackboard=board_context,
                        ),
                        max_tokens=max_tokens,
                        on_token=on_token,
                    ),
                    timeout=PLANNER_TIMEOUT_SECONDS,
                )
                parsed = _json_object(raw)
                if parsed is None:
                    raw_snippet = str(raw.get("content") if isinstance(raw, Mapping) else raw or "")[:300]
                    last_failure = f"json_parse_attempt_{attempt}"
                    logger.warning(
                        "cognition_v2 planner output unparseable (attempt %s): %s",
                        attempt, raw_snippet,
                    )
                    continue
                result = _project_model_understanding(parsed, message=message, base_world_model=world_model)
                if result is not None:
                    return result
                last_failure = f"schema_validation_attempt_{attempt}"
                logger.warning(
                    "cognition_v2 planner output failed schema validation (attempt %s): %s",
                    attempt, json.dumps(parsed, ensure_ascii=False, default=str)[:400],
                )
                continue
            except Exception as exc:
                last_failure = f"{type(exc).__name__}_attempt_{attempt}"
                logger.warning("cognition_v2 planner call failed (attempt %s): %s", attempt, type(exc).__name__)
                if isinstance(exc, asyncio.TimeoutError):
                    # A timeout will not improve within this turn; stop retrying.
                    break
        logger.warning("cognition_v2 planner degraded turn: reason=%s", last_failure)
    else:
        # Falling back without a single call used to leave no trace at all, so a
        # whole turn could quietly lose its judgement -- including the decision
        # to go and look something up -- and nothing in the log said so.
        logger.warning(
            "cognition_v2 planner skipped: no model available (import failed or service unavailable)"
        )
    return _fallback(message, world_model)


def build_orchestration_directive(value: CognitionOrchestrationV2) -> str:
    full = value.public_dict()
    payload = {
        key: full[key]
        for key in (
            "contract_version", "source", "confidence", "help_opportunity",
            "research_task", "action_graph", "work_brief", "recommended_capability", "turn_mode", "goal_lifecycle", "goal_completion_reason",
            "interaction", "excluded_account_context",
        )
    }
    return (
        "DARYA 模型辅助判断（建议而非固定路线，不展示字段或隐藏推理）：\n"
        # The surrounding instructions name these fields, so they stay even when
        # empty; everything else drops its empty containers.
        + dumps_compact(
            payload,
            keep_keys=("action_graph", "excluded_account_context", "goal_lifecycle", "goal_completion_reason"),
        )
        + "\n请结合用户当前原话和完整对话自行复核这些建议；发现不贴合时可以调整或不用。"
        "先完成主要认知劳动，不把检索、比较、整理重新布置给用户。"
        "只有 action_graph 中获得真实回执的节点才可称为完成；PLANNED 不是完成。"
        "公开且会改变方案的信息应主动核对；私人问题最多一个，并先交付不依赖它的可用版本。"
        "用户明确委托现有能力可生成的成品时，应调用对应能力并等待真实回执；不得退化成提纲、脚本或否认当前能力。"
        "不得使用 contradicted、stale、inferred 或 unknown 信息写成确定事实。"
        "excluded_account_context 中的账号信息本轮已排除，不得出现在回答、资料或产物中，也不要复述它来征求同意。"
        "CONVERSATION只回应当前聊天，不插入教学；PAUSED/STOPPED不追问不推进。"
        "goal_lifecycle=COMPLETED 只代表约定目标已经交付或被用户确认收束，不可借此宣称掌握。"
        "用户停止不等于已掌握，重复答案不等于独立迁移。学会后结束当前目标，不自动添加下一课。"
    )


def evaluate_orchestration_result(
    value: CognitionOrchestrationV2,
    response: str,
    *,
    evidence_bundle: Any | None = None,
    capability_action: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate observable progress and receipts, never answer shape."""
    text = str(response or "").strip()
    evidence = _mapping(evidence_bundle)
    action = _mapping(capability_action)
    checks: list[dict[str, Any]] = []
    failures: list[str] = []
    warnings: list[str] = []

    usable = bool(text) and not re.fullmatch(r"[^?？]{0,600}[?？]", text, re.S) and bool(
        re.search(r"[。.!！\n:：]", text)
    )
    checks.append({"name": "usable_immediate_result", "passed": usable})
    if not usable:
        failures.append("usable_immediate_result_missing")

    handed_back = bool(re.search(
        r"(?:你|请你)(?:自己|自行).{0,10}(?:搜索|检索|查资料|整理|比较|调研)", text, re.I,
    ))
    checks.append({"name": "cognitive_labor_not_handed_back", "passed": not handed_back})
    if handed_back:
        failures.append("cognitive_labor_handed_back")

    research_supported = True
    if value.research_task.needed:
        evidence_status = str(evidence.get("status") or "").upper()
        sources = evidence.get("sources") or []
        research_supported = bool(sources) and evidence_status not in {"", "UNVERIFIED", "FAILED", "ERROR"}
        checks.append({
            "name": "research_evidence_receipt",
            "passed": research_supported,
            "status": evidence_status or "MISSING",
            "source_count": len(sources) if isinstance(sources, Sequence) else 0,
        })
        if not research_supported:
            warnings.append("research_not_supported")

    capability_bound = True
    if value.recommended_capability != "NONE":
        capability_bound = str(action.get("kind") or "").upper() == value.recommended_capability
        checks.append({
            "name": "capability_bound_to_existing_authority",
            "passed": capability_bound,
            "status": str(action.get("status") or "MISSING"),
        })
        if not capability_bound:
            warnings.append("planned_capability_not_bound")

    false_completion = bool(re.search(r"(?:已经|已)(?:完成|生成|制作|保存)", text)) and (
        value.recommended_capability != "NONE"
        and str(action.get("status") or "").lower() not in {"ready"}
        and not action.get("artifact")
    )
    checks.append({"name": "no_completion_without_receipt", "passed": not false_completion})
    if false_completion:
        failures.append("completion_claim_without_receipt")

    status = "FAIL" if failures else ("WARN" if warnings else "PASS")
    return {
        "contract_version": CONTRACT_VERSION,
        "status": status,
        "failures": failures,
        "warnings": warnings,
        "checks": checks,
        "research_needed": value.research_task.needed,
        "recommended_capability": value.recommended_capability,
        "action_node_count": len(value.action_graph),
    }


def proactive_semantic_route(value: CognitionOrchestrationV2):
    """Project one V2 CREATE node into the existing single artifact authority."""
    if (
        value.recommended_capability not in LOW_RISK_PROACTIVE_CAPABILITIES
        or value.confidence < 0.3
    ):
        return None
    from services.workspace_task_contracts import AgentType, SemanticAction, SemanticRoute
    mapping = {
        "HANDOUT": AgentType.LECTURE,
        "ASSESSMENT": AgentType.EXAM,
        "CODE_LAB": AgentType.CODE,
        "PRESENTATION": AgentType.PRESENTATION,
        "MINDMAP": AgentType.MINDMAP,
        "VIDEO": AgentType.VIDEO,
    }
    agent_type = mapping.get(value.recommended_capability)
    if agent_type is None:
        return None
    return SemanticRoute(
        action=SemanticAction.NEW_TASK,
        agent_type=agent_type,
        confidence=value.confidence,
        reason_code="cognition_v2_help_opportunity",
        slot_updates={"work_brief": asdict(value.work_brief)},
    )


__all__ = [
    "ActionNode", "CognitionOrchestrationV2", "HelpOpportunity", "InteractionSupport", "ResearchQuestion",
    "ResearchTask", "UserWorldModel", "WorkBrief", "WorldModelClaim", "capability_registry",
    "build_orchestration_directive", "build_world_model", "evaluate_orchestration_result",
    "plan_cognition_orchestration_v2",
    "proactive_semantic_route",
]
