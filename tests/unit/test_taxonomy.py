"""The advanced-search data layer: page mining, caching, and the CSRF dance."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.conftest import ScriptedTransport
from vinted_sniper.db import Database
from vinted_sniper.db.repo import Repo
from vinted_sniper.vinted.errors import MalformedResponseError
from vinted_sniper.vinted.session import SessionManager
from vinted_sniper.vinted.taxonomy import (
    Taxonomy,
    compact_tree,
    extract_catalog_tree,
    extract_csrf,
    flatten_options,
)
from vinted_sniper.vinted.transport import Response

TREE = [
    {
        "id": 1904,
        "title": "Women",
        "url": "/catalog/1904-women",
        "photo": {"url": "irrelevant"},
        "catalogs": [
            {"id": 4, "title": "Clothing", "catalogs": []},
            {"id": 1242, "title": 'Shoes & "sneakers"', "catalogs": []},
        ],
    },
    {"id": 5, "title": "Men", "catalogs": []},
]


def flight_page(
    tree: list[dict[str, Any]] | None = TREE, csrf: str | None = "abc-123-def-456"
) -> str:
    """A page the way Vinted serves it: the data inside an escaped JS string."""
    payload: dict[str, Any] = {}
    if csrf is not None:
        payload["CSRF_TOKEN"] = csrf
    if tree is not None:
        payload["catalogTree"] = tree
    embedded = json.dumps(json.dumps(payload))
    return f"<html><script>self.__next_f.push([1,{embedded}])</script></html>"


# --- Mining the page ------------------------------------------------------------------


def test_the_tree_is_found_inside_the_escaped_flight_payload() -> None:
    tree = extract_catalog_tree(flight_page())

    assert [node["id"] for node in tree] == [1904, 5]
    assert tree[0]["children"][1]["title"] == 'Shoes & "sneakers"'


def test_the_tree_is_found_when_served_as_plain_json_too() -> None:
    html = f'<script>{{"catalogTree":{json.dumps(TREE)}}}</script>'

    tree = extract_catalog_tree(html)

    assert [node["id"] for node in tree] == [1904, 5]


def test_a_page_without_the_tree_says_so() -> None:
    with pytest.raises(MalformedResponseError):
        extract_catalog_tree("<html>just a page</html>")


def test_a_truncated_tree_is_refused_rather_than_half_parsed() -> None:
    html = flight_page()
    cut = html[: html.rindex("]") - 40]

    with pytest.raises(MalformedResponseError):
        extract_catalog_tree(cut)


def test_the_csrf_token_is_found_in_flight_data_and_meta_tags() -> None:
    assert extract_csrf(flight_page(csrf="11112222-3333-4444")) == "11112222-3333-4444"
    assert extract_csrf('<meta name="csrf-token" content="tok"/>') == "tok"
    assert extract_csrf("<html>nothing</html>") is None


def test_compacting_keeps_only_what_the_picker_needs() -> None:
    compacted = compact_tree(
        [
            {"id": "7", "title": "  Kids ", "photo": {"big": "blob"}, "catalogs": []},
            {"id": None, "title": "no id"},
            {"title": "no id at all"},
            {"id": 9, "title": ""},
            "not even a dict",
        ]
    )

    assert compacted == [{"id": 7, "title": "Kids", "children": []}]


def test_facet_options_flatten_with_groups_and_counts() -> None:
    options = flatten_options(
        [
            {"id": 6, "title": "New with tags", "items_count": 34047},
            {
                "id": "SHOES-MEN-FR",
                "options": [{"id": "776", "title": "38", "items_count": 5708}],
            },
            {"id": "not-a-number", "title": "skipped"},
            42,
        ]
    )

    assert options == [
        {"id": 6, "title": "New with tags", "count": 34047},
        {"id": 776, "title": "38", "count": 5708, "group": "SHOES-MEN-FR"},
    ]


# --- The service ----------------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def taxonomy(db: Database, repo: Repo, transport: ScriptedTransport, clock: Clock) -> Taxonomy:
    sessions = SessionManager(db, transport)
    return Taxonomy(sessions, repo, clock=clock)


def queue_page(transport: ScriptedTransport, html: str) -> None:
    transport.queue_root(
        Response(status_code=200, text=html, headers={}, cookies={"access_token_web": "t"})
    )


def queue_bootstrap(transport: ScriptedTransport) -> None:
    """The homepage fetch that mints the session, which happens before any page mining."""
    queue_page(transport, "<html>the homepage</html>")


async def test_the_tree_is_fetched_once_then_served_from_the_database(
    taxonomy: Taxonomy, transport: ScriptedTransport
) -> None:
    queue_bootstrap(transport)
    queue_page(transport, flight_page())

    first = await taxonomy.categories("fr")
    fetches = len(transport.requests)
    second = await taxonomy.categories("fr")

    assert first[0]["title"] == "Women"
    assert second == first
    assert len(transport.requests) == fetches, "the second call should not touch Vinted"


async def test_the_cached_tree_expires_and_is_refetched(
    taxonomy: Taxonomy, transport: ScriptedTransport, clock: Clock
) -> None:
    queue_bootstrap(transport)
    queue_page(transport, flight_page())
    await taxonomy.categories("fr")

    clock.now += 8 * 24 * 3600
    fresh_tree = [{"id": 1, "title": "Everything", "catalogs": []}]
    queue_page(transport, flight_page(tree=fresh_tree))

    tree = await taxonomy.categories("fr")

    assert tree == [{"id": 1, "title": "Everything", "children": []}]


async def test_facet_requests_carry_the_csrf_token_from_the_page(
    taxonomy: Taxonomy, transport: ScriptedTransport
) -> None:
    queue_bootstrap(transport)
    queue_page(transport, flight_page(csrf="csrf-from-the-page"))
    transport.queue(
        Response(
            status_code=200,
            text=json.dumps(
                {"filter_code": "status", "options": [{"id": 6, "title": "New", "items_count": 3}]}
            ),
            headers={},
            cookies={},
        )
    )

    options = await taxonomy.facet_options("fr", "status", "1242")

    assert options == [{"id": 6, "title": "New", "count": 3}]
    api_request = transport.requests[-1]
    assert api_request["headers"]["X-Csrf-Token"] == "csrf-from-the-page"
    assert api_request["params"] == {"filter_code": "status", "catalog_ids": "1242"}


async def test_a_401_gets_one_fresh_session_and_one_retry(
    taxonomy: Taxonomy, transport: ScriptedTransport
) -> None:
    queue_bootstrap(transport)
    queue_page(transport, flight_page(csrf="stale"))
    transport.queue_status(401, json.dumps({"code": 100, "message": "expired"}))
    # The retry starts from scratch: new session, new page read, new token.
    queue_bootstrap(transport)
    queue_page(transport, flight_page(csrf="fresh"))
    transport.queue(
        Response(
            status_code=200,
            text=json.dumps({"filter_code": "color", "options": [{"id": 1, "title": "Black"}]}),
            headers={},
            cookies={},
        )
    )

    options = await taxonomy.facet_options("fr", "color")

    assert options == [{"id": 1, "title": "Black"}]
    facet_calls = [r for r in transport.requests if "filters/facets" in r["url"]]
    assert len(facet_calls) == 2
    assert facet_calls[-1]["headers"]["X-Csrf-Token"] == "fresh"


async def test_an_unknown_facet_is_refused_locally(taxonomy: Taxonomy) -> None:
    with pytest.raises(ValueError, match="not a filter"):
        await taxonomy.facet_options("fr", "shoe_smell")


async def test_brand_search_without_a_category_uses_the_global_endpoint(
    taxonomy: Taxonomy, transport: ScriptedTransport
) -> None:
    transport.queue(
        Response(
            status_code=200,
            text=json.dumps(
                {"brands": [{"id": 53, "title": "Nike", "item_count": 9, "slug": "nike"}]}
            ),
            headers={},
            cookies={},
        )
    )

    brands = await taxonomy.brands("fr", "nik")

    assert brands == [{"id": 53, "title": "Nike", "count": 9}]
    request = transport.requests[-1]
    assert "/api/v2/brands" in request["url"]
    assert request["params"] == {"keyword": "nik"}


async def test_brand_search_within_a_category_uses_the_scoped_endpoint(
    taxonomy: Taxonomy, transport: ScriptedTransport
) -> None:
    queue_bootstrap(transport)
    queue_page(transport, flight_page())
    transport.queue(
        Response(
            status_code=200,
            text=json.dumps({"options": [{"id": 53, "title": "Nike"}]}),
            headers={},
            cookies={},
        )
    )

    brands = await taxonomy.brands("fr", "nik", catalog_ids="1242")

    assert brands == [{"id": 53, "title": "Nike"}]
    request = transport.requests[-1]
    assert "/api/v2/catalog/filters/search" in request["url"]
    assert request["params"]["filter_search_text"] == "nik"
    assert request["params"]["catalog_ids"] == "1242"
    assert "X-Csrf-Token" in request["headers"]


async def test_a_blank_brand_query_asks_vinted_nothing(
    taxonomy: Taxonomy, transport: ScriptedTransport
) -> None:
    assert await taxonomy.brands("fr", "   ") == []
    assert transport.requests == []
