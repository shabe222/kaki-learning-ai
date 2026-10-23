"""Identity-scoped reset helpers for the local DARYA competition demo.

This module is intentionally not exposed as a public HTTP route.  The local
operator script supplies the one allow-listed demo username and performs the
reset inside one database transaction.  Normal users are never selected by a
prefix, wildcard, or caller-provided user id.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import (
    ChatSession,
    DaryaCurrentPerspectiveSnapshot,
    DaryaLearningLoopState,
    DaryaLearningRouteState,
    DaryaLongTermMemory,
    StudentProfile,
    TideArtifactAction,
    TideSharedPerspectiveSnapshot,
    TideUserContextClaim,
    TideWorkspaceObject,
    User,
    UserMemory,
    WorkspaceAgentTask,
    WorkspaceConversationMaterial,
)


DEMO_USERNAME = "darya_phase36_demo"
RESET_CONTRACT_VERSION = "darya_competition_demo_reset_v1"


class DemoResetRefused(ValueError):
    """Raised before mutation when the requested identity is not allow-listed."""


@dataclass(frozen=True)
class DemoResetResult:
    contract_version: str
    username: str
    user_id: int
    created_identity: bool
    memory_policy: str
    deleted_rows: dict[str, int]
    normal_user_count_before: int
    normal_user_count_after: int

    def public_dict(self) -> dict:
        return {
            "contract_version": self.contract_version,
            "username": self.username,
            "user_id": self.user_id,
            "created_identity": self.created_identity,
            "memory_policy": self.memory_policy,
            "deleted_rows": dict(self.deleted_rows),
            "normal_user_count_before": self.normal_user_count_before,
            "normal_user_count_after": self.normal_user_count_after,
            "normal_users_unchanged": self.normal_user_count_before == self.normal_user_count_after,
            "scope": "ONE_ALLOWLISTED_DEMO_IDENTITY",
        }


async def reset_demo_identity(
    db: AsyncSession,
    *,
    username: str,
    password_hash: str,
    memory_policy: str = "clear",
) -> DemoResetResult:
    """Create or reset the single local competition identity.

    ``memory_policy=clear`` is the reproducible default.  ``preserve`` exists
    only for a scenario that explicitly seeds and reuses a confirmed demo
    preference.  Both policies remain scoped to the allow-listed identity.
    """
    if username != DEMO_USERNAME:
        raise DemoResetRefused(
            f"reset refused: username must equal the allow-listed demo identity {DEMO_USERNAME!r}"
        )
    if memory_policy not in {"clear", "preserve"}:
        raise DemoResetRefused("reset refused: memory_policy must be 'clear' or 'preserve'")
    if not password_hash:
        raise DemoResetRefused("reset refused: a non-empty password hash is required")

    normal_before = int(await db.scalar(
        select(func.count(User.id)).where(User.username != DEMO_USERNAME)
    ) or 0)
    user = (await db.execute(select(User).where(User.username == DEMO_USERNAME))).scalar_one_or_none()
    created = user is None
    if user is None:
        user = User(username=DEMO_USERNAME, password_hash=password_hash, email=None, is_active=True)
        db.add(user)
        await db.flush()
    else:
        user.password_hash = password_hash
        user.email = None
        user.avatar_url = None
        user.is_active = True

    user_id = int(user.id)
    deleted: dict[str, int] = {}

    async def remove(model, name: str) -> None:
        result = await db.execute(delete(model).where(model.user_id == user_id))
        deleted[name] = int(result.rowcount or 0)

    # Dependents first; all predicates use the resolved demo user id.
    await remove(TideArtifactAction, "tide_artifact_actions")
    await remove(WorkspaceConversationMaterial, "workspace_conversation_materials")
    await remove(TideUserContextClaim, "tide_user_context_claims")
    await remove(DaryaLearningLoopState, "darya_learning_loop_states")
    await remove(DaryaLearningRouteState, "darya_learning_route_states")
    await remove(DaryaCurrentPerspectiveSnapshot, "darya_current_perspective_snapshots")
    await remove(TideSharedPerspectiveSnapshot, "tide_shared_perspective_snapshots")
    await remove(TideWorkspaceObject, "tide_workspace_objects")
    await remove(WorkspaceAgentTask, "workspace_agent_tasks")
    await remove(ChatSession, "chat_sessions")
    await remove(StudentProfile, "student_profiles")
    if memory_policy == "clear":
        await remove(DaryaLongTermMemory, "darya_long_term_memories")
        await remove(UserMemory, "user_memories")

    await db.flush()
    normal_after = int(await db.scalar(
        select(func.count(User.id)).where(User.username != DEMO_USERNAME)
    ) or 0)
    if normal_after != normal_before:
        raise RuntimeError("demo reset safety invariant failed: normal user count changed")

    return DemoResetResult(
        contract_version=RESET_CONTRACT_VERSION,
        username=DEMO_USERNAME,
        user_id=user_id,
        created_identity=created,
        memory_policy=memory_policy,
        deleted_rows=deleted,
        normal_user_count_before=normal_before,
        normal_user_count_after=normal_after,
    )
