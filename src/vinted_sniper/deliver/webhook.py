"""A plain JSON POST to a URL of your choosing.

The cheapest channel to support and the one that covers everything we did not build: n8n,
Home Assistant, Slack via an adapter, a shell script behind a tiny server. The payload is
documented in docs/configuration.md and treated as a contract — changing it breaks other
people's automations, so it changes only with a version bump.
"""

from __future__ import annotations

from typing import Any

import httpx

from vinted_sniper.db.repo import PendingNotification
from vinted_sniper.deliver.base import SendResult, require
from vinted_sniper.deliver.ratelimit import TokenBucket
from vinted_sniper.vinted.models import Item

PAYLOAD_VERSION = 1


class WebhookSender:
    """Posts listings as JSON to an arbitrary endpoint."""

    kind = "webhook"

    def __init__(
        self,
        config: dict[str, Any],
        *,
        client: httpx.AsyncClient | None = None,
        bucket: TokenBucket | None = None,
    ) -> None:
        self._url = require(config, "url", self.kind)
        self._headers = config.get("headers") or {}
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._owns_client = client is None
        self._bucket = bucket or TokenBucket(2.0, capacity=4)

    @property
    def max_batch(self) -> int:
        return 20

    async def send(self, batch: list[PendingNotification]) -> SendResult:
        if not batch:
            return SendResult.ok([])
        outbox_ids = [n.outbox_id for n in batch]
        payload = {
            "version": PAYLOAD_VERSION,
            "search": batch[0].query_name,
            "items": [_item_json(n.item) for n in batch],
        }
        await self._bucket.acquire()
        try:
            response = await self._client.post(self._url, json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            return SendResult.transient(outbox_ids, f"could not reach the webhook: {exc}")

        if response.is_success:
            return SendResult.ok(outbox_ids)
        if response.status_code in (httpx.codes.NOT_FOUND, httpx.codes.GONE):
            return SendResult.gone(f"the endpoint answered {response.status_code}")
        return SendResult.transient(outbox_ids, f"the endpoint answered {response.status_code}")

    async def send_status(self, message: str) -> None:
        await self._bucket.acquire()
        try:
            await self._client.post(
                self._url,
                json={"version": PAYLOAD_VERSION, "status": message},
                headers=self._headers,
            )
        except httpx.HTTPError:
            return

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _item_json(item: Item) -> dict[str, Any]:
    return {
        "id": item.item_id,
        "site": f"vinted.{item.tld}",
        "title": item.title,
        "url": item.url,
        "brand": item.brand,
        "size": item.size,
        "condition": item.condition,
        "price": str(item.price) if item.price is not None else None,
        "total_price": str(item.total_price) if item.total_price is not None else None,
        "currency": item.currency,
        "photo_url": item.photo_url,
        "listed_at": item.listed_at.isoformat() if item.listed_at else None,
        "seller": item.seller_login,
        "seller_rating": item.seller_rating,
        "links": {"message_seller": item.message_url, "buy": item.buy_url},
    }
