"""Turning a pasted Vinted search URL into something we can poll.

Users paste whatever is in their address bar. That URL carries tracking parameters that
change on every page load, sometimes points at a brand or category page rather than a
search, and always belongs to one country's site. This module reduces all of that to a
canonical URL (used as the uniqueness key for a search) plus the API parameters to send.
"""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlparse

# Vinted runs one site per country. The domain decides the catalog, the currency and the
# sellers you see, so it travels with the search from bootstrap through to the link in
# your notification.
KNOWN_TLDS: Final[frozenset[str]] = frozenset(
    {
        "fr", "de", "nl", "es", "it", "pl", "be", "at", "cz", "sk", "lt", "pt",
        "se", "ro", "hu", "gr", "fi", "dk", "ie", "lu", "co.uk", "com",
    }
)  # fmt: skip

# Query parameters the site adds for its own bookkeeping. They differ between two visits
# to the same search, so they must not reach the uniqueness key.
_VOLATILE_PARAMS: Final[frozenset[str]] = frozenset(
    {
        "time",
        "search_id",
        "page",
        "per_page",
        "disabled_personalization",
        "referrer",
        "utm_source",
        "utm_medium",
        "utm_campaign",
    }
)

# Browser parameter name -> API parameter name. The site uses PHP-style array syntax in
# the address bar and singular/plural names in the API.
_PARAM_ALIASES: Final[dict[str, str]] = {
    "catalog": "catalog_ids",
    "catalog_ids": "catalog_ids",
    "brand_ids": "brand_ids",
    "brand": "brand_ids",
    "status": "status_ids",
    "status_ids": "status_ids",
    "size_ids": "size_ids",
    "size": "size_ids",
    "color_ids": "color_ids",
    "color": "color_ids",
    "material_ids": "material_ids",
    "country_ids": "country_ids",
    "city_ids": "city_ids",
    "video_game_rating_ids": "video_game_rating_ids",
}

# Parameters that take a single value rather than a list.
_SCALAR_PARAMS: Final[frozenset[str]] = frozenset(
    {"search_text", "price_from", "price_to", "currency", "order", "is_for_swap"}
)

_BRAND_PATH = re.compile(r"^/brand/(\d+)")
_CATALOG_PATH = re.compile(r"^/catalog/(\d+)")


class InvalidSearchURLError(ValueError):
    """The pasted text is not a Vinted search URL we can work with."""


def extract_tld(url: str) -> str:
    """Return the country suffix of a Vinted URL, e.g. 'fr' or 'co.uk'."""
    host = urlparse(url).netloc.lower().split(":")[0]
    host = host.removeprefix("www.")
    if not host.startswith("vinted."):
        raise InvalidSearchURLError(f"{url!r} is not a vinted.* address")

    tld = host.removeprefix("vinted.")
    if tld not in KNOWN_TLDS:
        raise InvalidSearchURLError(
            f"{tld!r} is not a Vinted country site. Known sites: {', '.join(sorted(KNOWN_TLDS))}"
        )
    return tld


def parse_search_params(url: str) -> dict[str, str]:
    """Extract the API parameters a pasted search URL is asking for.

    Category and brand landing pages carry their filter in the path rather than the query
    string, so those are folded in too.
    """
    parsed = urlparse(url)
    collected: dict[str, list[str]] = {}

    if brand := _BRAND_PATH.match(parsed.path):
        collected.setdefault("brand_ids", []).append(brand.group(1))
    if catalog := _CATALOG_PATH.match(parsed.path):
        collected.setdefault("catalog_ids", []).append(catalog.group(1))

    for raw_key, value in parse_qsl(parsed.query, keep_blank_values=False):
        key = raw_key.removesuffix("[]")
        if key in _VOLATILE_PARAMS:
            continue
        if key in _SCALAR_PARAMS:
            collected[key] = [value]
        elif api_key := _PARAM_ALIASES.get(key):
            collected.setdefault(api_key, []).append(value)
        # Anything unrecognised is dropped: passing unknown parameters through has
        # historically produced empty result sets rather than errors.

    params = {key: ",".join(values) for key, values in collected.items() if values}

    # We only ever want what was posted since the last check, newest first.
    params["order"] = "newest_first"
    return params


def normalise_search_url(url: str) -> str:
    """Return a stable canonical URL for a search.

    Two pastes of the same search produce the same string here, which is what lets the
    database reject duplicates instead of polling the same catalog twice.
    """
    url = url.strip()
    if not url:
        raise InvalidSearchURLError("no URL given")
    if not url.startswith(("http://", "https://")):
        url = f"https://{url}"

    tld = extract_tld(url)
    params = parse_search_params(url)
    if len(params) == 1:  # only the order we added ourselves
        raise InvalidSearchURLError(
            "that URL has no search filters on it. Open Vinted, set up the search you want, "
            "then copy the address bar once results are showing."
        )

    # safe="+" keeps multi-word searches intact: the site encodes spaces as '+' and
    # re-encoding those to %2B turns "nike air" into a search for a literal plus sign.
    query = urlencode(sorted(params.items()), safe="+,")
    return f"https://www.vinted.{tld}/catalog?{query}"


def catalog_endpoint(tld: str) -> str:
    return f"https://www.vinted.{tld}/api/v2/catalog/items"


def brands_endpoint(tld: str) -> str:
    """Brand autocomplete, the same one the site's search box uses."""
    return f"https://www.vinted.{tld}/api/v2/brands"


def filters_search_endpoint(tld: str) -> str:
    """Search within one filter's options — e.g. brands that exist in a category."""
    return f"https://www.vinted.{tld}/api/v2/catalog/filters/search"


def filters_facets_endpoint(tld: str) -> str:
    """The options of one filter (condition, colour, size…), scoped to a category."""
    return f"https://www.vinted.{tld}/api/v2/catalog/filters/facets"


def catalog_page(tld: str) -> str:
    """The search page itself — its HTML embeds the category tree and the CSRF token."""
    return f"https://www.vinted.{tld}/catalog"


def site_root(tld: str) -> str:
    return f"https://www.vinted.{tld}/"


def item_url(tld: str, item_id: int) -> str:
    return f"https://www.vinted.{tld}/items/{item_id}"


def member_url(tld: str, user_id: int) -> str:
    """A seller's profile page. The site accepts the bare id; no login slug needed."""
    return f"https://www.vinted.{tld}/member/{user_id}"


def message_seller_url(tld: str, item_id: int) -> str:
    """Deep link to the 'ask the seller' screen for a listing."""
    return f"https://www.vinted.{tld}/items/{item_id}/want_it/new"


def buy_url(tld: str, item_id: int) -> str:
    """Deep link that opens checkout for a listing.

    This only opens the page in the user's own browser, where they log in and decide.
    Nothing here buys anything on their behalf.
    """
    return (
        f"https://www.vinted.{tld}/transaction/buy/new"
        f"?source_screen=item&transaction%5Bitem_id%5D={item_id}"
    )
