"""
视频生成 Provider 注册表

对应 OpenMAIC video-providers.ts 的 Python 翻译。
当前启用: Seedance（豆包）。架构预留: Kling / Veo / MiniMax。
"""

from __future__ import annotations

VIDEO_PROVIDERS = {
    "seedance": {
        "id": "seedance",
        "name": "Seedance（豆包）",
        "requiresApiKey": True,
        "defaultBaseUrl": "https://ark.cn-beijing.volces.com",
        "models": [
            {"id": "doubao-seedance-1-5-pro-251215", "name": "Seedance 1.5 Pro"},
            {"id": "doubao-seedance-1-0-pro-250528", "name": "Seedance 1.0 Pro"},
            {"id": "doubao-seedance-1-0-pro-fast-251015", "name": "Seedance 1.0 Pro Fast"},
            {"id": "doubao-seedance-1-0-lite-t2v-250428", "name": "Seedance 1.0 Lite T2V"},
        ],
        "supportedAspectRatios": ["16:9", "4:3", "1:1", "9:16", "3:4", "21:9"],
        "supportedDurations": [5, 10],
        "supportedResolutions": ["480p", "720p", "1080p"],
        "maxDuration": 10,
    },
    "kling": {
        "id": "kling",
        "name": "Kling（可灵）",
        "requiresApiKey": True,
        "defaultBaseUrl": "https://api-beijing.klingai.com",
        "models": [
            {"id": "kling-v2-6", "name": "Kling V2.6"},
            {"id": "kling-v1-6", "name": "Kling V1.6"},
        ],
        "supportedAspectRatios": ["16:9", "1:1", "9:16"],
        "supportedDurations": [5, 10],
        "maxDuration": 10,
    },
    "veo": {
        "id": "veo",
        "name": "Veo（Google）",
        "requiresApiKey": True,
        "defaultBaseUrl": "https://generativelanguage.googleapis.com",
        "models": [
            {"id": "veo-3.1-fast-generate-001", "name": "Veo 3.1 Fast"},
        ],
        "supportedAspectRatios": ["16:9", "1:1", "9:16"],
        "supportedDurations": [8],
        "maxDuration": 8,
    },
}


def get_provider(provider_id: str) -> dict | None:
    return VIDEO_PROVIDERS.get(provider_id)


def get_all_providers() -> dict:
    return VIDEO_PROVIDERS
