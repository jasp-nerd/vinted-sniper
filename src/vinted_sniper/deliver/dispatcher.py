"""Getting queued notifications out.

Nothing here talks to Vinted and nothing in the poller talks to a chat platform. The two
sides meet at the outbox table, which is what makes delivery survivable: a notification is
written in the same transaction that records the listing, so a crash cannot lose it, and it
is only marked sent once the far end has actually accepted it.

Each destination gets one worker sending one thing at a time. That is slower than firing
everything at once and it is the point — ordered, paced delivery is what keeps you inside
the platform limits instead of apologising to them afterwards.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import httpx

from vinted_sniper.config import Settings
from vinted_sniper.db.repo import Destination, Repo
from vinted_sniper.deliver.base import Sender, SenderConfigError
from vinted_sniper.deliver.discord import DiscordSender
from vinted_sniper.deliver.ntfy import NtfySender
from vinted_sniper.deliver.ratelimit import Gate, TokenBucket
from vinted_sniper.deliver.telegram import TelegramSender
from vinted_sniper.deliver.webhook import WebhookSender
from vinted_sniper.log import get_logger

log = get_logger(__name__)

# Give up on a notification after this many tries. Anything still failing is a
# configuration problem, not bad luck.
MAX_ATTEMPTS = 6

# Retries stay short on purpose. Notifications for one destination go out in order, so a
# long sleep at the front of the queue holds up every fresher listing behind it.
MAX_RETRY_DELAY_S = 5.0

# Telegram counts messages across the whole bot, not per chat, so every Telegram
# destination shares this.
TELEGRAM_GLOBAL_PER_S = 25.0


class Dispatcher:
    """Runs one sending worker per destination."""

    def __init__(
        self,
        *,
        repo: Repo,
        settings: Settings,
        stop: asyncio.Event,
        work_available: asyncio.Event,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._stop = stop
        self._work = work_available
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._owns_client = client is None
        self._senders: dict[int, Sender] = {}
        self._discord_gate = Gate()
        self._telegram_budget = TokenBucket(TELEGRAM_GLOBAL_PER_S, capacity=TELEGRAM_GLOBAL_PER_S)

    async def run(self, idle_interval_s: float = 2.0) -> None:
        """Deliver whatever is queued, then wait to be told there is more."""
        recovered = await self._repo.recover_leases()
        if recovered:
            log.info("outbox.recovered", count=recovered)

        while not self._stop.is_set():
            try:
                await self.drain()
            except Exception as exc:  # keep the loop alive; one bad destination is not fatal
                log.exception("dispatcher.cycle_failed", error=str(exc))

            self._work.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._work.wait(), timeout=idle_interval_s)

        await self.aclose()

    async def drain(self) -> int:
        """Send everything currently due. Returns how many notifications went out."""
        expired = await self._repo.expire_stale_notifications(self._settings.outbox_expiry_minutes)
        if expired:
            log.info("outbox.expired", count=expired, reason="older than the delivery window")

        destination_ids = await self._repo.destinations_with_work()
        if not destination_ids:
            return 0

        results = await asyncio.gather(
            *(self._serve(destination_id) for destination_id in destination_ids),
            return_exceptions=True,
        )

        sent = 0
        for destination_id, result in zip(destination_ids, results, strict=True):
            if isinstance(result, BaseException):
                log.exception(
                    "dispatcher.destination_failed",
                    destination_id=destination_id,
                    error=str(result),
                )
                continue
            sent += result
        return sent

    async def _serve(self, destination_id: int) -> int:
        destination = await self._repo.get_destination(destination_id)
        if destination is None or not destination.active:
            return 0

        try:
            sender = await self._sender_for(destination)
        except SenderConfigError as exc:
            await self._repo.deactivate_destination(destination_id, str(exc))
            log.error("destination.misconfigured", destination_id=destination_id, error=str(exc))
            return 0

        if sender.kind == "discord" and self._discord_gate.is_closed:
            return 0

        batch = await self._repo.claim_batch(destination_id, sender.max_batch)
        if not batch:
            return 0

        if sender.kind == "telegram":
            # One bot token serves every Telegram destination, so the shared budget is
            # spent here rather than inside the sender.
            await self._telegram_budget.acquire(len(batch))

        result = await sender.send(batch)

        if result.pause_all_for_s:
            self._discord_gate.close_for(result.pause_all_for_s)
            log.warning("discord.global_pause", seconds=round(result.pause_all_for_s, 1))

        await self._repo.mark_sent(result.delivered)
        if result.delivered:
            await self._repo.reset_destination_failures(destination_id)

        handled = set(result.delivered)

        if result.permanent_error:
            await self._repo.deactivate_destination(destination_id, result.permanent_error)
            await self._drop_sender(destination_id)
            log.warning(
                "destination.disabled",
                destination_id=destination_id,
                name=destination.name,
                reason=result.permanent_error,
            )
            return len(result.delivered)

        if result.retry:
            await self._repo.note_destination_failure(destination_id)
            retry_ids = [outbox_id for outbox_id in result.retry if outbox_id not in handled]
            over_budget = [n.outbox_id for n in batch if n.attempts + 1 >= MAX_ATTEMPTS]
            give_up = [outbox_id for outbox_id in retry_ids if outbox_id in over_budget]
            keep = [outbox_id for outbox_id in retry_ids if outbox_id not in over_budget]

            delay = min(result.retry_after_s or 1.0, MAX_RETRY_DELAY_S)
            await self._repo.mark_retry(keep, delay_s=delay, error=result.error or "send failed")
            await self._repo.mark_failed(give_up, result.error or "gave up after repeated failures")
            handled.update(retry_ids)

        # Anything the sender neither delivered nor asked to retry was rejected outright.
        abandoned = [n.outbox_id for n in batch if n.outbox_id not in handled]
        await self._repo.mark_failed(abandoned, result.error or "the destination rejected it")

        return len(result.delivered)

    async def notify_status(self, message: str) -> None:
        """Send an operational notice to whichever destinations asked for them."""
        for destination_id in await self._repo.status_destination_ids():
            destination = await self._repo.get_destination(destination_id)
            if destination is None:
                continue
            with contextlib.suppress(Exception):
                sender = await self._sender_for(destination)
                await sender.send_status(message)

    async def _sender_for(self, destination: Destination) -> Sender:
        if (existing := self._senders.get(destination.id)) is not None:
            return existing
        sender = self._build(destination)
        self._senders[destination.id] = sender
        return sender

    def _build(self, destination: Destination) -> Sender:
        config: dict[str, Any] = destination.config
        match destination.kind:
            case "discord":
                return DiscordSender(config, client=self._client)
            case "telegram":
                token = self._settings.telegram_bot_token
                if token is None:
                    raise SenderConfigError(
                        "a Telegram destination exists but VINTED_SNIPER_TELEGRAM_BOT_TOKEN "
                        "is not set"
                    )
                return TelegramSender(
                    config, bot_token=token.get_secret_value(), client=self._client
                )
            case "webhook":
                return WebhookSender(config, client=self._client)
            case "ntfy":
                return NtfySender(config, client=self._client)
            case unknown:
                raise SenderConfigError(f"unknown destination type {unknown!r}")

    async def _drop_sender(self, destination_id: int) -> None:
        sender = self._senders.pop(destination_id, None)
        if sender is not None:
            with contextlib.suppress(Exception):
                await sender.aclose()

    async def aclose(self) -> None:
        for destination_id in list(self._senders):
            await self._drop_sender(destination_id)
        if self._owns_client:
            await self._client.aclose()
