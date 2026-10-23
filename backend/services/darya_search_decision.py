"""DARYA Search Decision for non-Tide conversation flows.

Extends the existing Tide search capability to general DARYA conversations,
enabling grounded answers for current facts without requiring Tide experience.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from services.tide_search_evidence import SearchContext


@dataclass
class SearchDecisionResult:
    """Decision on whether to invoke external search for this turn."""

    should_search: bool
    reason: str
    trigger: str
    confidence: float
    search_context: SearchContext | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "should_search": self.should_search,
            "reason": self.reason,
            "trigger": self.trigger,
            "confidence": self.confidence,
        }


# Patterns for detecting search-requiring messages
_EXPLICIT_SEARCH = re.compile(
    r"查一下|查资料|搜索|检索|找资料|核对|核实|求证|联网查|"
    r"search|look\s*up|find\s+(?:a\s+)?source|verify|fact.?check",
    re.I,
)
_CURRENT_FACT = re.compile(
    r"最新|版本号|发布时间|价格|当前版本|"
    r"latest|current\s+version|version\s+number|release\s+date|price",
    re.I,
)
# Bare time words are ordinary learner speech; require a current-fact subject.
_CURRENT_TIME_WORD = re.compile(
    r"最新|当前|现在|今天|近期|最近|今年|latest|current|today|recent",
    re.I,
)
_CURRENT_FACT_SUBJECT = re.compile(
    r"版本|发布|价格|政策|规则|生效|官方|官网|公告|更新|排名|新闻|统计|行情|汇率|利率|岗位|招录|招聘|招考|职位表|录取|分数线|名额|天气|气温|股价|油价|比分|赛果|路况|"
    r"release|version|price|policy|official|announcement|news|update|statistics",
    re.I,
)
# “今天讲解累了”“现在只有20分钟”描述学习者当天的状态，不是会变化的外部事实。
_LEARNER_DAY_STATE = re.compile(
    r"(?:今天|这会儿|今晚|此刻)(?:[^。！？；\n]{0,8})?(?:累(?:了|的?)?|忙|疲惫|困|烦|没(?:什么)?(?:时间|心情|精力)|状态不好|状态一般|只想|先不|到此为止)|"
    r"(?:今天|现在)(?:只有|时间有限|先学|先到|先暂停|到此为止)",
    re.I,
)
_VERSION_QUERY = re.compile(
    r"python\s*\d+\.\d+|python\s+最新|docker\s+最新|什么版本|哪个版本|版本是|"
    r"python\s+latest|docker\s+latest|what\s+version|which\s+version",
    re.I,
)
_CONFLICT_CLAIM = re.compile(
    r"冲突|矛盾|说法不一|哪个对|到底是|"
    r"conflict|contradict|which\s+is\s+correct|different\s+sources",
    re.I,
)
_PROFESSIONAL_SOURCE = re.compile(
    r"专业依据|权威依据|官方文档|官网|研究|论文|"
    r"authoritative|official\s+doc|research|paper|standard",
    re.I,
)

# Patterns suggesting internal context is sufficient
_CONCEPT_EXPLANATION = re.compile(
    r"^(?:什么是|解释|讲讲|介绍|说明).{0,30}(?:概念|原理|定义|特点|优缺点)|"
    r"^(?:what\s+is|explain|describe).{0,30}(?:concept|principle|definition)",
    re.I,
)
_STABLE_KNOWLEDGE = re.compile(
    r"闭包|作用域|继承|多态|算法|数据结构|设计模式|"
    r"closure|scope|inheritance|polymorphism|algorithm|data\s+structure|design\s+pattern",
    re.I,
)
_CASUAL_CHAT = re.compile(
    r"^(?:嗯|哦|好的|谢谢|了解|明白了|继续|下一个)|"
    r"^(?:ok|okay|thanks|got\s+it|understood|continue|next)",
    re.I,
)


def decide_search_need(
    message: str,
    *,
    context_history: list[dict[str, Any]] | None = None,
    domain_pack_available: bool = False,
    existing_profile: dict[str, Any] | None = None,
) -> SearchDecisionResult:
    """Decide whether external search is needed for this message.

    Decision logic:
    1. SEARCH_REQUIRED: explicit request, current facts, version queries, conflicts
    2. SEARCH_NOT_NEEDED: stable concepts with domain pack, casual chat
    3. Edge cases: prefer search for ambiguous current claims

    Args:
        message: Current user message
        context_history: Recent conversation history
        domain_pack_available: Whether installed Domain Pack covers this topic
        existing_profile: User's learning profile

    Returns:
        SearchDecisionResult with should_search flag and reasoning
    """
    text = str(message or "").strip()

    # Empty or too-short messages don't need search
    if len(text) < 3:
        return SearchDecisionResult(
            should_search=False,
            reason="消息过短，无需外部检索",
            trigger="EMPTY_MESSAGE",
            confidence=1.0,
        )

    # Casual acknowledgments don't need search
    if _CASUAL_CHAT.search(text):
        return SearchDecisionResult(
            should_search=False,
            reason="简单应答，无需外部检索",
            trigger="CASUAL_CHAT",
            confidence=0.95,
        )

    # Explicit search requests always trigger
    if _EXPLICIT_SEARCH.search(text):
        return SearchDecisionResult(
            should_search=True,
            reason="用户明确要求查找资料或核实信息",
            trigger="EXPLICIT_SEARCH",
            confidence=1.0,
            search_context=SearchContext(message=text),
        )

    # Conflict claims require multi-source verification
    if _CONFLICT_CLAIM.search(text):
        return SearchDecisionResult(
            should_search=True,
            reason="用户指出冲突信息，需要查找多个来源核实",
            trigger="CONFLICT_CLAIM",
            confidence=0.95,
            search_context=SearchContext(message=text),
        )

    # Current facts and version queries need live data; learner day-state
    # phrases that merely contain a time word do not.
    current_claim_text = _LEARNER_DAY_STATE.sub("", text)
    if _CURRENT_FACT.search(current_claim_text) or (
        _CURRENT_TIME_WORD.search(current_claim_text)
        and _CURRENT_FACT_SUBJECT.search(current_claim_text)
    ) or _VERSION_QUERY.search(text):
        return SearchDecisionResult(
            should_search=True,
            reason="问题涉及会变化的当前事实或版本号，需要实时核对",
            trigger="CURRENT_FACT",
            confidence=0.9,
            search_context=SearchContext(message=text),
        )

    # Professional sources requested
    if _PROFESSIONAL_SOURCE.search(text):
        return SearchDecisionResult(
            should_search=True,
            reason="用户需要专业依据或权威来源",
            trigger="PROFESSIONAL_SOURCE",
            confidence=0.85,
            search_context=SearchContext(message=text),
        )

    # Stable concept explanations with domain pack don't need search
    if domain_pack_available and _STABLE_KNOWLEDGE.search(text):
        if _CONCEPT_EXPLANATION.search(text):
            return SearchDecisionResult(
                should_search=False,
                reason="已安装领域包可覆盖该稳定概念，无需外部检索",
                trigger="STABLE_CONCEPT",
                confidence=0.8,
            )

    # Default: prefer not searching for general conversation
    # Domain Packs and existing context should handle most learning queries
    return SearchDecisionResult(
        should_search=False,
        reason="普通对话或学习问题，现有上下文足够",
        trigger="GENERAL_CONVERSATION",
        confidence=0.7,
    )


def build_search_context_from_conversation(
    message: str,
    *,
    existing_profile: dict[str, Any] | None = None,
    recent_history: list[dict[str, Any]] | None = None,
) -> SearchContext:
    """Build SearchContext with conversation background for better retrieval.

    Args:
        message: Current user message
        existing_profile: User's learning profile
        recent_history: Recent conversation messages

    Returns:
        SearchContext with enriched query context
    """
    context = SearchContext(message=message)

    # Extract learning domain from profile
    if existing_profile:
        current_domain = existing_profile.get("current_domain")
        if current_domain and len(current_domain) < 50:
            # Add domain as disambiguation context, but don't overwhelm the query
            context.shared_perspective = {"domain": current_domain}

    return context
