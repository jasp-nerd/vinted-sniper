"""The data behind the advanced-search picker: categories, brands, filter options.

Vinted retired the JSON endpoints that used to serve its category tree, so the tree now
comes from where the site itself gets it — embedded in the search page's HTML. That page
also carries the CSRF token that the filter endpoints ask for, which makes one document
fetch per country site enough to unlock everything here.

The tree changes rarely and weighs a few hundred kilobytes, so it is cached in the
database for a week. Brand autocomplete and filter options are live lookups: they answer
in milliseconds and their item counts are only worth showing fresh.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from http import HTTPStatus
from typing import Any, Final

from vinted_sniper.db.repo import Repo
from vinted_sniper.log import get_logger
from vinted_sniper.vinted import headers as hdr
from vinted_sniper.vinted import urls
from vinted_sniper.vinted.client import raise_for_status
from vinted_sniper.vinted.errors import MalformedResponseError, NetworkError
from vinted_sniper.vinted.session import SessionManager
from vinted_sniper.vinted.transport import TransportError

log = get_logger(__name__)

# The category tree is near-static — Vinted reshuffles it a few times a year.
CATALOG_TREE_TTL_S: Final = 7 * 24 * 3600

# The filters whose options the picker can ask for. Anything else 404s on Vinted's side.
FACET_CODES: Final[frozenset[str]] = frozenset({"status", "color", "size", "material"})

# How many autocomplete rows to hand the UI. Vinted's own dropdown shows about this many.
_MAX_BRANDS: Final = 15

# The tree JSON inside the page is ~1MB before compaction; this bounds the bracket walk.
_TREE_SEGMENT_LIMIT: Final = 4_000_000

_CSRF_PATTERNS: Final = (
    re.compile(r'CSRF_TOKEN\\":\s*\\"([^"\\]+)'),
    re.compile(r'"CSRF_TOKEN":\s*"([^"\\]+)"'),
    re.compile(r'<meta name="csrf-token" content="([^"]+)"'),
)


def extract_csrf(html: str) -> str | None:
    """Pull the CSRF token out of a Vinted page.

    It appears inside the Next.js flight payload (with escaped quotes) and sometimes as a
    plain meta tag; both are tried because Vinted has moved it before.
    """
    for pattern in _CSRF_PATTERNS:
        if match := pattern.search(html):
            return match.group(1)
    return None


def extract_catalog_tree(html: str) -> list[dict[str, Any]]:
    """Read the category tree out of the search page's HTML.

    The tree sits in the page's React flight data as `catalogTree:[...]` — usually inside
    a JS string, so every quote is escaped. The array is found by marker, unescaped, and
    cut at its matching bracket; anything less literal than that breaks the moment the
    surrounding framework payload changes shape.
    """
    escaped_at = html.find('catalogTree\\"')
    if escaped_at != -1:
        start = html.find("[", escaped_at)
        if start == -1:
            raise MalformedResponseError("catalogTree marker present but no array follows")
        segment = html[start : start + _TREE_SEGMENT_LIMIT]
        segment = segment.replace('\\"', '"').replace("\\\\", "\\")
    else:
        plain_at = html.find('"catalogTree":')
        if plain_at == -1:
            raise MalformedResponseError("the page carries no catalogTree data")
        start = html.find("[", plain_at)
        if start == -1:
            raise MalformedResponseError("catalogTree marker present but no array follows")
        segment = html[start : start + _TREE_SEGMENT_LIMIT]

    try:
        payload: Any = json.loads(segment[: _matching_bracket(segment)])
    except ValueError as exc:
        raise MalformedResponseError(f"catalogTree data did not parse: {exc}") from exc
    if not isinstance(payload, list):
        raise MalformedResponseError("catalogTree is not a list")
    return compact_tree(payload)


def _matching_bracket(text: str) -> int:
    """Index just past the `]` that closes the array `text` starts with."""
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index + 1
    raise MalformedResponseError("catalogTree data is truncated")


def compact_tree(nodes: list[Any]) -> list[dict[str, Any]]:
    """Keep only what the picker renders: id, title, children.

    The raw nodes carry photos and a dozen visibility flags; dropping them cuts the cached
    tree by an order of magnitude.
    """
    compacted: list[dict[str, Any]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        try:
            node_id = int(node["id"])
        except (KeyError, TypeError, ValueError):
            continue
        title = node.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        children = node.get("catalogs")
        compacted.append(
            {
                "id": node_id,
                "title": title.strip(),
                "children": compact_tree(children) if isinstance(children, list) else [],
            }
        )
    return compacted


def flatten_options(raw_options: list[Any], group: str | None = None) -> list[dict[str, Any]]:
    """Normalise a facet's options to flat {id, title, count, group?} rows.

    Sizes arrive grouped by size chart (each group holding its own options); everything
    else arrives flat. Both shapes end up flat here, with the group name kept as a label.
    """
    options: list[dict[str, Any]] = []
    for entry in raw_options:
        if not isinstance(entry, dict):
            continue
        nested = entry.get("options")
        if isinstance(nested, list) and nested:
            raw_label = entry.get("title") or entry.get("id")
            label = str(raw_label) if raw_label is not None else None
            options.extend(flatten_options(nested, group=label))
            continue
        try:
            option_id = int(entry["id"])
        except (KeyError, TypeError, ValueError):
            continue
        option: dict[str, Any] = {"id": option_id, "title": str(entry.get("title", option_id))}
        count = entry.get("items_count")
        if isinstance(count, int):
            option["count"] = count
        if group:
            option["group"] = group
        options.append(option)
    return options


class Taxonomy:
    """Serves the picker's data for any country site, through the app's own sessions.

    Everything goes out with the same cookies, headers and (if configured) proxies as the
    polling traffic, so enabling the picker does not create a second, more conspicuous
    kind of client.
    """

    def __init__(
        self,
        sessions: SessionManager,
        repo: Repo,
        *,
        tree_ttl_s: int = CATALOG_TREE_TTL_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._sessions = sessions
        self._repo = repo
        self._tree_ttl_s = tree_ttl_s
        self._clock = clock
        self._csrf: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, tld: str) -> asyncio.Lock:
        return self._locks.setdefault(tld, asyncio.Lock())

    # --- Categories ------------------------------------------------------------------

    async def categories(self, tld: str) -> list[dict[str, Any]]:
        """The full category tree for one site, from cache when it is fresh."""
        if (cached := await self._cached_tree(tld)) is not None:
            return cached
        async with self._lock(tld):
            # Whoever held the lock first has usually filled the cache by now.
            if (cached := await self._cached_tree(tld)) is not None:
                return cached
            return await self._refresh_from_page(tld)

    async def _cached_tree(self, tld: str) -> list[dict[str, Any]] | None:
        raw = await self._repo.get_state_value(f"catalog_tree:{tld}")
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        fetched_at = payload.get("fetched_at")
        tree = payload.get("tree")
        if not isinstance(fetched_at, int | float) or not isinstance(tree, list):
            return None
        if self._clock() - fetched_at >= self._tree_ttl_s:
            return None
        return tree

    async def _refresh_from_page(self, tld: str) -> list[dict[str, Any]]:
        """Fetch the search page and bank both things it contains: tree and CSRF token."""
        session = await self._sessions.get(tld)
        transport = self._sessions.transport_for(session)
        try:
            response = await transport.get(
                urls.catalog_page(tld),
                headers=hdr.document_headers(tld, session.identity),
                cookies=session.cookie_header,
                follow_redirects=True,
            )
        except TransportError as exc:
            raise NetworkError(f"could not load the vinted.{tld} search page: {exc}") from exc

        raise_for_status(response, tld)
        await self._sessions.merge_cookies(session, response.cookies)

        if csrf := extract_csrf(response.text):
            self._csrf[tld] = csrf

        tree = extract_catalog_tree(response.text)
        await self._repo.set_state_value(
            f"catalog_tree:{tld}",
            json.dumps({"fetched_at": int(self._clock()), "tree": tree}),
        )
        log.info("taxonomy.tree_refreshed", tld=tld, roots=len(tree))
        return tree

    # --- Brands ----------------------------------------------------------------------

    async def brands(self, tld: str, query: str, catalog_ids: str = "") -> list[dict[str, Any]]:
        """Brand autocomplete: global, or narrowed to brands present in a category."""
        query = query.strip()
        if not query:
            return []

        if catalog_ids:
            payload = await self._api_get(
                tld,
                urls.filters_search_endpoint(tld),
                {
                    "filter_search_code": "brand",
                    "filter_search_text": query,
                    "catalog_ids": catalog_ids,
                },
                with_csrf=True,
            )
            raw = payload.get("options") if isinstance(payload, dict) else None
            return flatten_options(raw or [])[:_MAX_BRANDS]

        payload = await self._api_get(
            tld, urls.brands_endpoint(tld), {"keyword": query}, with_csrf=False
        )
        raw = payload.get("brands") if isinstance(payload, dict) else None
        brands: list[dict[str, Any]] = []
        for entry in raw or []:
            if not isinstance(entry, dict):
                continue
            try:
                brand_id = int(entry["id"])
            except (KeyError, TypeError, ValueError):
                continue
            brand: dict[str, Any] = {"id": brand_id, "title": str(entry.get("title", brand_id))}
            if isinstance(count := entry.get("item_count"), int):
                brand["count"] = count
            brands.append(brand)
        return brands[:_MAX_BRANDS]

    # --- Other filters ---------------------------------------------------------------

    async def facet_options(
        self, tld: str, code: str, catalog_ids: str = ""
    ) -> list[dict[str, Any]]:
        """The options of one filter — condition, colour, size or material."""
        if code not in FACET_CODES:
            raise ValueError(f"{code!r} is not a filter this app knows how to ask for")
        params = {"filter_code": code}
        if catalog_ids:
            params["catalog_ids"] = catalog_ids
        payload = await self._api_get(
            tld, urls.filters_facets_endpoint(tld), params, with_csrf=True
        )
        raw = payload.get("options") if isinstance(payload, dict) else None
        return flatten_options(raw or [])

    # --- Shared request path ---------------------------------------------------------

    async def _api_get(
        self,
        tld: str,
        url: str,
        params: dict[str, str],
        *,
        with_csrf: bool,
        _retry: bool = True,
    ) -> Any:
        session = await self._sessions.get(tld)
        transport = self._sessions.transport_for(session)
        headers = hdr.api_headers(tld, session.identity)
        if with_csrf:
            headers["X-Csrf-Token"] = await self._ensure_csrf(tld)
            if anon_id := session.cookies.get("anon_id"):
                headers["X-Anon-Id"] = anon_id

        try:
            response = await transport.get(
                url,
                headers=headers,
                cookies=session.cookie_header,
                params=params,
            )
        except TransportError as exc:
            raise NetworkError(str(exc)) from exc

        if response.status_code == HTTPStatus.UNAUTHORIZED and _retry:
            # Either the anonymous token or the CSRF token aged out. Both come from the
            # same place, so start a fresh session, re-read the page, and try once more.
            log.info("taxonomy.reauth", tld=tld, url=url)
            self._csrf.pop(tld, None)
            await self._sessions.rotate(tld)
            return await self._api_get(tld, url, params, with_csrf=with_csrf, _retry=False)

        raise_for_status(response, tld)
        await self._sessions.merge_cookies(session, response.cookies)

        try:
            return response.json()
        except ValueError as exc:
            preview = response.text[:200].replace("\n", " ")
            raise MalformedResponseError(f"vinted.{tld} did not return JSON: {preview!r}") from exc

    async def _ensure_csrf(self, tld: str) -> str:
        if token := self._csrf.get(tld):
            return token
        async with self._lock(tld):
            if token := self._csrf.get(tld):
                return token
            await self._refresh_from_page(tld)
        if token := self._csrf.get(tld):
            return token
        raise MalformedResponseError(f"the vinted.{tld} search page carried no CSRF token")
