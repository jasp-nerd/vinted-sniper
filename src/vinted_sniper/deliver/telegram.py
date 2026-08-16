"""Telegram delivery.

Sent as HTML rather than MarkdownV2 on purpose. MarkdownV2 requires eighteen characters to
be escaped anywhere they appear, and Vinted titles are full of them — a stray `.` or `!`
turns into a hard send failure rather than a formatting wobble. HTML needs three.

The photo rides along as a link preview instead of an upload. That keeps the full message
length and the buttons, both of which a photo message would cost us, and saves fetching the
image at all.
"""

from __future__ import annotations

import html
from typing import Any

import httpx

from vinted_sniper.db.repo import PendingNotification
from vinted_sniper.deliver.base import SendResult, require
from vinted_sniper.deliver.ratelimit import TokenBucket
from vinted_sniper.log import get_logger
from vinted_sniper.vinted.models import Item

log = get_logger(__name__)

API_ROOT = "https://api.telegram.org"

# Telegram asks for no more than one message a second in a single chat.
MESSAGES_PER_S = 1.0

# Past this many listings at once, the rest are folded into one digest. A chat that gets
# twenty separate alerts in a minute is unreadable, and it is also how you get throttled.
DIGEST_THRESHOLD = 5

MAX_MESSAGE_CHARS = 4096

# Telegram states these plainly, so they are worth acting on rather than retrying.
_PERMANENT_MARKERS = (
    "bot was blocked by the user",
    "chat not found",
    "user is deactivated",
    "bot was kicked",
    "have no rights to send",
)


class TelegramSender:
    """Posts listings to one Telegram chat, group, or forum topic."""

    kind = "telegram"

    def __init__(
        self,
        config: dict[str, Any],
        *,
        bot_token: str,
        client: httpx.AsyncClient | None = None,
        bucket: TokenBucket | None = None,
    ) -> None:
        self._chat_id = str(config.get("chat_id") or "").strip()
        if not self._chat_id:
            require(config, "chat_id", self.kind)
        self._thread_id = config.get("message_thread_id")
        self._token = bot_token
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._owns_client = client is None
        self._bucket = bucket or TokenBucket(MESSAGES_PER_S, capacity=2)

    @property
    def max_batch(self) -> int:
        return 10

    async def send(self, batch: list[PendingNotification]) -> SendResult:
        if not batch:
            return SendResult.ok([])

        individually = batch[:DIGEST_THRESHOLD]
        overflow = batch[DIGEST_THRESHOLD:]
        delivered: list[int] = []

        for notification in individually:
            result = await self._post(
                "sendMessage",
                self._listing_payload(notification.item),
                [notification.outbox_id],
            )
            if not result.delivered:
                remaining = [n.outbox_id for n in batch if n.outbox_id not in delivered]
                return SendResult(
                    delivered=delivered,
                    retry=[] if result.permanent_error else remaining,
                    retry_after_s=result.retry_after_s,
                    permanent_error=result.permanent_error,
                    error=result.error,
                )
            delivered.extend(result.delivered)

        if overflow:
            result = await self._post(
                "sendMessage",
                self._digest_payload([n.item for n in overflow]),
                [n.outbox_id for n in overflow],
            )
            if not result.delivered:
                return SendResult(
                    delivered=delivered,
                    retry=[] if result.permanent_error else result.retry,
                    retry_after_s=result.retry_after_s,
                    permanent_error=result.permanent_error,
                    error=result.error,
                )
            delivered.extend(result.delivered)

        return SendResult.ok(delivered)

    async def send_status(self, message: str) -> None:
        await self._post(
            "sendMessage",
            self._base_payload() | {"text": html.escape(message)[:MAX_MESSAGE_CHARS]},
            [],
        )

    def _base_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"chat_id": self._chat_id, "parse_mode": "HTML"}
        if self._thread_id:
            payload["message_thread_id"] = self._thread_id
        return payload

    def _listing_payload(self, item: Item) -> dict[str, Any]:
        lines = [f"<b>{html.escape(item.title)}</b>", html.escape(item.price_line())]

        details = " · ".join(
            html.escape(part) for part in (item.brand, item.size, item.condition) if part
        )
        if details:
            lines.append(details)
        if item.seller_login:
            seller = html.escape(item.seller_login)
            if item.seller_rating is not None:
                seller += f" ({item.seller_rating:.0%})"
            lines.append(f"Seller: {seller}")
        if item.listed_at:
            lines.append(f"Listed {item.listed_at.strftime('%H:%M UTC')}")

        payload = self._base_payload() | {
            "text": "\n".join(lines)[:MAX_MESSAGE_CHARS],
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {"text": "Open listing", "url": item.url},
                        {"text": "Message seller", "url": item.message_url},
                        {"text": "Buy", "url": item.buy_url},
                    ]
                ]
            },
        }
        if item.photo_url:
            payload["link_preview_options"] = {
                "url": item.photo_url,
                "prefer_large_media": True,
                "show_above_text": True,
            }
        else:
            payload["link_preview_options"] = {"is_disabled": True}
        return payload

    def _digest_payload(self, items: list[Item]) -> dict[str, Any]:
        lines = [f"<b>{len(items)} more matches</b>"]
        for index, item in enumerate(items, start=1):
            lines.append(
                f'{index}. <a href="{html.escape(item.url, quote=True)}">'
                f"{html.escape(item.title[:80])}</a> — {html.escape(item.price_line())}"
            )
        return self._base_payload() | {
            "text": "\n".join(lines)[:MAX_MESSAGE_CHARS],
            "link_preview_options": {"is_disabled": True},
        }

    async def _post(
        self, method: str, payload: dict[str, Any], outbox_ids: list[int]
    ) -> SendResult:
        await self._bucket.acquire()
        try:
            response = await self._client.post(
                f"{API_ROOT}/bot{self._token}/{method}", json=payload
            )
        except httpx.HTTPError as exc:
            return SendResult.transient(outbox_ids, f"could not reach Telegram: {exc}")

        try:
            body = response.json()
        except ValueError:
            return SendResult.transient(outbox_ids, "Telegram sent a reply we could not read")

        if response.is_success and body.get("ok"):
            return SendResult.ok(outbox_ids)

        description = str(body.get("description", response.text))[:300]
        retry_after = body.get("parameters", {}).get("retry_after")

        if retry_after is not None:
            log.warning("telegram.rate_limited", retry_after_s=retry_after)
            return SendResult(
                delivered=[],
                retry=outbox_ids,
                retry_after_s=float(retry_after),
                error="rate limited by Telegram",
            )

        return _classify(description, response.status_code, outbox_ids)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


def _classify(description: str, status_code: int, outbox_ids: list[int]) -> SendResult:
    """Decide what a Telegram error means for the queue."""
    lowered = description.lower()
    if any(marker in lowered for marker in _PERMANENT_MARKERS):
        return SendResult.gone(f"Telegram says: {description}")

    if status_code == httpx.codes.BAD_REQUEST:
        # Malformed request; sending it again changes nothing.
        log.error("telegram.rejected_payload", description=description)
        return SendResult(delivered=[], retry=[], error=f"Telegram rejected it: {description}")

    return SendResult.transient(outbox_ids, f"Telegram error: {description}")
