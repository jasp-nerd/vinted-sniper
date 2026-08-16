from __future__ import annotations

from pathlib import Path

import pytest

from vinted_sniper.db import Database, apply_pending, current_version
from vinted_sniper.db.migrations import discover


async def test_fresh_database_gets_full_schema(tmp_path: Path) -> None:
    async with Database(tmp_path / "app.db") as db:
        applied = await apply_pending(db)

        assert applied == len(discover())
        assert await current_version(db) == applied

        tables = {
            row["name"]
            for row in await db.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"queries", "query_state", "destinations", "items", "outbox", "sessions"} <= tables


async def test_second_run_applies_nothing(tmp_path: Path) -> None:
    async with Database(tmp_path / "app.db") as db:
        await apply_pending(db)
        version_after_first = await current_version(db)

        assert await apply_pending(db) == 0
        assert await current_version(db) == version_after_first


async def test_gap_in_numbering_is_rejected(tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0001_init.sql").write_text("CREATE TABLE a (x INTEGER);")
    (migrations / "0003_later.sql").write_text("CREATE TABLE b (x INTEGER);")

    with pytest.raises(ValueError, match="gap or duplicate"):
        discover(migrations)


async def test_database_newer_than_code_is_refused(tmp_path: Path) -> None:
    async with Database(tmp_path / "app.db") as db:
        await db.execute("PRAGMA user_version = 99")

        with pytest.raises(RuntimeError, match="newer release"):
            await apply_pending(db)


async def test_foreign_keys_cascade_on_delete(tmp_path: Path) -> None:
    async with Database(tmp_path / "app.db") as db:
        await apply_pending(db)
        await db.execute(
            "INSERT INTO queries (id, name, url, tld, params_json, created_at, updated_at) "
            "VALUES (1, 'test', 'https://www.vinted.fr/catalog?x=1', 'fr', '{}', 0, 0)"
        )
        await db.execute("INSERT INTO query_state (query_id) VALUES (1)")

        await db.execute("DELETE FROM queries WHERE id = 1")

        assert await db.fetch_one("SELECT 1 FROM query_state WHERE query_id = 1") is None


async def test_poll_interval_floor_is_enforced_by_schema(tmp_path: Path) -> None:
    async with Database(tmp_path / "app.db") as db:
        await apply_pending(db)

        with pytest.raises(Exception, match="CHECK constraint failed"):
            await db.execute(
                "INSERT INTO queries "
                "(name, url, tld, params_json, poll_interval_s, created_at, updated_at) "
                "VALUES ('fast', 'https://www.vinted.fr/catalog?y=1', 'fr', '{}', 2, 0, 0)"
            )
