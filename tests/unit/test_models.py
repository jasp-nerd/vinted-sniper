from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from vinted_sniper.vinted.models import ParseError, parse_item


def catalog_entry(**overrides: Any) -> dict[str, Any]:
    """A catalog entry shaped like the real thing, trimmed to what we read."""
    entry: dict[str, Any] = {
        "id": 9683334896,
        "title": "Nike Air",
        "url": "https://www.vinted.fr/items/9683334896-nike-air",
        "brand_title": "Nike Air",
        "size_title": "38.5",
        "status": "Bon état",
        "price": {"amount": "15.0", "currency_code": "EUR"},
        "total_item_price": {"amount": "16.45", "currency_code": "EUR"},
        "service_fee": {"amount": "1.45", "currency_code": "EUR"},
        "photo": {
            "full_size_url": "https://images.vinted.net/full.jpeg",
            "high_resolution": {"id": "abc", "timestamp": 1_755_374_000},
        },
        "user": {"id": 7, "login": "seller", "feedback_reputation": 0.93},
        "promoted": False,
        "favourite_count": 2,
        "view_count": 11,
        "item_box": {"accessibility_label": "Nike Air, brand: Nike, 15,00 €"},
    }
    entry.update(overrides)
    return entry


def test_a_full_entry_parses() -> None:
    item = parse_item(catalog_entry(), "fr")

    assert item.item_id == 9683334896
    assert item.title == "Nike Air"
    assert item.brand == "Nike Air"
    assert item.size == "38.5"
    assert item.condition == "Bon état"
    assert item.price == Decimal("15.0")
    assert item.total_price == Decimal("16.45")
    assert item.currency == "EUR"
    assert item.photo_ts == 1_755_374_000
    assert item.seller_login == "seller"
    assert item.tld == "fr"


def test_total_price_is_kept_apart_from_the_asking_price() -> None:
    item = parse_item(catalog_entry(), "fr")

    assert item.total_price is not None
    assert item.price is not None
    assert item.total_price > item.price
    assert "with protection" in item.price_line()


def test_price_line_stays_simple_when_there_is_no_fee() -> None:
    item = parse_item(
        catalog_entry(total_item_price={"amount": "15.0", "currency_code": "EUR"}), "fr"
    )

    assert item.price_line() == "15.0 EUR"


def test_a_listing_without_an_id_is_rejected() -> None:
    entry = catalog_entry()
    del entry["id"]

    with pytest.raises(ParseError):
        parse_item(entry, "fr")


@pytest.mark.parametrize(
    "overrides",
    [
        {"photo": None},
        {"photo": []},
        {"user": None},
        {"brand_title": ""},
        {"size_title": None},
        {"price": None},
        {"total_item_price": "not-a-price"},
        {"favourite_count": None},
    ],
)
def test_missing_or_reshaped_fields_do_not_lose_the_listing(overrides: dict[str, Any]) -> None:
    item = parse_item(catalog_entry(**overrides), "fr")

    assert item.item_id == 9683334896
    assert item.title == "Nike Air"


def test_no_label_brand_reads_as_absent() -> None:
    assert parse_item(catalog_entry(brand_title="NO LABEL"), "fr").brand is None


def test_bare_numeric_price_is_accepted() -> None:
    item = parse_item(catalog_entry(price=12.5), "fr")

    assert item.price == Decimal("12.5")


def test_url_is_derived_when_the_payload_omits_it() -> None:
    entry = catalog_entry()
    del entry["url"]

    assert parse_item(entry, "de").url == "https://www.vinted.de/items/9683334896"


def test_raw_payload_is_only_kept_when_asked_for() -> None:
    assert parse_item(catalog_entry(), "fr").raw is None
    assert parse_item(catalog_entry(), "fr", keep_raw=True).raw is not None


def test_action_links_follow_the_country_site() -> None:
    item = parse_item(catalog_entry(), "co.uk")

    assert item.message_url.startswith("https://www.vinted.co.uk/")
    assert item.buy_url.startswith("https://www.vinted.co.uk/")
