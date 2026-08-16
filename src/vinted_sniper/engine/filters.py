"""Deciding whether a listing is worth telling you about.

Vinted's own filters get you most of the way there; these cover what its search cannot.
The important one is price: Vinted filters on the asking price, but you pay the asking
price plus buyer protection, so a `price_to=30` search happily returns things that cost you
33. Filtering on the total is the difference between a useful alert and a misleading one.
"""

from __future__ import annotations

from dataclasses import dataclass

from vinted_sniper.db.repo import Query
from vinted_sniper.vinted.models import Item


@dataclass(frozen=True, slots=True)
class Rejection:
    """Why a listing was skipped. Worth keeping around: it is what makes a quiet search
    explainable rather than suspicious."""

    reason: str
    detail: str


def check(item: Item, query: Query) -> Rejection | None:
    """Return why this listing should be skipped, or None if it should be sent."""
    if item.promoted:
        # Boosted listings resurface old stock. They are not new, whatever the feed says.
        return Rejection("promoted", "listing is a paid bump")

    if banned := _banned_keyword(item, query.banned_keywords):
        return Rejection("banned_keyword", f"title contains {banned!r}")

    if query.max_total_price is not None:
        payable = item.total_price if item.total_price is not None else item.price
        if payable is None:
            return Rejection("no_price", "listing has no price to compare")
        if payable > query.max_total_price:
            return Rejection(
                "over_budget",
                f"{payable} above limit {query.max_total_price} (buyer protection included)",
            )

    if query.conditions and (item.condition or "").lower() not in {
        condition.lower() for condition in query.conditions
    }:
        return Rejection("condition", f"condition {item.condition!r} not wanted")

    return None


def matches(item: Item, query: Query) -> bool:
    return check(item, query) is None


def _banned_keyword(item: Item, banned: list[str]) -> str | None:
    if not banned:
        return None
    haystack = item.title.lower()
    for word in banned:
        candidate = word.strip().lower()
        if candidate and candidate in haystack:
            return word
    return None
