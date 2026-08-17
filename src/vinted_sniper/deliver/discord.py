"""Discord delivery, through webhooks.

A webhook is the whole integration: paste a URL, get notifications. No bot to invite, no
gateway connection to keep alive, and when someone deletes the webhook we get a clean 404
instead of a bot that silently stops working.

Every listing renders as one rich embed: an author line naming the search it matched, the
title linking to the listing, a row of links (item, dashboard, seller), an inline grid of
the facts a buying decision needs — total price included — and the photo full width. The
links live in the embed itself rather than in buttons, so a single listing and a batch of
ten read identically and nothing is lost when embeds are stacked.

Two details are doing real work here. Discord collapses embeds that share a link, so every
embed carries its own listing URL rather than the search's. And rejected requests count
against an allowance that, once spent, gets the whole address blocked — so a destination
that answers 404 is switched off rather than retried.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
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

# Up to this many listings at once, each gets its own message — the shape people expect
# an alert to have. Past it, one message per listing turns into a wall, so the embeds
# stack into a single message instead.
STACK_THRESHOLD = 3

# The documented per-webhook limit is around five requests every two seconds, but webhooks
# in the same server have been observed sharing one allowance. One request every half
# second stays clear of both.
REQUESTS_PER_S = 2.0

BOT_NAME = "Vinted Sniper"
# The dart-on-target emoji as a hosted PNG, shown as the webhook's avatar and in footers.
ICON_URL = "https://cdn.jsdelivr.net/gh/jdecked/twemoji@15.1.0/assets/72x72/1f3af.png"
BRAND_COLOUR = 0x007782

# Vinted's country sites named by the ISO code their flag needs. Everything not listed is
# already its own ISO code.
_ISO_BY_TLD = {"co.uk": "GB", "com": "US"}


class DiscordSender:
    """Posts listings to one Discord webhook."""

    kind = "discord"

    def __init__(
        self,
        config: dict[str, Any],
        *,
        dashboard_url: str | None = None,
        client: httpx.AsyncClient | None = None,
        bucket: TokenBucket | None = None,
    ) -> None:
        self._url = require(config, "webhook_url", self.kind)
        self._dashboard_url = dashboard_url
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._owns_client = client is None
        self._bucket = bucket or TokenBucket(REQUESTS_PER_S, capacity=3)

    @property
    def max_batch(self) -> int:
        return MAX_EMBEDS

    async def send(self, batch: list[PendingNotification]) -> SendResult:
        if not batch:
            return SendResult.ok([])

        if len(batch) <= STACK_THRESHOLD:
            delivered: list[int] = []
            for notification in batch:
                result = await self._post(
                    self._message([self._embed(notification)]), [notification.outbox_id]
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
        payload = self._message([self._embed(n) for n in chunk])
        return await self._post(payload, [n.outbox_id for n in chunk])

    async def send_status(self, message: str) -> None:
        await self._post(
            {"content": message[:2000], "username": BOT_NAME, "avatar_url": ICON_URL}, []
        )

    def _message(self, embeds: list[dict[str, Any]]) -> dict[str, Any]:
        return {"username": BOT_NAME, "avatar_url": ICON_URL, "embeds": embeds}

    def _embed(self, notification: PendingNotification) -> dict[str, Any]:
        item = notification.item
        detected = notification.detected_at or int(time.time())

        links = [f"**[View item]({item.url})**"]
        if self._dashboard_url:
            links.append(f"[Dashboard]({self._dashboard_url})")
        if item.seller_url:
            links.append(f"[Seller]({item.seller_url})")

        fields: list[dict[str, Any]] = []
        if item.price is not None:
            fields.append({"name": "Price", "value": _price_value(item), "inline": True})
        if item.size:
            fields.append({"name": "Size", "value": item.size[:1024], "inline": True})
        if item.condition:
            fields.append({"name": "Condition", "value": item.condition[:1024], "inline": True})
        if item.brand:
            fields.append({"name": "Brand", "value": item.brand[:1024], "inline": True})
        fields.append({"name": "Location", "value": _location(item.tld), "inline": True})
        if item.seller_rating is not None:
            fields.append({"name": "Seller rating", "value": _rating(item), "inline": True})
        if item.seller_login:
            fields.append(
                {"name": "Seller", "value": f"@{item.seller_login}"[:1024], "inline": True}
            )
        # Discord renders this in the reader's own timezone, as "2 minutes ago".
        fields.append({"name": "Detected", "value": f"<t:{detected}:R>", "inline": True})

        embed: dict[str, Any] = {
            "author": {"name": f"New match • {notification.query_name}"[:256]},
            "title": item.title[:256],
            # Distinct per listing on purpose: Discord folds together embeds that share
            # a URL.
            "url": item.url,
            "color": BRAND_COLOUR,
            "description": "  •  ".join(links),
            "fields": fields,
            "footer": {
                "text": f"{BOT_NAME} • vinted.{item.tld}"[:2048],
                "icon_url": ICON_URL,
            },
            "timestamp": datetime.fromtimestamp(detected, tz=UTC).isoformat(),
        }
        if item.photo_url:
            embed["image"] = {"url": item.photo_url}
        return embed

    async def _post(self, payload: dict[str, Any], outbox_ids: list[int]) -> SendResult:
        await self._bucket.acquire()
        try:
            response = await self._client.post(self._url, json=payload)
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

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _price_value(item: Item) -> str:
    """The asking price in bold, with the real total under it when the two differ.

    The total — buyer protection included — is the number a buying decision compares, so
    it must be visible even though Vinted leads with the smaller figure.
    """
    currency = f" {item.currency}" if item.currency else ""
    value = f"**{item.price}{currency}**"
    if item.total_price is not None and item.total_price != item.price:
        value += f"\n{item.total_price}{currency} total"
    return value


def _location(tld: str) -> str:
    """The country site the listing is on, as a flag and ISO code."""
    iso = _ISO_BY_TLD.get(tld, tld.upper())
    flag = "".join(chr(0x1F1E6 + ord(letter) - ord("A")) for letter in iso)
    return f"{flag} {iso}"


def _rating(item: Item) -> str:
    """Vinted's 0..1 reputation as the five-star figure users know, with its review count."""
    stars = round((item.seller_rating or 0.0) * 50) / 10
    if item.seller_feedback_count:
        return f"⭐ {stars:.1f} ({item.seller_feedback_count})"
    return f"⭐ {stars:.1f}"


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
