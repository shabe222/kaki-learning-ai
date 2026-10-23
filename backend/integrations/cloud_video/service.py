"""End-to-end internal Cloud Video service used by KAKI jobs only."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

from services.llm_service_real import get_llm_service

from .avatar_provider import AvatarProviderBlocked, AvatarProviderError, DashScopeAvatarProvider
from .contracts import VideoGenerationRequest
from .media_pipeline import (
    compose_teaching_video,
    media_duration_sec,
    official_teacher_reference,
    probe_video,
    render_dry_run_video,
    synthesize_speech,
)
from .script_service import (
    ScriptGenerationError, TeachingScript, build_local_teaching_script, generate_teaching_script,
)
from .settings import CloudVideoSettings


class CloudVideoServiceError(RuntimeError):
    code = "CLOUD_VIDEO_FAILED"


class CloudVideoServiceBlocked(CloudVideoServiceError):
    code = "BLOCKED_BY_SECRET"


StageCallback = Callable[[str], Awaitable[None]]


class CloudVideoService:
    def __init__(self, settings: CloudVideoSettings):
        self.settings = settings

    def _clip_duration(self, request: VideoGenerationRequest) -> int:
        return max(15, min(int(request.duration_sec or 45), 600))

    async def _resolve_script(self, request: VideoGenerationRequest) -> TeachingScript:
        llm = None
        try:
            llm = await get_llm_service()
            return await generate_teaching_script(
                request, llm, model_identifier=self.settings.script_model,
            )
        except Exception:
            return build_local_teaching_script(request)
        finally:
            if llm is not None:
                try:
                    await llm.close()
                except Exception:
                    pass

    async def _render_local_teacher_video(
        self, output_path: Path, *, topic: str, script_text: str, key_points,
        fallback_duration: int, caption: str, theme: str = "kaki",
    ) -> tuple[Path, str]:
        """本地渲染：逐句 TTS + 逐句讲解卡 + GLM 老师两帧交替（口部与头部动作）。

        卡片与旁白逐句对齐，内容随讲解持续变化；节拍标签来自视频脚本技能的
        钩子/过程/对照/误区/收束结构，没有标注时按句位推导下限节奏。任何一环
        失败都逐级降级：逐句 TTS 失败回退整段 TTS + 按字数估算的逐句时长；
        老师形象不可用回退纯文字卡；最终兜底是静态卡渲染——视频总能产出。
        返回 (path, model_label)。
        """
        # synthesize_speech 刻意不从 media_pipeline 局部导入：函数解析模块全局名，
        # 测试才能通过 monkeypatch 替换它，单元路径不依赖在线 TTS。
        from .media_pipeline import (
            build_talking_teacher_pair, classify_script_beats, media_duration_sec,
            render_script_card, render_scripted_lesson_video, split_script_sentences,
            probe_video,
        )

        fallback_duration = max(15, min(int(fallback_duration), 600))
        sentences = split_script_sentences(script_text) or [str(k) for k in (key_points or []) if str(k).strip()]
        workdir = output_path.parent / f"lesson-{output_path.stem[:16]}"
        workdir.mkdir(parents=True, exist_ok=True)

        async def _tts_per_sentence() -> tuple[list[str], list[float], list[Path]] | None:
            texts: list[str] = []
            durations: list[float] = []
            audios: list[Path] = []
            for index, sentence in enumerate(sentences):
                seg_path = workdir / f"seg-{index:03d}.mp3"
                try:
                    await synthesize_speech(sentence, seg_path)
                    seg_duration = float(media_duration_sec(seg_path))
                except Exception:
                    return None
                if seg_duration <= 0.2:
                    return None
                texts.append(sentence)
                durations.append(min(120.0, seg_duration + 0.15))
                audios.append(seg_path)
            return (texts, durations, audios) if texts else None

        prepared = None
        single_audio: Path | None = None
        if sentences:
            prepared = await _tts_per_sentence()
        if prepared is None:
            # 逐句 TTS 失败：整段合成一次，按字符占比估算逐句时长，卡片仍逐句变化。
            full_audio = workdir / "full.mp3"
            try:
                await synthesize_speech(script_text or "。".join(sentences), full_audio)
            except Exception:
                full_audio = None
            if not sentences:
                sentences = ["本期要点讲解"]
            if full_audio is not None:
                total_chars = sum(max(1, len(s)) for s in sentences)
                total_duration = min(600.0, max(3.0, float(media_duration_sec(full_audio))))
                durations = [max(0.6, total_duration * len(s) / total_chars) for s in sentences]
                # 整段音轨直混一次；逐句 concat 需要一一对应的音频文件。
                single_audio = full_audio
                prepared = (sentences, durations, [])
            else:
                # 语音通道完全不可用：静态卡兜底（保留旧行为，视频仍产出）。
                render_dry_run_video(
                    output_path, topic=topic, key_points=key_points or sentences,
                    duration_sec=fallback_duration, audio_path=None, caption=caption,
                    theme=theme,
                )
                return output_path, "local-ffmpeg-tts"

        texts, durations, audios = prepared
        beats = classify_script_beats(texts)
        teacher_pair = None
        try:
            from services.illustration_skill import generate_teacher_portrait
            outcome = await generate_teacher_portrait()
            if outcome.get("ok") and outcome.get("path"):
                pair = build_talking_teacher_pair(
                    Path(str(outcome["path"])), workdir / "teacher-frames", theme=theme,
                )
                if pair is not None:
                    teacher_pair = pair
        except Exception:
            teacher_pair = None

        if teacher_pair is None:
            # 无老师形象：逐句卡片仍然成立（内容持续变化），仅口部动作缺失。
            total_duration = sum(durations)
            timeline_lines = []
            elapsed = 0.0
            card_files: list[Path] = []
            for index, (sentence, seg_duration) in enumerate(zip(texts, durations)):
                card = render_script_card(
                    workdir / f"card-{index:03d}.png", topic=topic, sentence=sentence,
                    step=index + 1, total_steps=len(texts),
                    progress=(elapsed + seg_duration / 2) / total_duration,
                    teacher_frame=None, caption=caption,
                    theme=theme, beat=beats[index] if index < len(beats) else "",
                )
                card_files.append(card)
                elapsed += float(seg_duration)
            try:
                from .media_pipeline import _concat_cards_with_audio as _concat
                _concat(
                    output_path, cards=card_files, durations=durations, audios=audios,
                    single_audio=single_audio,
                )
                probe_video(output_path)
                return output_path, "local-lesson-cards"
            except Exception:
                render_dry_run_video(
                    output_path, topic=topic, key_points=key_points or texts,
                    duration_sec=fallback_duration, audio_path=None, caption=caption,
                    theme=theme,
                )
                return output_path, "local-ffmpeg-tts"

        render_scripted_lesson_video(
            output_path, topic=topic, sentences=texts, segment_durations=durations,
            segment_audios=audios, teacher_pair=teacher_pair, caption=caption,
            theme=theme, beats=beats, single_audio=single_audio,
        )
        return output_path, "local-teacher-glm"

    async def _generate_local(
        self, request: VideoGenerationRequest, *, on_stage: StageCallback,
    ) -> dict:
        duration = self._clip_duration(request)
        output_dir = self.settings.output_dir / str(request.user_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"artifact-{request.thread_id[:24].replace('/', '-')}-{uuid4().hex[:12]}"
        audio_path = output_dir / f"{stem}.mp3"
        final_path = output_dir / f"{stem}.mp4"
        await on_stage("generating_script")
        script = await self._resolve_script(request)
        await on_stage("generating_audio")
        await on_stage("generating_avatar")
        await on_stage("composing")
        _, model_label = await self._render_local_teacher_video(
            final_path, topic=request.topic, script_text=script.script,
            key_points=script.key_points, fallback_duration=duration,
            caption="DARYA 本机讲解", theme=request.theme,
        )
        probe = probe_video(final_path)
        measured = float((probe.get("format") or {}).get("duration") or duration)
        return {
            "video_path": final_path, "script": script.script,
            "key_points": script.key_points, "duration_sec": round(measured, 2),
            "provider": "local", "model": model_label,
            "script_model": script.model, "dry_run": True, "provider_task_id": None,
        }

    async def resume_pending_avatar(
        self, task_id: str, request: VideoGenerationRequest, *, on_stage: StageCallback,
    ) -> dict | None:
        """Re-attach to a previously submitted DashScope synthesis task.

        Returns the standard generate() payload when the paid task finished and
        the video was recovered, or None when the task terminally failed and a
        fresh generation is safe.  Raises AvatarTaskPendingError when the task
        is still rendering — the caller must not re-submit in that case.
        """
        from .avatar_provider import (
            AvatarProviderBlocked, AvatarProviderError, AvatarTaskPendingError,
        )

        provider = DashScopeAvatarProvider(self.settings)
        output_dir = self.settings.output_dir / str(request.user_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_id = "".join(ch for ch in str(task_id) if ch.isalnum() or ch == "-")[:64]
        avatar_path = output_dir / f"resume-{safe_id}.avatar.mp4"
        final_path = output_dir / f"resume-{safe_id}.mp4"
        await on_stage("generating_avatar")
        try:
            provider_result = await provider.fetch_task_result(
                task_id, avatar_path, timeout_sec=60, poll_interval_sec=5.0,
            )
        except AvatarProviderBlocked:
            raise
        except AvatarTaskPendingError:
            raise
        except AvatarProviderError:
            # Task failed or is unanswerable server-side; a new submission is
            # the only remaining path and does not double-bill the old one.
            return None
        await on_stage("composing")
        script = build_local_teaching_script(request)
        compose_teaching_video(
            avatar_video=avatar_path, output_path=final_path,
            topic=request.topic, key_points=script.key_points, theme=request.theme,
        )
        probe = probe_video(final_path)
        duration = float((probe.get("format") or {}).get("duration") or self._clip_duration(request))
        return {
            "video_path": final_path, "script": script.script,
            "key_points": script.key_points, "duration_sec": round(duration, 2),
            "provider": request.provider, "model": provider_result.get("model") or self.settings.avatar_model,
            "script_model": script.model, "dry_run": False,
            "provider_task_id": task_id,
        }

    async def generate(
        self, request: VideoGenerationRequest, *, on_stage: StageCallback,
        on_task_submitted: StageCallback | None = None,
    ) -> dict:
        if self.settings.blocker_code or request.dry_run:
            try:
                return await self._generate_local(request, on_stage=on_stage)
            except CloudVideoServiceError:
                raise
            except Exception as exc:
                raise CloudVideoServiceError(f"CLOUD_VIDEO_FAILED: {str(exc)[:400]}") from exc
        output_dir = self.settings.output_dir / str(request.user_id)
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = f"artifact-{request.thread_id[:24].replace('/', '-')}-{uuid4().hex[:12]}"
        audio_path = output_dir / f"{stem}.mp3"
        avatar_path = output_dir / f"{stem}.avatar.mp4"
        final_path = output_dir / f"{stem}.mp4"
        llm = None
        try:
            await on_stage("generating_script")
            llm = await get_llm_service()
            try:
                script = await generate_teaching_script(
                    request, llm, model_identifier=self.settings.script_model,
                )
            except ScriptGenerationError:
                script = build_local_teaching_script(request)
            await on_stage("generating_audio")
            # TTS（Edge 在线语音）失败时降级为静音/本地渲染，保证视频仍能产出；
            # 在线语音恢复后自动回到带旁白的完整流程。
            try:
                await synthesize_speech(script.script, audio_path)
            except Exception:
                audio_path = None
            await on_stage("generating_avatar")
            provider = DashScopeAvatarProvider(self.settings)
            audio_duration = 0
            if audio_path and Path(audio_path).exists():
                try:
                    audio_duration = int(math.ceil(media_duration_sec(audio_path)))
                except Exception:
                    audio_duration = 0
            full_duration = max(self._clip_duration(request), min(600, audio_duration + 1))
            # 无可用音频（TTS 降级）时不走数字人合成，直接本地渲染，避免空音频崩溃
            if full_duration > 20 or not audio_path:
                await on_stage("composing")
                _, model_label = await self._render_local_teacher_video(
                    final_path, topic=request.topic, script_text=script.script,
                    key_points=script.key_points, fallback_duration=full_duration,
                    caption="DARYA 完整讲解", theme=request.theme,
                )
                probe = probe_video(final_path)
                duration = float((probe.get("format") or {}).get("duration") or full_duration)
                return {
                    "video_path": final_path, "script": script.script,
                    "key_points": script.key_points, "duration_sec": round(duration, 2),
                    "provider": "local-full-length", "model": model_label,
                    "script_model": script.model, "dry_run": False, "provider_task_id": None,
                }
            try:
                provider_result = await provider.generate(
                    image_path=official_teacher_reference(), audio_path=audio_path,
                    output_path=avatar_path, on_task_submitted=on_task_submitted,
                )
            except AvatarProviderBlocked:
                raise
            except AvatarProviderError:
                await on_stage("composing")
                _, model_label = await self._render_local_teacher_video(
                    final_path, topic=request.topic, script_text=script.script,
                    key_points=script.key_points, fallback_duration=self._clip_duration(request),
                    caption="DARYA 本机讲解", theme=request.theme,
                )
                probe = probe_video(final_path)
                duration = float((probe.get("format") or {}).get("duration") or request.duration_sec)
                return {
                    "video_path": final_path, "script": script.script,
                    "key_points": script.key_points, "duration_sec": round(duration, 2),
                    "provider": "local", "model": model_label,
                    "script_model": script.model, "dry_run": True, "provider_task_id": None,
                }
            await on_stage("composing")
            compose_teaching_video(
                avatar_video=avatar_path, output_path=final_path,
                topic=request.topic, key_points=script.key_points, theme=request.theme,
            )
            probe = probe_video(final_path)
            duration = float((probe.get("format") or {}).get("duration") or request.duration_sec)
            return {
                "video_path": final_path, "script": script.script,
                "key_points": script.key_points, "duration_sec": round(duration, 2),
                "provider": request.provider, "model": provider_result.get("model") or self.settings.avatar_model,
                "script_model": script.model, "dry_run": False,
                "provider_task_id": provider_result.get("task_id"),
            }
        except CloudVideoServiceError:
            raise
        except AvatarProviderBlocked as exc:
            raise CloudVideoServiceBlocked(str(exc)) from exc
        except Exception as exc:
            code = getattr(exc, "code", "CLOUD_VIDEO_FAILED")
            raise CloudVideoServiceError(f"{code}: {str(exc)[:400]}") from exc
        finally:
            if llm is not None:
                try:
                    await llm.close()
                except Exception:
                    pass
