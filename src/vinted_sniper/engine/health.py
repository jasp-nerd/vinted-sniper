"""Answering "is this thing still working?"

The most common complaint about tools like this is not that they break, but that you cannot
tell whether they have. "No notifications" looks identical whether the search is quiet, the
address is blocked, or the process died an hour ago. Everything here exists to separate
those three cases.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any

from vinted_sniper.db.repo import Repo

# If the heartbeat is older than this, the process is wedged rather than idle. Comfortably
# longer than the slowest normal check plus its backoff.
HEARTBEAT_STALE_S = 300

# Checks without a new listing before the health view calls a search stale. The watchdog
# uses its own configurable threshold; this is only for the one-word summary.
STALE_SUMMARY_CYCLES = 10


@dataclass(frozen=True, slots=True)
class SearchHealth:
    query_id: int
    name: str
    tld: str
    paused: bool
    last_success_at: int | None
    last_status: str | None
    last_error: str | None
    newest_listing_at: int | None
    checks_without_new_listings: int
    items_total: int
    blocks: int
    rate_limits: int

    @property
    def state(self) -> str:
        """A one-word summary, chosen so the unhappy cases are never mistaken for quiet."""
        if self.paused:
            return "paused"
        if self.last_success_at is None:
            return "starting"
        if self.last_status and self.last_status != "ok":
            return "failing"
        if self.checks_without_new_listings >= STALE_SUMMARY_CYCLES:
            return "stale"
        return "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "name": self.name,
            "tld": self.tld,
            "state": self.state,
            "last_success_at": self.last_success_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "newest_listing_at": self.newest_listing_at,
            "checks_without_new_listings": self.checks_without_new_listings,
            "items_total": self.items_total,
            "blocks": self.blocks,
            "rate_limits": self.rate_limits,
        }


@dataclass(frozen=True, slots=True)
class Snapshot:
    generated_at: int
    heartbeat_at: int | None
    searches: list[SearchHealth]
    queued_notifications: int
    active_destinations: int

    @property
    def alive(self) -> bool:
        if self.heartbeat_at is None:
            return False
        return (self.generated_at - self.heartbeat_at) < HEARTBEAT_STALE_S

    def as_dict(self) -> dict[str, Any]:
        return {
            "alive": self.alive,
            "generated_at": self.generated_at,
            "heartbeat_at": self.heartbeat_at,
            "queued_notifications": self.queued_notifications,
            "active_destinations": self.active_destinations,
            "searches": [search.as_dict() for search in self.searches],
        }


async def snapshot(repo: Repo) -> Snapshot:
    """Gather the current state of every search in one pass."""
    queries = await repo.list_queries()
    states = {state.query_id: state for state in await repo.all_states()}

    searches = []
    for query in queries:
        state = states.get(query.id)
        searches.append(
            SearchHealth(
                query_id=query.id,
                name=query.name,
                tld=query.tld,
                paused=query.paused,
                last_success_at=state.last_success_at if state else None,
                last_status=state.last_status if state else None,
                last_error=state.last_error if state else None,
                newest_listing_at=state.newest_raw_ts if state else None,
                checks_without_new_listings=state.stale_cycles if state else 0,
                items_total=state.items_seen_total if state else 0,
                blocks=state.count_403 if state else 0,
                rate_limits=state.count_429 if state else 0,
            )
        )

    heartbeat = await repo.get_state_value("heartbeat_at")
    return Snapshot(
        generated_at=int(time.time()),
        heartbeat_at=int(heartbeat) if heartbeat else None,
        searches=searches,
        queued_notifications=await repo.outbox_depth(),
        active_destinations=len(await repo.list_destinations(active_only=True)),
    )


class Heartbeat:
    """Writes a timestamp the container health check can read.

    Checking that the process exists proves very little; a poller can be alive and stuck.
    This only gets written when the main loop actually comes round again.
    """

    def __init__(self, repo: Repo, stop: asyncio.Event, *, interval_s: float = 30.0) -> None:
        self._repo = repo
        self._stop = stop
        self._interval_s = interval_s

    async def run(self) -> None:
        while not self._stop.is_set():
            await self._repo.heartbeat()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._interval_s)


async def is_alive(repo: Repo) -> bool:
    """Used by `vinted-sniper heartbeat`, which is what Docker's health check runs."""
    raw = await repo.get_state_value("heartbeat_at")
    if raw is None:
        return False
    return (int(time.time()) - int(raw)) < HEARTBEAT_STALE_S
