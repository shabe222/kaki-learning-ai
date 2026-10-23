"""Apply/verify/revert the strict PC02B two-table migration."""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from database.artifact_foundation_migration import REVISION, downgrade, upgrade, verify


def snapshot(path: Path) -> dict:
    connection = sqlite3.connect(path)
    try:
        tables = sorted(row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ))
        count_names = [
            "generated_items", "generated_item_versions", "learning_resources",
            "workspace_learning_events", "artifact_reviews", "artifact_lineage_edges",
        ]
        return {
            "database": str(path.resolve()),
            "bytes": path.stat().st_size,
            "tables": [name for name in tables if name in count_names],
            "counts": {
                name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in count_names if name in tables
            },
            "foreign_key_check": list(connection.execute("PRAGMA foreign_key_check")),
        }
    finally:
        connection.close()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("upgrade", "verify", "downgrade"))
    parser.add_argument("--database", default="ml_learning.db")
    args = parser.parse_args()
    path = Path(args.database).resolve()
    before = snapshot(path)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    try:
        if args.action == "upgrade":
            await upgrade(engine)
        elif args.action == "verify":
            await verify(engine)
        else:
            await downgrade(engine)
    finally:
        await engine.dispose()
    after = snapshot(path)
    print(json.dumps({
        "revision": REVISION,
        "action": args.action,
        "result": "PASS",
        "before": before,
        "after": after,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
