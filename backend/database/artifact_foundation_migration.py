"""Strict additive/reversible migration for DARYA canonical artifact foundation V1."""
from __future__ import annotations

from sqlalchemy import inspect, text

from database.models import Base


REVISION = "pc02b_canonical_artifact_foundation_v1"
TABLES = ("artifact_reviews", "artifact_lineage_edges")


def _verify(sync_conn) -> None:
    inspector = inspect(sync_conn)
    missing_tables = [name for name in TABLES if not inspector.has_table(name)]
    if missing_tables:
        raise RuntimeError(f"{REVISION} missing tables: {missing_tables}")
    required = {
        "artifact_reviews": {
            "review_id", "user_id", "artifact_id", "artifact_version_id",
            "version_digest", "review_contract_version", "round", "reviewer_role",
            "authority_kind", "provider", "model", "verdict", "scores", "safe_summary", "issues_json",
            "preserve_json", "operation_key", "operation_guard", "created_at",
        },
        "artifact_lineage_edges": {
            "lineage_edge_id", "user_id", "source_artifact_id", "source_version_id",
            "source_version_digest", "source_block_refs", "target_artifact_id",
            "target_version_id", "transform_type", "transform_contract_version",
            "knowledge_refs", "source_refs", "learner_context_ref",
            "safe_operation_summary", "generator_provenance", "operation_key",
            "operation_guard", "created_at",
        },
    }
    for table_name, expected in required.items():
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        missing = sorted(expected - actual)
        if missing:
            raise RuntimeError(f"{REVISION} {table_name} missing columns: {missing}")


async def upgrade(engine) -> None:
    """Create only the two authorized foundation tables and verify atomically."""
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: [
                Base.metadata.tables[name].create(sync_conn, checkfirst=True)
                for name in TABLES
            ]
        )
        def add_review_authority(sync_conn) -> None:
            columns = {column["name"] for column in inspect(sync_conn).get_columns("artifact_reviews")}
            if "authority_kind" not in columns:
                sync_conn.execute(text(
                    "ALTER TABLE artifact_reviews ADD COLUMN authority_kind VARCHAR(32) NOT NULL DEFAULT 'UNTRUSTED'"
                ))
        await conn.run_sync(add_review_authority)
        await conn.run_sync(_verify)


async def verify(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(_verify)


async def downgrade(engine) -> None:
    """Explicit rollback; never called automatically by application startup."""
    async with engine.begin() as conn:
        for table_name in reversed(TABLES):
            await conn.execute(text(f'DROP TABLE IF EXISTS "{table_name}"'))
