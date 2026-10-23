"""Isolated conversational profile onboarding built on workspace task/chat storage."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.normalized_learner_state import preference_tokens

from database.models import ChatSession, StudentProfile, WorkspaceAgentTask
from services.cache_service import CacheService
from services.tide_long_term_memory import persist_observed_world_model_claims

logger = logging.getLogger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task] = set()


PROFILE_ONBOARDING_MODE = "profile_onboarding"

QUESTIONS = (
    {
        "key": "learning_goal",
        "label": "学习目标",
        "prompt": "先从最重要的开始：这段时间，你最想通过学习解决什么问题？",
        "clarify": "我还不想替你下结论。可以更具体一点吗，比如想学会什么、完成什么，或为哪件事做准备？",
    },
    {
        "key": "current_stage",
        "label": "当前阶段",
        "prompt": "你现在处在什么学习阶段？也可以告诉我专业、年级或目前的工作方向。",
        "clarify": "这部分我没有听清。你可以只说年级、专业，或目前正在做的方向。",
    },
    {
        "key": "knowledge_baseline",
        "label": "已有基础",
        "prompt": "关于你想学的主题，你已经接触过哪些内容？哪些部分还比较陌生？",
        "clarify": "没关系，我们不做考试。只要说一个会的点和一个不熟的点，就足够我安排起点。",
    },
    {
        "key": "learning_preference",
        "label": "学习偏好",
        "prompt": "什么样的讲法更适合你：先看例子、先懂原理、边做边学，还是别的方式？",
        "clarify": "如果还拿不准，可以回想一次学得比较顺的经历，当时是怎么看、怎么练的？",
    },
    {
        "key": "study_rhythm",
        "label": "学习节奏",
        "prompt": "你通常什么时候方便学习？一次大概能投入多久？",
        "clarify": "给一个大致范围就可以，比如工作日晚上半小时，或周末集中学习。",
    },
    {
        "key": "motivation",
        "label": "持续动力",
        "prompt": "最后一个问题：什么样的进展最能让你愿意继续学下去？",
        "clarify": "可以从这些方向里选一个来描述：完成作品、看到进度、通过测评，或真正解决一个问题。",
    },
)

UNCERTAIN_ANSWERS = {
    "不知道", "不清楚", "不确定", "没想好", "随便", "都可以", "无所谓", "不知道呢", "不太确定"
}


def _normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _is_supported_answer(value: str) -> bool:
    text = _normalise_text(value)
    return len(text) >= 2 and text not in UNCERTAIN_ANSWERS


def _confidence(value: str) -> float:
    text = _normalise_text(value)
    if len(text) >= 24:
        return 0.9
    if len(text) >= 10:
        return 0.8
    return 0.65


def _summary(entries: dict[str, dict[str, Any]]) -> str:
    lines = []
    for question in QUESTIONS:
        entry = entries.get(question["key"])
        if entry and entry.get("value"):
            lines.append(f"{question['label']}：{entry['value']}")
    return "\n".join(lines)


def _remaining_keys(entries: dict[str, dict[str, Any]]) -> list[str]:
    """还未填写的画像槽位（保持 QUESTIONS 顺序）。"""
    return [
        question["key"]
        for question in QUESTIONS
        if not str((entries.get(question["key"]) or {}).get("value") or "").strip()
    ]


async def _llm_onboarding_assist(
    entries: dict[str, dict[str, Any]],
    user_text: str,
    remaining_keys: list[str],
) -> dict[str, Any] | None:
    """LLM 语义层：从用户原话中抽取可填槽位，并生成上下文化的下一问。

    程序仍拥有字段清单、确认、持久化；LLM 只做理解/追问/消歧。
    任何失败都返回 None，由调用方回退到固定提示语，保证流程可用。
    """
    if not remaining_keys:
        return None
    try:
        from services.llm_service_real import get_llm_service
        llm = await get_llm_service()
    except Exception:
        return None

    labels = {question["key"]: question["label"] for question in QUESTIONS}
    filled_lines = [
        f"- {labels[key]}：{str(entry.get('value'))[:120]}"
        for key, entry in entries.items()
        if key in labels and str(entry.get("value") or "").strip()
    ]
    remaining_lines = [f"- {key}（{labels[key]}）" for key in remaining_keys]
    prompt = (
        "你是学习产品「柿子智学」里的引导角色柿小吉，正在用对话了解一位新同学。\n"
        "已知信息：\n" + ("\n".join(filled_lines) if filled_lines else "- （暂无）") + "\n"
        "还需要了解的槽位（只能从这些 key 中选择）：\n" + "\n".join(remaining_lines) + "\n\n"
        "这位同学刚刚说：\n「" + user_text[:800] + "」\n\n"
        "请严格输出 JSON（不要 Markdown 代码块）：\n"
        "{\n"
        '  "extracted": {"<槽位key>": "<从原话中提取的对应内容>"},\n'
        '  "next_question": "<一句中文追问>"\n'
        "}\n"
        "规则：\n"
        "- extracted 只允许使用上方列出的槽位 key；只提取原话明确表达的内容，禁止推测或编造；没有可提取就留空对象。\n"
        "- 每个提取值不超过 120 字，用同学的原意概括。\n"
        "- next_question 针对" + f"「{remaining_keys[0]}（{labels[remaining_keys[0]]}）」" + "发问，"
        "要自然地接住同学刚说的话（可以提到他的目标或处境），不超过 60 字，一次只问一件事。\n"
        "- 如果同学表达了与剩余槽位相关的担忧，可以在追问前先简短回应。"
    )
    try:
        result = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=600,
        )
        raw = result.get("content", "") if isinstance(result, dict) else str(result)
        raw = str(raw).strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            start, end = raw.find("{"), raw.rfind("}") + 1
            if start < 0 or end <= start:
                return None
            payload = json.loads(raw[start:end])
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    extracted_raw = payload.get("extracted")
    extracted: dict[str, str] = {}
    if isinstance(extracted_raw, dict):
        for key, value in extracted_raw.items():
            if key in remaining_keys and isinstance(value, str):
                text = _normalise_text(value)[:200]
                if _is_supported_answer(text):
                    extracted[key] = text
    next_question = _normalise_text(payload.get("next_question") or "")
    if not (4 <= len(next_question) <= 200):
        next_question = ""
    return {"extracted": extracted, "next_question": next_question}


def _queue_background(coro) -> None:
    task = asyncio.create_task(coro)
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)


def _snapshot(task: WorkspaceAgentTask, reply: str | None = None) -> dict[str, Any]:
    slots = dict(task.slots or {})
    current = int(slots.get("question_index", 0))
    total = len(QUESTIONS)
    response = {
        "task_id": task.task_id,
        "thread_id": task.thread_id,
        "task_type": PROFILE_ONBOARDING_MODE,
        "state": task.state,
        "reply": reply if reply is not None else str(slots.get("last_reply", "")),
        "progress": {
            "current": min(current, total),
            "total": total,
            "percent": round(min(current, total) / total * 100),
        },
        "draft": dict(slots.get("entries") or {}),
        "summary": str(slots.get("summary") or ""),
        "attachments": list(slots.get("attachments") or []),
    }
    return response


async def store_onboarding_attachment(
    db: AsyncSession,
    user_id: int,
    task_id: str,
    file_name: str,
    file_type: str,
    text: str,
    page_count: int | None = None,
    section_count: int = 0,
    preview: str = "",
) -> dict[str, Any]:
    """Store attachment metadata in the task's slots. Text is truncated for storage."""
    task = await _task_for_user(db, user_id, task_id)
    slots = dict(task.slots or {})
    attachments: list[dict[str, Any]] = list(slots.get("attachments") or [])
    import uuid as _uuid
    att_id = _uuid.uuid4().hex[:12]
    stored_text = text[:3000] if len(text) > 3000 else text
    attachment = {
        "id": att_id,
        "file_name": file_name[:200],
        "file_type": file_type,
        "text": stored_text,
        "char_count": len(text),
        "page_count": page_count,
        "section_count": section_count,
        "preview": preview[:200],
        "created_at": datetime.utcnow().isoformat(),
    }
    attachments.append(attachment)
    slots["attachments"] = attachments
    task.slots = slots
    task.version += 1
    await db.commit()
    return attachment


async def delete_onboarding_attachment(
    db: AsyncSession,
    user_id: int,
    task_id: str,
    attachment_id: str,
) -> bool:
    """Delete an attachment from the task's slots. Returns True if deleted."""
    task = await _task_for_user(db, user_id, task_id)
    slots = dict(task.slots or {})
    attachments: list[dict[str, Any]] = list(slots.get("attachments") or [])
    before = len(attachments)
    attachments = [a for a in attachments if a.get("id") != attachment_id]
    if len(attachments) == before:
        return False
    slots["attachments"] = attachments
    task.slots = slots
    task.version += 1
    await db.commit()
    return True


async def _task_for_user(db: AsyncSession, user_id: int, task_id: str) -> WorkspaceAgentTask:
    result = await db.execute(
        select(WorkspaceAgentTask).where(WorkspaceAgentTask.task_id == task_id)
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise LookupError("画像任务不存在")
    if task.user_id != user_id or task.agent_type != PROFILE_ONBOARDING_MODE:
        raise PermissionError("无权访问该画像任务")
    return task


async def _append_message(
    db: AsyncSession,
    task: WorkspaceAgentTask,
    role: str,
    content: str,
) -> None:
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.user_id == task.user_id,
            ChatSession.thread_id == task.thread_id,
        )
    )
    session = result.scalar_one()
    messages = list(session.messages or [])
    messages.append({
        "role": role,
        "content": content,
        "task_type": PROFILE_ONBOARDING_MODE,
        "created_at": datetime.utcnow().isoformat(),
    })
    session.messages = messages
    session.message_count = len(messages)
    session.last_message_at = datetime.utcnow()


async def start_profile_onboarding(db: AsyncSession, user_id: int) -> dict[str, Any]:
    result = await db.execute(
        select(WorkspaceAgentTask)
        .where(
            WorkspaceAgentTask.user_id == user_id,
            WorkspaceAgentTask.agent_type == PROFILE_ONBOARDING_MODE,
            WorkspaceAgentTask.state.in_(("active", "suspended", "awaiting_confirmation")),
        )
        .order_by(desc(WorkspaceAgentTask.id))
        .limit(1)
    )
    task = result.scalar_one_or_none()
    if task is not None:
        if task.state == "suspended":
            task.state = "active"
            slots = dict(task.slots or {})
            remaining = _remaining_keys(dict(slots.get("entries") or {}))
            if remaining:
                prompt = next(q["prompt"] for q in QUESTIONS if q["key"] == remaining[0])
                reply = "我们从刚才停下的地方继续。" + prompt
            else:
                reply = "我们从刚才停下的地方继续。"
            slots["last_reply"] = reply
            task.slots = slots
            await _append_message(db, task, "assistant", reply)
            await db.commit()
        return _snapshot(task)

    task_id = f"profile_{uuid.uuid4().hex}"
    thread_id = f"profile_onboarding_{uuid.uuid4().hex}"
    opening = (
        "你好，我是 DARYA。我会用几个简短的问题了解你的目标、基础和习惯，"
        "只记录你明确告诉我的内容；不确定的地方，我会先向你确认。"
        + QUESTIONS[0]["prompt"]
    )
    task = WorkspaceAgentTask(
        task_id=task_id,
        user_id=user_id,
        thread_id=thread_id,
        agent_type=PROFILE_ONBOARDING_MODE,
        state="active",
        slots={
            "question_index": 0,
            "entries": {},
            "uncertain_attempts": {},
            "last_reply": opening,
            "summary": "",
        },
        version=1,
        execution_key=task_id,
    )
    session = ChatSession(
        user_id=user_id,
        thread_id=thread_id,
        messages=[{
            "role": "assistant",
            "content": opening,
            "task_type": PROFILE_ONBOARDING_MODE,
            "created_at": datetime.utcnow().isoformat(),
        }],
        message_count=1,
        mode=PROFILE_ONBOARDING_MODE,
        is_active=True,
    )
    db.add_all((task, session))
    await db.commit()
    await db.refresh(task)
    return _snapshot(task, opening)


async def get_profile_onboarding(
    db: AsyncSession,
    user_id: int,
    task_id: str,
) -> dict[str, Any]:
    return _snapshot(await _task_for_user(db, user_id, task_id))


async def send_profile_onboarding_message(
    db: AsyncSession,
    user_id: int,
    task_id: str,
    message: str,
    source_type: str = "user_text",
    source_metadata: dict | None = None,
) -> dict[str, Any]:
    task = await _task_for_user(db, user_id, task_id)
    if task.state != "active":
        raise ValueError("画像任务当前不接受新的回答")
    text = _normalise_text(message)
    if not text:
        raise ValueError("回答不能为空")
    slots = dict(task.slots or {})
    await _append_message(db, task, "user", text)

    entries = dict(slots.get("entries") or {})
    remaining = _remaining_keys(entries)

    # LLM 语义理解：从原话中抽取所有可填槽位 + 生成上下文化追问
    assist = await _llm_onboarding_assist(entries, text, remaining)
    extracted = dict((assist or {}).get("extracted") or {})
    for key, value in extracted.items():
        entries[key] = {
            "value": value,
            "source_text": text[:500],
            "source_type": source_type,
            "source_metadata": source_metadata,
            "confidence": 0.8,
            "confirmed": False,
        }

    if not extracted:
        # 语义层没有可提取内容：保持原有确定性行为（当前题 = 第一个未填槽位）
        question = next(q for q in QUESTIONS if q["key"] in remaining)
        if not _is_supported_answer(text):
            attempts = dict(slots.get("uncertain_attempts") or {})
            attempts[question["key"]] = int(attempts.get(question["key"], 0)) + 1
            slots["uncertain_attempts"] = attempts
            reply = question["clarify"]
            slots["last_reply"] = reply
            task.slots = slots
            task.version += 1
            await _append_message(db, task, "assistant", reply)
            await db.commit()
            return _snapshot(task, reply)
        entries[question["key"]] = {
            "value": text,
            "source_text": text,
            "source_type": source_type,
            "source_metadata": source_metadata,
            "confidence": _confidence(text),
            "confirmed": False,
        }

    remaining_after = _remaining_keys(entries)
    slots["entries"] = entries
    slots["question_index"] = len(QUESTIONS) - len(remaining_after)

    if not remaining_after:
        task.state = "awaiting_confirmation"
        slots["summary"] = _summary(entries)
        reply = "这是我目前对你的初步了解。请确认或修改后再保存：\n" + slots["summary"]
    else:
        next_key = remaining_after[0]
        fixed_prompt = next(q["prompt"] for q in QUESTIONS if q["key"] == next_key)
        llm_question = str((assist or {}).get("next_question") or "")
        # LLM 追问需针对当前该问的槽位，否则回退固定提示
        reply = llm_question or fixed_prompt
        if len(extracted) > 1:
            reply = f"我记下了 {len(extracted)} 件事。" + reply
    slots["last_reply"] = reply
    task.slots = slots
    task.version += 1
    await _append_message(db, task, "assistant", reply)
    await db.commit()
    return _snapshot(task, reply)


def _extract_identity(entries: dict[str, dict[str, Any]]) -> tuple[str | None, str | None]:
    text = str((entries.get("current_stage") or {}).get("value") or "")
    grade_match = re.search(r"(大[一二三四]|研[一二三]|研究生|博士|已工作|工作中)", text)
    major_match = re.search(r"([\u4e00-\u9fffA-Za-z]{2,24})(?:专业)", text)
    grade = grade_match.group(1) if grade_match else None
    major = major_match.group(1) if major_match else None
    return major, grade


def _build_normalized_profile(
    entries: dict[str, dict[str, Any]],
    summary: str,
) -> dict[str, Any]:
    """从 onboarding 原始证据中提取规范化画像字段。
    只提取能从文本中确定性推断的字段，未知字段设为 null。
    """
    def _value(key: str) -> str | None:
        entry = entries.get(key) or {}
        v = (entry.get("value") or "").strip()
        return v if v else None

    goal_text = _value("learning_goal") or ""
    stage_text = _value("current_stage") or ""
    baseline_text = _value("knowledge_baseline") or ""
    preference_text = _value("learning_preference") or ""
    rhythm_text = _value("study_rhythm") or ""
    motivation_text = _value("motivation") or ""

    # 统一 taxonomy；不再把宽泛的“看”误判为 visual。
    preferred_modalities = preference_tokens(preference_text)

    # 确定性提取：学习时间
    import re as _re
    daily_minutes: int | None = None
    rhythm_val = _value("study_rhythm") or ""
    # 匹配 "30分钟" / "2小时" / "1.5h" 等
    hour_match = _re.search(r"(\d+(?:\.\d+)?)\s*(?:小时|h|hour)", rhythm_val)
    min_match = _re.search(r"(\d+)\s*(?:分钟|min)", rhythm_val)
    if hour_match:
        daily_minutes = int(float(hour_match.group(1)) * 60)
    elif min_match:
        daily_minutes = int(min_match.group(1))
    preferred_period: str | None = None
    if any(w in (rhythm_text or "") for w in ("早上", "上午", "早晨")):
        preferred_period = "morning"
    elif any(w in (rhythm_text or "") for w in ("下午",)):
        preferred_period = "afternoon"
    elif any(w in (rhythm_text or "") for w in ("晚上", "夜里", "睡前")):
        preferred_period = "evening"
    elif any(w in (rhythm_text or "") for w in ("周末",)):
        preferred_period = "weekend"

    # 确定性提取：动机类型
    primary_motivation: str | None = None
    mt = motivation_text or ""
    if any(w in mt for w in ("工作", "求职", "面试", "就业", "转行", "工程师", "岗位")):
        primary_motivation = "job_hunting"
    elif any(w in mt for w in ("研究", "科研", "论文", "学术")):
        primary_motivation = "research"
    elif any(w in mt for w in ("考试", "考研", "考证", "通过")):
        primary_motivation = "exam"
    elif any(w in mt for w in ("好奇", "兴趣", "了解", "想学")):
        primary_motivation = "curiosity"

    # 确定性提取：能力水平
    current_level: dict[str, str | None] = {
        "python": None,
        "ml": None,
    }
    bl = baseline_text or ""
    if any(w in bl for w in ("不会", "零基础", "没学过", "完全不懂", "新手")):
        current_level["python"] = "beginner"
        current_level["ml"] = "beginner"
    if any(w in bl for w in ("学过", "了解", "知道", "会一点", "入门")):
        if not current_level.get("python"):
            current_level["python"] = "basic"
        if not current_level.get("ml"):
            current_level["ml"] = "basic"

    normalized = {
        "learning_goal": {
            "primary": goal_text[:200] if goal_text else None,
            "status": "confirmed" if goal_text else "unknown",
        },
        "current_level": current_level,
        "interest_areas": [],
        "preferred_modalities": preferred_modalities,
        "study_rhythm": {
            "preferred_period": preferred_period,
            "daily_minutes": daily_minutes,
            "weekly_days": None,
        },
        "motivation": {
            "primary": primary_motivation,
            "description": motivation_text[:120] if motivation_text else None,
        },
        "strengths": [],
        "difficulties": [],
        "current_focus": goal_text[:120] if goal_text else None,
        "interaction_preferences": {
            "explanation_style": preferred_modalities[0] if preferred_modalities else None,
            "language": "zh-CN",
        },
        "self_description": {
            "stage": stage_text[:120] if stage_text else None,
            "baseline": baseline_text[:200] if baseline_text else None,
            "preference": preference_text[:200] if preference_text else None,
            "rhythm": rhythm_text[:120] if rhythm_text else None,
            "motivation": motivation_text[:120] if motivation_text else None,
        },
    }
    return normalized


def _compute_completeness(entries: dict[str, dict[str, Any]]) -> float:
    """计算画像完整度：已回答且有实质内容的题目数 / 总题目数"""
    total = len(QUESTIONS)
    answered = sum(
        1 for q in QUESTIONS
        if (entries.get(q["key"]) or {}).get("value", "").strip()
    )
    return round(answered / total, 2) if total else 0


def _build_ai_summary(normalized: dict[str, Any], summary: str) -> dict[str, Any]:
    """从规范化画像生成 AI 精简摘要。"""
    return {
        "profile_version": -1,  # 由调用方填充
        "learning_goal": normalized["learning_goal"]["primary"],
        "current_level": normalized["current_level"],
        "preferred_modalities": normalized["preferred_modalities"],
        "study_rhythm": normalized["study_rhythm"],
        "motivation": {"primary": normalized["motivation"]["primary"]},
        "strengths": normalized["strengths"],
        "difficulties": normalized["difficulties"],
        "current_focus": normalized["current_focus"],
        "interaction_preferences": normalized["interaction_preferences"],
    }


def enrich_profile_from_normalized(profile_data: dict[str, Any]) -> dict[str, Any]:
    """统一画像适配器：当旧结构化字段为空时，从 normalized_profile 提取数据填充。

    所有下游模块（学习路径、画像分析、AI对话等）通过 load_existing_profile()
    间接调用此函数，确保不各自理解画像 Schema。
    """
    pd = profile_data.get("profile_data") or {}
    normalized = pd.get("normalized_profile") if isinstance(pd, dict) else None

    if not isinstance(normalized, dict):
        return profile_data

    # behavior_pattern fallback：学习时段/频率
    if not profile_data.get("behavior_pattern"):
        rhythm = normalized.get("study_rhythm") or {}
        bp: dict[str, Any] = {}
        daily = rhythm.get("daily_minutes")
        if isinstance(daily, (int, float)) and daily > 0:
            bp["avg_session_minutes"] = int(daily)
            bp["weekly_hours"] = round(daily * 5 / 60, 1)  # 假设每周5天
        period = rhythm.get("preferred_period")
        if period:
            bp["preferred_period"] = period
        if bp:
            profile_data["behavior_pattern"] = bp

    # motivation_profile fallback：学习动机
    if not profile_data.get("motivation_profile"):
        mot = normalized.get("motivation") or {}
        goal = normalized.get("learning_goal") or {}
        mp: dict[str, Any] = {}
        if mot.get("primary"):
            mp["primary"] = mot["primary"]
        if mot.get("description"):
            mp["description"] = mot["description"]
        if goal.get("primary"):
            mp["career_target"] = goal["primary"]
        if mp:
            profile_data["motivation_profile"] = mp

    # skill_matrix 和 cognitive_profile 不自动填充：
    # 旧结构需要 wizard/profile_analyzer 独立填充，错误的映射比空值更危险。

    return profile_data


async def _write_profile(
    db: AsyncSession,
    user_id: int,
    status: str,
    entries: dict[str, dict[str, Any]],
    summary: str,
) -> StudentProfile:
    result = await db.execute(select(StudentProfile).where(StudentProfile.user_id == user_id))
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = StudentProfile(user_id=user_id)
        db.add(profile)

    # 1. 保存原始证据
    profile_data = dict(profile.profile_data or {})
    profile_data[PROFILE_ONBOARDING_MODE] = {
        "status": status,
        "entries": entries,
        "summary": summary,
        "updated_at": datetime.utcnow().isoformat(),
    }

    if status == "completed":
        # 2. 生成规范化画像
        normalized = _build_normalized_profile(entries, summary)
        completeness = _compute_completeness(entries)
        profile_data["normalized_profile"] = normalized
        profile_data["completeness"] = completeness

        # 3. 确定性字段映射到专用列
        major, grade = _extract_identity(entries)
        if major:
            profile.major = major
        if grade:
            profile.grade_level = grade
        goal = entries.get("learning_goal") or {}
        if goal.get("value"):
            profile.target_role = str(goal["value"])[:100]

        # 4. 结构化列保持 null，不写入非结构化文本
        # cognitive_profile, behavior_pattern, motivation_profile 保持原样
        # 它们由后续的 wizard/profile_analyzer/学习行为系统独立填充

        confidences = [float(item.get("confidence", 0)) for item in entries.values()]
        profile.confidence_score = sum(confidences) / len(confidences) if confidences else 0
        profile.profile_version = max(int(profile.profile_version or 0) + 1, 1)
    else:
        # 暂停/跳过：保存进度但不标记完成
        profile_data["completeness"] = _compute_completeness(entries)
        if profile.confidence_score is None:
            profile.confidence_score = 0
        profile.profile_version = max(int(profile.profile_version or 0), 1)

    profile.profile_data = profile_data
    profile.last_updated_from = PROFILE_ONBOARDING_MODE
    return profile


async def _seed_graph_after_confirm(
    user_id: int,
    entries: dict[str, dict[str, Any]],
    summary: str,
) -> None:
    """画像确认后播种正式学习图谱。

    只能调用现有 Formal LearningGraph 编排器，不能回退到旧骨架：
    首页、导师、评估器和“我的学习”需要共享同一个带 external_id / revision
    的正式图谱，才能让后续真实作答进入可信评估与路线更新。节点仍从
    planned 开始，绝不由画像写入掌握度。失败只记日志，不影响画像确认。
    """
    try:
        from database import AsyncSessionLocal
        from services.learning_graph_service import list_user_graphs
        from services.learning_graph_orchestrator_integration import build_formal_request, orchestrate_and_save
    except Exception:
        logger.exception("[onboarding] graph seed imports unavailable")
        return

    goal = str((entries.get("learning_goal") or {}).get("value") or "").strip()
    if not goal:
        return
    try:
        async with AsyncSessionLocal() as session:
            if await list_user_graphs(session, user_id):
                return
            seed_id = f"onboarding-{user_id}-{uuid.uuid4().hex[:12]}"
            request = await build_formal_request(
                session,
                user_id=user_id,
                thread_id=f"profile_onboarding_{user_id}",
                request_id=seed_id,
                # The formal graph contract uses user_goal as the canonical topic
                # for fallback node titles. Keep it concise; confirmed profile
                # evidence is already persisted and consumed by Tutor/UI without
                # turning a long profile summary into a graph node name.
                user_goal=goal[:500],
                domain=goal[:100],
                source_material_ids=[],
                locale="zh-CN",
                update_mode="CREATE",
            )
            public = await orchestrate_and_save(
                session,
                user_id=user_id,
                request=request,
                idempotency_key=seed_id,
            )
            logger.info("[onboarding] seeded formal graph %s for user %s", public.get("graph_id"), user_id)
    except Exception:
        logger.exception("[onboarding] graph seed failed for user %s", user_id)


async def confirm_profile_onboarding(
    db: AsyncSession,
    user_id: int,
    task_id: str,
    edits: dict[str, str] | None = None,
) -> dict[str, Any]:
    task = await _task_for_user(db, user_id, task_id)
    if task.state != "awaiting_confirmation":
        raise ValueError("请先完成画像对话再确认")
    slots = dict(task.slots or {})
    entries = dict(slots.get("entries") or {})
    for key, value in (edits or {}).items():
        text = _normalise_text(value)
        if key in {question["key"] for question in QUESTIONS} and text:
            original = dict(entries.get(key) or {})
            entries[key] = {
                "value": text,
                "source_text": original.get("source_text", text),
                "confidence": 1.0,
                "confirmed": True,
            }
    for key, entry in entries.items():
        confirmed = dict(entry)
        confirmed["confirmed"] = True
        entries[key] = confirmed
    summary = _summary(entries)
    slots.update({"entries": entries, "summary": summary, "last_reply": "我已经记下这份初步画像。以后你可以在学习画像中继续修改。"})
    task.slots = slots
    task.state = "completed"
    task.version += 1
    await _write_profile(db, user_id, "completed", entries, summary)
    await _append_message(db, task, "assistant", slots["last_reply"])
    await db.commit()
    try:
        await CacheService.invalidate_profile(user_id)
    except Exception:
        pass
    # 学习事实链路：画像确认后播种正式图谱骨架（后台任务，不阻塞确认响应）
    _queue_background(_seed_graph_after_confirm(user_id, entries, summary))
    return _snapshot(task, slots["last_reply"])


async def pause_profile_onboarding(db: AsyncSession, user_id: int, task_id: str) -> dict[str, Any]:
    task = await _task_for_user(db, user_id, task_id)
    if task.state not in {"active", "awaiting_confirmation"}:
        raise ValueError("画像任务当前不能暂停")
    slots = dict(task.slots or {})
    slots["last_reply"] = "已经替你保存到这里。之后可以从学习画像继续。"
    task.slots = slots
    task.state = "suspended"
    task.version += 1
    await _write_profile(db, user_id, "suspended", dict(slots.get("entries") or {}), str(slots.get("summary") or ""))
    await db.commit()
    try:
        await CacheService.invalidate_profile(user_id)
    except Exception:
        pass
    return _snapshot(task, slots["last_reply"])


async def skip_profile_onboarding(db: AsyncSession, user_id: int, task_id: str) -> dict[str, Any]:
    task = await _task_for_user(db, user_id, task_id)
    if task.state in {"completed", "skipped"}:
        return _snapshot(task)
    slots = dict(task.slots or {})
    entries = dict(slots.get("entries") or {})
    summary = _summary(entries) if entries else ""
    slots["last_reply"] = (
        "已直接开始。刚才明确说过的普通身份与目标会作为可纠正的观察信息保留，不会被当成外部核验或正式学习判断。"
        if entries else "已跳过初步画像。我不会根据没有确认的信息替你下结论。"
    )
    task.slots = slots
    task.state = "skipped"
    task.version += 1
    await _write_profile(db, user_id, "skipped", entries, summary)
    if entries:
        source_messages = list(dict.fromkeys(
            str(entry.get("source_text") or "").strip()
            for entry in entries.values()
            if str(entry.get("source_text") or "").strip()
        ))
        current_message = " ".join(source_messages)
        claims = []
        for key, kind in (("current_stage", "IDENTITY"), ("learning_goal", "GOAL"),
                          ("learning_preference", "PREFERENCE"), ("study_rhythm", "CONSTRAINT")):
            entry = dict(entries.get(key) or {})
            value = str(entry.get("value") or "").strip()
            span = str(entry.get("source_text") or "").strip()
            if value and span:
                claims.append({
                    "kind": kind,
                    "value": value,
                    "source_ref": span,
                    "confidence": float(entry.get("confidence") or 0.8),
                })
        await persist_observed_world_model_claims(
            db,
            user_id=user_id,
            thread_id=task.thread_id,
            source_message_id=f"{task.task_id}:partial",
            current_message=current_message,
            claims=claims,
        )
    await db.commit()
    try:
        await CacheService.invalidate_profile(user_id)
    except Exception:
        pass
    return _snapshot(task, slots["last_reply"])
