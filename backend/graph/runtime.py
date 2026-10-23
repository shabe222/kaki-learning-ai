"""Runtime loader for optional graph workflow dependencies."""

from __future__ import annotations

from typing import Any, AsyncIterator


async def execute_workflow(*args: Any, **kwargs: Any) -> AsyncIterator[dict]:
    try:
        from graph.workflow import execute_workflow as _execute_workflow
    except ModuleNotFoundError as exc:
        missing = exc.name or "workflow dependency"
        raise RuntimeError(
            f"AI workflow dependency is not installed: {missing}. "
            "Install backend/requirements.txt before using workflow endpoints."
        ) from exc

    async for event in _execute_workflow(*args, **kwargs):
        yield event
