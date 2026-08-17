"""Every SQL statement in the application.

Keeping them here means there is one place to look when the schema changes, and the rest
of the code deals in ordinary Python objects rather than rows.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import aiosqlite

from vinted_sniper.db.connection import Database
from vinted_sniper.vinted.models import Item

# --- Row types ---------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Query:
    """A saved search."""

    id: int
    name: str
    url: str
    tld: str
    params: dict[str, str]
    poll_interval_s: int
    paused: bool
    banned_keywords: list[str] = field(default_factory=list)
    max_total_price: Decimal | None = None
    conditions: list[str] | None = None
    countries: list[str] | None = None


@dataclass(frozen=True, slots=True)
class QueryState:
    """What happened last time we checked a search."""

    query_id: int
    last_polled_at: int | None = None
    last_success_at: int | None = None
    last_status: str | None = None
    last_error: str | None = None
    newest_item_ts: int | None = None
    newest_raw_ts: int | None = None
    stale_cycles: int = 0
    items_seen_total: int = 0
    count_403: int = 0
    count_429: int = 0

    @property
    def is_first_run(self) -> bool:
        """True until a check has completed, which is what suppresses the opening flood."""
        return self.last_success_at is None


@dataclass(frozen=True, slots=True)
class Destination:
    """Somewhere notifications go."""

    id: int
    kind: str
    name: str
    config: dict[str, Any]
    active: bool = True
    notify_status: bool = False
    failure_count: int = 0


@dataclass(frozen=True, slots=True)
class PendingNotification:
    """One queued notification, joined with what it needs to render."""

    outbox_id: int
    destination_id: int
    query_id: int
    query_name: str
    attempts: int
    item: Item
    # When the poller first saw the listing, which is the honest "found it" moment even
    # if delivery retries push the message out later.
    detected_at: int | None = None


def _json_list(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    value = json.loads(raw)
    return [str(v) for v in value] if isinstance(value, list) else None


class Repo:
    """Data access for the whole app."""

    def __init__(self, db: Database) -> None:
        self._db = db

    # --- Searches ------------------------------------------------------------------

    async def add_query(
        self,
        *,
        name: str,
        url: str,
        tld: str,
        params: dict[str, str],
        poll_interval_s: int,
        banned_keywords: list[str] | None = None,
        max_total_price: Decimal | None = None,
    ) -> int:
        now = int(time.time())
        query_id = await self._db.insert(
            "INSERT INTO queries (name, url, tld, params_json, poll_interval_s, "
            "banned_keywords_json, max_total_price, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                name,
                url,
                tld,
                json.dumps(params),
                poll_interval_s,
                json.dumps(banned_keywords or []),
                float(max_total_price) if max_total_price is not None else None,
                now,
                now,
            ),
        )
        await self._db.execute("INSERT INTO query_state (query_id) VALUES (?)", (query_id,))
        return query_id

    async def list_queries(self, *, include_paused: bool = True) -> list[Query]:
        sql = "SELECT * FROM queries"
        if not include_paused:
            sql += " WHERE paused = 0"
        return [self._to_query(row) for row in await self._db.fetch_all(sql + " ORDER BY id")]

    async def get_query(self, query_id: int) -> Query | None:
        row = await self._db.fetch_one("SELECT * FROM queries WHERE id = ?", (query_id,))
        return self._to_query(row) if row else None

    async def find_query_by_url(self, url: str) -> Query | None:
        row = await self._db.fetch_one("SELECT * FROM queries WHERE url = ?", (url,))
        return self._to_query(row) if row else None

    async def set_paused(self, query_id: int, paused: bool) -> None:
        await self._db.execute(
            "UPDATE queries SET paused = ?, updated_at = ? WHERE id = ?",
            (int(paused), int(time.time()), query_id),
        )

    async def delete_query(self, query_id: int) -> None:
        await self._db.execute("DELETE FROM queries WHERE id = ?", (query_id,))

    @staticmethod
    def _to_query(row: aiosqlite.Row) -> Query:
        return Query(
            id=row["id"],
            name=row["name"],
            url=row["url"],
            tld=row["tld"],
            params=json.loads(row["params_json"]),
            poll_interval_s=row["poll_interval_s"],
            paused=bool(row["paused"]),
            banned_keywords=_json_list(row["banned_keywords_json"]) or [],
            max_total_price=(
                Decimal(str(row["max_total_price"])) if row["max_total_price"] is not None else None
            ),
            conditions=_json_list(row["conditions_json"]),
            countries=_json_list(row["countries_json"]),
        )

    # --- Search state --------------------------------------------------------------

    async def get_state(self, query_id: int) -> QueryState:
        row = await self._db.fetch_one("SELECT * FROM query_state WHERE query_id = ?", (query_id,))
        if row is None:
            await self._db.execute(
                "INSERT OR IGNORE INTO query_state (query_id) VALUES (?)", (query_id,)
            )
            return QueryState(query_id=query_id)
        return QueryState(
            query_id=row["query_id"],
            last_polled_at=row["last_polled_at"],
            last_success_at=row["last_success_at"],
            last_status=row["last_status"],
            last_error=row["last_error"],
            newest_item_ts=row["newest_item_ts"],
            newest_raw_ts=row["newest_raw_ts"],
            stale_cycles=row["stale_cycles"],
            items_seen_total=row["items_seen_total"],
            count_403=row["count_403"],
            count_429=row["count_429"],
        )

    async def all_states(self) -> list[QueryState]:
        rows = await self._db.fetch_all("SELECT * FROM query_state")
        return [
            QueryState(
                query_id=row["query_id"],
                last_polled_at=row["last_polled_at"],
                last_success_at=row["last_success_at"],
                last_status=row["last_status"],
                last_error=row["last_error"],
                newest_item_ts=row["newest_item_ts"],
                newest_raw_ts=row["newest_raw_ts"],
                stale_cycles=row["stale_cycles"],
                items_seen_total=row["items_seen_total"],
                count_403=row["count_403"],
                count_429=row["count_429"],
            )
            for row in rows
        ]

    async def record_failure(self, query_id: int, status: str, error: str) -> None:
        """Note a failed check. The counters feed the health view and the troubleshooting docs."""
        column = {"http_403": "count_403", "http_429": "count_429"}.get(status)
        bump = f", {column} = {column} + 1" if column else ""
        await self._db.execute(
            "UPDATE query_state SET last_polled_at = ?, last_status = ?, last_error = ?"
            f"{bump} WHERE query_id = ?",
            (int(time.time()), status, error[:500], query_id),
        )

    async def record_success(
        self,
        query_id: int,
        *,
        newest_raw_ts: int | None,
        newest_item_ts: int | None,
        seen: int,
        stale_cycles: int,
    ) -> None:
        now = int(time.time())
        await self._db.execute(
            "UPDATE query_state SET last_polled_at = ?, last_success_at = ?, last_status = 'ok', "
            "last_error = NULL, newest_raw_ts = ?, "
            "newest_item_ts = COALESCE(?, newest_item_ts), "
            "items_seen_total = items_seen_total + ?, stale_cycles = ? WHERE query_id = ?",
            (now, now, newest_raw_ts, newest_item_ts, seen, stale_cycles, query_id),
        )

    # --- Destinations --------------------------------------------------------------

    async def add_destination(
        self,
        *,
        kind: str,
        name: str,
        config: dict[str, Any],
        notify_status: bool = False,
    ) -> int:
        return await self._db.insert(
            "INSERT INTO destinations (kind, name, config_json, notify_status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (kind, name, json.dumps(config), int(notify_status), int(time.time())),
        )

    async def list_destinations(self, *, active_only: bool = False) -> list[Destination]:
        sql = "SELECT * FROM destinations"
        if active_only:
            sql += " WHERE active = 1"
        return [self._to_destination(row) for row in await self._db.fetch_all(sql + " ORDER BY id")]

    async def get_destination(self, destination_id: int) -> Destination | None:
        row = await self._db.fetch_one("SELECT * FROM destinations WHERE id = ?", (destination_id,))
        return self._to_destination(row) if row else None

    async def update_destination_config(self, destination_id: int, config: dict[str, Any]) -> None:
        await self._db.execute(
            "UPDATE destinations SET config_json = ? WHERE id = ?",
            (json.dumps(config), destination_id),
        )

    async def deactivate_destination(self, destination_id: int, reason: str) -> None:
        """Stop sending to a destination for good.

        Used when the far end says the target is gone — a deleted webhook, a bot the user
        blocked. Retrying those forever is what gets an address rate-limited or banned.
        """
        await self._db.execute(
            "UPDATE destinations SET active = 0, deactivated_reason = ? WHERE id = ?",
            (reason[:500], destination_id),
        )
        await self._db.execute(
            "UPDATE outbox SET status = 'cancelled', last_error = ? "
            "WHERE destination_id = ? AND status IN ('pending', 'sending')",
            (f"destination disabled: {reason[:200]}", destination_id),
        )

    async def note_destination_failure(self, destination_id: int) -> None:
        await self._db.execute(
            "UPDATE destinations SET failure_count = failure_count + 1 WHERE id = ?",
            (destination_id,),
        )

    async def reset_destination_failures(self, destination_id: int) -> None:
        await self._db.execute(
            "UPDATE destinations SET failure_count = 0 WHERE id = ?", (destination_id,)
        )

    @staticmethod
    def _to_destination(row: aiosqlite.Row) -> Destination:
        return Destination(
            id=row["id"],
            kind=row["kind"],
            name=row["name"],
            config=json.loads(row["config_json"]),
            active=bool(row["active"]),
            notify_status=bool(row["notify_status"]),
            failure_count=row["failure_count"],
        )

    # --- Routing -------------------------------------------------------------------

    async def route(self, query_id: int, destination_id: int) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO query_destinations (query_id, destination_id) VALUES (?, ?)",
            (query_id, destination_id),
        )

    async def unroute(self, query_id: int, destination_id: int) -> None:
        await self._db.execute(
            "DELETE FROM query_destinations WHERE query_id = ? AND destination_id = ?",
            (query_id, destination_id),
        )

    async def destination_ids_for_query(self, query_id: int) -> list[int]:
        rows = await self._db.fetch_all(
            "SELECT d.id FROM destinations d "
            "JOIN query_destinations qd ON qd.destination_id = d.id "
            "WHERE qd.query_id = ? AND d.active = 1",
            (query_id,),
        )
        return [row["id"] for row in rows]

    async def status_destination_ids(self) -> list[int]:
        rows = await self._db.fetch_all(
            "SELECT id FROM destinations WHERE active = 1 AND notify_status = 1"
        )
        return [row["id"] for row in rows]

    # --- Items and the outbox ------------------------------------------------------

    async def known_item_ids(self, item_ids: list[int]) -> set[int]:
        """Which of these listings have we already recorded?"""
        if not item_ids:
            return set()
        placeholders = ",".join("?" * len(item_ids))
        rows = await self._db.fetch_all(
            f"SELECT item_id FROM items WHERE item_id IN ({placeholders})", item_ids
        )
        return {row["item_id"] for row in rows}

    async def record_new_items(
        self,
        query: Query,
        items: list[Item],
        destination_ids: list[int],
        *,
        notify: list[Item] | None = None,
        keep_raw: bool = False,
    ) -> int:
        """Store listings and queue their notifications in one transaction.

        Doing both at once is what makes a crash safe: either a listing is recorded and its
        notifications are queued, or neither happened and the next check finds it again.

        `notify` is the subset worth telling someone about, which is not always everything
        being recorded: a search's first check records the whole page so those listings are
        never mistaken for new, while announcing at most one of them. Passing None means
        every recorded listing is announced.
        """
        if not items:
            return 0

        to_announce = items if notify is None else notify

        now = int(time.time())
        async with self._db.transaction() as conn:
            await conn.executemany(
                "INSERT OR IGNORE INTO items (item_id, query_id, tld, title, brand, size, "
                "condition, price, total_price, currency, url, photo_url, photo_urls_json, "
                "photo_ts, seller_login, seller_id, seller_rating, seller_feedback_count, "
                "favourite_count, view_count, raw_json, first_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        item.item_id,
                        query.id,
                        item.tld,
                        item.title,
                        item.brand,
                        item.size,
                        item.condition,
                        float(item.price) if item.price is not None else None,
                        float(item.total_price) if item.total_price is not None else None,
                        item.currency,
                        item.url,
                        item.photo_url,
                        json.dumps(list(item.photo_urls)) if item.photo_urls else None,
                        item.photo_ts,
                        item.seller_login,
                        item.seller_id,
                        item.seller_rating,
                        item.seller_feedback_count,
                        item.favourite_count,
                        item.view_count,
                        json.dumps(item.raw) if (keep_raw and item.raw) else None,
                        now,
                    )
                    for item in items
                ],
            )

            if to_announce and destination_ids:
                await conn.executemany(
                    "INSERT OR IGNORE INTO outbox (item_id, query_id, destination_id, "
                    "next_attempt_at, created_at) VALUES (?, ?, ?, ?, ?)",
                    [
                        (item.item_id, query.id, destination_id, now, now)
                        for item in to_announce
                        for destination_id in destination_ids
                    ],
                )
        return len(items)

    async def prune_items(self, older_than_days: int) -> int:
        cutoff = int(time.time()) - older_than_days * 86_400
        return await self._db.execute("DELETE FROM items WHERE first_seen_at < ?", (cutoff,))

    async def recent_items(self, limit: int = 50) -> list[aiosqlite.Row]:
        return await self._db.fetch_all(
            "SELECT i.*, q.name AS query_name FROM items i "
            "LEFT JOIN queries q ON q.id = i.query_id "
            "ORDER BY i.first_seen_at DESC LIMIT ?",
            (limit,),
        )

    # --- Outbox delivery -----------------------------------------------------------

    async def destinations_with_work(self) -> list[int]:
        rows = await self._db.fetch_all(
            "SELECT DISTINCT destination_id FROM outbox "
            "WHERE status = 'pending' AND next_attempt_at <= ?",
            (int(time.time()),),
        )
        return [row["destination_id"] for row in rows]

    async def claim_batch(
        self, destination_id: int, limit: int, *, lease_seconds: int = 120
    ) -> list[PendingNotification]:
        """Take the next few notifications for a destination.

        Ordered by insertion so listings arrive in the order they were found, and leased so
        that a process killed mid-send does not strand them.
        """
        now = int(time.time())
        async with self._db.transaction() as conn:
            async with conn.execute(
                "SELECT o.id, o.destination_id, o.query_id, o.attempts, q.name AS query_name, "
                "i.* FROM outbox o "
                "JOIN items i ON i.item_id = o.item_id "
                "LEFT JOIN queries q ON q.id = o.query_id "
                "WHERE o.destination_id = ? AND o.status = 'pending' AND o.next_attempt_at <= ? "
                "ORDER BY o.id LIMIT ?",
                (destination_id, now, limit),
            ) as cursor:
                rows = list(await cursor.fetchall())

            if rows:
                await conn.executemany(
                    "UPDATE outbox SET status = 'sending', lease_expires_at = ? WHERE id = ?",
                    [(now + lease_seconds, row["id"]) for row in rows],
                )

        return [
            PendingNotification(
                outbox_id=row["id"],
                destination_id=row["destination_id"],
                query_id=row["query_id"],
                query_name=row["query_name"] or "search",
                attempts=row["attempts"],
                item=Item(
                    item_id=row["item_id"],
                    tld=row["tld"],
                    title=row["title"] or f"Listing {row['item_id']}",
                    url=row["url"],
                    brand=row["brand"],
                    size=row["size"],
                    condition=row["condition"],
                    price=Decimal(str(row["price"])) if row["price"] is not None else None,
                    total_price=(
                        Decimal(str(row["total_price"])) if row["total_price"] is not None else None
                    ),
                    currency=row["currency"],
                    photo_url=row["photo_url"],
                    photo_urls=tuple(json.loads(row["photo_urls_json"] or "[]")),
                    photo_ts=row["photo_ts"],
                    seller_login=row["seller_login"],
                    seller_id=row["seller_id"],
                    seller_rating=row["seller_rating"],
                    seller_feedback_count=row["seller_feedback_count"],
                    favourite_count=row["favourite_count"] or 0,
                    view_count=row["view_count"] or 0,
                ),
                detected_at=row["first_seen_at"],
            )
            for row in rows
        ]

    async def mark_sent(self, outbox_ids: list[int]) -> None:
        if not outbox_ids:
            return
        now = int(time.time())
        await self._db.execute_many(
            "UPDATE outbox SET status = 'sent', sent_at = ?, lease_expires_at = NULL WHERE id = ?",
            [(now, outbox_id) for outbox_id in outbox_ids],
        )

    async def mark_retry(self, outbox_ids: list[int], *, delay_s: float, error: str) -> None:
        """Put notifications back in the queue after a failure."""
        if not outbox_ids:
            return
        next_attempt = int(time.time() + delay_s)
        await self._db.execute_many(
            "UPDATE outbox SET status = 'pending', attempts = attempts + 1, "
            "next_attempt_at = ?, lease_expires_at = NULL, last_error = ? WHERE id = ?",
            [(next_attempt, error[:500], outbox_id) for outbox_id in outbox_ids],
        )

    async def mark_failed(self, outbox_ids: list[int], error: str) -> None:
        if not outbox_ids:
            return
        await self._db.execute_many(
            "UPDATE outbox SET status = 'failed', lease_expires_at = NULL, last_error = ? "
            "WHERE id = ?",
            [(error[:500], outbox_id) for outbox_id in outbox_ids],
        )

    async def discard_pending_for(self, destination_id: int, reason: str) -> int:
        """Throw away what is queued for a destination without sending it."""
        return await self._db.execute(
            "UPDATE outbox SET status = 'cancelled', last_error = ? "
            "WHERE destination_id = ? AND status = 'pending'",
            (reason[:500], destination_id),
        )

    async def recover_leases(self) -> int:
        """Return notifications stranded by a crash to the queue. Run once at startup.

        Anything still marked as sending was interrupted mid-flight: this process is the
        only writer, and it has only just started, so no live worker can be holding one.
        """
        return await self._db.execute(
            "UPDATE outbox SET status = 'pending', lease_expires_at = NULL WHERE status = 'sending'"
        )

    async def expire_stale_notifications(self, older_than_minutes: int) -> int:
        """Drop notifications too old to be worth sending.

        An alert about a listing from two hours ago is not news, and quietly flushing them
        keeps a destination that was offline from dumping its whole backlog on return.
        """
        cutoff = int(time.time()) - older_than_minutes * 60
        return await self._db.execute(
            "UPDATE outbox SET status = 'cancelled', last_error = 'expired before delivery' "
            "WHERE status = 'pending' AND created_at < ?",
            (cutoff,),
        )

    async def outbox_depth(self) -> int:
        value = await self._db.fetch_value(
            "SELECT COUNT(*) FROM outbox WHERE status IN ('pending', 'sending')"
        )
        return int(value or 0)

    # --- Miscellaneous state -------------------------------------------------------

    async def set_state_value(self, key: str, value: str) -> None:
        await self._db.execute(
            "INSERT INTO app_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    async def get_state_value(self, key: str) -> str | None:
        value = await self._db.fetch_value("SELECT value FROM app_state WHERE key = ?", (key,))
        return str(value) if value is not None else None

    async def heartbeat(self) -> None:
        await self.set_state_value("heartbeat_at", str(int(time.time())))
