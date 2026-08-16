"""SQLite connection handling.

One connection for the whole process, with writes serialised behind a lock. SQLite can
handle more than that, but this app has one writer by design and a single connection makes
the concurrency story something you can hold in your head.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from types import TracebackType
from typing import Any, Self

import aiosqlite

_PRAGMAS = (
    # WAL lets the web UI read while the pollers write.
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
    "PRAGMA busy_timeout=5000",
)


class Database:
    """Thin wrapper around one aiosqlite connection."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    @property
    def path(self) -> Path:
        return self._path

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(self._path)
        conn.row_factory = aiosqlite.Row
        for pragma in _PRAGMAS:
            await conn.execute(pragma)
        await conn.commit()
        self._conn = conn

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    @property
    def _live(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() was never awaited")
        return self._conn

    # --- Reads (no lock; WAL allows concurrent readers) -------------------------------

    async def fetch_one(self, sql: str, params: Sequence[Any] = ()) -> aiosqlite.Row | None:
        async with self._live.execute(sql, params) as cursor:
            return await cursor.fetchone()

    async def fetch_all(self, sql: str, params: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        async with self._live.execute(sql, params) as cursor:
            return list(await cursor.fetchall())

    async def fetch_value(self, sql: str, params: Sequence[Any] = ()) -> Any:
        row = await self.fetch_one(sql, params)
        return None if row is None else row[0]

    # --- Writes (serialised) ----------------------------------------------------------

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        """Run one statement and commit. Returns lastrowid."""
        async with self._write_lock:
            cursor = await self._live.execute(sql, params)
            await self._live.commit()
            return cursor.lastrowid or 0

    async def execute_many(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        async with self._write_lock:
            await self._live.executemany(sql, rows)
            await self._live.commit()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Run several statements atomically.

        Recording an item and queueing its notifications has to be all-or-nothing;
        otherwise a crash mid-write either drops the alert or replays it.
        """
        async with self._write_lock:
            try:
                yield self._live
            except BaseException:
                await self._live.rollback()
                raise
            await self._live.commit()
