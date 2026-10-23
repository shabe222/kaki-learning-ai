"""Secret-safe settings projection for the internal Cloud Video adapter."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CloudVideoSettings:
    dashscope_api_key: str = field(default="", repr=False)
    llm_api_key: str = field(default="", repr=False)
    llm_base_url: str = "https://api.deepseek.com/v1"
    script_model: str = "deepseek-v4.1-flash"
    provider: str = "s2v"
    s2v_model: str = "wan2.2-s2v"
    s2v_detect_model: str = "wan2.2-s2v-detect"
    emo_model: str = "emo-v1"
    emo_detect_model: str = "emo-detect-v1"
    output_dir: Path = Path("data/generated_videos")
    keys_rotated: bool = False

    @classmethod
    def from_app_settings(cls, settings: Any) -> "CloudVideoSettings":
        output_value = str(getattr(settings, "CLOUD_VIDEO_OUTPUT_DIR", "") or "").strip()
        output_dir = Path(output_value) if output_value else Path(__file__).resolve().parents[2] / "data" / "generated_videos"
        provider = str(getattr(settings, "CLOUD_VIDEO_PROVIDER", "s2v") or "s2v").strip().lower()
        if provider not in {"s2v", "emo"}:
            provider = "s2v"
        return cls(
            dashscope_api_key=str(getattr(settings, "DASHSCOPE_API_KEY", "") or ""),
            llm_api_key=str(getattr(settings, "DEEPSEEK_API_KEY", "") or ""),
            llm_base_url=str(getattr(settings, "DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1") or ""),
            script_model=str(getattr(settings, "DEEPSEEK_MODEL", "deepseek-v4.1-flash") or "deepseek-v4.1-flash"),
            provider=provider,
            s2v_model=str(getattr(settings, "CLOUD_VIDEO_S2V_MODEL", "wan2.2-s2v") or "wan2.2-s2v"),
            s2v_detect_model=str(getattr(settings, "CLOUD_VIDEO_S2V_DETECT_MODEL", "wan2.2-s2v-detect") or "wan2.2-s2v-detect"),
            emo_model=str(getattr(settings, "CLOUD_VIDEO_EMO_MODEL", "emo-v1") or "emo-v1"),
            emo_detect_model=str(getattr(settings, "CLOUD_VIDEO_EMO_DETECT_MODEL", "emo-detect-v1") or "emo-detect-v1"),
            output_dir=output_dir.resolve(),
            keys_rotated=bool(getattr(settings, "CLOUD_VIDEO_KEYS_ROTATED", False)),
        )

    @property
    def dashscope_configured(self) -> bool:
        return self.keys_rotated and bool(self.dashscope_api_key.strip())

    @property
    def llm_configured(self) -> bool:
        return self.keys_rotated and bool(self.llm_api_key.strip())

    @property
    def blocker_code(self) -> str | None:
        if not self.keys_rotated and (self.dashscope_api_key or self.llm_api_key):
            return "BLOCKED_BY_SECRET"
        if not self.dashscope_configured or not self.llm_configured:
            return "BLOCKED_BY_SECRET"
        return None

    @property
    def avatar_model(self) -> str:
        return self.s2v_model if self.provider == "s2v" else self.emo_model

    def public_health(self) -> dict[str, object]:
        return {
            "status": "ready" if self.blocker_code is None else "blocked",
            "blocker_code": self.blocker_code,
            "local_generation": True,
            "keys_rotated": self.keys_rotated,
            "dashscope_configured": self.dashscope_configured,
            "llm_configured": self.llm_configured,
            "provider": self.provider,
            "avatar_model": self.avatar_model,
            "script_model": self.script_model,
        }
