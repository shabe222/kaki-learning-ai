"""对话历史加载/保存服务"""
from __future__ import annotations
import logging
import json
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database.models import ChatSession

logger = logging.getLogger(__name__)


def is_legacy_thread(thread_id: str) -> bool:
    """Legacy records are browse-only and never workspace conversation context."""
    normalized = (thread_id or "").strip().lower()
    return not normalized or normalized == "default" or normalized.startswith("qa-")


async def load_chat_history(db: AsyncSession, user_id: int, thread_id: str) -> list[dict]:
    """从数据库加载历史对话消息"""
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.user_id == user_id,
            ChatSession.thread_id == thread_id
        ).order_by(ChatSession.last_message_at.desc()).limit(1)
    )
    session = result.scalar_one_or_none()
    if session and session.messages:
        messages = session.messages
        # 只取最近20条，避免 token 爆炸
        recent = [dict(row) for row in (messages[-20:] if len(messages) > 20 else messages)]
        for row in recent:
            action = row.get("conversation_action") or {}
            result = action.get("learning_result") if isinstance(action, dict) else None
            if isinstance(result, dict) and result.get("source") == "server_judged_first_attempts":
                row["content"] = str(row.get("content") or "") + "\n[服务端作答回执，不是用户自述] " + json.dumps(result, ensure_ascii=False)
        logger.info(f"[Context] 历史消息加载 user_id={user_id} thread={thread_id} count={len(recent)}")
        return recent

    logger.info(f"[Context] 无历史对话 user_id={user_id} thread={thread_id}")
    return []


async def get_thread_style(db: AsyncSession, user_id: int, thread_id: str):
    result = await db.execute(
        select(ChatSession.response_style).where(
            ChatSession.user_id == user_id,
            ChatSession.thread_id == thread_id,
        )
    )
    return result.scalar_one_or_none()


async def set_thread_style(db: AsyncSession, user_id: int, thread_id: str, style: str) -> None:
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.user_id == user_id,
            ChatSession.thread_id == thread_id,
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        session = ChatSession(user_id=user_id, thread_id=thread_id, messages=[])
        db.add(session)
    session.response_style = style
    await db.commit()


async def save_chat_session(db: AsyncSession, user_id: int, thread_id: str,
                             history: list, message: str, assistant_reply: str = None):
    """保存对话会话（用户消息 + 助手回复）"""
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.user_id == user_id,
            ChatSession.thread_id == thread_id
        )
    )
    session = result.scalar_one_or_none()

    new_messages = [{"role": "user", "content": message}]
    if assistant_reply:
        new_messages.append({"role": "assistant", "content": assistant_reply})

    if session:
        existing = session.messages or []
        session.messages = existing + new_messages
        session.message_count = len(session.messages)
        session.last_message_at = datetime.utcnow()
    else:
        session = ChatSession(
            user_id=user_id,
            thread_id=thread_id,
            messages=list(history) + new_messages,
            message_count=len(history) + len(new_messages),
            last_message_at=datetime.utcnow()
        )
        db.add(session)

    await db.commit()
    from services.proactive_tutor import update_contact_preference_from_user_text
    await update_contact_preference_from_user_text(db, user_id=user_id, text=message)


async def append_chat_message(db: AsyncSession, user_id: int, thread_id: str,
                              role: str, content: str, message_id: str = None,
                              metadata: dict | None = None) -> None:
    """Append one durable message without trusting client-provided history."""
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.user_id == user_id,
            ChatSession.thread_id == thread_id
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        session = ChatSession(user_id=user_id, thread_id=thread_id, messages=[])
        db.add(session)
    messages = list(session.messages or [])
    message = {"role": role, "content": content}
    if message_id:
        message["id"] = message_id
    for key, value in (metadata or {}).items():
        if value is not None and value != []:
            message[key] = value
    messages.append(message)
    session.messages = messages
    session.message_count = len(messages)
    session.last_message_at = datetime.utcnow()
    await db.commit()
    if role == "user":
        from services.proactive_tutor import update_contact_preference_from_user_text
        await update_contact_preference_from_user_text(db, user_id=user_id, text=content)
    if role == "user" and message_id:
        # Import locally to keep chat persistence independent from impression models at module load.
        from services.impression_service import observe_user_message
        await observe_user_message(
            db,
            user_id=user_id,
            thread_id=thread_id,
            message_id=message_id,
            text=content,
            observed_at=session.last_message_at,
        )
