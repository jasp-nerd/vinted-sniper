"""The shape of a listing.

Every field here comes out of the catalog search response, so a notification never needs a
second request. Parsing is deliberately forgiving: Vinted renames and reshapes fields
without warning, and a missing size should cost you the size line, not the alert.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict

from vinted_sniper.vinted import urls


class ParseError(ValueError):
    """A listing was missing something we cannot sensibly invent, such as its id."""


class Item(BaseModel):
    """One Vinted listing, as much as the search response tells us about it."""

    model_config = ConfigDict(frozen=True)

    item_id: int
    tld: str
    title: str
    url: str

    brand: str | None = None
    size: str | None = None
    condition: str | None = None

    price: Decimal | None = None
    # What the buyer is actually charged, buyer protection included. Filters and
    # notifications lead with this, because it is the number people compare.
    total_price: Decimal | None = None
    currency: str | None = None

    photo_url: str | None = None
    # The search response has no "listed at" field. The main photo's upload time is the
    # closest thing to it and is what the whole ecosystem uses to judge freshness.
    photo_ts: int | None = None

    seller_login: str | None = None
    seller_id: int | None = None
    seller_rating: float | None = None
    # How many reviews sit behind the rating. A 4.8 from three reviews and a 4.8 from
    # three hundred are different offers, so notifications show both together.
    seller_feedback_count: int | None = None

    promoted: bool = False
    favourite_count: int = 0
    view_count: int = 0

    # Vinted's own one-line description of the listing, already localised.
    summary: str | None = None
    raw: dict[str, Any] | None = None

    @property
    def listed_at(self) -> datetime | None:
        if self.photo_ts is None:
            return None
        return datetime.fromtimestamp(self.photo_ts, tz=UTC)

    @property
    def message_url(self) -> str:
        return urls.message_seller_url(self.tld, self.item_id)

    @property
    def buy_url(self) -> str:
        return urls.buy_url(self.tld, self.item_id)

    @property
    def seller_url(self) -> str | None:
        if self.seller_id is None:
            return None
        return urls.member_url(self.tld, self.seller_id)

    def price_line(self) -> str:
        """Price as a human reads it, showing the total when it differs from the ask."""
        if self.price is None:
            return "price unknown"
        currency = f" {self.currency}" if self.currency else ""
        if self.total_price is not None and self.total_price != self.price:
            return f"{self.price}{currency} ({self.total_price}{currency} with protection)"
        return f"{self.price}{currency}"


def _first(source: dict[str, Any], *paths: str) -> Any:
    """Return the first present value among dotted paths. Tolerates renamed fields."""
    for path in paths:
        value: Any = source
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                value = None
                break
            value = value[part]
        if value is not None:
            return value
    return None


def _money(value: Any) -> tuple[Decimal | None, str | None]:
    """Read a price, which arrives either as {'amount', 'currency_code'} or a bare number."""
    if value is None:
        return None, None
    if isinstance(value, dict):
        amount, currency = value.get("amount"), value.get("currency_code")
    else:
        amount, currency = value, None
    try:
        return (Decimal(str(amount)) if amount is not None else None), currency
    except (InvalidOperation, ValueError):
        return None, currency


def _text(value: Any) -> str | None:
    """Normalise a text field, treating blanks and Vinted's 'no brand' marker as absent."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or stripped.upper() == "NO LABEL":
        return None
    return stripped


def parse_item(payload: dict[str, Any], tld: str, *, keep_raw: bool = False) -> Item:
    """Build an Item from one entry of a catalog response."""
    raw_id = _first(payload, "id", "item_id")
    try:
        item_id = int(raw_id)
    except (TypeError, ValueError) as exc:
        raise ParseError(f"listing has no usable id: {raw_id!r}") from exc

    price, currency = _money(_first(payload, "price"))
    total_price, total_currency = _money(_first(payload, "total_item_price"))

    photo = _first(payload, "photo") or {}
    if not isinstance(photo, dict):
        photo = {}

    user = _first(payload, "user") or {}
    if not isinstance(user, dict):
        user = {}

    url = _first(payload, "url") or urls.item_url(tld, item_id)

    return Item(
        item_id=item_id,
        tld=tld,
        title=_text(_first(payload, "title")) or f"Listing {item_id}",
        url=url,
        brand=_text(_first(payload, "brand_title", "brand.title")),
        size=_text(_first(payload, "size_title", "size.title")),
        condition=_text(_first(payload, "status", "condition")),
        price=price,
        total_price=total_price,
        currency=currency or total_currency,
        photo_url=_first(photo, "full_size_url", "url"),
        photo_ts=_coerce_int(_first(photo, "high_resolution.timestamp")),
        seller_login=_text(_first(user, "login")),
        seller_id=_coerce_int(_first(user, "id")),
        seller_rating=_coerce_float(_first(user, "feedback_reputation")),
        seller_feedback_count=_coerce_int(_first(user, "feedback_count")),
        promoted=bool(_first(payload, "promoted") or False),
        favourite_count=_coerce_int(_first(payload, "favourite_count")) or 0,
        view_count=_coerce_int(_first(payload, "view_count")) or 0,
        summary=_text(_first(payload, "item_box.accessibility_label")),
        raw=payload if keep_raw else None,
    )


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
