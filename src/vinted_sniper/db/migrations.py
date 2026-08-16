"""Schema migrations.

Files in migrations/ are named NNNN_description.sql and applied in order. SQLite's
user_version pragma records how far we got. Each file runs in its own transaction, so a
failure leaves the database at the last version that fully applied.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from vinted_sniper.db.connection import Database
from vinted_sniper.log import get_logger

log = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILENAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    path: Path

    @property
    def name(self) -> str:
        return self.path.name


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Return migrations in version order, refusing anything malformed or out of sequence."""
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _FILENAME.match(path.name)
        if match is None:
            raise ValueError(f"Migration {path.name!r} does not match NNNN_lower_snake_case.sql")
        found.append(Migration(version=int(match.group(1)), path=path))

    for expected, migration in enumerate(found, start=1):
        if migration.version != expected:
            raise ValueError(
                f"Migration numbering has a gap or duplicate: expected {expected:04d}, "
                f"found {migration.name}"
            )
    return found


async def current_version(db: Database) -> int:
    version = await db.fetch_value("PRAGMA user_version")
    return int(version or 0)


async def apply_pending(db: Database, directory: Path = MIGRATIONS_DIR) -> int:
    """Apply every migration newer than the recorded version. Returns how many ran."""
    migrations = discover(directory)
    version = await current_version(db)

    if version > len(migrations):
        raise RuntimeError(
            f"Database is at schema version {version} but only {len(migrations)} migrations "
            "exist. This database was written by a newer release; upgrade rather than "
            "downgrade."
        )

    pending = [m for m in migrations if m.version > version]
    for migration in pending:
        sql = migration.path.read_text(encoding="utf-8")
        async with db.transaction() as conn:
            await conn.executescript(sql)
            # executescript commits any open transaction, so set the version after it.
            await conn.execute(f"PRAGMA user_version = {migration.version}")
        log.info("migration.applied", version=migration.version, name=migration.name)

    return len(pending)
