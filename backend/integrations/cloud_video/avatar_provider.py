"""DashScope avatar adapter reduced from the teammate module.

It is an internal provider only: no FastAPI app, upload page, directory listing,
fallback avatar, or secret-bearing configuration is retained.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

import httpx

from .media_pipeline import prepare_s2v_audio
from .settings import CloudVideoSettings

logger = logging.getLogger(__name__)


class AvatarProviderError(RuntimeError):
    code = "AVATAR_PROVIDER_FAILED"


class AvatarProviderBlocked(AvatarProviderError):
    code = "BLOCKED_BY_SECRET"


class AvatarCapabilityError(AvatarProviderError):
    code = "AVATAR_REFERENCE_UNSUPPORTED"


class AvatarTaskPendingError(AvatarProviderError):
    """The submitted provider task is still rendering.

    Distinct from a plain AvatarProviderError so callers know that re-submitting
    would bill a second synthesis while the first is still running.
    """

    code = "AVATAR_TASK_PENDING"


_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _data_url(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"


def safe_media_filename(path: Path, kind: Literal["audio", "image"]) -> str:
    suffix = path.suffix.lower()
    if kind == "audio":
        return "speech.mp3" if suffix != ".wav" else "speech.wav"
    if suffix not in _IMAGE_SUFFIXES:
        suffix = ".jpg"
    return f"reference{suffix}"


def _extract_video_url(payload: dict[str, Any]) -> str:
    output = dict(payload.get("output") or payload)
    results = output.get("results")
    candidates: list[Any] = [
        output.get("video_url"), output.get("result_url"), output.get("output_video_url"),
    ]
    if isinstance(results, dict):
        candidates.extend([results.get("video_url"), results.get("url"), results.get("result_url")])
    elif isinstance(results, list):
        for item in results:
            if isinstance(item, dict):
                candidates.extend([item.get("video_url"), item.get("url"), item.get("result_url")])
    for raw in candidates:
        url = str(raw or "").strip()
        if url.startswith("http://") and "aliyuncs.com" in url:
            url = "https://" + url[len("http://"):]
        if url.startswith("https://"):
            return url
    return ""


def oss_object_key(upload_dir: str, filename: str) -> str:
    return f"{str(upload_dir or '').strip().rstrip('/')}/{filename}"


class DashScopeAvatarProvider:
    base_url = "https://dashscope.aliyuncs.com/api/v1"

    def __init__(self, settings: CloudVideoSettings):
        self.settings = settings

    def assert_available(self) -> None:
        if not self.settings.dashscope_configured:
            raise AvatarProviderBlocked(
                "BLOCKED_BY_SECRET: 请先在本机轮换并确认 DashScope 凭据。"
            )

    def sanitize_error(self, value: Any) -> str:
        text = str(value or "视频 Provider 请求失败")[:1200]
        for secret in (self.settings.dashscope_api_key, self.settings.llm_api_key):
            if secret:
                text = text.replace(secret, "[REDACTED]")
        text = re.sub(r"(?i)Authorization\s*:\s*Bearer\s+[^\s;,]+", "Authorization: [REDACTED]", text)
        text = re.sub(r"(?i)Bearer\s+[^\s;,]+", "[REDACTED]", text)
        text = re.sub(r"(?i)(api[_-]?key\s*[=:]\s*)[^\s;,]+", r"\1[REDACTED]", text)
        return text

    def _headers(self, *, async_request: bool = False, oss_resolve: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.settings.dashscope_api_key}"}
        if async_request:
            headers["X-DashScope-Async"] = "enable"
        if oss_resolve:
            headers["X-DashScope-OssResourceResolve"] = "enable"
        return headers

    async def _upload_temporary(self, path: Path, *, kind: Literal["audio", "image"], model: str) -> str:
        self.assert_available()
        filename = safe_media_filename(path, kind)
        content_type = {
            ".mp3": "audio/mpeg", ".wav": "audio/wav",
            ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        }.get(Path(filename).suffix.lower(), "application/octet-stream")
        async with httpx.AsyncClient(timeout=60.0) as client:
            policy_response = await client.get(
                f"{self.base_url}/uploads",
                headers=self._headers(),
                params={"action": "getPolicy", "model": model},
            )
            if policy_response.status_code != 200:
                raise AvatarProviderError(self.sanitize_error(policy_response.text))
            policy = dict((policy_response.json() or {}).get("data") or {})
            upload_host = str(policy.get("upload_host") or "")
            upload_dir = str(policy.get("upload_dir") or "")
            if not upload_host or not upload_dir:
                raise AvatarProviderError("未能获取 DashScope 临时上传凭证。")
            key = oss_object_key(upload_dir, filename)
            files = {
                "OSSAccessKeyId": (None, str(policy.get("oss_access_key_id") or "")),
                "Signature": (None, str(policy.get("signature") or "")),
                "policy": (None, str(policy.get("policy") or "")),
                "x-oss-object-acl": (None, str(policy.get("x_oss_object_acl") or "private")),
                "x-oss-forbid-overwrite": (None, str(policy.get("x_oss_forbid_overwrite") or "true")),
                "key": (None, key),
                "success_action_status": (None, "200"),
                "file": (filename, path.read_bytes(), content_type),
            }
            uploaded = await client.post(upload_host, files=files)
            if uploaded.status_code not in {200, 204}:
                raise AvatarProviderError(self.sanitize_error(uploaded.text or "临时音频上传失败"))
        return f"oss://{key}"

    async def detect_reference(self, image_path: Path) -> dict[str, Any]:
        self.assert_available()
        model = self.settings.s2v_detect_model if self.settings.provider == "s2v" else self.settings.emo_detect_model
        payload: dict[str, Any] = {
            "model": model,
            "input": {"image_url": _data_url(image_path, "image/jpeg")},
        }
        if self.settings.provider == "emo":
            payload["parameters"] = {"ratio": "1:1"}
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    f"{self.base_url}/services/aigc/image2video/face-detect",
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json=payload,
                )
            data = response.json()
        except Exception as exc:
            raise AvatarProviderError(self.sanitize_error(exc)) from exc
        if response.status_code != 200 or data.get("code"):
            raise AvatarCapabilityError(self.sanitize_error(data.get("message") or response.text))
        output = dict(data.get("output") or {})
        if not output.get("check_pass"):
            raise AvatarCapabilityError("正式 DARYA 讲师 reference 未通过当前 provider capability check。")
        return output

    async def fetch_task_result(
        self, task_id: str, output_path: Path, *,
        timeout_sec: int = 900, poll_interval_sec: float = 5.0,
    ) -> dict[str, Any]:
        """Re-attach to a previously submitted synthesis task and download it.

        Raises AvatarTaskPendingError when the task is still rendering after the
        deadline, and a plain AvatarProviderError when the task terminally
        failed — only the latter is safe to answer with a fresh paid submission.
        """
        self.assert_available()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                deadline = asyncio.get_running_loop().time() + timeout_sec
                result: dict[str, Any] = {}
                while asyncio.get_running_loop().time() < deadline:
                    query = await client.get(f"{self.base_url}/tasks/{task_id}", headers=self._headers())
                    result = query.json()
                    status = str((result.get("output") or {}).get("task_status") or result.get("task_status") or "").upper()
                    logger.info("dashscope avatar reattach task=%s status=%s", task_id, status or "unknown")
                    if status in {"SUCCEEDED", "SUCCESS"}:
                        video_url = _extract_video_url(result)
                        if not video_url:
                            raise AvatarProviderError("Provider 未返回可下载的数字人视频地址。")
                        download = await client.get(video_url, follow_redirects=True, timeout=180.0)
                        download.raise_for_status()
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        output_path.write_bytes(download.content)
                        return {"task_id": task_id, "model": self.settings.avatar_model, "video_path": str(output_path)}
                    if status in {"FAILED", "CANCELED", "CANCELLED", "UNKNOWN"}:
                        raise AvatarProviderError(self.sanitize_error((result.get("output") or {}).get("message") or result))
                    await asyncio.sleep(poll_interval_sec)
        except AvatarTaskPendingError:
            raise
        except AvatarProviderError:
            raise
        except Exception as exc:
            raise AvatarProviderError(self.sanitize_error(exc)) from exc
        raise AvatarTaskPendingError("数字人任务仍在生成，稍后可重新尝试取回，无需重新提交。")

    async def generate(
        self, *, image_path: Path, audio_path: Path, output_path: Path,
        timeout_sec: int = 900, poll_interval_sec: float = 5.0,
        on_task_submitted: Callable[[str], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        detection = await self.detect_reference(image_path)
        encoded_audio = audio_path.with_name(audio_path.stem + ".s2v.mp3")
        audio_path = prepare_s2v_audio(audio_path, encoded_audio)
        image_url = await self._upload_temporary(
            image_path, kind="image", model=self.settings.avatar_model,
        )
        audio_url = await self._upload_temporary(
            audio_path, kind="audio", model=self.settings.avatar_model,
        )
        input_data: dict[str, Any] = {"image_url": image_url, "audio_url": audio_url}
        parameters: dict[str, Any] = {}
        if self.settings.provider == "emo":
            if not detection.get("face_bbox") or not detection.get("ext_bbox"):
                raise AvatarCapabilityError("EMO capability check 未返回所需的人脸边界。")
            input_data.update(face_bbox=detection["face_bbox"], ext_bbox=detection["ext_bbox"])
            parameters["style_level"] = "normal"
        else:
            parameters["resolution"] = "480P"
        payload = {"model": self.settings.avatar_model, "input": input_data, "parameters": parameters}
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.base_url}/services/aigc/image2video/video-synthesis",
                    headers={
                        **self._headers(async_request=True, oss_resolve=True),
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
            data = response.json()
            task_id = str((data.get("output") or {}).get("task_id") or "")
            if response.status_code not in {200, 201} or not task_id:
                raise AvatarProviderError(self.sanitize_error(data.get("message") or response.text))
            if on_task_submitted is not None:
                try:
                    await on_task_submitted(task_id)
                except Exception:
                    logger.warning("persisting avatar task id failed (non-fatal)", exc_info=True)
            deadline = asyncio.get_running_loop().time() + timeout_sec
            result: dict[str, Any] = {}
            async with httpx.AsyncClient(timeout=30.0) as client:
                while asyncio.get_running_loop().time() < deadline:
                    query = await client.get(f"{self.base_url}/tasks/{task_id}", headers=self._headers())
                    result = query.json()
                    status = str((result.get("output") or {}).get("task_status") or result.get("task_status") or "").upper()
                    logger.info("dashscope avatar task status=%s", status or "unknown")
                    if status in {"SUCCEEDED", "SUCCESS"}:
                        break
                    if status in {"FAILED", "CANCELED", "CANCELLED", "UNKNOWN"}:
                        raise AvatarProviderError(self.sanitize_error((result.get("output") or {}).get("message") or result))
                    await asyncio.sleep(poll_interval_sec)
                else:
                    raise AvatarProviderError("数字人生成超时，任务未标记成功。")
                video_url = _extract_video_url(result)
                if not video_url:
                    raise AvatarProviderError("Provider 未返回可下载的数字人视频地址。")
                download = await client.get(video_url, follow_redirects=True, timeout=180.0)
                download.raise_for_status()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(download.content)
            return {"task_id": task_id, "model": self.settings.avatar_model, "video_path": str(output_path)}
        except AvatarProviderError:
            raise
        except Exception as exc:
            raise AvatarProviderError(self.sanitize_error(exc)) from exc
