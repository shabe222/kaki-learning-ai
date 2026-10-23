"""Phase 2A confirmed, structured user-memory rules and persistence."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import UserMemory


ALLOWED_MEMORY_KEYS = {
    "response_style",
    "content_format",
    "explanation_language",
    "teaching_method",
    "difficulty_preference",
    "learning_goal",
    "general_preference",
}

ENUM_VALUES = {
    "response_style": {"concise", "normal", "detailed"},
    "content_format": {"text", "visual", "video", "code", "practice_first", "mixed"},
    "explanation_language": {"zh-CN", "en", "bilingual"},
    "teaching_method": {
        "direct", "socratic", "feynman", "example_first", "step_by_step",
        "hands_on", "theory_first",
    },
    "difficulty_preference": {"basic", "intermediate", "advanced", "adaptive"},
}

SENSITIVE_OR_TEMPORARY = (
    "今天有点累", "今天很累", "心情", "生病", "健康", "宗教", "政治",
    "身份证", "手机号", "住址", "讨厌某某", "讨厌老师",
)

EXPLICIT_MEMORY_TERMS = (
    "请记住", "帮我记下来", "帮我记下", "记下来", "把这个偏好记住", "默认", "记住我",
)

MEMORY_ACTION_RE = re.compile(r"(?:记住|记下来|记下|以后按.{0,30}来)")

MEMORY_CATEGORY_BY_KEY = {
    "response_style": "communication_preference",
    "content_format": "learning_preference",
    "explanation_language": "learning_preference",
    "teaching_method": "learning_preference",
    "difficulty_preference": "learning_preference",
    "learning_goal": "learning_goal",
    "general_preference": "general_preference",
}


def is_explicit_memory_instruction(message: str) -> bool:
    text = (message or "").strip()
    return any(term in text for term in EXPLICIT_MEMORY_TERMS) or bool(MEMORY_ACTION_RE.search(text))


def _bounded_text(value: Any, field: str, required: bool = True) -> Optional[str]:
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise ValueError("%s must be text" % field)
    cleaned = value.strip()
    if required and not cleaned:
        raise ValueError("%s is required" % field)
    if len(cleaned) > 120:
        raise ValueError("%s is too long" % field)
    return cleaned or None


def validate_memory_value(key: str, value: Any) -> Any:
    if key not in ALLOWED_MEMORY_KEYS:
        raise ValueError("memory key is not allowed")
    if isinstance(value, dict) and key != "learning_goal":
        allowed_fields = {
            "category", "description", "raw_text", "source", "confidence", "preset",
        }
        if set(value.keys()) - allowed_fields:
            raise ValueError("explicit memory contains unknown fields")
        category = _bounded_text(value.get("category"), "category")
        description = _bounded_text(value.get("description"), "description")
        raw_text = value.get("raw_text")
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ValueError("raw_text is required")
        raw_text = raw_text.strip()
        if len(raw_text) > 300:
            raise ValueError("raw_text is too long")
        if value.get("source") != "explicit":
            raise ValueError("explicit memory source is required")
        if value.get("confidence") != 1 and value.get("confidence") != 1.0:
            raise ValueError("explicit memory confidence must be 1")
        preset = value.get("preset")
        if preset is not None:
            if key not in ENUM_VALUES or preset not in ENUM_VALUES[key]:
                raise ValueError("memory preset is not allowed")
        result = {
            "category": category,
            "description": description,
            "raw_text": raw_text,
            "source": "explicit",
            "confidence": 1.0,
        }
        if preset is not None:
            result["preset"] = preset
        return result
    if key in ENUM_VALUES:
        if value not in ENUM_VALUES[key]:
            raise ValueError("memory value is not allowed")
        return value
    if not isinstance(value, dict):
        raise ValueError("learning_goal must be an object")
    goal_fields = {"subject", "topic", "target", "deadline"}
    explicit_fields = {"category", "description", "raw_text", "source", "confidence"}
    if set(value.keys()) - goal_fields - explicit_fields:
        raise ValueError("learning_goal contains unknown fields")
    result = {
        "subject": _bounded_text(value.get("subject"), "subject"),
        "topic": _bounded_text(value.get("topic"), "topic"),
        "target": _bounded_text(value.get("target"), "target"),
        "deadline": _bounded_text(value.get("deadline"), "deadline", required=False),
    }
    if value.get("source") == "explicit":
        raw_text = value.get("raw_text")
        if not isinstance(raw_text, str) or not raw_text.strip() or len(raw_text.strip()) > 300:
            raise ValueError("raw_text is required")
        if value.get("confidence") != 1 and value.get("confidence") != 1.0:
            raise ValueError("explicit memory confidence must be 1")
        result.update({
            "category": _bounded_text(value.get("category"), "category"),
            "description": _bounded_text(value.get("description"), "description"),
            "raw_text": raw_text.strip(),
            "source": "explicit",
            "confidence": 1.0,
        })
    return result


def _explicit_description(text: str, candidate: Optional[Dict[str, Any]]) -> str:
    if candidate:
        description = str(candidate.get("display_text") or text).strip()
    elif any(term in text for term in ("AI味", "AI 味", "模板", "机械", "套话")):
        qualities = []
        if "自然" in text:
            qualities.append("自然")
        if "直接" in text:
            qualities.append("直接")
        natural = "、".join(qualities) or "自然"
        description = "减少 AI 模板感，回答%s，避免机械套话" % natural
    else:
        description = text.strip().rstrip("。！？")
    return description[:120]


def build_explicit_memory_candidate(text: str, thread_id: str,
                                    source_message_id: str) -> Optional[Dict[str, Any]]:
    """Build a confirmed manual memory without applying inference confidence thresholds."""
    normalized = (text or "").strip()
    if not normalized or any(term in normalized for term in SENSITIVE_OR_TEMPORARY):
        return None

    detected = detect_memory_candidate(normalized, thread_id, source_message_id)
    if detected:
        key = detected["key"]
        category = detected.get("category") or MEMORY_CATEGORY_BY_KEY.get(key, "general_preference")
        preset = detected.get("value") if key in ENUM_VALUES else None
    elif any(term in normalized for term in (
        "回答", "对话", "交流", "表达", "语气", "风格", "AI味", "AI 味", "模板", "套话", "机械",
    )):
        key = "response_style"
        category = "communication_preference"
        preset = "normal"
    else:
        key = "general_preference"
        category = "general_preference"
        preset = None

    description = _explicit_description(normalized, detected)
    metadata = {
        "category": category,
        "description": description,
        "raw_text": normalized,
        "source": "explicit",
        "confidence": 1.0,
    }
    if key == "learning_goal" and detected:
        value = dict(detected["value"])
        value.update(metadata)
    else:
        value = metadata
        if preset is not None:
            value["preset"] = preset
    return {
        "key": key,
        "value": validate_memory_value(key, value),
        "memory_type": "learning_goal" if key == "learning_goal" else "preference",
        "display_text": description,
        "evidence_text": normalized[:160],
        "source_thread_id": thread_id,
        "source_message_id": source_message_id,
        "schema_version": 2,
        "authorization": "explicit",
        "category": category,
        "priority": "primary",
    }


def _candidate(key: str, value: Any, display_text: str, message: str,
               thread_id: str, message_id: str) -> Dict[str, Any]:
    return {
        "key": key,
        "value": validate_memory_value(key, value),
        "memory_type": "learning_goal" if key == "learning_goal" else "preference",
        "display_text": display_text,
        "evidence_text": message.strip()[:160],
        "source_thread_id": thread_id,
        "source_message_id": message_id,
        "schema_version": 1,
        "authorization": "explicit" if is_explicit_memory_instruction(message) else "confirm",
        "category": MEMORY_CATEGORY_BY_KEY.get(key, "preference"),
        "priority": "primary",
    }


def _semantic_preference_candidates(text: str, thread_id: str, message_id: str,
                                    allow_inferred_subject: bool = False) -> List[Dict[str, Any]]:
    """Extract bounded preference dimensions without depending on one exact sentence."""
    normalized = (text or "").strip()
    if not normalized or any(term in normalized for term in SENSITIVE_OR_TEMPORARY):
        return []
    preference_signal = allow_inferred_subject or is_explicit_memory_instruction(normalized) or any(
        term in normalized for term in (
            "我喜欢", "我偏好", "我更喜欢", "比较喜欢", "喜欢用",
            "我想要你", "希望你", "请你说", "请你回答",
        )
    )
    if not preference_signal:
        return []

    candidates: List[Dict[str, Any]] = []
    if any(term in normalized for term in (
        "边做边学", "动手实践", "实际操作", "亲自操作", "亲自运行", "运行代码理解",
    )):
        candidates.append(_candidate(
            "teaching_method", "hands_on",
            "你喜欢边做边学，通过实际操作或运行代码理解概念",
            normalized, thread_id, message_id,
        ))
    elif re.search(r"(?:看完|先看|先学).{0,16}(?:理论|定义).{0,16}(?:再|然后).{0,12}(?:练习|实践|操作)", normalized):
        candidates.append(_candidate(
            "teaching_method", "theory_first",
            "你学习时喜欢先看理论，再通过练习巩固",
            normalized, thread_id, message_id,
        ))
    elif re.search(r"(?:先看|先从|先用).{0,10}(?:实际)?(?:例子|实例|案例).{0,18}(?:再|然后).{0,12}(?:抽象|定义|理论)", normalized):
        candidates.append(_candidate(
            "teaching_method", "example_first",
            "你学习新概念时喜欢先看实例，再理解抽象定义",
            normalized, thread_id, message_id,
        ))

    if any(term in normalized for term in (
        "简短直接", "简短回答", "回答简短", "简洁一点", "简短一点", "不要太长",
        "更简洁", "简洁回答", "说话简洁", "回答简洁", "简洁些",
    )):
        candidates.append(_candidate(
            "response_style", "concise", "交流时偏好简短直接的回答",
            normalized, thread_id, message_id,
        ))
    elif any(term in normalized for term in ("详细回答", "讲详细一点", "完整分析")):
        candidates.append(_candidate(
            "response_style", "detailed", "交流时偏好完整详细的回答",
            normalized, thread_id, message_id,
        ))
    return candidates


def _candidate_order(candidates: List[Dict[str, Any]], focus_text: str) -> List[Dict[str, Any]]:
    learning_terms = ("学习", "方法", "概念", "理论", "例子", "实例", "练习", "实践", "代码")
    communication_terms = ("回答", "交流", "表达", "简短", "详细")
    learning_pos = min((focus_text.find(term) for term in learning_terms if term in focus_text), default=10 ** 6)
    communication_pos = min((focus_text.find(term) for term in communication_terms if term in focus_text), default=10 ** 6)
    primary_category = (
        "learning_preference" if learning_pos <= communication_pos else "communication_preference"
    )
    ordered = sorted(
        candidates,
        key=lambda item: (
            item.get("category") != primary_category,
            0 if item.get("category") == "learning_preference" else 1,
        ),
    )
    for index, item in enumerate(ordered):
        item["priority"] = "primary" if index == 0 else "secondary"
    return ordered


def resolve_memory_action_candidates(message: str, recent_messages: List[Dict[str, Any]],
                                     thread_id: str, source_message_id: str) -> List[Dict[str, Any]]:
    """Resolve an explicit remember action against current content or the latest conclusion."""
    text = (message or "").strip()
    if not is_explicit_memory_instruction(text):
        return []

    source_text = text
    focus_text = text
    focus_message_id = None
    candidates = _semantic_preference_candidates(text, thread_id, source_message_id)
    if not candidates:
        # “记住了吗”指向的是用户刚才表达的偏好。优先回看用户原话，
        # 不把助手的复述当成授权或唯一证据来源。
        latest_user = next((
            item for item in reversed(list(recent_messages or []))
            if item.get("role") == "user" and str(item.get("content") or "").strip()
        ), None)
        if latest_user:
            source_text = str(latest_user.get("content") or "").strip()
            focus_text = source_text
            focus_message_id = latest_user.get("id")
            candidates = _semantic_preference_candidates(
                source_text, thread_id, source_message_id, allow_inferred_subject=True,
            )
    if not candidates:
        latest_assistant = next((
            item for item in reversed(list(recent_messages or []))
            if item.get("role") == "assistant" and str(item.get("content") or "").strip()
        ), None)
        if latest_assistant:
            source_text = str(latest_assistant.get("content") or "").strip()
            focus_text = source_text
            focus_message_id = latest_assistant.get("id")
            candidates = _semantic_preference_candidates(
                source_text, thread_id, source_message_id, allow_inferred_subject=True,
            )

    deduped: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        candidate["authorization"] = "explicit"
        candidate["source_message_id"] = source_message_id
        candidate["evidence_text"] = source_text[:160]
        if focus_message_id:
            candidate["focus_message_id"] = focus_message_id
        deduped.setdefault(candidate["key"], candidate)
    return _candidate_order(list(deduped.values()), focus_text)


def build_saved_memory_confirmation(candidates: List[Dict[str, Any]]) -> str:
    """Confirm only structured values that the save operation actually accepted."""
    if not candidates:
        return "这次没有保存新的长期记忆。"
    ordered = sorted(candidates, key=lambda item: item.get("priority") != "primary")
    statements = [str(item.get("display_text") or "").strip().rstrip("。") for item in ordered]
    statements = [item for item in statements if item]
    if not statements:
        return "这次没有保存新的长期记忆。"
    confirmation = "记住了：" + statements[0] + "。"
    if len(statements) > 1:
        confirmation += "另外，" + "；".join(statements[1:]) + "。"
    return confirmation


def detect_memory_candidate(message: str, thread_id: str,
                            source_message_id: str) -> Optional[Dict[str, Any]]:
    text = (message or "").strip()
    if not text or any(term in text for term in SENSITIVE_OR_TEMPORARY):
        return None
    stable = any(term in text for term in ("以后", "更喜欢", "偏好", "请记住", "长期"))
    ordinary_preference = any(term in text for term in (
        "我喜欢", "比较喜欢", "我想要你", "希望你", "请你说", "请你回答",
        "长回答看着有点累", "长回答看着累",
    ))

    if (stable or ordinary_preference) and any(term in text for term in (
        "简短", "简洁", "更简洁", "简单一点", "不要太长", "不喜欢太长", "回答短", "长回答看着",
    )):
        return _candidate("response_style", "concise", "回答风格偏好为“简短”", text, thread_id, source_message_id)
    if stable and any(term in text for term in ("详细", "完整分析", "步骤完整")):
        return _candidate("response_style", "detailed", "回答风格偏好为“详细”", text, thread_id, source_message_id)
    if stable and any(term in text for term in ("正常回答", "正常长度")):
        return _candidate("response_style", "normal", "回答风格偏好为“正常”", text, thread_id, source_message_id)

    if stable or ordinary_preference:
        format_rules = [
            (("代码示例", "代码"), "code", "内容形式偏好为“代码”"),
            (("先看视频", "视频"), "video", "内容形式偏好为“视频”"),
            (("先做题", "先练习"), "practice_first", "内容形式偏好为“先练习”"),
            (("图示", "可视化"), "visual", "内容形式偏好为“图示”"),
        ]
        for terms, value, label in format_rules:
            if any(term in text for term in terms):
                return _candidate("content_format", value, label, text, thread_id, source_message_id)
        if "中英双语" in text or "双语" in text:
            return _candidate("explanation_language", "bilingual", "讲解语言偏好为“中英双语”", text, thread_id, source_message_id)
        if "中文" in text:
            return _candidate("explanation_language", "zh-CN", "讲解语言偏好为“中文”", text, thread_id, source_message_id)
        if "英文" in text or "英语讲解" in text:
            return _candidate("explanation_language", "en", "讲解语言偏好为“英文”", text, thread_id, source_message_id)
        method_rules = [
            ("苏格拉底", "socratic", "教学方式偏好为“苏格拉底式提问”"),
            ("费曼", "feynman", "教学方式偏好为“费曼学习法”"),
            ("先举例", "example_first", "教学方式偏好为“先举例”"),
            ("先看例子", "example_first", "教学方式偏好为“先举例”"),
            ("一步一步", "step_by_step", "教学方式偏好为“分步骤”"),
            ("直接回答", "direct", "教学方式偏好为“直接回答”"),
        ]
        for term, value, label in method_rules:
            if term in text:
                return _candidate("teaching_method", value, label, text, thread_id, source_message_id)

    if re.search(r"(我要|正在|目标是|准备|希望).*(英语四级|大学英语四级|CET-4)", text, re.I):
        return _candidate("learning_goal", {
            "subject": "英语",
            "topic": "大学英语四级",
            "target": "备考并通过英语四级",
            "deadline": None,
        }, "学习目标为“备考并通过英语四级”", text, thread_id, source_message_id)
    goal_match = re.search(r"(?:当前目标是|目标是|我要复习|正在学习)([^。！？!?]{2,40})", text)
    if goal_match:
        topic = goal_match.group(1).strip()
        return _candidate("learning_goal", {
            "subject": topic,
            "topic": topic,
            "target": "学习并掌握%s" % topic,
            "deadline": None,
        }, "学习目标为“%s”" % topic, text, thread_id, source_message_id)
    return None


async def list_confirmed_memories(db: AsyncSession, user_id: int) -> List[UserMemory]:
    result = await db.execute(
        select(UserMemory).where(
            UserMemory.user_id == user_id,
            UserMemory.status == "confirmed",
        ).order_by(UserMemory.updated_at.desc())
    )
    return list(result.scalars().all())


async def upsert_confirmed_memory(db: AsyncSession, user_id: int, key: str, value: Any,
                                  evidence_text: str, source_thread_id: str,
                                  source_message_id: str) -> UserMemory:
    validated = validate_memory_value(key, value)
    result = await db.execute(
        select(UserMemory).where(UserMemory.user_id == user_id, UserMemory.key == key)
    )
    item = result.scalar_one_or_none()
    if item is None:
        item = UserMemory(user_id=user_id, key=key)
        db.add(item)
    item.memory_type = "learning_goal" if key == "learning_goal" else "preference"
    item.value = validated
    item.status = "confirmed"
    item.evidence_text = (evidence_text or "").strip()[:160]
    item.source_thread_id = source_thread_id
    item.source_message_id = source_message_id
    item.schema_version = 2 if isinstance(validated, dict) and validated.get("source") == "explicit" else 1
    item.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(item)
    return item


async def persist_memory_action(db: AsyncSession, user_id: int, message: str,
                                recent_messages: List[Dict[str, Any]], thread_id: str,
                                source_message_id: str) -> Optional[Dict[str, Any]]:
    """Resolve, persist, then confirm one explicit memory action in that order."""
    candidates = resolve_memory_action_candidates(
        message, recent_messages, thread_id, source_message_id,
    )
    if not candidates:
        return None
    saved_items = []
    saved_candidates = []
    for candidate in candidates:
        item = await upsert_confirmed_memory(
            db=db,
            user_id=user_id,
            key=candidate["key"],
            value=candidate["value"],
            evidence_text=candidate["evidence_text"],
            source_thread_id=candidate["source_thread_id"],
            source_message_id=candidate["source_message_id"],
        )
        saved_items.append(item)
        saved_candidates.append(candidate)
    return {
        "items": saved_items,
        "candidates": saved_candidates,
        "confirmation": build_saved_memory_confirmation(saved_candidates),
    }


async def delete_confirmed_memory(db: AsyncSession, user_id: int, memory_id: int) -> bool:
    result = await db.execute(
        select(UserMemory).where(UserMemory.id == memory_id, UserMemory.user_id == user_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        return False
    await db.delete(item)
    await db.commit()
    return True
