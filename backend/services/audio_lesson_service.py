"""Audio lessons — LLM script (audio-lesson-script skill) + existing Edge TTS.

The lesson is a real playable audio artifact: spoken-style segments are
generated from a source artifact's own content, synthesized through the
existing synthesize_speech pipeline, and served from /static.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import GeneratedItem
from services.extract_llm_json import extract_json_object
from services.skill_library import get_skill, skill_directive
from services.workspace_artifact_versions import ensure_initial_version

logger = logging.getLogger(__name__)

STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"


class AudioLessonError(RuntimeError):
    code = "AUDIO_LESSON_ERROR"


async def generate_audio_lesson(
    db: AsyncSession, *, user_id: int, thread_id: str = "",
    source_artifact_id: int | None = None, topic: str = "",
    llm: Any | None = None,
) -> GeneratedItem:
    """Generate one audio lesson from a source artifact or a topic."""
    from services.flashcard_service import artifact_source_text

    source_text = ""
    if source_artifact_id:
        row = await db.get(GeneratedItem, int(source_artifact_id))
        if row is None or row.user_id != int(user_id):
            raise AudioLessonError("源产物不存在或无权访问。")
        source_text = artifact_source_text(row)
        topic = topic or row.title
    if not source_text and not str(topic or "").strip():
        raise AudioLessonError("需要提供源产物或主题，不能凭空生成音频课。")

    skill = get_skill("audio-lesson-script")
    if skill is None:
        raise AudioLessonError("audio-lesson-script 技能包缺失。")
    if llm is None:
        from config import settings
        if str(settings.LLM_PROVIDER).lower() == "mock":
            raise AudioLessonError("音频课生成需要配置真实模型（当前为 mock 模式）。")
        from services.llm_service_real import get_llm_service
        llm = await get_llm_service()

    prompt = {
        "skill": skill_directive([skill]),
        "topic": str(topic or "")[:200],
        "source_material": source_text[:7000] or None,
        "requirement": "依据 skill 与源材料生成音频课讲稿；输出 JSON。",
    }
    from services.llm_service_real import extract_llm_text
    raw = await llm.chat(
        messages=[{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
        temperature=0.3, max_tokens=3000, use_cache=False, write_cache=False,
    )
    parsed = extract_json_object(extract_llm_text(raw))
    title, segments = _validated_segments((parsed or {}), fallback_topic=str(topic or "这一课"))

    from integrations.cloud_video.media_pipeline import media_duration_sec, synthesize_speech
    audio_dir = STATIC_ROOT / "audio"
    audio_path = audio_dir / f"audio-lesson-{uuid4().hex[:12]}.mp3"
    speech_text = "\n\n".join(str(segment["speech"]) for segment in segments)
    try:
        await synthesize_speech(speech_text, audio_path)
    except Exception as exc:  # noqa: BLE001 - honest failure, no silent placeholder
        logger.warning("audio lesson TTS failed: %s", exc)
        raise AudioLessonError("语音合成失败，请稍后重试。") from exc
    try:
        duration = round(float(media_duration_sec(audio_path)), 1)
    except Exception:
        duration = max(60, len(speech_text) // 4)

    item = GeneratedItem(
        user_id=int(user_id), type="study_audio_lesson",
        title=title[:200],
        chapter=str(topic or "")[:120] or None,
        content=json.dumps({"transcript": speech_text, "segments": segments}, ensure_ascii=False),
        raw_data={
            "kind": "audio_lesson", "status": "ready", "thread_id": str(thread_id or ""),
            "source_artifact_id": int(source_artifact_id) if source_artifact_id else None,
            "audio_url": f"/static/audio/{audio_path.name}",
            "duration_sec": duration,
            "generation_provenance": {"mode": "skill_llm", "skills": ["audio-lesson-script"]},
        },
    )
    db.add(item)
    await db.flush()
    await ensure_initial_version(db, item)
    await db.commit()
    await db.refresh(item)
    return item


def _validated_segments(parsed: dict[str, Any], *, fallback_topic: str) -> tuple[str, list[dict[str, str]]]:
    title = str(parsed.get("title") or f"音频课：{fallback_topic}").strip() or f"音频课：{fallback_topic}"
    raw_segments = parsed.get("segments")
    if not isinstance(raw_segments, list) or not (3 <= len(raw_segments) <= 12):
        raise AudioLessonError("模型未返回有效的讲稿分段。")
    segments: list[dict[str, str]] = []
    for entry in raw_segments:
        if not isinstance(entry, dict):
            raise AudioLessonError("讲稿分段格式无效。")
        speech = str(entry.get("speech") or "").strip()
        if not speech:
            raise AudioLessonError("讲稿分段内容缺失。")
        segments.append({
            "heading": str(entry.get("heading") or "")[:80],
            "speech": speech[:1200],
        })
    return title, segments
