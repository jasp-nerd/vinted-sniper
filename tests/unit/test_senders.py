"""What we actually send, and what we do when the platform pushes back."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import httpx
import pytest

from vinted_sniper.db.repo import PendingNotification
from vinted_sniper.deliver.discord import DiscordSender
from vinted_sniper.deliver.ratelimit import Gate, TokenBucket
from vinted_sniper.deliver.telegram import TelegramSender
from vinted_sniper.vinted.models import Item


def notification(item_id: int, **kwargs: Any) -> PendingNotification:
    defaults: dict[str, Any] = {
        "item_id": item_id,
        "tld": "fr",
        "title": f"Item {item_id}",
        "url": f"https://www.vinted.fr/items/{item_id}",
        "price": Decimal("15.00"),
        "total_price": Decimal("16.45"),
        "currency": "EUR",
        "brand": "Nike",
        "size": "M",
        "condition": "Very good",
        "photo_url": f"https://images.vinted.net/{item_id}.jpeg",
        "photo_ts": 1_760_000_000,
    }
    defaults.update(kwargs)
    return PendingNotification(
        outbox_id=item_id,
        destination_id=1,
        query_id=1,
        query_name="my search",
        attempts=0,
        item=Item(**defaults),
    )


class Recorder:
    """Captures requests and replays scripted responses."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.requests: list[httpx.Request] = []
        self._responses = list(responses)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._responses:
            return self._responses.pop(0)
        return httpx.Response(204)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))

    def payload(self, index: int = 0) -> Any:
        return json.loads(self.requests[index].content)


def fast_bucket() -> TokenBucket:
    """A bucket that never actually waits, so tests do not either."""
    return TokenBucket(1000.0, capacity=1000.0)


# --- Discord -----------------------------------------------------------------------


async def test_a_few_listings_arrive_one_per_message_with_buttons() -> None:
    recorder = Recorder()
    sender = DiscordSender(
        {"webhook_url": "https://discord.test/hook"},
        client=recorder.client(),
        bucket=fast_bucket(),
    )

    result = await sender.send([notification(1), notification(2)])

    assert result.delivered == [1, 2]
    assert len(recorder.requests) == 2
    payload = recorder.payload(0)
    labels = [c["label"] for c in payload["components"][0]["components"]]
    assert labels == ["Open listing", "Message seller", "Buy"]


async def test_many_listings_are_batched_into_one_message() -> None:
    recorder = Recorder()
    sender = DiscordSender(
        {"webhook_url": "https://discord.test/hook"},
        client=recorder.client(),
        bucket=fast_bucket(),
    )

    result = await sender.send([notification(i) for i in range(1, 7)])

    assert len(recorder.requests) == 1, "six separate messages would be a wall of noise"
    assert len(result.delivered) == 6
    assert len(recorder.payload()["embeds"]) == 6


async def test_a_mid_size_batch_keeps_a_numbered_button_row_per_listing() -> None:
    recorder = Recorder()
    sender = DiscordSender(
        {"webhook_url": "https://discord.test/hook"},
        client=recorder.client(),
        bucket=fast_bucket(),
    )

    await sender.send([notification(i) for i in range(1, 6)])

    payload = recorder.payload()
    assert len(payload["components"]) == 5, "one row per listing, matched to its embed"
    assert payload["embeds"][2]["title"].startswith("#3 · ")
    labels = [c["label"] for c in payload["components"][2]["components"]]
    assert labels == ["#3 Message seller", "#3 Buy"]


async def test_a_large_batch_moves_the_links_into_each_embed() -> None:
    recorder = Recorder()
    sender = DiscordSender(
        {"webhook_url": "https://discord.test/hook"},
        client=recorder.client(),
        bucket=fast_bucket(),
    )

    await sender.send([notification(i) for i in range(1, 7)])

    payload = recorder.payload()
    assert "components" not in payload, "six rows of buttons would exceed Discord's five"
    links = payload["embeds"][0]["fields"][-1]
    assert links["name"] == "Links"
    assert "Message seller" in links["value"] and "Buy" in links["value"]


async def test_every_embed_carries_its_own_listing_link() -> None:
    recorder = Recorder()
    sender = DiscordSender(
        {"webhook_url": "https://discord.test/hook"},
        client=recorder.client(),
        bucket=fast_bucket(),
    )

    await sender.send([notification(i) for i in range(1, 6)])

    urls = [embed["url"] for embed in recorder.payload()["embeds"]]
    assert len(set(urls)) == 5, "Discord folds together embeds that share a URL"


async def test_the_total_price_is_what_gets_shown() -> None:
    recorder = Recorder()
    sender = DiscordSender(
        {"webhook_url": "https://discord.test/hook"},
        client=recorder.client(),
        bucket=fast_bucket(),
    )

    await sender.send([notification(1)])

    price_field = recorder.payload()["embeds"][0]["fields"][0]
    assert "16.45" in price_field["value"]
    assert "with protection" in price_field["value"]


async def test_a_deleted_webhook_is_switched_off_rather_than_retried() -> None:
    recorder = Recorder(httpx.Response(404, json={"message": "Unknown Webhook"}))
    sender = DiscordSender(
        {"webhook_url": "https://discord.test/hook"},
        client=recorder.client(),
        bucket=fast_bucket(),
    )

    result = await sender.send([notification(1)])

    assert result.permanent_error is not None
    assert result.retry == [], "retrying a dead webhook spends the allowance that protects the rest"


async def test_rate_limiting_asks_for_the_wait_discord_specified() -> None:
    recorder = Recorder(httpx.Response(429, json={"retry_after": 3.5, "global": False}))
    sender = DiscordSender(
        {"webhook_url": "https://discord.test/hook"},
        client=recorder.client(),
        bucket=fast_bucket(),
    )

    result = await sender.send([notification(1)])

    assert result.retry == [1]
    assert result.retry_after_s == 3.5
    assert result.pause_all_for_s is None


async def test_a_global_rate_limit_pauses_every_destination() -> None:
    recorder = Recorder(
        httpx.Response(
            429,
            json={"retry_after": 10.0, "global": True},
            headers={"x-ratelimit-scope": "global"},
        )
    )
    sender = DiscordSender(
        {"webhook_url": "https://discord.test/hook"},
        client=recorder.client(),
        bucket=fast_bucket(),
    )

    result = await sender.send([notification(1)])

    assert result.pause_all_for_s == 10.0


# --- Telegram ----------------------------------------------------------------------


async def test_telegram_sends_one_message_per_listing_with_a_photo_preview() -> None:
    recorder = Recorder(*[httpx.Response(200, json={"ok": True})] * 2)
    sender = TelegramSender(
        {"chat_id": "123"}, bot_token="t", client=recorder.client(), bucket=fast_bucket()
    )

    result = await sender.send([notification(1), notification(2)])

    assert result.delivered == [1, 2]
    payload = recorder.payload(0)
    assert payload["parse_mode"] == "HTML"
    assert payload["link_preview_options"]["prefer_large_media"] is True
    assert payload["reply_markup"]["inline_keyboard"][0][0]["url"].endswith("/items/1")


@pytest.mark.parametrize(
    "title",
    [
        "Nike Air Max 90 (2023) — 100% authentic!",
        "T-shirt <script>alert(1)</script>",
        "Vintage & rare • size M",
        "Levi's 501 W32/L34 [new]",
    ],
)
async def test_hostile_titles_do_not_break_the_message(title: str) -> None:
    recorder = Recorder(httpx.Response(200, json={"ok": True}))
    sender = TelegramSender(
        {"chat_id": "123"}, bot_token="t", client=recorder.client(), bucket=fast_bucket()
    )

    result = await sender.send([notification(1, title=title)])

    assert result.delivered == [1]
    text = recorder.payload()["text"]
    assert "<script>" not in text
    assert "&lt;" in text or "<b>" in text


async def test_a_burst_collapses_into_a_digest() -> None:
    recorder = Recorder(*[httpx.Response(200, json={"ok": True})] * 6)
    sender = TelegramSender(
        {"chat_id": "123"}, bot_token="t", client=recorder.client(), bucket=fast_bucket()
    )

    result = await sender.send([notification(i) for i in range(1, 9)])

    assert len(result.delivered) == 8
    assert len(recorder.requests) == 6, "five listings, then one digest for the rest"
    assert "3 more matches" in recorder.payload(5)["text"]


async def test_a_blocked_bot_stops_being_retried() -> None:
    recorder = Recorder(
        httpx.Response(
            403,
            json={"ok": False, "description": "Forbidden: bot was blocked by the user"},
        )
    )
    sender = TelegramSender(
        {"chat_id": "123"}, bot_token="t", client=recorder.client(), bucket=fast_bucket()
    )

    result = await sender.send([notification(1)])

    assert result.permanent_error is not None
    assert result.retry == []


async def test_telegram_rate_limits_are_honoured_exactly() -> None:
    recorder = Recorder(
        httpx.Response(
            429,
            json={
                "ok": False,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 7},
            },
        )
    )
    sender = TelegramSender(
        {"chat_id": "123"}, bot_token="t", client=recorder.client(), bucket=fast_bucket()
    )

    result = await sender.send([notification(1)])

    assert result.retry_after_s == 7.0
    assert result.retry == [1]


async def test_forum_topics_are_supported() -> None:
    recorder = Recorder(httpx.Response(200, json={"ok": True}))
    sender = TelegramSender(
        {"chat_id": "-100123", "message_thread_id": "42"},
        bot_token="t",
        client=recorder.client(),
        bucket=fast_bucket(),
    )

    await sender.send([notification(1)])

    assert recorder.payload()["message_thread_id"] == "42"


# --- Pacing ------------------------------------------------------------------------


async def test_the_bucket_spaces_requests_out() -> None:
    clock = {"now": 0.0}
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds

    bucket = TokenBucket(2.0, capacity=1.0, clock=lambda: clock["now"], sleep=sleep)

    await bucket.acquire()
    await bucket.acquire()
    await bucket.acquire()

    assert slept, "a burst past the capacity has to wait"
    assert sum(slept) == pytest.approx(1.0, abs=0.01), "two per second means half a second each"


def test_a_closed_gate_reopens_on_its_own() -> None:
    clock = {"now": 100.0}
    gate = Gate(clock=lambda: clock["now"])

    assert gate.wait_s == 0.0
    gate.close_for(5.0)
    assert gate.wait_s == pytest.approx(5.0)

    clock["now"] += 5.1
    assert gate.wait_s == 0.0
