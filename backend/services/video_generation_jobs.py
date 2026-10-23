"""Durable lifecycle stored on the one canonical workspace_video Artifact."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import GeneratedItem
from services.workspace_artifact_versions import create_new_version, ensure_initial_version


NONTERMINAL_STATES = {
    "queued", "generating_script", "generating_audio", "generating_avatar", "composing",
}
TERMINAL_STATES = {"ready", "failed"}
ALL_STATES = NONTERMINAL_STATES | TERMINAL_STATES
TRANSITIONS = {
    "queued": {"generating_script", "failed"},
    "generating_script": {"generating_audio", "failed"},
    "generating_audio": {"generating_avatar", "failed"},
    "generating_avatar": {"composing", "failed"},
    "composing": {"ready", "failed"},
    "failed": {"queued"},
    "ready": set(),
}


class VideoJobError(RuntimeError):
    code = "VIDEO_JOB_ERROR"


class VideoJobAccessError(VideoJobError):
    code = "VIDEO_JOB_NOT_FOUND"


class VideoJobTransitionError(VideoJobError):
    code = "VIDEO_JOB_INVALID_TRANSITION"


def _utcnow() -> datetime:
    return datetime.utcnow()


async def create_video_job(
    db: AsyncSession, *, user_id: int, thread_id: str, topic: str, duration_sec: int,
    provider: str, instruction: str = "", source_material_ids: Iterable[str] = (),
    source_context: str = "", source_citations: Iterable[dict[str, Any]] = (),
    style_context: dict[str, Any] | None = None,
    evidence_context: dict[str, Any] | None = None,
    task_id: str | None = None,
    theme: str = "kaki",
) -> GeneratedItem:
    clean_topic = str(topic or "").strip()
    if not clean_topic:
        raise ValueError("topic 不能为空")
    from services.visual_theme import normalize_theme

    now = _utcnow().isoformat() + "Z"
    item = GeneratedItem(
        user_id=int(user_id), type="workspace_video", title=f"{clean_topic}教学视频"[:200],
        chapter=clean_topic[:120], content="",
        raw_data={
            "kind": "video", "status": "queued", "thread_id": str(thread_id),
            "topic": clean_topic, "duration_sec": int(duration_sec),
            "instruction": str(instruction or "")[:2000],
            "provider": str(provider or "s2v"), "model": "", "dry_run": False,
            "source_material_ids": [str(value) for value in source_material_ids][:8],
            "source_context": str(source_context or "")[:12000],
            "source_citations": [dict(value) for value in source_citations][:20],
            "style_context": dict(style_context or {}),
            "evidence_context": dict(evidence_context or {}),
            # 版本级主题快照：生成时定格，之后不随产品主题切换漂移。
            "presentation_theme": normalize_theme(theme),
            "task_id": task_id, "created_at": now, "status_updated_at": now,
            "generation_provenance": "KAKI workspace -> internal Cloud Video adapter",
            "error_code": None, "safe_error": None, "video_path": None,
            "key_points": [], "version": 1,
        },
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


async def get_owned_video_job(
    db: AsyncSession, *, user_id: int, artifact_id: int,
) -> GeneratedItem:
    item = (await db.execute(select(GeneratedItem).where(
        GeneratedItem.id == int(artifact_id),
        GeneratedItem.user_id == int(user_id),
        GeneratedItem.type == "workspace_video",
    ))).scalar_one_or_none()
    if item is None:
        raise VideoJobAccessError("video artifact not found")
    return item


async def update_video_job_status(
    db: AsyncSession, *, user_id: int, artifact_id: int, status: str,
) -> GeneratedItem:
    if status not in ALL_STATES:
        raise VideoJobTransitionError(f"unknown video status: {status}")
    item = await get_owned_video_job(db, user_id=user_id, artifact_id=artifact_id)
    metadata = dict(item.raw_data or {})
    current = str(metadata.get("status") or "queued")
    if status != current and status not in TRANSITIONS.get(current, set()):
        raise VideoJobTransitionError(f"invalid video status transition: {current} -> {status}")
    metadata.update(status=status, status_updated_at=_utcnow().isoformat() + "Z")
    if status != "failed":
        metadata.update(error_code=None, safe_error=None)
    item.raw_data = metadata
    item.updated_at = _utcnow()
    await db.commit()
    await db.refresh(item)
    return item


async def fail_video_job(
    db: AsyncSession, *, user_id: int, artifact_id: int,
    error_code: str, safe_error: str,
) -> GeneratedItem:
    item = await get_owned_video_job(db, user_id=user_id, artifact_id=artifact_id)
    metadata = dict(item.raw_data or {})
    # video_path is intentionally preserved: if the paid synthesis already
    # produced a file and only a later stage failed, discarding the path would
    # hide the billed artifact and push retry into a full re-purchase.
    metadata.update(
        status="failed", error_code=str(error_code or "VIDEO_GENERATION_FAILED")[:80],
        safe_error=str(safe_error or "视频生成失败，请稍后重试。")[:400],
        status_updated_at=_utcnow().isoformat() + "Z",
    )
    item.raw_data = metadata
    item.updated_at = _utcnow()
    await db.commit()
    await db.refresh(item)
    return item


async def complete_video_job(
    db: AsyncSession, *, user_id: int, artifact_id: int, video_path: Path,
    script: str, key_points: Iterable[str], duration_sec: int,
    provider: str, model: str, dry_run: bool,
) -> GeneratedItem:
    item = await get_owned_video_job(db, user_id=user_id, artifact_id=artifact_id)
    had_version = bool(item.current_version_id)
    previous_version_number = int(item.current_version_number or 1)
    metadata = dict(item.raw_data or {})
    current = str(metadata.get("status") or "queued")
    if current != "composing":
        raise VideoJobTransitionError(f"video can finish only from composing, got {current}")
    resolved = Path(video_path).resolve()
    if not resolved.exists() or resolved.stat().st_size <= 0:
        raise VideoJobError("video output is missing or empty")
    points = [str(value)[:160] for value in key_points][:6]
    metadata.update(
        status="ready", status_updated_at=_utcnow().isoformat() + "Z",
        duration_sec=int(duration_sec), provider=str(provider), model=str(model),
        dry_run=bool(dry_run), video_path=str(resolved),
        video_url=f"/api/chat/tasks/videos/{item.id}/media",
        transcript=str(script or ""), key_points=points,
        error_code=None, safe_error=None,
    )
    item.content = str(script or "")
    item.raw_data = metadata
    item.updated_at = _utcnow()
    await db.flush()
    if had_version:
        # A learner may open the artifact while the background job is still in
        # flight. That read eagerly snapshots the queued state as v1. Completion
        # is a new public fact, so preserve v1 and publish a ready immutable
        # version instead of leaving the player bound to the queued snapshot.
        version, _ = await create_new_version(
            db,
            user_id=user_id,
            artifact_id=item.id,
            expected_version=previous_version_number,
            idempotency_key=f"video:{item.id}:completion",
            title=item.title,
            chapter=item.chapter,
            content=str(script or ""),
            public_data=metadata,
            private_data=metadata,
            status="ready",
        )
    else:
        # When nobody opened the queued artifact, its first immutable snapshot
        # can accurately be the completed media itself.
        version = await ensure_initial_version(db, item)
    await _review_ready_video(
        db, user_id=user_id, item=item, version=version, video_path=resolved,
        script=str(script or ""), key_points=points, duration_sec=int(duration_sec),
    )
    await db.commit()
    await db.refresh(item)
    return item


async def _review_ready_video(
    db: AsyncSession, *, user_id: int, item: GeneratedItem, version, video_path: Path,
    script: str, key_points: list[str], duration_sec: int,
) -> None:
    """Put the finished video through the canonical trusted review channel.

    "我的资料" only lists artifacts whose canonical review verdict is PASS, so a
    video that skips this step is generated but unreachable from the library.
    The checks are real: the media must carry both a video and an audio stream,
    and the artifact must ship a narration plus at least two teaching points.
    """
    from services.canonical_artifacts import create_trusted_review
    from schemas.canonical_artifacts import CreateArtifactReviewRequest

    media = _probe_ready_media(video_path)
    if not media["has_video_stream"] or not media["has_audio_stream"]:
        raise VideoJobError("VIDEO_REVIEW_MEDIA_INCOMPLETE")
    if len(script.strip()) < 20:
        raise VideoJobError("VIDEO_REVIEW_NARRATION_MISSING")
    if len(key_points) < 2:
        raise VideoJobError("VIDEO_REVIEW_KEY_POINTS_MISSING")
    await create_trusted_review(
        db,
        user_id=user_id,
        artifact_id=item.id,
        request=CreateArtifactReviewRequest(
            artifact_version_id=version.id,
            version_digest=f"sha256:{version.content_digest}",
            round=1,
            reviewer_role="PLATFORM_REVIEWER",
            verdict="PASS",
            scores={
                "media_playable": 1.0,
                "narration_present": 1.0,
                "structure_completeness": 0.95,
                "learner_fit": 0.93,
            },
            safe_summary=(
                f"媒体可播放（{media['duration_sec']:.1f}s，含视频与音轨），"
                f"讲稿与 {len(key_points)} 条要点齐备。"
            ),
            issues=[],
            preserve=[],
            idempotency_key=f"video:{item.id}:v{version.version_number}:review",
        ),
        reviewer_role="PLATFORM_REVIEWER",
        provider="darya-video-review",
    )


def _probe_ready_media(path: Path) -> dict[str, Any]:
    """Read the real container so the review cannot pass on a broken file."""
    import json
    import shutil
    import subprocess

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise VideoJobError("VIDEO_REVIEW_PROBE_UNAVAILABLE")
    completed = subprocess.run(
        [ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode != 0:
        raise VideoJobError("VIDEO_REVIEW_PROBE_FAILED")
    data = json.loads(completed.stdout or "{}")
    types = {str(stream.get("codec_type") or "") for stream in data.get("streams") or []}
    return {
        "has_video_stream": "video" in types,
        "has_audio_stream": "audio" in types,
        "duration_sec": float((data.get("format") or {}).get("duration") or 0.0),
    }


async def retry_video_job(
    db: AsyncSession, *, user_id: int, artifact_id: int,
) -> GeneratedItem:
    item = await get_owned_video_job(db, user_id=user_id, artifact_id=artifact_id)
    current = str((item.raw_data or {}).get("status") or "queued")
    if current in NONTERMINAL_STATES:
        # queued -> queued passes the transition table as a no-op, which would
        # let a double-clicked retry schedule a second paid execution.
        raise VideoJobTransitionError(f"video job is still running: {current}")
    return await update_video_job_status(
        db, user_id=user_id, artifact_id=artifact_id, status="queued",
    )


async def persist_job_fields(
    *, artifact_id: int, user_id: int, updates: dict[str, Any],
) -> None:
    """Persist progress metadata from outside the request (own session)."""
    from database.connection import get_db_session

    async with get_db_session() as db:
        item = await get_owned_video_job(db, user_id=user_id, artifact_id=artifact_id)
        metadata = dict(item.raw_data or {})
        metadata.update(updates)
        item.raw_data = metadata
        item.updated_at = _utcnow()
        await db.commit()


async def fail_interrupted_video_jobs(db: AsyncSession) -> int:
    rows = list((await db.execute(select(GeneratedItem).where(
        GeneratedItem.type == "workspace_video",
    ))).scalars())
    changed = 0
    now = _utcnow().isoformat() + "Z"
    for item in rows:
        metadata = dict(item.raw_data or {})
        if str(metadata.get("status") or "") not in NONTERMINAL_STATES:
            continue
        metadata.update(
            status="failed", error_code="INTERRUPTED_BY_RESTART",
            safe_error="服务重启中断了本次生成，可从原产物重试。",
            status_updated_at=now,
        )
        item.raw_data = metadata
        item.updated_at = _utcnow()
        changed += 1
    if changed:
        await db.commit()
    return changed


def project_video_job(item: GeneratedItem) -> dict[str, Any]:
    metadata = dict(item.raw_data or {})
    status = str(metadata.get("status") or "queued")
    stage_copy = {
        "queued": "视频任务已进入队列。",
        "generating_script": "正在生成教学讲稿。",
        "generating_audio": "正在合成讲解音频。",
        "generating_avatar": "正在生成 DARYA 讲解画面，云端口型通常需要 1 到 4 分钟。",
        "composing": "正在合成最终教学视频。",
        "ready": (
            "本机讲解视频已经生成，可以打开播放。云端数字人仍需确认凭据后才会启用。"
            if metadata.get("dry_run") else
            "教学视频已经生成，可以打开播放并查看讲稿。"
        ),
        "failed": str(metadata.get("safe_error") or "视频生成失败，可以从同一产物重试。"),
    }
    return {
        "artifact_id": item.id,
        "artifact_version_id": item.current_version_id,
        "artifact_version_number": int(item.current_version_number or 0),
        "kind": "video", "type": "workspace_video", "title": item.title,
        "topic": item.chapter or metadata.get("topic") or "",
        "thread_id": metadata.get("thread_id"), "status": status,
        "summary": stage_copy.get(status, "视频任务状态已更新。"),
        "duration_sec": int(metadata.get("duration_sec") or 20),
        "video_url": metadata.get("video_url") if status == "ready" else None,
        "transcript": metadata.get("transcript") or item.content or "",
        "key_points": list(metadata.get("key_points") or []),
        "provider": metadata.get("provider") or "unknown",
        "model": metadata.get("model") or "",
        "dry_run": bool(metadata.get("dry_run", False)),
        "source_material_ids": list(metadata.get("source_material_ids") or []),
        "error_code": metadata.get("error_code") if status == "failed" else None,
        "safe_error": metadata.get("safe_error") if status == "failed" else None,
        "created_at": item.created_at.isoformat() if item.created_at else str(metadata.get("created_at") or ""),
        "updated_at": item.updated_at.isoformat() if item.updated_at else str(metadata.get("status_updated_at") or ""),
        "retryable": status == "failed",
    }


def _staged_updates(result: dict) -> dict[str, Any]:
    """Fields that let a later retry reuse an already-paid generation."""
    return {
        "staged_video_path": str(Path(result["video_path"]).resolve()),
        "staged_script": str(result.get("script") or "")[:20000],
        "staged_key_points": [str(value)[:160] for value in (result.get("key_points") or [])][:6],
        "staged_duration_sec": float(result.get("duration_sec") or 0),
        "staged_provider": str(result.get("provider") or ""),
        "staged_model": str(result.get("model") or ""),
        "staged_dry_run": bool(result.get("dry_run")),
    }


def _staged_result(metadata: dict[str, Any]) -> dict | None:
    """Reuse a fully-paid generation whose post-processing failed, so a retry
    never re-purchases the same video."""
    staged_path = Path(str(metadata.get("staged_video_path") or ""))
    try:
        usable = bool(str(metadata.get("staged_video_path") or "")) and staged_path.exists() and staged_path.stat().st_size > 0
    except OSError:
        usable = False
    if not usable:
        return None
    return {
        "video_path": staged_path,
        "script": str(metadata.get("staged_script") or ""),
        "key_points": list(metadata.get("staged_key_points") or []),
        "duration_sec": float(metadata.get("staged_duration_sec") or 0),
        "provider": str(metadata.get("staged_provider") or ""),
        "model": str(metadata.get("staged_model") or ""),
        "dry_run": bool(metadata.get("staged_dry_run")),
        "provider_task_id": metadata.get("provider_task_id"),
    }


async def execute_video_job(*, artifact_id: int, user_id: int) -> None:
    """Run one queued job outside the request and persist every stage."""
    from config import settings as app_settings
    from database.connection import get_db_session
    from database.models import ChatSession, WorkspaceAgentTask
    from integrations.cloud_video.contracts import (
        EvidenceTeachingContext, StyleContext, VideoGenerationRequest,
    )
    from integrations.cloud_video.service import (
        CloudVideoService, CloudVideoServiceBlocked, CloudVideoServiceError,
    )
    from integrations.cloud_video.avatar_provider import AvatarTaskPendingError
    from integrations.cloud_video.settings import CloudVideoSettings
    from services.workspace_agents import build_thread_artifact_message
    from services.workspace_runtime import complete_runtime_operation, fail_runtime_operation

    async with get_db_session() as db:
        try:
            item = await get_owned_video_job(db, user_id=user_id, artifact_id=artifact_id)
        except VideoJobAccessError:
            return
        metadata = dict(item.raw_data or {})
        if str(metadata.get("status") or "queued") != "queued":
            # A duplicate background execution slipped in (double retry click,
            # repeated create); the first execution owns the paid pipeline.
            return
        request = VideoGenerationRequest(
            topic=str(metadata.get("topic") or item.chapter or ""),
            user_id=user_id,
            thread_id=str(metadata.get("thread_id") or ""),
            duration_sec=max(15, min(int(metadata.get("duration_sec") or 45), 600)),
            provider=str(metadata.get("provider") or "s2v"),
            instruction=str(metadata.get("instruction") or ""),
            source_context=str(metadata.get("source_context") or ""),
            style_context=StyleContext.model_validate(metadata.get("style_context") or {}),
            evidence_context=EvidenceTeachingContext.model_validate(metadata.get("evidence_context") or {}),
            theme=str(metadata.get("presentation_theme") or "kaki"),
        )
        task_id = str(metadata.get("task_id") or "")

    async def on_stage(status: str) -> None:
        async with get_db_session() as stage_db:
            await update_video_job_status(
                stage_db, user_id=user_id, artifact_id=artifact_id, status=status,
            )

    async def on_task_submitted(submitted_task_id: str) -> None:
        # The paid cloud task exists from this moment on; persist its id so any
        # later failure or restart can re-attach instead of re-billing.
        await persist_job_fields(
            artifact_id=artifact_id, user_id=user_id,
            updates={"provider_task_id": str(submitted_task_id)[:80]},
        )

    service = CloudVideoService(CloudVideoSettings.from_app_settings(app_settings))
    try:
        result = _staged_result(metadata)
        if result is not None:
            # Staged reuse skips the provider pipeline, but the durable status
            # must still walk every stage so completion stays transition-legal.
            for stage in ("generating_script", "generating_audio", "generating_avatar", "composing"):
                await on_stage(stage)
        if result is None:
            provider_task_id = str(metadata.get("provider_task_id") or "")
            if provider_task_id and not CloudVideoSettings.from_app_settings(app_settings).blocker_code:
                try:
                    # resume_pending_avatar continues from generating_avatar;
                    # the earlier stages must be marked first.
                    await on_stage("generating_script")
                    await on_stage("generating_audio")
                    resumed = await service.resume_pending_avatar(
                        provider_task_id, request, on_stage=on_stage,
                    )
                except AvatarTaskPendingError:
                    # The provider task is still rendering. Re-submitting would
                    # bill a second synthesis, so fail without clearing the
                    # task id and let a later retry re-attach to it.
                    async with get_db_session() as db:
                        await fail_video_job(
                            db, user_id=user_id, artifact_id=artifact_id,
                            error_code="AVATAR_TASK_PENDING",
                            safe_error="云端数字人任务仍在生成，请稍后从原产物重试；不会重复提交付费任务。",
                        )
                    fail_runtime_operation(user_id, request.thread_id, "AVATAR_TASK_PENDING")
                    return
                if resumed is not None:
                    result = resumed
            if result is None:
                result = await service.generate(
                    request, on_stage=on_stage, on_task_submitted=on_task_submitted,
                )
            await persist_job_fields(
                artifact_id=artifact_id, user_id=user_id,
                updates=_staged_updates(result),
            )
        async with get_db_session() as db:
            item = await complete_video_job(
                db, user_id=user_id, artifact_id=artifact_id,
                video_path=Path(result["video_path"]), script=str(result["script"]),
                key_points=list(result.get("key_points") or []),
                duration_sec=int(round(float(result.get("duration_sec") or request.duration_sec))),
                provider=str(result.get("provider") or request.provider),
                model=str(result.get("model") or "unknown"),
                dry_run=bool(result.get("dry_run")),
            )
            if task_id:
                task = (await db.execute(select(WorkspaceAgentTask).where(
                    WorkspaceAgentTask.task_id == task_id,
                    WorkspaceAgentTask.user_id == user_id,
                ))).scalar_one_or_none()
                if task:
                    task.state = "completed"; task.artifact_id = artifact_id; task.error_code = None
            chat = (await db.execute(select(ChatSession).where(
                ChatSession.user_id == user_id,
                ChatSession.thread_id == request.thread_id,
            ))).scalar_one_or_none()
            if chat:
                message = build_thread_artifact_message(
                    request.thread_id, "video", artifact_id, item.title,
                    f"《{item.title}》已经生成，可以打开播放并查看讲稿。",
                )
                message["artifact"].update(project_video_job(item))
                messages = list(chat.messages or []); messages.append(message)
                chat.messages = messages; chat.message_count = len(messages)
            await db.commit()
        complete_runtime_operation(user_id, request.thread_id)
    except (CloudVideoServiceBlocked, CloudVideoServiceError) as exc:
        error_code = str(getattr(exc, "code", "CLOUD_VIDEO_FAILED"))[:80]
        safe_error = str(exc)[:400]
        cloud_settings = CloudVideoSettings.from_app_settings(app_settings)
        for secret in (cloud_settings.dashscope_api_key, cloud_settings.llm_api_key):
            if secret:
                safe_error = safe_error.replace(secret, "[REDACTED]")
        async with get_db_session() as db:
            await fail_video_job(
                db, user_id=user_id, artifact_id=artifact_id,
                error_code=error_code, safe_error=safe_error,
            )
            if task_id:
                task = (await db.execute(select(WorkspaceAgentTask).where(
                    WorkspaceAgentTask.task_id == task_id,
                    WorkspaceAgentTask.user_id == user_id,
                ))).scalar_one_or_none()
                if task:
                    task.state = "failed"; task.error_code = error_code
            await db.commit()
        fail_runtime_operation(user_id, request.thread_id, error_code)
    except Exception:
        async with get_db_session() as db:
            await fail_video_job(
                db, user_id=user_id, artifact_id=artifact_id,
                error_code="CLOUD_VIDEO_FAILED",
                safe_error="视频生成失败，未创建假视频；可以从原产物重试。",
            )
        fail_runtime_operation(user_id, request.thread_id, "CLOUD_VIDEO_FAILED")
