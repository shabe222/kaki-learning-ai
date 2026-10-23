from sqlalchemy.ext.asyncio import (
    create_async_engine, AsyncSession
)
try:
    # SQLAlchemy 2.0+ 写法
    from sqlalchemy.ext.asyncio import async_sessionmaker
except ImportError:
    # SQLAlchemy 1.4 兼容写法
    from sqlalchemy.orm import sessionmaker as async_sessionmaker
from typing import Optional
from contextlib import asynccontextmanager
from redis import asyncio as aioredis
from redis.asyncio import Redis
import logging
from database.models import Base

logger = logging.getLogger(__name__)

# ===== 全局连接实例 =====
_async_engine = None
_async_session_factory = None
_redis_client: Optional[Redis] = None


# ===== PostgreSQL 连接 =====

async def init_db(database_url: str) -> None:
    """初始化数据库连接并创建表"""
    global _async_engine, _async_session_factory

    logger.info(f"[DB] 连接数据库: {database_url[:50]}...")

    _async_engine = create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
        pool_recycle=3600,
    )

    # === P2 数据安全：SQLite 启用 WAL 模式 + synchronous=NORMAL 减少崩溃丢数据 ===
    # WAL 提供更鲁棒的并发写入，NORMAL 平衡性能与安全（不会因断电丢失已确认事务）
    if database_url.startswith("sqlite"):
        from sqlalchemy import event
        @event.listens_for(_async_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _):
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA journal_mode = WAL")       # WAL 模式：读写并发 + 更鲁棒
            cur.execute("PRAGMA synchronous = NORMAL")     # 折中：写盘 + 性能
            cur.execute("PRAGMA foreign_keys = ON")        # 启用外键
            cur.execute("PRAGMA busy_timeout = 5000")      # 5秒忙等待超时，避免无限挂起
            cur.close()

    _async_session_factory = async_sessionmaker(
        _async_engine,
        class_=AsyncSession,
        expire_on_commit=False
    )

    # 自动建表（开发环境）
    async with _async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 兼容旧库：补齐可能缺失的列（SQLite 不支持 IF NOT EXISTS 列，用 PRAGMA 检测）
    await _ensure_column(_async_engine, "learning_plans", "target_role", "VARCHAR(100)")
    await _ensure_column(_async_engine, "learning_plans", "weekly_hours", "FLOAT DEFAULT 10.0")
    await _ensure_column(_async_engine, "learning_plans", "profile_snapshot", "JSON")
    await _ensure_column(_async_engine, "learning_plans", "progress_pct", "FLOAT DEFAULT 0.0")
    await _ensure_column(_async_engine, "chat_sessions", "response_style", "VARCHAR(20)")
    await _ensure_column(_async_engine, "chat_sessions", "mode", "VARCHAR(20)")
    await _ensure_column(_async_engine, "chat_sessions", "active_material_id", "VARCHAR(64)")
    await _ensure_column(_async_engine, "generated_items", "current_version_id", "INTEGER")
    await _ensure_column(_async_engine, "generated_items", "current_version_number", "INTEGER NOT NULL DEFAULT 0")
    await _ensure_column(_async_engine, "generated_items", "updated_at", "DATETIME")
    await _ensure_column(_async_engine, "darya_long_term_memories", "scope", "VARCHAR(20) NOT NULL DEFAULT 'ACCOUNT'")
    await _ensure_column(_async_engine, "generated_item_versions", "private_data_snapshot", "JSON")
    await _ensure_column(_async_engine, "tutor_sessions", "workflow_checkpoint", "JSON")
    await _ensure_column(_async_engine, "tutor_sessions", "session_summary", "JSON")
    await _ensure_column(_async_engine, "tutor_sessions", "learner_state_version", "VARCHAR(100)")
    await _ensure_column(_async_engine, "tutor_sessions", "workflow_revision", "INTEGER NOT NULL DEFAULT 0")
    await _ensure_column(_async_engine, "tutor_sessions", "last_transition_key", "VARCHAR(128)")
    await _ensure_column(_async_engine, "tutor_sessions", "pause_reason", "VARCHAR(300)")
    await _ensure_column(_async_engine, "tutor_sessions", "paused_at", "DATETIME")
    await _ensure_column(_async_engine, "tutor_sessions", "resumed_at", "DATETIME")
    await _ensure_column(_async_engine, "tutor_sessions", "completed_at", "DATETIME")
    # Batch C tables are created by metadata; explicit table reconciliation
    # below keeps old SQLite/production databases additive and idempotent.

    # 兜底：补建「在 Base.metadata 注册过、但老库里没建出」的整张表
    # ——SQLAlchemy 的 create_all 对已存在 DB 不会补建运行期后加的新模型，
    # 这里用 _ensure_table 强制同步一次 metadata。
    await _ensure_tables(_async_engine, Base)
    await ensure_learning_graph_orchestrator_schema(_async_engine)
    # PC02B authority tables are strict: startup must not continue with a
    # partially migrated review/lineage foundation.
    from database.artifact_foundation_migration import upgrade as upgrade_artifact_foundation
    await upgrade_artifact_foundation(_async_engine)

    # 初始化管理员账号（幂等）
    try:
        from services.admin_init import ensure_initial_admin
        async with _async_session_factory() as session:
            await ensure_initial_admin(session)
    except Exception as e:
        logger.warning(f"[DB] 管理员初始化失败（不影响主流程）: {e}")

    logger.info("[DB] 数据库初始化完成，所有表已创建")


def session_scope():
    """A session for work that outlives the request that started it.

    后台任务（研究、视频）不能用请求的会话：请求返回时那个会话已经关了，
    而任务还要继续写状态。
    """
    if _async_session_factory is None:
        raise RuntimeError("[DB] 数据库未初始化，请先调用 init_db()")
    return _async_session_factory()


async def _ensure_column(engine, table: str, column: str, col_type: str) -> None:
    """如果表中缺某列，添加它（轻量迁移，避免老库表结构不一致）
    兼容 SQLite（用 PRAGMA table_info）和 PostgreSQL（用 information_schema.columns）
    """
    try:
        async with engine.begin() as conn:
            from sqlalchemy import text
            dialect = engine.dialect.name
            if dialect == "sqlite":
                # SQLite: 用 PRAGMA table_info
                r = await conn.execute(text(f"PRAGMA table_info({table})"))
                cols = {row[1] for row in r.fetchall()}  # row[1] = name
                if column in cols:
                    return
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            else:
                # PostgreSQL / MySQL: 用 information_schema
                r = await conn.execute(text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = :c"
                ), {"t": table, "c": column})
                if r.scalar() is not None:
                    return
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
            logger.info(f"[DB-MIGRATE] {table}.{column} ADD COLUMN {col_type}")
    except Exception as e:
        logger.warning(f"[DB-MIGRATE] skip {table}.{column}: {e}")


async def _ensure_tables(engine, base) -> None:
    """兜底：把 Base.metadata 里所有表都过一遍，缺的 create_all 补建。

    SQLAlchemy 的 `Base.metadata.create_all` 只对「还没建过」的库生效；
    老的 SQLite/PG 库里漏掉的整张表（比如运行期后加的 AgentInvocation）不会自动建。
    这里再跑一次 create_all，让 metadata 与 DB schema 同步。
    """
    try:
        async with engine.begin() as conn:
            await conn.run_sync(base.metadata.create_all)
        logger.info("[DB-MIGRATE] ensure_tables 同步完成")
    except Exception as e:
        logger.warning(f"[DB-MIGRATE] ensure_tables 失败（不影响主流程）: {e}")


_ORCHESTRATOR_COLUMNS = {
    "learning_graphs": {
        "external_id": "VARCHAR(100)",
        "graph_revision": "INTEGER NOT NULL DEFAULT 0",
        "graph_summary": "TEXT",
    },
    "graph_nodes": {
        "external_id": "VARCHAR(100)",
        "continuity_key": "VARCHAR(100)",
        "unit_id": "VARCHAR(100)",
        "summary": "TEXT",
        "node_type": "VARCHAR(30)",
        "learning_objectives": "JSON",
        "key_concepts": "JSON",
        "mastery_evidence": "JSON",
        "source_refs": "JSON",
        "resource_plan_ids": "JSON",
    },
}

_ORCHESTRATOR_TABLES = (
    "graph_learning_units",
    "graph_knowledge_edges",
    "graph_resource_plans",
    "graph_resource_generation_runs",
    "graph_orchestration_runs",
)

_ORCHESTRATOR_UNIQUE_INDEXES = {
    "uq_learning_graphs_user_external_idx": (
        "learning_graphs", "user_id, external_id", "external_id IS NOT NULL",
    ),
    "uq_graph_nodes_graph_external_idx": (
        "graph_nodes", "graph_id, external_id", "external_id IS NOT NULL",
    ),
    "uq_graph_nodes_graph_continuity_idx": (
        "graph_nodes", "graph_id, continuity_key", "continuity_key IS NOT NULL",
    ),
    "uq_graph_resource_plans_node_continuity_idx": (
        "graph_resource_plans", "node_id, continuity_key", None,
    ),
    "uq_graph_resource_runs_user_execution_idx": (
        "graph_resource_generation_runs", "user_id, execution_key", None,
    ),
}


async def _sqlite_unique_column_sets(conn, table_name: str) -> set[tuple[str, ...]]:
    from sqlalchemy import text

    rows = (await conn.execute(text(f"PRAGMA index_list({table_name})"))).fetchall()
    result: set[tuple[str, ...]] = set()
    for row in rows:
        if not row[2]:
            continue
        info = (await conn.execute(text(
            f'PRAGMA index_info("{row[1]}")'
        ))).fetchall()
        result.add(tuple(item[2] for item in info))
    return result


def _rebuild_sqlite_table(sync_conn, table_name: str) -> None:
    """Rebuild one companion table from current metadata, preserving every row."""
    from sqlalchemy.schema import CreateTable

    table = Base.metadata.tables[table_name]
    temporary = f"{table_name}_constraint_migration_v013"
    exists = sync_conn.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (temporary,),
    ).scalar()
    if exists:
        raise RuntimeError(f"stale constraint migration table exists: {temporary}")

    ddl = str(CreateTable(table).compile(dialect=sync_conn.dialect))
    marker = f"CREATE TABLE {table_name}"
    if marker not in ddl:
        raise RuntimeError(f"cannot compile migration DDL for {table_name}")
    sync_conn.exec_driver_sql(ddl.replace(
        marker, f"CREATE TABLE {temporary}", 1,
    ))
    columns = ", ".join(f'"{column.name}"' for column in table.columns)
    sync_conn.exec_driver_sql(
        f"INSERT INTO {temporary} ({columns}) SELECT {columns} FROM {table_name}"
    )
    sync_conn.exec_driver_sql(f"DROP TABLE {table_name}")
    sync_conn.exec_driver_sql(f"ALTER TABLE {temporary} RENAME TO {table_name}")
    for index in table.indexes:
        index.create(sync_conn, checkfirst=True)


async def _prepare_sqlite_orchestrator_constraints(engine) -> None:
    """Correct the early v0.1.3 constraints before the strict schema transaction."""
    from sqlalchemy import text

    async with engine.connect() as conn:
        await conn.execute(text("PRAGMA foreign_keys=OFF"))
        await conn.commit()
        try:
            async with conn.begin():
                table_names = {
                    row[0] for row in (await conn.execute(text(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ))).fetchall()
                }
                if "graph_resource_plans" in table_names:
                    uniques = await _sqlite_unique_column_sets(
                        conn, "graph_resource_plans",
                    )
                    if (
                        ("graph_id", "continuity_key") in uniques
                        or ("execution_task_id",) in uniques
                    ):
                        await conn.run_sync(
                            lambda sync_conn: _rebuild_sqlite_table(
                                sync_conn, "graph_resource_plans",
                            )
                        )
                if "graph_resource_generation_runs" in table_names:
                    uniques = await _sqlite_unique_column_sets(
                        conn, "graph_resource_generation_runs",
                    )
                    if ("execution_key",) in uniques:
                        await conn.run_sync(
                            lambda sync_conn: _rebuild_sqlite_table(
                                sync_conn, "graph_resource_generation_runs",
                            )
                        )
        finally:
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            await conn.commit()
            violations = (await conn.execute(text("PRAGMA foreign_key_check"))).fetchall()
            if violations:
                raise RuntimeError(
                    f"orchestrator constraint migration broke foreign keys: {violations[:3]}"
                )


async def _migrate_resource_run_identity_scope(conn, dialect: str) -> None:
    """Replace the v0.1.3 global execution-key constraint without losing runs."""
    from sqlalchemy import text

    if dialect == "sqlite":
        index_rows = (await conn.execute(text(
            "PRAGMA index_list(graph_resource_generation_runs)"
        ))).fetchall()
        unique_columns: set[tuple[str, ...]] = set()
        for row in index_rows:
            if not row[2]:
                continue
            info = (await conn.execute(text(
                f'PRAGMA index_info("{row[1]}")'
            ))).fetchall()
            unique_columns.add(tuple(item[2] for item in info))
        if ("execution_key",) not in unique_columns:
            return

        legacy_table = "graph_resource_generation_runs_global_scope_v013"
        legacy_exists = await conn.scalar(text(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=:name"
        ), {"name": legacy_table})
        if legacy_exists:
            raise RuntimeError("stale resource-run scope migration table exists")

        await conn.execute(text(
            f"ALTER TABLE graph_resource_generation_runs RENAME TO {legacy_table}"
        ))
        legacy_indexes = (await conn.execute(text(
            f"PRAGMA index_list({legacy_table})"
        ))).fetchall()
        for row in legacy_indexes:
            # SQLite-owned autoindexes disappear with the legacy table. Named
            # indexes must be released before SQLAlchemy creates the new table.
            if len(row) > 3 and row[3] == "c":
                quoted = str(row[1]).replace('"', '""')
                await conn.execute(text(f'DROP INDEX "{quoted}"'))

        table = Base.metadata.tables["graph_resource_generation_runs"]
        await conn.run_sync(lambda sync_conn: table.create(sync_conn, checkfirst=False))
        columns = ", ".join(f'"{column.name}"' for column in table.columns)
        await conn.execute(text(
            f"INSERT INTO graph_resource_generation_runs ({columns}) "
            f"SELECT {columns} FROM {legacy_table}"
        ))
        await conn.execute(text(f"DROP TABLE {legacy_table}"))
        return

    if dialect == "postgresql":
        await conn.execute(text(
            "ALTER TABLE graph_resource_generation_runs "
            "DROP CONSTRAINT IF EXISTS uq_graph_resource_runs_execution_key"
        ))
        await conn.execute(text(
            "DROP INDEX IF EXISTS uq_graph_resource_runs_execution_key"
        ))


def _verify_orchestrator_companion_schema(sync_conn) -> None:
    """Fail startup when an existing companion table is only partially compatible."""
    from sqlalchemy import UniqueConstraint, inspect

    inspector = inspect(sync_conn)
    errors: list[str] = []
    for table_name in _ORCHESTRATOR_TABLES:
        table = Base.metadata.tables[table_name]
        expected_columns = set(table.columns.keys())
        actual_columns = {row["name"] for row in inspector.get_columns(table_name)}
        missing_columns = sorted(expected_columns - actual_columns)
        if missing_columns:
            errors.append(f"{table_name} missing columns {missing_columns}")

        expected_foreign_keys = {
            (
                foreign_key.parent.name,
                foreign_key.column.table.name,
                foreign_key.column.name,
            )
            for foreign_key in table.foreign_keys
        }
        actual_foreign_keys: set[tuple[str, str, str]] = set()
        for foreign_key in inspector.get_foreign_keys(table_name):
            referred_table = foreign_key.get("referred_table")
            for local, remote in zip(
                foreign_key.get("constrained_columns") or [],
                foreign_key.get("referred_columns") or [],
            ):
                actual_foreign_keys.add((local, referred_table, remote))
        missing_foreign_keys = sorted(expected_foreign_keys - actual_foreign_keys)
        if missing_foreign_keys:
            errors.append(f"{table_name} missing foreign keys {missing_foreign_keys}")

        expected_uniques = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, UniqueConstraint)
        }
        actual_uniques = {
            tuple(item.get("column_names") or [])
            for item in inspector.get_unique_constraints(table_name)
        }
        actual_uniques.update({
            tuple(item.get("column_names") or [])
            for item in inspector.get_indexes(table_name)
            if item.get("unique")
        })
        missing_uniques = sorted(expected_uniques - actual_uniques)
        if missing_uniques:
            errors.append(f"{table_name} missing unique constraints {missing_uniques}")

    if errors:
        raise RuntimeError("orchestrator companion schema mismatch: " + "; ".join(errors))


async def ensure_learning_graph_orchestrator_schema(engine) -> None:
    """Strict additive migration for the v0.1.3 graph aggregate.

    Legacy rows remain nullable and are never assigned invented identities or
    academic contracts.  Unlike the general best-effort compatibility helper,
    any failure here is fatal so the application cannot advertise a partially
    migrated formal graph contract.
    """
    from sqlalchemy import text

    if engine.dialect.name == "sqlite":
        await _prepare_sqlite_orchestrator_constraints(engine)

    async with engine.begin() as conn:
        dialect = engine.dialect.name
        for table, columns in _ORCHESTRATOR_COLUMNS.items():
            if dialect == "sqlite":
                result = await conn.execute(text(f"PRAGMA table_info({table})"))
                existing = {row[1] for row in result.fetchall()}
            else:
                result = await conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :table"
                ), {"table": table})
                existing = {row[0] for row in result.fetchall()}
            for column, definition in columns.items():
                if column not in existing:
                    await conn.execute(text(
                        f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
                    ))

        def _create_companion_tables(sync_conn):
            for table_name in _ORCHESTRATOR_TABLES:
                Base.metadata.tables[table_name].create(sync_conn, checkfirst=True)

        await conn.run_sync(_create_companion_tables)
        await _migrate_resource_run_identity_scope(conn, dialect)

        if dialect == "postgresql":
            await conn.execute(text(
                "ALTER TABLE graph_resource_plans "
                "DROP CONSTRAINT IF EXISTS uq_graph_resource_plans_graph_continuity"
            ))
            await conn.execute(text(
                "ALTER TABLE graph_resource_plans "
                "DROP CONSTRAINT IF EXISTS uq_graph_resource_plans_execution_task"
            ))

        for index_name, (table, columns, predicate) in _ORCHESTRATOR_UNIQUE_INDEXES.items():
            where = (
                f" WHERE {predicate}"
                if predicate and dialect in {"sqlite", "postgresql"}
                else ""
            )
            await conn.execute(text(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
                f"ON {table} ({columns}){where}"
            ))

        if dialect == "sqlite":
            index_rows = (await conn.execute(text(
                "PRAGMA index_list(graph_resource_generation_runs)"
            ))).fetchall()
            unique_columns: set[tuple[str, ...]] = set()
            for row in index_rows:
                if not row[2]:
                    continue
                info = (await conn.execute(text(
                    f'PRAGMA index_info("{row[1]}")'
                ))).fetchall()
                unique_columns.add(tuple(item[2] for item in info))
            if ("user_id", "execution_key") not in unique_columns:
                raise RuntimeError("resource execution identity is not user scoped")
            if ("execution_key",) in unique_columns:
                raise RuntimeError("global resource execution identity remains active")

        await conn.run_sync(_verify_orchestrator_companion_schema)

        # Prove the additive contract before the transaction is allowed to end.
        for table, columns in _ORCHESTRATOR_COLUMNS.items():
            if dialect == "sqlite":
                result = await conn.execute(text(f"PRAGMA table_info({table})"))
                actual = {row[1] for row in result.fetchall()}
            else:
                result = await conn.execute(text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = :table"
                ), {"table": table})
                actual = {row[0] for row in result.fetchall()}
            missing = set(columns) - actual
            if missing:
                raise RuntimeError(f"orchestrator migration missing {table}: {sorted(missing)}")

    logger.info("[DB-MIGRATE] learning graph orchestrator v0.1.3 schema ready")


async def close_db() -> None:
    """关闭数据库连接"""
    global _async_engine

    if _async_engine:
        await _async_engine.dispose()
        _async_engine = None
        logger.info("[DB] 数据库连接已关闭")


@asynccontextmanager
async def get_db_session() -> AsyncSession:
    """获取数据库会话（兼容 context manager 与 Depends 两种用法）"""
    if _async_session_factory is None:
        raise RuntimeError("[DB] 数据库未初始化，请先调用 init_db()")

    session = _async_session_factory()
    try:
        yield session
    finally:
        await session.close()


def get_session_factory():
    """返回异步 session factory（供 router 直接使用）"""
    return _async_session_factory


# 直接获取 session 实例（非依赖注入场景）
AsyncSessionLocal = get_db_session


# ===== Redis 连接 =====

async def init_redis(redis_url: str) -> Redis:
    """初始化Redis连接（失败时降级为内存缓存）"""
    global _redis_client

    logger.info(f"[Redis] 连接: {redis_url}")

    try:
        _redis_client = aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True,
            max_connections=20,
            socket_timeout=5,
            socket_connect_timeout=5,
        )

        # 连接测试
        await _redis_client.ping()
        logger.info("[Redis] 连接成功")
        return _redis_client
    except Exception as e:
        logger.warning(f"[Redis] 连接失败（降级为内存缓存）: {e}")
        _redis_client = None
        return None


async def close_redis() -> None:
    """关闭Redis连接"""
    global _redis_client

    if _redis_client:
        await _redis_client.close()
        _redis_client = None
        logger.info("[Redis] 连接已关闭")


def get_redis() -> Optional[Redis]:
    """获取Redis客户端"""
    return _redis_client
