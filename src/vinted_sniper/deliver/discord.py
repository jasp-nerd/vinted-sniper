"""Discord delivery, through webhooks.

A webhook is the whole integration: paste a URL, get notifications. No bot to invite, no
gateway connection to keep alive, and when someone deletes the webhook we get a clean 404
instead of a bot that silently stops working.

Two details are doing real work here. Discord collapses embeds that share a link, so every
embed carries its own listing URL rather than the search's. And rejected requests count
against an allowance that, once spent, gets the whole address blocked — so a destination
that answers 404 is switched off rather than retried.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any

import httpx

from vinted_sniper.db.repo import PendingNotification
from vinted_sniper.deliver.base import SendResult, require
from vinted_sniper.deliver.ratelimit import TokenBucket
from vinted_sniper.log import get_logger
from vinted_sniper.vinted.models import Item

log = get_logger(__name__)

# Discord's own cap on embeds in one message.
MAX_EMBEDS = 10

# Above this many listings at once, one message per listing turns into a wall. Batching
# them into embeds keeps it readable.
BUTTON_THRESHOLD = 3

# A message holds at most five rows of buttons, so a batch up to this size can give each
# listing its own row, tied to its embed by number. Past that the links move into the
# embeds themselves.
MAX_BUTTON_ROWS = 5

# The documented per-webhook limit is around five requests every two seconds, but webhooks
# in the same server have been observed sharing one allowance. One request every half
# second stays clear of both.
REQUESTS_PER_S = 2.0

BRAND_COLOUR = 0x09B1BA
LINK_BUTTON = 5


class DiscordSender:
    """Posts listings to one Discord webhook."""

    kind = "discord"

    def __init__(
        self,
        config: dict[str, Any],
        *,
        client: httpx.AsyncClient | None = None,
        bucket: TokenBucket | None = None,
    ) -> None:
        self._url = require(config, "webhook_url", self.kind)
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._owns_client = client is None
        self._bucket = bucket or TokenBucket(REQUESTS_PER_S, capacity=3)

    @property
    def max_batch(self) -> int:
        return MAX_EMBEDS

    async def send(self, batch: list[PendingNotification]) -> SendResult:
        if not batch:
            return SendResult.ok([])

        if len(batch) <= BUTTON_THRESHOLD:
            delivered: list[int] = []
            for notification in batch:
                result = await self._post(
                    self._single_message(notification), [notification.outbox_id]
                )
                if result.delivered:
                    delivered.extend(result.delivered)
                    continue
                # Stop at the first problem and hand back what is left, so the queue keeps
                # its order and nothing is sent twice.
                remaining = [n.outbox_id for n in batch if n.outbox_id not in delivered]
                return SendResult(
                    delivered=delivered,
                    retry=[] if result.permanent_error else remaining,
                    retry_after_s=result.retry_after_s,
                    permanent_error=result.permanent_error,
                    error=result.error,
                    pause_all_for_s=result.pause_all_for_s,
                )
            return SendResult.ok(delivered)

        chunk = batch[:MAX_EMBEDS]
        if len(chunk) <= MAX_BUTTON_ROWS:
            payload = {
                "embeds": [
                    _embed(n.item, n.query_name, number=i) for i, n in enumerate(chunk, start=1)
                ],
                "components": [_button_row(n.item, i) for i, n in enumerate(chunk, start=1)],
            }
        else:
            payload = {"embeds": [_embed(n.item, n.query_name, link_field=True) for n in chunk]}
        return await self._post(payload, [n.outbox_id for n in chunk])

    async def send_status(self, message: str) -> None:
        await self._post({"content": message[:2000]}, [])

    async def _post(self, payload: dict[str, Any], outbox_ids: list[int]) -> SendResult:
        await self._bucket.acquire()
        try:
            response = await self._client.post(
                self._url, json=payload, params={"with_components": "true"}
            )
        except httpx.HTTPError as exc:
            return SendResult.transient(outbox_ids, f"could not reach Discord: {exc}")

        if response.is_success:
            return SendResult.ok(outbox_ids)

        if response.status_code == HTTPStatus.TOO_MANY_REQUESTS:
            retry_after = _retry_after(response)
            # A global limit means every webhook on this connection has to wait, not just
            # this one.
            is_global = response.headers.get("x-ratelimit-scope") == "global" or _body_flag(
                response, "global"
            )
            log.warning("discord.rate_limited", retry_after_s=retry_after, is_global=is_global)
            return SendResult(
                delivered=[],
                retry=outbox_ids,
                retry_after_s=retry_after,
                error="rate limited by Discord",
                pause_all_for_s=retry_after if is_global else None,
            )

        if response.status_code in (
            HTTPStatus.UNAUTHORIZED,
            HTTPStatus.FORBIDDEN,
            HTTPStatus.NOT_FOUND,
        ):
            return SendResult.gone(
                f"Discord answered {response.status_code}; the webhook looks deleted or "
                "its permissions were removed"
            )

        if response.status_code == HTTPStatus.BAD_REQUEST:
            # Our payload is wrong. Retrying an identical payload will fail identically,
            # and each attempt spends the allowance that protects everything else.
            log.error("discord.rejected_payload", body=response.text[:400])
            return SendResult(
                delivered=[], retry=[], error=f"Discord rejected the message: {response.text[:200]}"
            )

        return SendResult.transient(outbox_ids, f"Discord answered {response.status_code}")

    def _single_message(self, notification: PendingNotification) -> dict[str, Any]:
        item = notification.item
        return {
            "embeds": [_embed(item, notification.query_name)],
            "components": [
                {
                    "type": 1,
                    "components": [
                        {"type": 2, "style": LINK_BUTTON, "label": "Open listing", "url": item.url},
                        {
                            "type": 2,
                            "style": LINK_BUTTON,
                            "label": "Message seller",
                            "url": item.message_url,
                        },
                        {"type": 2, "style": LINK_BUTTON, "label": "Buy", "url": item.buy_url},
                    ],
                }
            ],
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _button_row(item: Item, number: int) -> dict[str, Any]:
    """One row of link buttons for one listing in a batched message.

    The number ties the row to its embed, whose title carries the same prefix. No "open"
    button: that numbered title is already the listing link.
    """
    return {
        "type": 1,
        "components": [
            {
                "type": 2,
                "style": LINK_BUTTON,
                "label": f"#{number} Message seller",
                "url": item.message_url,
            },
            {"type": 2, "style": LINK_BUTTON, "label": f"#{number} Buy", "url": item.buy_url},
        ],
    }


def _embed(
    item: Item, query_name: str, *, number: int | None = None, link_field: bool = False
) -> dict[str, Any]:
    fields: list[dict[str, Any]] = [{"name": "Price", "value": item.price_line(), "inline": True}]
    if item.size:
        fields.append({"name": "Size", "value": item.size[:1024], "inline": True})
    if item.brand:
        fields.append({"name": "Brand", "value": item.brand[:1024], "inline": True})
    if item.condition:
        fields.append({"name": "Condition", "value": item.condition[:1024], "inline": True})
    if item.seller_login:
        seller = item.seller_login[:900]
        if item.seller_rating is not None:
            seller += f" ({item.seller_rating:.0%})"
        fields.append({"name": "Seller", "value": seller, "inline": True})
    if item.listed_at:
        # Discord renders this in the reader's own timezone, as "3 minutes ago".
        fields.append(
            {
                "name": "Listed",
                "value": f"<t:{int(item.listed_at.timestamp())}:R>",
                "inline": True,
            }
        )
    if link_field:
        fields.append(
            {
                "name": "Links",
                "value": f"[Message seller]({item.message_url}) · [Buy]({item.buy_url})",
                "inline": True,
            }
        )

    title = item.title if number is None else f"#{number} · {item.title}"
    embed: dict[str, Any] = {
        "title": title[:256],
        # Distinct per listing on purpose: Discord folds together embeds that share a URL.
        "url": item.url,
        "color": BRAND_COLOUR,
        "fields": fields,
        "footer": {"text": f"vinted.{item.tld} · {query_name}"[:2048]},
    }
    if item.photo_url:
        embed["image"] = {"url": item.photo_url}
    return embed


def _retry_after(response: httpx.Response) -> float:
    for header in ("retry-after", "x-ratelimit-reset-after"):
        if raw := response.headers.get(header):
            try:
                return float(raw)
            except ValueError:
                continue
    try:
        body = response.json()
    except ValueError:
        return 2.0
    if not isinstance(body, dict):
        return 2.0
    try:
        return float(body["retry_after"])
    except (KeyError, TypeError, ValueError):
        return 2.0


def _body_flag(response: httpx.Response, key: str) -> bool:
    try:
        body = response.json()
    except ValueError:
        return False
    return bool(body.get(key)) if isinstance(body, dict) else False
