"""Push notifications through ntfy.

Worth having because it needs no account and no bot: install the app, pick a topic name,
done. Tapping the notification opens the listing. The public server allows a few hundred
messages a day, which suits one person watching a handful of searches; anyone busier should
self-host ntfy or use Discord or Telegram.
"""

from __future__ import annotations

from typing import Any

import httpx

from vinted_sniper.db.repo import PendingNotification
from vinted_sniper.deliver.base import SendResult, require
from vinted_sniper.deliver.ratelimit import TokenBucket

DEFAULT_SERVER = "https://ntfy.sh"


class NtfySender:
    """Sends one push per listing."""

    kind = "ntfy"

    def __init__(
        self,
        config: dict[str, Any],
        *,
        client: httpx.AsyncClient | None = None,
        bucket: TokenBucket | None = None,
    ) -> None:
        self._topic = require(config, "topic", self.kind)
        self._server = str(config.get("server") or DEFAULT_SERVER).rstrip("/")
        self._token = config.get("token")
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._owns_client = client is None
        self._bucket = bucket or TokenBucket(1.0, capacity=3)

    @property
    def max_batch(self) -> int:
        # One push per listing, and a phone that buzzes five times in a row is already
        # pushing it.
        return 5

    async def send(self, batch: list[PendingNotification]) -> SendResult:
        delivered: list[int] = []
        for notification in batch:
            item = notification.item
            headers = {
                "Title": item.title[:200],
                "Click": item.url,
                "Tags": "shopping_bags",
            }
            if item.photo_url:
                headers["Attach"] = item.photo_url
            if self._token:
                headers["Authorization"] = f"Bearer {self._token}"

            await self._bucket.acquire()
            try:
                response = await self._client.post(
                    f"{self._server}/{self._topic}",
                    content=item.price_line().encode(),
                    headers=headers,
                )
            except httpx.HTTPError as exc:
                remaining = [n.outbox_id for n in batch if n.outbox_id not in delivered]
                return SendResult(
                    delivered=delivered, retry=remaining, error=f"could not reach ntfy: {exc}"
                )

            if response.is_success:
                delivered.append(notification.outbox_id)
                continue
            if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
                remaining = [n.outbox_id for n in batch if n.outbox_id not in delivered]
                return SendResult(
                    delivered=delivered,
                    retry=remaining,
                    retry_after_s=60.0,
                    error="ntfy daily allowance reached",
                )
            remaining = [n.outbox_id for n in batch if n.outbox_id not in delivered]
            return SendResult(
                delivered=delivered,
                retry=remaining,
                error=f"ntfy answered {response.status_code}",
            )
        return SendResult.ok(delivered)

    async def send_status(self, message: str) -> None:
        await self._bucket.acquire()
        try:
            await self._client.post(
                f"{self._server}/{self._topic}",
                content=message.encode(),
                headers={"Title": "vinted-sniper", "Tags": "warning", "Priority": "low"},
            )
        except httpx.HTTPError:
            return

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
