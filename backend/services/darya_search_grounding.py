"""DARYA Search/Evidence planner over the accepted Evidence/Trust authority.

This module owns decision and user-safe projection only. It does not persist a
Domain Pack, write durable memory, or mutate learner evidence/mastery/route.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import re
from typing import Any, Literal
from urllib.parse import urlparse

from services.darya_competition_learning import CLAIM_CONTRACT_VERSION
from services.tide_search_evidence import EvidenceBundle, SearchContext


SearchRequirement = Literal["SEARCH_NOT_NEEDED", "SEARCH_RECOMMENDED", "SEARCH_REQUIRED"]

_EXPLICIT_SEARCH = re.compile(
    r"查一下|查资料|搜索|检索|找资料|找来源|核对|核实|求证|联网|"
    r"search|look\s*up|browse|find\s+(?:a\s+)?source|verify|fact.?check",
    re.I,
)
_CURRENT_CLAIM = re.compile(
    r"最新|版本|发布|价格|政策|规则|生效|"
    r"latest|current\s+version|release|price|policy|effective",
    re.I,
)
# A bare time word is ordinary learner speech (“现在给我讲”, “今天讲解累了”).
# Treat it as a current-fact trigger only when it co-occurs with an
# external-fact subject; bare time words never trigger alone.
_CURRENT_TIME_WORD = re.compile(
    r"最新|当前|现在|今天|近期|最近|今年|latest|current|today|recent",
    re.I,
)
_CURRENT_FACT_SUBJECT = re.compile(
    r"版本|发布|价格|政策|规则|生效|官方|官网|公告|更新|排名|新闻|统计|行情|汇率|利率|岗位|招录|招聘|招考|职位表|录取|分数线|名额|天气|气温|股价|油价|比分|赛果|路况|"
    r"release|version|price|policy|official|announcement|news|update|statistics",
    re.I,
)
_PERSONAL_CURRENT_CONTEXT = re.compile(
    r"(?:我|用户)(?:现在|目前|当前)的?(?:基础|水平|情况|目标|进度)|"
    r"适合我(?:现在|目前|当前)的?(?:基础|水平|情况|目标|进度)?",
    re.I,
)
# “今天讲解累了”“现在只有20分钟”描述学习者当天的状态，不是会变化的外部事实。
_LEARNER_DAY_STATE = re.compile(
    r"(?:今天|这会儿|今晚|此刻)(?:[^。！？；\n]{0,8})?(?:累(?:了|的?)?|忙|疲惫|困|烦|没(?:什么)?(?:时间|心情|精力)|状态不好|状态一般|只想|先不|到此为止)|"
    r"(?:今天|现在)(?:只有|时间有限|先学|先到|先暂停|到此为止)",
    re.I,
)
_LEARNER_OBJECT_CONTEXT = re.compile(
    r"(?:当前|现在|目前)(?:的)?(?:目标|任务|进度|题目|这道题|这一题|知识点|问题)",
    re.I,
)
_CONFLICT = re.compile(r"冲突|矛盾|说法不一|哪个对|互相否定|conflict|contradict|disagree", re.I)
_PROFESSIONAL_BASIS = re.compile(
    r"专业依据|权威依据|论文|标准|研究依据|临床|法律依据|金融数据|"
    r"authoritative|primary source|paper|standard|clinical|legal basis",
    re.I,
)
_OPEN_TOPIC_LEARNING = re.compile(
    r"我想学|想学习|学习一下|教我|带我学|入门|基础|教程|课程|怎么学|"
    r"\b(?:learn|study|teach\s+me|tutorial|course|basics?|introduction\s+to)\b",
    re.I,
)
_VERSION_CLAIM = re.compile(r"版本|稳定版|发布|version|stable|release", re.I)
_VERSION_TOKEN = re.compile(
    r"(?<![\w.])(?:v(?:ersion)?\s*)?(\d+\.\d+(?:\.\d+){0,2}(?:[-+._]?[a-z]+\d*)?)(?![\w.])",
    re.I,
)
_VERSION_SIGNAL = re.compile(
    r"latest|current|stable|release|download|最新|当前|稳定版|发布|下载",
    re.I,
)

# Provider bootstrap locators, not knowledge packs: no teaching content,
# learner state, or durable installation is stored here. Unknown topics still
# use the general provider chain and fail closed when evidence is insufficient.
_OFFICIAL_BOOTSTRAPS: tuple[tuple[re.Pattern[str], tuple[str, ...]], ...] = (
    (re.compile(r"\bpython\b.*(?:闭包|nonlocal|作用域|命名空间)|(?:闭包|nonlocal|作用域|命名空间).*\bpython\b", re.I | re.S),
     (
         "https://docs.python.org/3/reference/executionmodel.html#naming-and-binding",
         "https://docs.python.org/3/reference/simple_stmts.html#the-nonlocal-statement",
     )),
    (re.compile(r"\bpython\b", re.I), ("https://www.python.org/downloads/",)),
    (re.compile(r"\bdocker\b.*\bbridge\b|\bbridge\b.*\bdocker\b", re.I | re.S),
     (
         "https://docs.docker.com/engine/network/drivers/bridge/",
         "https://raw.githubusercontent.com/docker/docs/main/content/manuals/engine/network/drivers/bridge.md",
     )),
    (re.compile(r"\bdocker\b.*(?:network|网络)|(?:network|网络).*\bdocker\b", re.I | re.S),
     ("https://docs.docker.com/engine/network/",)),
)


@dataclass(frozen=True)
class SearchPlan:
    decision: SearchRequirement
    reason: str
    trigger: str
    domain_status: str
    installed_domain_id: str | None
    preferred_urls: tuple[str, ...] = ()

    @property
    def should_search(self) -> bool:
        return self.decision != "SEARCH_NOT_NEEDED"

    def public_dict(self) -> dict[str, Any]:
        return {**asdict(self), "should_search": self.should_search}


def search_plan_from_model(
    cognition: Any,
    *,
    domain_status: str,
    installed_domain_id: str | None,
) -> SearchPlan:
    """Bound a structured model research decision without reclassifying text."""
    payload = cognition.public_dict() if hasattr(cognition, "public_dict") else cognition
    payload = payload if isinstance(payload, dict) else {}
    research = payload.get("research_task")
    research = research if isinstance(research, dict) else {}
    source = str(payload.get("source") or "")
    needed = source == "structured_model" and research.get("needed") is True
    if needed:
        return SearchPlan(
            "SEARCH_REQUIRED",
            "模型根据完整对话建立了会改变回答的研究任务。",
            "MODEL_RESEARCH_TASK",
            domain_status,
            installed_domain_id,
        )
    return SearchPlan(
        "SEARCH_NOT_NEEDED",
        (
            "模型根据完整对话判断本轮不需要外部检索。"
            if source == "structured_model"
            else "模型辅助判断降级；不启用旧语义规则替代模型。"
        ),
        "MODEL_RESEARCH_DECISION" if source == "structured_model" else "MODEL_SUPPORT_FALLBACK",
        domain_status,
        installed_domain_id,
    )


def _bootstrap_urls(message: str) -> tuple[str, ...]:
    for pattern, urls in _OFFICIAL_BOOTSTRAPS:
        if pattern.search(message):
            return urls
    return ()


def merge_search_authority(
    *,
    v2_status: str | None,
    v2_research_needed: bool,
    regex_plan: SearchPlan,
) -> SearchPlan:
    """Give the orchestrator's structured decision authority over retrieval.

    Three postures:

    - ``structured_model``: the V2 research_task owns whether this turn needs
      external research; the regex plan applies only when the user explicitly
      asked to verify or declared conflicting sources.
    - ``safe_fallback``: the planner tried and degraded this turn. Degrade to a
      minimal-trust posture, not to the old rule engine: proactive triggers
      (current-claim phrasing, open-topic grounding) stay off, and only the
      user's explicit verification intent still forces a search.
    - ``None`` (no V2 decision): legacy non-V2 flows keep the regex plan.
    """
    if v2_status is None:
        return regex_plan
    if regex_plan.trigger in {"EXPLICIT_REQUEST", "SOURCE_CONFLICT"}:
        return regex_plan
    if v2_status == "structured_model" and v2_research_needed:
        return SearchPlan(
            "SEARCH_REQUIRED",
            "编排研究任务判定本轮需要外部检索。",
            "V2_RESEARCH",
            regex_plan.domain_status,
            regex_plan.installed_domain_id,
            regex_plan.preferred_urls,
        )
    if v2_status == "structured_model":
        return SearchPlan(
            "SEARCH_NOT_NEEDED",
            "本轮由编排研究任务判定不需要外部检索。",
            "V2_RESEARCH_DECISION",
            regex_plan.domain_status,
            regex_plan.installed_domain_id,
        )
    # safe_fallback: a degraded planner must not resurrect proactive search.
    return SearchPlan(
        "SEARCH_NOT_NEEDED",
        "编排本轮降级；除用户明确要求核对外不做主动检索，由对话直接回答。",
        "V2_DEGRADED_MINIMAL_TRUST",
        regex_plan.domain_status,
        regex_plan.installed_domain_id,
    )


def decide_search_plan(
    *,
    message: str,
    domain_status: str,
    installed_domain_id: str | None,
    domain_confidence: float = 0.0,
    suppress_external: bool = False,
) -> SearchPlan:
    text = str(message or "").strip()
    preferred = _bootstrap_urls(text)
    if suppress_external:
        return SearchPlan(
            "SEARCH_NOT_NEEDED", "用户当前约束明确排除外部检索。", "USER_BOUNDARY",
            domain_status, installed_domain_id,
        )
    # Conflict is more specific than a generic request to search or verify.
    # Preserve that semantic so Review cannot silently collapse two claims
    # into an ordinary single-source lookup.
    if _CONFLICT.search(text):
        return SearchPlan(
            "SEARCH_REQUIRED", "用户要求比较相互冲突的外部说法。", "SOURCE_CONFLICT",
            domain_status, installed_domain_id, preferred,
        )
    if _EXPLICIT_SEARCH.search(text):
        return SearchPlan(
            "SEARCH_REQUIRED", "用户明确要求查找、核对或提供来源。", "EXPLICIT_REQUEST",
            domain_status, installed_domain_id, preferred,
        )
    # “适合我现在的基础” describes learner context, not a time-sensitive
    # external fact.  Treating that word as freshness caused unrelated search
    # calls and made ordinary artifact creation look degraded.
    current_claim_text = _PERSONAL_CURRENT_CONTEXT.sub("", text)
    current_claim_text = _LEARNER_DAY_STATE.sub("", current_claim_text)
    current_claim_text = _LEARNER_OBJECT_CONTEXT.sub("", current_claim_text)
    if _CURRENT_CLAIM.search(current_claim_text) or (
        _CURRENT_TIME_WORD.search(current_claim_text)
        and _CURRENT_FACT_SUBJECT.search(current_claim_text)
    ):
        return SearchPlan(
            "SEARCH_REQUIRED", "问题包含会变化的版本、日期、规则或其他当前事实。", "CURRENT_CLAIM",
            domain_status, installed_domain_id, preferred,
        )
    if domain_status == "UNSUPPORTED" or (
        domain_status == "NOT_LEARNING" and _OPEN_TOPIC_LEARNING.search(text)
    ):
        return SearchPlan(
            "SEARCH_REQUIRED",
            "这是未安装 Domain Pack 的新学习主题，需要先建立本会话的可核验依据。",
            "OPEN_TOPIC_GROUNDING", domain_status, installed_domain_id, preferred,
        )
    if domain_status == "AMBIGUOUS":
        return SearchPlan(
            "SEARCH_NOT_NEEDED", "先让用户消解已安装领域歧义，不以外部搜索替代澄清。", "DOMAIN_CLARIFICATION",
            domain_status, installed_domain_id,
        )
    if _PROFESSIONAL_BASIS.search(text):
        return SearchPlan(
            "SEARCH_RECOMMENDED", "专业依据或领域判断仍需外部来源补强。", "EVIDENCE_RECOMMENDED",
            domain_status, installed_domain_id, preferred,
        )
    return SearchPlan(
        "SEARCH_NOT_NEEDED",
        "已安装领域的稳定知识或普通对话已有足够上下文，不做无意义检索。",
        "TRUSTED_CONTEXT", domain_status, installed_domain_id,
    )


def _freshness(source: Any, plan: SearchPlan) -> dict[str, Any]:
    current = plan.trigger == "CURRENT_CLAIM"
    return {
        "status": "LIVE_RETRIEVED" if current else "RETRIEVED_THIS_TURN",
        "retrieved_at": source.retrieved_at,
        "source_date": source.published_at,
        "current_claim": current,
    }


def _support_status(bundle: EvidenceBundle) -> str:
    if bundle.status == "CONFLICTING":
        return "CONFLICTING"
    if bundle.sufficient and bundle.status == "SUPPORTED":
        return "SUPPORTED"
    if bundle.sources:
        return "LEAD_ONLY"
    return "UNVERIFIED"


def _claim_evidence_span(claim: str, evidence: str, *, limit: int = 420) -> str:
    text = re.sub(r"\s+", " ", evidence or "").strip()
    if not text:
        return ""
    if re.search(r"\bscope\b|适用范围|默认|\bdefault\b", claim or "", re.I):
        contrast = re.search(
            r"\bdifferences?\s+between\b|\bwhereas\b|\bunlike\b|区别|不同|相比",
            text,
            re.I,
        )
        if contrast is not None:
            start = max(0, contrast.start() - 100)
            return text[start:start + max(limit, 700)]
    if not _VERSION_CLAIM.search(claim or ""):
        return text[:limit]
    match = next((
        item for item in _VERSION_TOKEN.finditer(text)
        if _VERSION_SIGNAL.search(text[max(0, item.start() - 140):item.end() + 140])
    ), None)
    if match is None:
        return ""
    start = max(0, match.start() - 180)
    return text[start:start + limit]


_SCOPE_CONTRAST = re.compile(
    r"\b(?:differences?\s+between|whereas|while|unlike|compared\s+with|on\s+the\s+other\s+hand)\b|"
    r"(?:区别|不同|相比|适用范围)",
    re.I,
)
_SCOPE_QUALIFIERS = (
    re.compile(r"\bdefault\b|默认", re.I),
    re.compile(r"\buser[- ]defined\b|\bcustom\b|用户定义|自定义", re.I),
    re.compile(r"\blegacy\b|旧版|历史", re.I),
    re.compile(r"\bcurrent\b|当前|现行", re.I),
    re.compile(r"\benterprise\b|企业版", re.I),
    re.compile(r"\bcommunity\b|社区版", re.I),
)
_POSITIVE_CAPABILITY = re.compile(r"\b(?:can|provide|allow|support|resolve)\b|可以|提供|允许|支持", re.I)
_LIMITED_CAPABILITY = re.compile(r"\b(?:cannot|can't|only|unless|not)\b|不能|仅|除非|不支持", re.I)


def _scope_resolution_source(bundle: EvidenceBundle | None) -> Any | None:
    """Return a strong source that resolves an apparent conflict by scope.

    This is intentionally domain-agnostic: it requires one auditable source to
    state at least two distinct applicability qualifiers, contrast them, and
    contain both a positive capability and a limitation. Mere topical overlap
    or a one-sided source cannot resolve a user-declared conflict.
    """
    if bundle is None:
        return None
    query_terms = set(re.findall(r"[a-z0-9]{4,}", (bundle.query or "").lower()))
    for source in bundle.sources:
        if source.source_type not in {"primary", "authoritative"}:
            continue
        text = f"{source.title}. {source.excerpt}"
        source_terms = set(re.findall(r"[a-z0-9]{4,}", text.lower()))
        qualifier_count = sum(bool(pattern.search(text)) for pattern in _SCOPE_QUALIFIERS)
        if (
            query_terms & source_terms
            and qualifier_count >= 2
            and _SCOPE_CONTRAST.search(text)
            and _POSITIVE_CAPABILITY.search(text)
            and _LIMITED_CAPABILITY.search(text)
        ):
            return source
    return None


def calibrate_bundle_for_search_plan(plan: SearchPlan, bundle: EvidenceBundle) -> EvidenceBundle:
    """Fail closed when a reported conflict has no auditable source pair.

    One related page cannot resolve or even represent both sides of an A/B
    conflict.  Keep retrieval telemetry, but prevent a one-sided discovery
    lead from reaching the final answer or source chips as conflict evidence.
    """
    if plan.trigger == "CURRENT_CLAIM" and _VERSION_CLAIM.search(bundle.query or ""):
        strong = [
            source for source in bundle.sources
            if source.source_type in {"primary", "authoritative"}
        ]
        if not any(_claim_evidence_span(bundle.query, source.excerpt) for source in strong):
            return replace(
                bundle,
                status="UNVERIFIED",
                sources=[],
                sufficient=False,
                review_disposition="CALIBRATED_FALLBACK",
                missing_evidence=list(dict.fromkeys([
                    *bundle.missing_evidence,
                    "强来源正文没有直接出现可核对的当前版本事实",
                ])),
                conclusion_hint="来源入口相关，但当前版本号缺少可审计的正文证据。",
            )
    if plan.trigger != "SOURCE_CONFLICT" or bundle.source_relationship == "CONFLICTING":
        return bundle
    if _scope_resolution_source(bundle) is not None:
        return replace(
            bundle,
            status="SUPPORTED",
            sufficient=True,
            review_disposition="ANSWER",
            missing_evidence=[],
            relationship_notes=[
                "权威来源明确区分了两个说法的适用范围；这是范围错位，不是同一条件下的事实冲突。",
                *bundle.relationship_notes,
            ],
            conclusion_hint="权威来源已给出可审计的范围区分；应解释两个说法各自适用的条件。",
        )
    missing = list(dict.fromkeys([
        *bundle.missing_evidence,
        "尚未取得可分别对应冲突两边的可比来源",
    ]))
    return replace(
        bundle,
        status="UNVERIFIED",
        sources=[],
        sufficient=False,
        review_disposition="CALIBRATED_FALLBACK",
        missing_evidence=missing,
        conclusion_hint="用户指出了冲突，但尚未取得可分别对应两边的可比来源；保持不确定。",
    )


def build_search_grounding(plan: SearchPlan, bundle: EvidenceBundle | None) -> dict[str, Any]:
    sources = []
    support = _support_status(bundle) if bundle is not None else "NOT_SEARCHED"
    declared_conflict = plan.trigger == "SOURCE_CONFLICT"
    scope_resolution = declared_conflict and _scope_resolution_source(bundle) is not None
    if declared_conflict and not scope_resolution:
        support = "CONFLICTING"
    bundle_sources = list(bundle.sources if bundle is not None else [])
    trusted_sources = [
        source for source in bundle_sources
        if source.source_type in {"primary", "authoritative", "secondary"}
    ]
    display_sources = trusted_sources or bundle_sources[:1]
    for source in display_sources[:3]:
        parsed = urlparse(source.url)
        source_id = "source-" + sha256(source.url.encode("utf-8")).hexdigest()[:12]
        source_support = (
            "DISCOVERY_ONLY" if source.source_type in {"community", "unknown"}
            else "CORROBORATES" if source.source_type == "secondary" and support == "SUPPORTED"
            else support
        )
        sources.append({
            "source_id": source_id,
            "title": source.title,
            "publisher": source.source,
            "url": source.url,
            "locator": parsed.path + (("#" + parsed.fragment) if parsed.fragment else ""),
            "authority": source.source_type,
            "trust_reason": source.trust_reason,
            "freshness": _freshness(source, plan),
            "claim_provenance": {
                "contract_version": CLAIM_CONTRACT_VERSION,
                "claim": bundle.query,
                "support_status": source_support,
                "evidence_span": _claim_evidence_span(bundle.query, source.excerpt),
            },
        })
    attempts = bundle.provider_attempts if bundle is not None else []
    invocation_count = sum(1 for item in attempts if item.outcome != "SKIPPED_DUPLICATE")
    conflict_status = "NONE"
    if scope_resolution:
        conflict_status = "RESOLVED_SCOPE_MISMATCH"
    elif declared_conflict or (bundle is not None and bundle.source_relationship == "CONFLICTING"):
        conflict_status = "UNRESOLVED"
    conflict_notes = list(bundle.relationship_notes if bundle is not None else [])
    if declared_conflict and not scope_resolution and not any("用户" in note and "冲突" in note for note in conflict_notes):
        conflict_notes.insert(0, "用户提供或指出了相互冲突的说法；现有证据尚未形成可审计的消解结论。")
    review_status = (
        "PASS" if bundle is not None and bundle.sufficient and conflict_status != "UNRESOLVED"
        else "CALIBRATED" if bundle is not None and (bundle.sources or bundle.search_executed)
        else "NOT_RUN"
    )
    effective_sufficient = bool(bundle and bundle.sufficient and conflict_status != "UNRESOLVED")
    evidence_status = (
        "CONFLICTING" if conflict_status == "UNRESOLVED"
        else bundle.status if bundle is not None else "NOT_SEARCHED"
    )
    review_disposition = (
        "CALIBRATED_FALLBACK" if conflict_status == "UNRESOLVED"
        else bundle.review_disposition if bundle is not None else "NO_SEARCH"
    )
    return {
        "contract_version": "darya_session_grounded_knowledge_v1",
        "decision": plan.decision,
        "decision_reason": plan.reason,
        "trigger": plan.trigger,
        "search_invocation_count": invocation_count,
        "search_executed": bool(bundle and bundle.search_executed),
        "provider": bundle.provider if bundle is not None else None,
        "provider_lifecycle": {
            "attempts": [asdict(item) for item in attempts],
            "retrieval_rounds": [asdict(item) for item in (bundle.retrieval_rounds if bundle is not None else [])],
            "search_budget": (bundle.search_budget if bundle is not None else {}),
        },
        "evidence_status": evidence_status,
        "review_disposition": review_disposition,
        "sufficient": effective_sufficient,
        "sources": sources,
        "conflict": {
            "status": conflict_status,
            "notes": conflict_notes,
            "resolution_policy": "比较来源权威性、日期、适用范围与版本；无法消解时保持不确定。",
        },
        "grounding_scope": "SESSION_THREAD",
        "installed_domain_pack_created": False,
        "durable_memory_write": False,
        "formal_learning_evidence_created": False,
        "mastery_or_route_write": False,
        "blackboard_search_owner": "DARYA_CONVERSATION",
        "game_integration_seam": "GAME_CONTEXT_TO_DARYA_CONVERSATION_TO_SEARCH_GROUNDING",
        "review": {
            "status": review_status,
            "claim_support_status": support,
            "anti_hallucination_gate": "ENFORCED",
        },
        "collaboration_flow": [
            {"role": "DIAGNOSIS_LEARNER_CONTEXT", "status": "completed"},
            {"role": "SEARCH_RETRIEVAL_EVIDENCE_COLLECTION", "status": "completed" if bundle else "skipped"},
            {"role": "DOMAIN_EXPERT_GENERATION", "status": "bounded_by_evidence" if bundle else "trusted_context"},
            {"role": "REVIEW_VERIFICATION_CORRECTION", "status": review_status.lower()},
            {"role": "DARYA_SYNTHESIS_DECISION", "status": "ready"},
        ],
    }


def build_search_grounding_directive(grounding: dict[str, Any]) -> str:
    if grounding.get("decision") == "SEARCH_NOT_NEEDED":
        return "本轮 Search 判定为不需要；直接自然回答，不要声称查过资料。"
    scope_note = (
        "这是 SESSION/THREAD grounded knowledge context，不是已安装 Domain Pack，"
        "不得声称安装、永久记忆或更新掌握度。"
    )
    conflict_note = (
        " 用户提供或指出的两个说法仍未被证据层消解；必须明确保留冲突，不能随机选边。"
        if (grounding.get("conflict") or {}).get("status") == "UNRESOLVED" else ""
    )
    if (grounding.get("conflict") or {}).get("status") == "RESOLVED_SCOPE_MISMATCH":
        conflict_note = " 两个说法的表面冲突已由权威证据按适用范围消解；必须用人话说明各自条件，不能说成谁绝对对或错。"
    return (
        scope_note
        + " 检索已经在本轮完成；最终回答使用完成态陈述，禁止再说‘我帮你搜一下’、‘稍等’或把已完成检索描述成未来动作。"
        + " 先用证据支持的部分形成有用解释；未覆盖的专业主张必须明确标为尚未核实。"
        + " Search 结果必须先经过现有 Review/Trust 门禁，不能绕过验证直接写入最终答案。"
        + conflict_note
    )
