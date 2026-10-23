"""Evidence-backed, non-sensitive AI impressions for the learning workspace."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import AIImpression, ChatSession


SUPPORTED_DIMENSIONS = {
    "response_preference",
    "learning_style",
    "difficulty_preference",
    "interaction_style",
    "goal_orientation",
    "task_execution_style",
    "verification_preference",
    "learning_pace",
    "content_preference",
    "metacognitive_behavior",
}

IMPRESSION_SCHEMA_VERSION = 2

SENSITIVE_TERMS = (
    "政治倾向", "政治立场", "宗教", "种族", "民族身份", "性取向", "性别认同",
    "诊断", "抑郁症", "焦虑症", "精神疾病", "人格障碍", "犯罪", "前科", "病史",
)

SENSITIVE_RE = re.compile(
    r"政治倾向|政治立场|宗教|种族|民族身份|性取向|性别认同|"
    r"诊断|抑郁症|焦虑症|精神疾病|人格障碍|犯罪|前科|病史|"
    r"穆斯林|伊斯兰|天主教|基督教|佛教徒|道教徒|无神论|"
    r"糖尿病|高血压|癌症|抑郁|焦虑|自闭症|双相|精神分裂|艾滋|肝炎|哮喘|"
    r"同性恋|双性恋|异性恋|跨性别|非二元|酷儿|"
    r"民主党|共和党|共产党|中共党员|保守派|自由派|投票给|"
    r"坐过牢|服过刑|被定罪|刑事拘留|"
    r"\breligion(?:s|ous)?\b|\bpolitic(?:s|al|ally)?\b|\bpolitical affiliation\b|"
    r"\brace\b|\bethnic(?:ity)?\b|\bmuslim\b|\bislam(?:ic)?\b|\bcatholic\b|"
    r"\bchristian\b|\bjewish\b|\bhindu\b|\bbuddhist\b|\batheist\b|"
    r"\bsexual(?:ity| orientation)?\b|\bgay\b|\blesbian\b|\bbisexual\b|"
    r"\bhomosexual\b|\bheterosexual\b|\btransgender\b|\bnon[- ]?binary\b|\bqueer\b|"
    r"\bdemocrat\b|\brepublican\b|\bcommunist\b|\bconservative\b|\bliberal\b|\bvote for\b|"
    r"\bcriminal(?: record)?\b|\bcrime record\b|\bconvict(?:ed|ion)?\b|\bfelon(?:y)?\b|"
    r"\bcancer\b|\bdiabet(?:es|ic)\b|\bdepress(?:ion|ed)\b|\banxi(?:ety|ous)\b|"
    r"\bautis(?:m|tic)\b|\bbipolar\b|\bschizophren(?:ia|ic)\b|\bHIV\b|\bAIDS\b|"
    r"\bdiagnos(?:is|ed)\b|\bmedical history\b|\bhealth condition\b|"
    r"\bI am (?:Black|White|Asian|Latino|Hispanic|Indigenous)\b",
    re.I,
)

EXPLICIT_STABLE_TERMS = ("以后", "长期", "总是", "我喜欢", "我偏好", "请记住", "更喜欢")


@dataclass(frozen=True)
class ImpressionRule:
    key: str
    dimension: str
    summary: str
    scope: str
    supporting: Tuple[str, ...]
    opposing: Tuple[str, ...] = ()
    supporting_summary: str = "近期行为支持这条印象"
    opposing_summary: str = "近期行为与这条印象不一致"
    profile_mapping_key: Optional[str] = None


RULES: Tuple[ImpressionRule, ...] = (
    ImpressionRule(
        "response_preference.concise", "response_preference",
        "你可能更喜欢简短、直接的回答", "AI 对话",
        ("回答短一点", "简短回答", "不要讲太多", "不要说太多", "直接告诉我结果", "一句一句聊"),
        ("讲详细一点", "详细说明", "展开讲", "完整分析"),
        "你要求回答更简短或更直接", "你本次要求更详细的说明",
        "response_style",
    ),
    ImpressionRule(
        "response_preference.detailed", "response_preference",
        "你可能更喜欢完整、细致的解释", "AI 对话",
        ("讲详细一点", "详细说明", "展开讲", "完整分析"),
        ("回答短一点", "不要讲太多", "直接告诉我结果"),
        "你要求提供更详细的说明", "你本次要求更简短的回答",
        "response_style",
    ),
    ImpressionRule(
        "learning_style.practice_first", "learning_style",
        "你可能更喜欢先练习，再结合结果看讲解", "练习与讲解",
        ("先做题", "先练习", "先出题", "做完再讲", "答完再讲"),
        ("先讲解", "先讲再练"),
        "你选择先练习后看讲解", "你本次选择先讲解",
        "teaching_method",
    ),
    ImpressionRule(
        "learning_style.example_first", "learning_style",
        "你可能更喜欢先看实例，再理解抽象定义", "学习方法",
        ("先看例子", "先看实例", "先看实际例子", "先从例子", "先从实例", "再看抽象定义"),
        ("先看理论再练习", "先学理论再实践"),
        "你明确选择先从实例进入新概念", "你本次明确选择先理解理论",
        "teaching_method",
    ),
    ImpressionRule(
        "learning_style.hands_on", "learning_style",
        "你可能更喜欢边做边学，通过实际操作理解概念", "学习方法",
        ("边做边学", "动手实践", "实际操作", "亲自操作", "亲自运行代码", "运行代码看看"),
        ("只看理论", "不想动手", "不要实践"),
        "你选择通过实际操作或运行代码理解内容", "你本次明确不希望动手实践",
        "teaching_method",
    ),
    ImpressionRule(
        "difficulty_preference.basic", "difficulty_preference",
        "在当前学习场景中，你似乎更偏好基础难度", "练习与讲解",
        ("简单一点", "基础一点", "最简单", "入门题"),
        ("难一点", "提高难度", "有挑战"),
        "你要求降低到基础难度", "你本次要求提高难度",
        "difficulty_preference",
    ),
    ImpressionRule(
        "difficulty_preference.advanced", "difficulty_preference",
        "在当前学习场景中，你似乎更偏好有挑战的难度", "练习与讲解",
        ("难一点", "提高难度", "有挑战", "进阶题"),
        ("简单一点", "基础一点", "最简单"),
        "你要求提高任务难度", "你本次要求降低难度",
        "difficulty_preference",
    ),
    ImpressionRule(
        "interaction_style.iterative", "interaction_style",
        "你可能更喜欢通过连续调整逐步得到结果", "AI 对话与任务",
        ("再改一下", "换一种", "再来一个", "继续修改", "按这个调整"),
        (), "你通过后续指令继续调整结果", profile_mapping_key="interaction_style",
    ),
    ImpressionRule(
        "goal_orientation.outcome", "goal_orientation",
        "从近期行为看，你似乎更关注可交付的结果", "学习任务",
        ("直接给结果", "最终结果", "可交付", "完成以后", "验收结果"),
        (), "你多次明确要求可检查的结果", profile_mapping_key="goal_orientation",
    ),
    ImpressionRule(
        "task_execution_style.confirm_then_run", "task_execution_style",
        "你可能更重视先确认要求，再执行任务", "学习任务",
        ("先确认", "确认参数", "不要自作主张", "不清楚就问", "先问我"),
        (), "你要求在执行前确认不明确的条件", profile_mapping_key="task_execution_style",
    ),
    ImpressionRule(
        "verification_preference.evidence_first", "verification_preference",
        "你可能更重视验证结果和可核对的证据", "学习任务",
        ("先测试", "验证一下", "验收", "给出证据", "亲自测试", "测试通过"),
        (), "你要求对结果进行测试或提供证据", profile_mapping_key="verification_preference",
    ),
    ImpressionRule(
        "learning_pace.stepwise", "learning_pace",
        "你可能更喜欢一次处理一个步骤", "学习与任务",
        ("一条一条来", "一步一步来", "一次一个", "一句一句聊"),
        ("一次全部", "一起完成"),
        "你要求按步骤逐项推进", "你本次要求一次性处理",
        "learning_pace",
    ),
    ImpressionRule(
        "content_preference.code", "content_preference",
        "你可能更喜欢用代码示例理解内容", "学习内容",
        ("给代码", "代码示例", "用 Python", "用代码说明"),
        (), "你选择使用代码示例", profile_mapping_key="content_format_preference",
    ),
    ImpressionRule(
        "content_preference.visual", "content_preference",
        "你可能更喜欢通过图解或可视化理解内容", "学习内容",
        ("用图解", "看图理解", "可视化讲解", "画个图", "思维导图"),
        ("不要图解", "不想看图"), "你选择图解或可视化表达", "你明确不希望使用图解",
        "content_format_preference",
    ),
    ImpressionRule(
        "content_preference.video", "content_preference",
        "你可能更喜欢通过视频理解内容", "学习内容",
        ("我喜欢看视频", "视频讲解", "用视频", "看个短视频"),
        ("不想看视频", "不要视频"), "你选择使用视频学习", "你明确不希望使用视频",
        "content_format_preference",
    ),
    ImpressionRule(
        "content_preference.reading", "content_preference",
        "你可能更喜欢通过阅读文字材料理解内容", "学习内容",
        ("我喜欢阅读", "读文章", "看文字材料", "通过阅读", "文字学习"),
        ("不想阅读", "不要文字材料"), "你选择通过阅读学习", "你明确不希望阅读材料",
        "content_format_preference",
    ),
    ImpressionRule(
        "content_preference.document", "content_preference",
        "你可能更喜欢结构化文档或讲义", "学习内容",
        ("我喜欢看讲义", "给我讲义", "结构化文档", "看文档", "整理成讲义"),
        ("不要讲义", "不想看文档"), "你选择结构化文档或讲义", "你明确不希望使用文档",
        "content_format_preference",
    ),
    ImpressionRule(
        "content_preference.practice", "content_preference",
        "你可能更喜欢通过练习巩固内容", "学习内容",
        ("我喜欢做题", "多做练习", "练着学", "先给我题", "用实践学"),
        ("不想做题", "不要练习"), "你选择通过练习学习", "你明确不希望继续练习",
        "exercise_type_preference",
    ),
    ImpressionRule(
        "content_preference.avoid_video", "content_preference",
        "你当前明确不希望使用视频", "学习内容",
        ("我不想看视频", "不要视频", "别给我视频"),
        (), "你明确排除了视频学习", profile_mapping_key="content_format_preference",
    ),
    ImpressionRule(
        "content_preference.avoid_document", "content_preference",
        "你当前明确不希望使用文档或讲义", "学习内容",
        ("我不想看文档", "不要讲义", "别给我讲义"),
        (), "你明确排除了文档学习", profile_mapping_key="content_format_preference",
    ),
    ImpressionRule(
        "content_preference.avoid_code", "content_preference",
        "你当前明确不希望使用代码", "学习内容",
        ("我不想看代码", "不要代码", "别用代码"),
        (), "你明确排除了代码示例", profile_mapping_key="content_format_preference",
    ),
    ImpressionRule(
        "content_preference.avoid_practice", "content_preference",
        "你当前明确不希望继续练习", "学习内容",
        ("我不想做题", "不要练习", "先别出题"),
        (), "你明确排除了练习", profile_mapping_key="exercise_type_preference",
    ),
    ImpressionRule(
        "content_preference.jade", "content_preference",
        "从近期对话看，你似乎对玉石相关内容更感兴趣", "玉石学习与练习",
        ("玉石", "和田玉", "翡翠"),
        ("不想再聊玉石", "不再学习玉石", "对玉石没兴趣"),
        "你多次询问玉石、和田玉或翡翠相关内容",
        "你明确表示暂时不再关注玉石相关内容",
        "content_topic_interest",
    ),
    ImpressionRule(
        "metacognitive_behavior.self_check", "metacognitive_behavior",
        "在当前学习场景中，你似乎会主动检查理解和结果", "学习过程",
        ("我哪里错了", "检查一下", "为什么错", "复盘", "我理解得对吗"),
        (), "你主动检查答案或理解是否正确", profile_mapping_key="metacognitive_behavior",
    ),
)


def _contains(text: str, terms: Sequence[str]) -> bool:
    return any(term in text for term in terms)


def contains_sensitive(text: str) -> bool:
    """Reject protected/sensitive traits in both supported UI languages."""
    return bool(SENSITIVE_RE.search(text or ""))


def _safe_message(text: str) -> bool:
    return bool(text.strip()) and not contains_sensitive(text)


def _is_explicit_stable(text: str) -> bool:
    return _contains(text, EXPLICIT_STABLE_TERMS) or bool(
        re.search(r"我.{0,16}(?:喜欢|偏好|更倾向)", text)
    )


def _parse_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    return fallback


def _collect_evidence(sessions: Iterable[ChatSession], rule: ImpressionRule) -> List[Dict[str, Any]]:
    evidence: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for session in sessions:
        fallback = session.last_message_at or session.created_at or datetime.utcnow()
        for index, message in enumerate(session.messages or []):
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            text = str(message.get("content") or "").strip()
            if not _safe_message(text):
                continue
            polarity = None
            summary = None
            if rule.opposing and _contains(text, rule.opposing):
                polarity, summary = "opposing", rule.opposing_summary
            elif _contains(text, rule.supporting):
                polarity, summary = "supporting", rule.supporting_summary
            if not polarity:
                continue
            source_id = str(message.get("id") or f"{session.thread_id}:{index}")
            observed_at = _parse_datetime(message.get("created_at"), fallback)
            evidence[(source_id, polarity)] = {
                "source_type": "conversation_behavior",
                "source_id": source_id,
                "thread_id": session.thread_id,
                "occurred_at": observed_at.isoformat(),
                "summary": summary,
                "polarity": polarity,
                "explicit": _is_explicit_stable(text),
            }
    return sorted(evidence.values(), key=lambda item: item["occurred_at"])


def _message_evidence(rule: ImpressionRule, text: str, source_id: str,
                      thread_id: str, observed_at: datetime) -> Optional[Dict[str, Any]]:
    if not _safe_message(text):
        return None
    if rule.opposing and _contains(text, rule.opposing):
        polarity, summary = "opposing", rule.opposing_summary
    elif _contains(text, rule.supporting):
        polarity, summary = "supporting", rule.supporting_summary
    else:
        return None
    return {
        "source_type": "conversation_behavior",
        "source_id": source_id,
        "thread_id": thread_id,
        "occurred_at": observed_at.isoformat(),
        "summary": summary,
        "polarity": polarity,
        "explicit": _is_explicit_stable(text),
    }


def _confidence(evidence: Sequence[Dict[str, Any]], now: datetime) -> float:
    supporting = [item for item in evidence if item["polarity"] == "supporting"]
    opposing = [item for item in evidence if item["polarity"] == "opposing"]
    confidence = 0.65 + min(0.25, 0.05 * len(supporting)) - min(0.45, 0.25 * len(opposing))
    if supporting:
        latest = max(_parse_datetime(item["occurred_at"], now) for item in supporting)
        inactive_days = max(0, (now - latest).days)
        confidence -= min(0.2, inactive_days / 3650.0)
    return round(max(0.05, min(0.95, confidence)), 2)


async def refresh_user_impressions(db: AsyncSession, user_id: int) -> List[AIImpression]:
    sessions_result = await db.execute(
        select(ChatSession).where(ChatSession.user_id == user_id).order_by(ChatSession.created_at.asc())
    )
    sessions = list(sessions_result.scalars().all())
    existing_result = await db.execute(select(AIImpression).where(AIImpression.user_id == user_id))
    existing = {item.impression_key: item for item in existing_result.scalars().all()}
    now = datetime.utcnow()

    for rule in RULES:
        evidence = _collect_evidence(sessions, rule)
        supporting = [item for item in evidence if item["polarity"] == "supporting"]
        explicit = any(item.get("explicit") for item in supporting)
        if not supporting:
            continue
        item = existing.get(rule.key)
        if item is None:
            item = AIImpression(user_id=user_id, impression_key=rule.key)
            db.add(item)
            existing[rule.key] = item
        if item.status in {"rejected", "confirmed_by_user"}:
            continue
        item.dimension = rule.dimension
        item.summary = rule.summary
        item.scope = rule.scope
        item.evidence_refs = evidence
        item.evidence_count = len(evidence)
        item.first_observed_at = _parse_datetime(evidence[0]["occurred_at"], now)
        item.last_observed_at = _parse_datetime(evidence[-1]["occurred_at"], now)
        item.confidence = _confidence(evidence, now)
        if any(ref["polarity"] == "opposing" for ref in evidence):
            item.status = "uncertain"
        elif explicit or len(supporting) >= 2:
            item.status = "active"
        else:
            item.status = "candidate"
        item.source_type = "ai_inference"
        item.confirmed_by_user = False
        item.profile_mapping_key = rule.profile_mapping_key
        item.schema_version = IMPRESSION_SCHEMA_VERSION
        item.updated_at = now

    await db.commit()
    result = await db.execute(
        select(AIImpression).where(AIImpression.user_id == user_id).order_by(AIImpression.impression_key.asc())
    )
    return list(result.scalars().all())


async def observe_user_message(db: AsyncSession, user_id: int, thread_id: str,
                               message_id: str, text: str,
                               observed_at: Optional[datetime] = None) -> List[AIImpression]:
    """Incrementally update only impression dimensions touched by one durable user message."""
    now = observed_at or datetime.utcnow()
    updated: List[AIImpression] = []
    for rule in RULES:
        evidence = _message_evidence(rule, text, str(message_id), thread_id, now)
        if evidence is None:
            continue
        result = await db.execute(select(AIImpression).where(
            AIImpression.user_id == user_id,
            AIImpression.impression_key == rule.key,
        ))
        item = result.scalar_one_or_none()
        if item is not None and item.status in {"rejected", "confirmed_by_user"}:
            continue
        # Opposing evidence may weaken an existing impression, but it must not
        # create a blank preference assertion out of nothing. Creating the ORM
        # row before this check leaves required fields unset and can violate the
        # AIImpression NOT NULL contract on a later flush.
        if item is None and evidence["polarity"] != "supporting":
            continue
        if item is None:
            item = AIImpression(user_id=user_id, impression_key=rule.key)
            db.add(item)

        refs = list(item.evidence_refs or [])
        refs_by_key = {(str(ref.get("source_id")), ref.get("polarity")): ref for ref in refs}
        refs_by_key[(evidence["source_id"], evidence["polarity"])] = evidence
        refs = sorted(refs_by_key.values(), key=lambda ref: ref["occurred_at"])
        supporting = [ref for ref in refs if ref["polarity"] == "supporting"]
        opposing = [ref for ref in refs if ref["polarity"] == "opposing"]
        if not supporting:
            continue

        item.dimension = rule.dimension
        item.summary = rule.summary
        item.scope = rule.scope
        item.evidence_refs = refs
        item.evidence_count = len(refs)
        item.first_observed_at = _parse_datetime(refs[0]["occurred_at"], now)
        item.last_observed_at = _parse_datetime(refs[-1]["occurred_at"], now)
        item.confidence = _confidence(refs, now)
        if opposing:
            item.status = "uncertain"
        elif evidence["explicit"] or len(supporting) >= 2:
            item.status = "active"
        else:
            item.status = "candidate"
        item.source_type = "ai_inference"
        item.confirmed_by_user = False
        item.profile_mapping_key = rule.profile_mapping_key
        item.schema_version = IMPRESSION_SCHEMA_VERSION
        item.updated_at = now
        updated.append(item)
    if updated:
        await db.commit()
        for item in updated:
            await db.refresh(item)
    return updated


async def list_user_impressions(db: AsyncSession, user_id: int) -> List[AIImpression]:
    result = await db.execute(
        select(AIImpression).where(AIImpression.user_id == user_id).order_by(AIImpression.updated_at.desc())
    )
    return list(result.scalars().all())


def build_relevant_impression_context(
    impressions: Iterable[AIImpression], current_message: str,
) -> List[Dict[str, Any]]:
    text = (current_message or "").strip()
    is_exam = bool(re.search(r"题|练习|考试|作答|难度|讲解", text))
    is_learning = is_exam or bool(re.search(r"学习|课程|知识|理解|复习", text))
    always_relevant = {"response_preference", "interaction_style"}
    exam_relevant = {"learning_style", "difficulty_preference", "content_preference", "learning_pace"}
    task_relevant = {"task_execution_style", "verification_preference", "goal_orientation"}
    allowed = set(always_relevant)
    if is_exam:
        allowed.update(exam_relevant)
    if is_learning:
        allowed.add("metacognitive_behavior")
    if re.search(r"生成|创建|修改|执行|任务|验证|测试", text):
        allowed.update(task_relevant)

    context = []
    for item in impressions:
        if item.dimension not in allowed or item.status in {"rejected", "uncertain"}:
            continue
        if item.status != "confirmed_by_user" and item.confidence < 0.75:
            continue
        context.append({
            "impression_key": item.impression_key,
            "dimension": item.dimension,
            "summary": item.summary,
            "confidence": item.confidence,
            "scope": item.scope,
            "confirmed_by_user": bool(item.confirmed_by_user),
            "source_type": item.source_type,
            "profile_mapping_key": item.profile_mapping_key,
        })
    context.sort(key=lambda item: (not item["confirmed_by_user"], item["dimension"]))
    return context
