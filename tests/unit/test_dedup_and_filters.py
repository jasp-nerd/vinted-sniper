from __future__ import annotations

import time
from decimal import Decimal

import pytest

from vinted_sniper.db.repo import Query
from vinted_sniper.engine import dedup, filters
from vinted_sniper.vinted.models import Item

NOW = 1_760_000_000


def item(item_id: int, *, ts: int | None = NOW, **kwargs: object) -> Item:
    defaults: dict[str, object] = {
        "item_id": item_id,
        "tld": "fr",
        "title": f"Item {item_id}",
        "url": f"https://www.vinted.fr/items/{item_id}",
        "photo_ts": ts,
    }
    defaults.update(kwargs)
    return Item(**defaults)  # type: ignore[arg-type]


def search(**kwargs: object) -> Query:
    defaults: dict[str, object] = {
        "id": 1,
        "name": "test",
        "url": "https://www.vinted.fr/catalog?search_text=x",
        "tld": "fr",
        "params": {},
        "poll_interval_s": 60,
        "paused": False,
    }
    defaults.update(kwargs)
    return Query(**defaults)  # type: ignore[arg-type]


# --- Deduplication -----------------------------------------------------------------


def test_first_check_records_everything_and_announces_nothing() -> None:
    listings = [item(1), item(2), item(3)]

    result = dedup.select(
        candidates=listings,
        all_items=listings,
        known_ids=set(),
        high_water_mark=None,
        now=NOW,
        freshness_window_s=1200,
        is_first_run=True,
    )

    assert len(result.to_record) == 3
    assert result.to_notify == []


def test_first_check_can_announce_exactly_one_as_a_smoke_test() -> None:
    listings = [item(1, ts=NOW - 100), item(2, ts=NOW), item(3, ts=NOW - 50)]

    result = dedup.select(
        candidates=listings,
        all_items=listings,
        known_ids=set(),
        high_water_mark=None,
        now=NOW,
        freshness_window_s=1200,
        is_first_run=True,
        first_run_mode="newest",
    )

    assert [i.item_id for i in result.to_notify] == [2], "the newest listing, and only that"
    assert len(result.to_record) == 3


def test_listings_older_than_the_freshness_window_are_ignored() -> None:
    listings = [item(1, ts=NOW - 5000), item(2, ts=NOW - 60)]

    result = dedup.select(
        candidates=listings,
        all_items=listings,
        known_ids=set(),
        high_water_mark=None,
        now=NOW,
        freshness_window_s=1200,
        is_first_run=False,
    )

    assert [i.item_id for i in result.to_notify] == [2]
    assert result.skipped_stale == 1


def test_listings_already_seen_are_ignored_even_when_fresh() -> None:
    listings = [item(1), item(2)]

    result = dedup.select(
        candidates=listings,
        all_items=listings,
        known_ids={1},
        high_water_mark=None,
        now=NOW,
        freshness_window_s=1200,
        is_first_run=False,
    )

    assert [i.item_id for i in result.to_notify] == [2]


def test_the_high_water_mark_stops_older_listings_drifting_back_in() -> None:
    listings = [item(1, ts=NOW - 500), item(2, ts=NOW - 100)]

    result = dedup.select(
        candidates=listings,
        all_items=listings,
        known_ids=set(),
        high_water_mark=NOW - 300,
        now=NOW,
        freshness_window_s=1200,
        is_first_run=False,
    )

    assert [i.item_id for i in result.to_notify] == [2]


def test_announcements_are_ordered_oldest_first() -> None:
    listings = [item(3, ts=NOW - 10), item(1, ts=NOW - 300), item(2, ts=NOW - 100)]

    result = dedup.select(
        candidates=listings,
        all_items=listings,
        known_ids=set(),
        high_water_mark=None,
        now=NOW,
        freshness_window_s=1200,
        is_first_run=False,
    )

    assert [i.item_id for i in result.to_notify] == [1, 2, 3]


def test_a_listing_without_a_timestamp_is_let_through() -> None:
    listings = [item(1, ts=None)]

    result = dedup.select(
        candidates=listings,
        all_items=listings,
        known_ids=set(),
        high_water_mark=NOW,
        now=NOW,
        freshness_window_s=1200,
        is_first_run=False,
    )

    assert [i.item_id for i in result.to_notify] == [1]


def test_the_watchdog_timestamp_ignores_filtering() -> None:
    everything = [item(1, ts=NOW), item(2, ts=NOW - 10)]

    result = dedup.select(
        candidates=[],  # everything was filtered out
        all_items=everything,
        known_ids=set(),
        high_water_mark=None,
        now=NOW,
        freshness_window_s=1200,
        is_first_run=False,
    )

    assert result.newest_raw_ts == NOW, "a quiet search still proves the catalog is moving"
    assert result.to_notify == []


# --- Filters -----------------------------------------------------------------------


def test_the_price_limit_counts_buyer_protection() -> None:
    # Vinted's own price filter would allow this: the asking price is under the limit.
    listing = item(1, price=Decimal("18.00"), total_price=Decimal("20.50"))

    rejection = filters.check(listing, search(max_total_price=Decimal("20.00")))

    assert rejection is not None
    assert rejection.reason == "over_budget"
    assert "buyer protection" in rejection.detail


def test_a_listing_exactly_on_the_limit_is_kept() -> None:
    listing = item(1, price=Decimal("18.00"), total_price=Decimal("20.00"))

    assert filters.matches(listing, search(max_total_price=Decimal("20.00")))


def test_the_asking_price_is_used_when_there_is_no_total() -> None:
    listing = item(1, price=Decimal("25.00"))

    assert not filters.matches(listing, search(max_total_price=Decimal("20.00")))


def test_boosted_listings_are_skipped() -> None:
    rejection = filters.check(item(1, promoted=True), search())

    assert rejection is not None
    assert rejection.reason == "promoted"


@pytest.mark.parametrize("title", ["Nike REPLICA shoes", "replica bag", "Cheap Replica"])
def test_excluded_words_match_regardless_of_case(title: str) -> None:
    listing = item(1, title=title)

    assert not filters.matches(listing, search(banned_keywords=["replica"]))


def test_excluded_words_do_not_reject_everything_else() -> None:
    assert filters.matches(item(1, title="Nike Air Max"), search(banned_keywords=["replica"]))


def test_condition_filter() -> None:
    listing = item(1, condition="Satisfactory")

    assert not filters.matches(listing, search(conditions=["New with tags", "Very good"]))
    assert filters.matches(listing, search(conditions=["Satisfactory"]))


def test_no_filters_means_everything_matches() -> None:
    assert filters.matches(item(1, price=Decimal("999")), search())


def test_freshness_window_uses_real_clock_units() -> None:
    """A regression guard: the window is seconds, and a 20-minute window is 1200 of them."""
    now = int(time.time())
    listings = [item(1, ts=now - 19 * 60), item(2, ts=now - 21 * 60)]

    result = dedup.select(
        candidates=listings,
        all_items=listings,
        known_ids=set(),
        high_water_mark=None,
        now=now,
        freshness_window_s=20 * 60,
        is_first_run=False,
    )

    assert [i.item_id for i in result.to_notify] == [1]
