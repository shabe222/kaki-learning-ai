"""Authenticated, thread-scoped runtime state for the standalone AI workspace."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from typing import Dict, Tuple

from config import settings


_runtime: Dict[Tuple[int, str], dict] = {}
_TERMINAL_TTL_SECONDS = 3.0


def _provider() -> str:
    return str(getattr(settings, "LLM_PROVIDER", "unknown") or "unknown").lower()


def _idle() -> dict:
    return {
        "backend": "connected",
        "auth": "valid",
        "provider": _provider(),
        "operation": "idle",
        "state": "idle",
        "error_code": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "_updated_monotonic": time.monotonic(),
    }


def _public(value: dict) -> dict:
    return {key: item for key, item in value.items() if not key.startswith("_")}


def clear_runtime_status() -> None:
    _runtime.clear()


def begin_runtime_operation(user_id: int, thread_id: str, operation: str) -> dict:
    value = _idle()
    value.update({"operation": operation, "state": "running"})
    _runtime[(int(user_id), str(thread_id))] = value
    return _public(value)


def complete_runtime_operation(user_id: int, thread_id: str) -> dict:
    value = _idle()
    value.update({"operation": "completed", "state": "completed"})
    _runtime[(int(user_id), str(thread_id))] = value
    return _public(value)


def fail_runtime_operation(user_id: int, thread_id: str, error_code: str = "OPERATION_FAILED") -> dict:
    safe_code = re.sub(r"[^A-Z0-9_-]", "_", str(error_code or "OPERATION_FAILED").upper())[:80]
    value = _idle()
    value.update({"operation": "failed", "state": "failed", "error_code": safe_code})
    _runtime[(int(user_id), str(thread_id))] = value
    return _public(value)


def get_runtime_status(user_id: int, thread_id: str) -> dict:
    key = (int(user_id), str(thread_id))
    value = _runtime.get(key)
    if value is None:
        return _public(_idle())
    if value.get("state") in {"completed", "failed"}:
        elapsed = time.monotonic() - float(value.get("_updated_monotonic") or 0)
        if elapsed > _TERMINAL_TTL_SECONDS:
            value = _idle()
            _runtime[key] = value
    return _public(value)
