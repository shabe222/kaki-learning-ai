"""Durable version-bound code run facts for workspace code artifacts."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import GeneratedItem, GeneratedItemCodeRun, GeneratedItemVersion
from services.workspace_artifact_versions import (
    ArtifactVersionNotFound,
    get_owned_artifact,
    get_version,
)
from services.workspace_code import rerun_python_code


class CodeRunError(ValueError):
    code = "CODE_RUN_ERROR"
    status_code = 409


class CodeRunNotFound(CodeRunError):
    code = "CODE_RUN_NOT_FOUND"
    status_code = 404


class CodeRunConflict(CodeRunError):
    code = "CODE_RUN_CONFLICT"
    status_code = 409


class CodeRunInvalid(CodeRunError):
    code = "CODE_RUN_INVALID"
    status_code = 422


_TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "SANDBOX_UNAVAILABLE"}
_ALLOWED_OBSERVATIONS = {"NOT_ANALYZED", "EXPECTED", "UNEXPECTED", "NEEDS_EXPLANATION"}
_HOST_PATH = re.compile(r"(?:[A-Za-z]:\\|/)(?:[^\s:'\"]+/){1,8}[^\s:'\"]+")
_SECRETISH = re.compile(r"(?i)(api[_-]?key|access[_-]?token|secret|password)\s*[:=]\s*[^\s,;]+")
_LINE = re.compile(r"(?:line|第)\s*(\d{1,6})", re.I)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _execution_digest(user_id: int, artifact_id: int, version_id: int, key: str) -> str:
    return _sha(f"code-run-execution-v1\0{user_id}\0{artifact_id}\0{version_id}\0{key}")


def _guard(*, thread_id: str, version_id: int, request_id: str, parameters: Mapping[str, Any]) -> str:
    return _sha(_canonical({
        "thread_id": thread_id,
        "artifact_version_id": version_id,
        "execution_request_id": request_id,
        "parameters": parameters,
    }))


def _safe_parameters(parameters: Mapping[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in dict(parameters or {}).items():
        name = str(key)[:60]
        if name.lower() in {"env", "environment", "token", "secret", "api_key", "command", "cwd"}:
            continue
        if value is None or isinstance(value, (bool, int, float)):
            result[name] = value
        elif isinstance(value, str):
            result[name] = value[:500]
    return result


def _safe_output(value: Any) -> str:
    text = str(value or "").replace("\x00", "")[:8192]
    text = _SECRETISH.sub(r"\1=[redacted]", text)
    # The learner may see an error line, but never the host temp path.
    text = _HOST_PATH.sub("[sandbox-path]", text)
    return text


def _error_line(stderr: str) -> int | None:
    matches = list(_LINE.finditer(stderr or ""))
    if not matches:
        return None
    try:
        return int(matches[-1].group(1))
    except (TypeError, ValueError):
        return None


def _public(row: GeneratedItemCodeRun, *, artifact_version_number: int) -> dict[str, Any]:
    return {
        "run_id": row.id,
        "artifact_id": row.artifact_id,
        "artifact_version_id": row.artifact_version_id,
        "artifact_version_number": int(artifact_version_number),
        "run_number": row.run_number,
        "status": row.status,
        "language": row.language,
        "entrypoint": row.entrypoint,
        "parameters": dict(row.parameters_public or {}),
        "stdout": row.stdout_public or "",
        "stderr": row.stderr_public or "",
        "exit_code": row.exit_code,
        "timed_out": bool(row.timed_out),
        "duration_ms": row.duration_ms,
        "error_category": row.error_category,
        "error_line": row.error_line,
        "output_refs": list(row.output_refs_public or []),
        "observation_status": row.observation_status,
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "started_at": row.started_at.isoformat() if row.started_at else "",
        "finished_at": row.finished_at.isoformat() if row.finished_at else "",
        "terminal": row.status in _TERMINAL,
    }


async def _owned_version(
    db: AsyncSession, *, user_id: int, artifact_id: int, artifact_version_id: int,
) -> tuple[GeneratedItem, GeneratedItemVersion]:
    try:
        item = await get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
    except ArtifactVersionNotFound as exc:
        raise CodeRunNotFound("代码资源不存在或无权访问") from exc
    if item.type != "workspace_code":
        raise CodeRunInvalid("当前学习产物不是可运行代码")
    try:
        version = await get_version(db, item=item, version_id=artifact_version_id)
    except ArtifactVersionNotFound as exc:
        raise CodeRunNotFound("代码版本不存在或无权访问") from exc
    data = dict(version.public_data_snapshot or {})
    if str(data.get("kind") or "code") != "code":
        raise CodeRunInvalid("当前版本不是代码资源")
    return item, version


async def execute_code_version(
    db: AsyncSession,
    *,
    user_id: int,
    thread_id: str,
    artifact_id: int,
    artifact_version_id: int,
    execution_request_id: str,
    idempotency_key: str,
    parameters: Mapping[str, Any] | None = None,
    source_override: str | None = None,
    executor: Callable[..., Any] = rerun_python_code,
    observation_status: str = "NOT_ANALYZED",
) -> tuple[dict[str, Any], bool]:
    """Execute one exact immutable version and persist one idempotent run."""
    if not idempotency_key or len(idempotency_key) > 240:
        raise CodeRunInvalid("缺少有效的运行幂等身份")
    if not execution_request_id or len(execution_request_id) > 120:
        raise CodeRunInvalid("缺少有效的运行请求身份")
    item, version = await _owned_version(
        db, user_id=user_id, artifact_id=artifact_id, artifact_version_id=artifact_version_id,
    )
    data = dict(version.public_data_snapshot or {})
    owned_thread = str(data.get("thread_id") or (item.raw_data or {}).get("thread_id") or "")
    if owned_thread and owned_thread != thread_id:
        raise CodeRunNotFound("代码资源不属于当前会话")
    language = str(data.get("language") or (item.raw_data or {}).get("language") or "python")[:30]
    if language.lower() != "python":
        raise CodeRunInvalid("当前沙箱仅支持 Python 代码")
    execution_source = version.content_snapshot or "" if source_override is None else str(source_override)
    if len(execution_source.encode("utf-8")) > 24 * 1024:
        raise CodeRunInvalid("代码超过当前练习运行环境的 24KB 限制")
    safe_parameters = _safe_parameters(parameters)
    safe_parameters.update({
        "working_copy": source_override is not None,
        "source_digest": f"sha256:{_sha(execution_source)}",
    })
    digest = _execution_digest(user_id, artifact_id, version.id, idempotency_key)
    guard = _guard(
        thread_id=thread_id, version_id=version.id,
        request_id=execution_request_id, parameters=safe_parameters,
    )
    existing = await db.scalar(select(GeneratedItemCodeRun).where(
        GeneratedItemCodeRun.artifact_version_id == version.id,
        GeneratedItemCodeRun.execution_key_digest == digest,
    ))
    if existing is not None:
        if existing.request_guard != guard:
            raise CodeRunConflict("相同幂等身份对应了不同运行参数")
        return _public(existing, artifact_version_number=version.version_number), True

    current_max = await db.scalar(select(func.max(GeneratedItemCodeRun.run_number)).where(
        GeneratedItemCodeRun.artifact_version_id == version.id,
    ))
    run = GeneratedItemCodeRun(
        user_id=user_id,
        thread_id=thread_id,
        artifact_id=artifact_id,
        artifact_version_id=version.id,
        run_number=int(current_max or 0) + 1,
        execution_key_digest=digest,
        request_guard=guard,
        status="PENDING",
        language="python",
        entrypoint="main.py",
        parameters_public=safe_parameters,
        stdout_public="",
        stderr_public="",
        output_refs_public=[],
        observation_status=(observation_status if observation_status in _ALLOWED_OBSERVATIONS else "NOT_ANALYZED"),
    )
    # Database unique constraints are the final concurrency guard. A colliding
    # transaction fails before execution and the router rolls the whole unit back.
    db.add(run)
    await db.flush()
    run.status = "RUNNING"
    run.started_at = _now()
    await db.flush()

    try:
        result = executor(source=execution_source, language=language)
    except TypeError:
        # Test doubles may accept positional args only; production executor remains unchanged.
        result = executor(execution_source, language)
    except Exception as exc:
        run.status = "SANDBOX_UNAVAILABLE"
        run.stderr_public = "代码沙箱暂时不可用，请稍后重试。"
        run.error_category = "sandbox_unavailable"
        run.finished_at = _now()
        run.duration_ms = 0
        await db.flush()
        return _public(run, artifact_version_number=version.version_number), False

    payload = result.to_dict() if hasattr(result, "to_dict") else dict(result or {})
    run.stdout_public = _safe_output(payload.get("stdout"))
    run.stderr_public = _safe_output(payload.get("stderr") or payload.get("error_message"))
    run.exit_code = int(payload.get("exit_code")) if payload.get("exit_code") is not None else None
    run.timed_out = bool(payload.get("timed_out"))
    run.duration_ms = max(0, int(payload.get("duration_ms") or 0))
    run.error_category = str(payload.get("error_category") or "")[:80] or None
    run.error_line = _error_line(run.stderr_public)
    run.output_refs_public = [_safe_output(value) for value in list(payload.get("output_refs") or [])[:20]]
    if run.timed_out:
        run.status = "TIMED_OUT"
    elif run.error_category == "spawn_failed":
        run.status = "SANDBOX_UNAVAILABLE"
    elif run.exit_code == 0:
        run.status = "SUCCEEDED"
    else:
        run.status = "FAILED"
    run.finished_at = _now()
    await db.flush()
    return _public(run, artifact_version_number=version.version_number), False


async def list_code_runs(
    db: AsyncSession, *, user_id: int, artifact_id: int, artifact_version_id: int | None = None,
) -> list[dict[str, Any]]:
    try:
        item = await get_owned_artifact(db, user_id=user_id, artifact_id=artifact_id)
    except ArtifactVersionNotFound as exc:
        raise CodeRunNotFound("代码资源不存在或无权访问") from exc
    if item.type != "workspace_code":
        raise CodeRunNotFound("代码资源不存在或无权访问")
    stmt = select(GeneratedItemCodeRun, GeneratedItemVersion.version_number).join(
        GeneratedItemVersion, GeneratedItemVersion.id == GeneratedItemCodeRun.artifact_version_id,
    ).where(
        GeneratedItemCodeRun.user_id == user_id,
        GeneratedItemCodeRun.artifact_id == artifact_id,
    )
    if artifact_version_id is not None:
        await _owned_version(
            db, user_id=user_id, artifact_id=artifact_id,
            artifact_version_id=artifact_version_id,
        )
        stmt = stmt.where(GeneratedItemCodeRun.artifact_version_id == artifact_version_id)
    rows = list((await db.execute(stmt.order_by(
        GeneratedItemCodeRun.artifact_version_id.desc(),
        GeneratedItemCodeRun.run_number.desc(),
    ))).all())
    return [_public(row, artifact_version_number=version_number) for row, version_number in rows]


async def get_code_run(
    db: AsyncSession, *, user_id: int, artifact_id: int, run_id: int,
) -> dict[str, Any]:
    result = (await db.execute(select(GeneratedItemCodeRun, GeneratedItemVersion.version_number).join(
        GeneratedItemVersion, GeneratedItemVersion.id == GeneratedItemCodeRun.artifact_version_id,
    ).where(
        GeneratedItemCodeRun.id == run_id,
        GeneratedItemCodeRun.user_id == user_id,
        GeneratedItemCodeRun.artifact_id == artifact_id,
    ))).first()
    if result is None:
        raise CodeRunNotFound("代码运行记录不存在或无权访问")
    row, version_number = result
    return _public(row, artifact_version_number=version_number)
