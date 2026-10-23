"""Formal workspace VIDEO adapter over the existing real video provider chain.

This module deliberately refuses mock/fallback outputs.  Legacy video routes may
keep their compatibility behaviour, but the unified workspace task/artifact and
learning-graph paths must either produce a real provider result or fail closed.
"""
from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse

from config import settings


class WorkspaceVideoError(RuntimeError):
    code = "VIDEO_GENERATION_FAILED"


class WorkspaceVideoProviderBlocked(WorkspaceVideoError):
    code = "REAL_PROVIDER_BLOCKED"


def _provider_name() -> str:
    return str(getattr(settings, "LLM_PROVIDER", "mock") or "mock").strip().lower()


def _assert_real_runtime() -> str:
    mode = str(getattr(settings, "MODE", "mock") or "mock").strip().lower()
    provider = _provider_name()
    if mode == "mock" or provider == "mock":
        raise WorkspaceVideoProviderBlocked(
            "正式 workspace 视频生成需要真实 Provider；当前运行环境处于 mock。"
        )
    if provider not in {"deepseek", "spark", "qwen"}:
        raise WorkspaceVideoProviderBlocked(
            f"正式 workspace 视频生成未配置可用 Provider：{provider or 'unknown'}"
        )
    if not bool(getattr(settings, "OPENMAIC_ENABLED", False)):
        raise WorkspaceVideoProviderBlocked("OpenMAIC 视频生成 Provider 当前未启用。")
    return provider


def _looks_mock(result: Mapping[str, Any]) -> bool:
    provider = str(result.get("llm_provider") or result.get("provider") or "").lower()
    video_url = str(result.get("video_url") or "").lower()
    transcript = str(result.get("transcript") or "").lower()
    return (
        provider == "mock"
        or "placeholder.local" in video_url
        or "fallback-" in video_url
        or "[mock" in transcript
    )


_VIDEO_EXTENSIONS = {".mp4", ".webm", ".ogg", ".ogv", ".mov", ".m4v"}
_NON_VIDEO_PAGE_EXTENSIONS = {".html", ".htm", ".xhtml", ".json", ".txt", ".md"}
_MEDIA_TYPE_KEYS = ("media_type", "content_type", "mime_type")


def _normalise_media_type(value: Any) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def _result_media_types(result: Mapping[str, Any]) -> list[str]:
    nested = result.get("video_result")
    nested_map = nested if isinstance(nested, Mapping) else {}
    values: list[str] = []
    for source in (result, nested_map):
        for key in _MEDIA_TYPE_KEYS:
            media_type = _normalise_media_type(source.get(key))
            if media_type and media_type not in values:
                values.append(media_type)
    return values


def _assert_playable_video_media(result: Mapping[str, Any]) -> dict[str, str]:
    """Fail closed unless a provider result is credibly a video media resource.

    The formal workspace VIDEO contract deliberately does not trust a non-empty
    URL by itself.  In particular, the current legacy OpenMAIC provider can
    return a presigned HTML classroom page; that output must never be persisted
    as ``workspace_video`` and handed to ``<video src>``.
    """
    video_url = str(result.get("video_url") or "").strip()
    parsed = urlparse(video_url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise WorkspaceVideoProviderBlocked(
            "视频 Provider 返回了非安全媒体 URL；正式 workspace 仅接受 http/https 视频资源。"
        )

    path = (parsed.path or "").lower()
    suffix = ""
    if "." in path.rsplit("/", 1)[-1]:
        suffix = "." + path.rsplit("/", 1)[-1].rsplit(".", 1)[-1]

    nested = result.get("video_result")
    nested_map = nested if isinstance(nested, Mapping) else {}
    object_name = str(nested_map.get("object_name") or "").strip().lower()
    if (
        suffix in _NON_VIDEO_PAGE_EXTENSIONS
        or any(object_name.endswith(ext) for ext in _NON_VIDEO_PAGE_EXTENSIONS)
        or bool(nested_map.get("html_content"))
        or bool(nested_map.get("classroom"))
    ):
        raise WorkspaceVideoProviderBlocked(
            "视频 Provider 当前返回的是 HTML/classroom 页面而非可播放视频媒体；正式 workspace 已拒绝持久化。"
        )

    media_types = _result_media_types(result)
    if media_types:
        if not all(media_type.startswith("video/") for media_type in media_types):
            raise WorkspaceVideoProviderBlocked(
                "视频 Provider 返回了非 video/* 媒体类型；正式 workspace 已拒绝持久化。"
            )
        return {"media_type": media_types[0], "media_shape": "declared_video_media_type"}

    if suffix in _VIDEO_EXTENSIONS:
        return {"media_type": "", "media_shape": f"video_extension:{suffix}"}

    raise WorkspaceVideoProviderBlocked(
        "视频 Provider 未返回可验证的视频媒体类型或视频资源扩展名；正式 workspace 已拒绝持久化。"
    )


async def generate_workspace_video(
    *,
    topic: str,
    user_id: int,
    duration_sec: int = 60,
    instruction: str = "",
    source_context: str = "",
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate one formal workspace video using the existing real provider/client.

    No fallback is allowed. Provider unavailability is projected as
    REAL_PROVIDER_BLOCKED so callers never persist a fake workspace_video.
    """
    clean_topic = str(topic or "").strip()
    if not clean_topic:
        raise ValueError("topic 不能为空")
    duration = int(duration_sec or 60)
    if not 15 <= duration <= 600:
        raise ValueError("duration_sec 必须在 15 到 600 秒之间")
    provider = _assert_real_runtime()

    request_topic = clean_topic
    additions = []
    if str(instruction or "").strip():
        additions.append("教学要求：" + str(instruction).strip())
    if str(source_context or "").strip():
        additions.append("参考资料（只用于生成，不得编造资料外事实）：\n" + str(source_context).strip())
    if additions:
        request_topic += "\n" + "\n".join(additions)

    try:
        from services.video_service import VideoService

        result = await VideoService(llm_provider=provider).generate(
            request_topic,
            profile=dict(profile or {}),
            duration_sec=duration,
            user_id=int(user_id),
        )
    except (NotImplementedError, ConnectionError, TimeoutError) as exc:
        raise WorkspaceVideoProviderBlocked(str(exc) or "视频 Provider 当前不可用") from exc
    except RuntimeError as exc:
        text = str(exc)
        if any(token in text.lower() for token in (
            "api_key", "not configured", "未配置", "disabled", "不可用", "network", "timeout",
        )):
            raise WorkspaceVideoProviderBlocked(text) from exc
        raise WorkspaceVideoError(text or "视频生成失败") from exc
    except Exception as exc:
        raise WorkspaceVideoError(str(exc) or "视频生成失败") from exc

    if not isinstance(result, Mapping) or _looks_mock(result):
        raise WorkspaceVideoProviderBlocked("视频 Provider 返回了 mock/fallback 结果，正式 workspace 已拒绝持久化。")
    video_url = str(result.get("video_url") or "").strip()
    if not video_url:
        raise WorkspaceVideoError("真实视频 Provider 未返回 video_url")
    media_contract = _assert_playable_video_media(result)
    transcript = str(result.get("transcript") or "").strip()
    return {
        "title": f"{clean_topic}教学视频",
        "topic": clean_topic,
        "duration_sec": duration,
        "video_url": video_url,
        "transcript": transcript,
        "provider": str(result.get("llm_provider") or provider),
        "provider_source": "services.video_service.VideoService -> services.video_service_openmaic",
        "media_type": media_contract["media_type"],
        "media_shape": media_contract["media_shape"],
        "generation_ms": int(result.get("generation_ms") or 0),
        "video_result": dict(result.get("video_result") or {}),
    }
