"""The dashboard.

Worth testing properly because it is the part most people will actually touch, and because
it holds webhook URLs and chat ids — so "is it locked" is a correctness question.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.conftest import ScriptedTransport
from vinted_sniper.config import Settings
from vinted_sniper.db import Database
from vinted_sniper.db.repo import Repo
from vinted_sniper.vinted.session import SessionManager
from vinted_sniper.vinted.taxonomy import Taxonomy
from vinted_sniper.vinted.transport import Response
from vinted_sniper.web.server import SESSION_COOKIE, create_app

TOKEN = "test-token-please-ignore"


@pytest.fixture
def web_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        db_path=tmp_path / "app.db",
        web_enabled=True,
        web_auth_token=SecretStr(TOKEN),
    )


@pytest.fixture
def client(web_settings: Settings, repo: Repo) -> Iterator[TestClient]:
    with TestClient(create_app(web_settings, repo)) as test_client:
        yield test_client


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    client.cookies.set(SESSION_COOKIE, TOKEN)
    return client


# --- Access -------------------------------------------------------------------------


def test_the_dashboard_sends_you_to_the_login_page(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/api/health"),
        ("post", "/searches"),
        ("post", "/destinations"),
        ("post", "/searches/1/delete"),
    ],
)
def test_nothing_useful_works_without_the_token(client: TestClient, method: str, path: str) -> None:
    response = getattr(client, method)(path, follow_redirects=False)

    assert response.status_code in (401, 303, 422)
    assert response.status_code != 200


def test_the_wrong_token_is_refused(client: TestClient) -> None:
    response = client.post("/login", data={"access_token": "not-it"}, follow_redirects=False)

    assert response.status_code == 401
    assert SESSION_COOKIE not in response.cookies


def test_the_right_token_signs_you_in(client: TestClient) -> None:
    response = client.post("/login", data={"access_token": TOKEN}, follow_redirects=False)

    assert response.status_code == 303
    assert response.cookies[SESSION_COOKIE] == TOKEN


def test_the_health_check_needs_no_token(client: TestClient) -> None:
    """The container's health check has no way to sign in."""
    response = client.get("/healthz")

    assert response.status_code in (200, 503)
    assert "alive" in response.json()


def test_without_a_token_the_dashboard_is_open(tmp_path: Path, repo: Repo) -> None:
    """The default for a localhost dashboard: no password, no sign-in page."""
    settings = Settings(_env_file=None, db_path=tmp_path / "a.db", web_enabled=True)  # type: ignore[call-arg]

    with TestClient(create_app(settings, repo)) as client:
        assert client.get("/", follow_redirects=False).status_code == 200
        assert client.get("/api/health").status_code == 200
        # The sign-in page has nothing to ask for, so it sends you to the dashboard.
        assert client.get("/login", follow_redirects=False).status_code == 303


async def test_without_a_token_the_feed_needs_no_key(tmp_path: Path, repo: Repo) -> None:
    settings = Settings(_env_file=None, db_path=tmp_path / "b.db", web_enabled=True)  # type: ignore[call-arg]

    with TestClient(create_app(settings, repo)) as client:
        client.post(
            "/searches",
            data={"url": "https://www.vinted.fr/catalog?search_text=nike"},
            follow_redirects=False,
        )
        query_id = (await repo.list_queries())[0].id
        assert client.get(f"/rss/{query_id}.xml").status_code == 200


# --- Using it -----------------------------------------------------------------------


async def test_adding_a_search_by_pasting_a_url(signed_in: TestClient, repo: Repo) -> None:
    response = signed_in.post(
        "/searches",
        data={
            "url": "https://www.vinted.fr/catalog?search_text=nike+air&price_to=40&time=999",
            "name": "",
            "interval": "60",
            "max_total_price": "25",
            "banned_keywords": "replica, fake",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    searches = await repo.list_queries()
    assert len(searches) == 1
    assert searches[0].tld == "fr"
    assert searches[0].max_total_price is not None
    assert searches[0].banned_keywords == ["replica", "fake"]
    assert "time=999" not in searches[0].url, "tracking parameters should not survive"


async def test_a_url_that_is_not_a_search_is_rejected_with_advice(
    signed_in: TestClient, repo: Repo
) -> None:
    response = signed_in.post(
        "/searches", data={"url": "https://example.com/nope"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert await repo.list_queries() == []


async def test_the_same_search_cannot_be_added_twice(signed_in: TestClient, repo: Repo) -> None:
    payload = {"url": "https://www.vinted.fr/catalog?search_text=nike"}
    signed_in.post("/searches", data=payload, follow_redirects=False)
    response = signed_in.post("/searches", data=payload, follow_redirects=False)

    assert "error=" in response.headers["location"]
    assert len(await repo.list_queries()) == 1


async def test_a_search_below_the_interval_floor_is_raised_to_it(
    signed_in: TestClient, repo: Repo
) -> None:
    signed_in.post(
        "/searches",
        data={"url": "https://www.vinted.fr/catalog?search_text=nike", "interval": "1"},
        follow_redirects=False,
    )

    searches = await repo.list_queries()
    assert searches[0].poll_interval_s >= 10


async def test_pausing_and_deleting_a_search(signed_in: TestClient, repo: Repo) -> None:
    signed_in.post(
        "/searches",
        data={"url": "https://www.vinted.fr/catalog?search_text=nike"},
        follow_redirects=False,
    )
    query_id = (await repo.list_queries())[0].id

    signed_in.post(f"/searches/{query_id}/pause", data={"paused": "1"}, follow_redirects=False)
    assert (await repo.list_queries())[0].paused is True

    signed_in.post(f"/searches/{query_id}/delete", follow_redirects=False)
    assert await repo.list_queries() == []


async def test_adding_a_discord_destination(signed_in: TestClient, repo: Repo) -> None:
    signed_in.post(
        "/destinations",
        data={
            "kind": "discord",
            "name": "my server",
            "target": "https://discord.com/api/webhooks/1/abc",
        },
        follow_redirects=False,
    )

    destinations = await repo.list_destinations()
    assert len(destinations) == 1
    assert destinations[0].config["webhook_url"].startswith("https://discord.com/")


async def test_a_discord_destination_that_is_not_a_url_is_refused(
    signed_in: TestClient, repo: Repo
) -> None:
    response = signed_in.post(
        "/destinations",
        data={"kind": "discord", "name": "typo", "target": "my-webhook"},
        follow_redirects=False,
    )

    assert "error=" in response.headers["location"]
    assert await repo.list_destinations() == []


async def test_the_dashboard_shows_what_is_being_watched(signed_in: TestClient, repo: Repo) -> None:
    signed_in.post(
        "/searches",
        data={"url": "https://www.vinted.fr/catalog?search_text=nike", "name": "my search"},
        follow_redirects=False,
    )

    body = signed_in.get("/").text

    assert "my search" in body
    assert "vinted.fr" in body


def test_the_health_api_answers_with_the_snapshot(signed_in: TestClient) -> None:
    body = signed_in.get("/api/health").json()

    assert set(body) >= {"alive", "searches", "queued_notifications"}


async def test_the_rss_feed_needs_the_token(signed_in: TestClient, repo: Repo) -> None:
    signed_in.post(
        "/searches",
        data={"url": "https://www.vinted.fr/catalog?search_text=nike"},
        follow_redirects=False,
    )
    query_id = (await repo.list_queries())[0].id

    assert signed_in.get(f"/rss/{query_id}.xml").status_code == 401

    response = signed_in.get(f"/rss/{query_id}.xml?key={TOKEN}")
    assert response.status_code == 200
    assert response.text.startswith("<?xml")


# --- The advanced-search builder ------------------------------------------------------


@pytest.fixture
def builder_client(
    web_settings: Settings, db: Database, repo: Repo, transport: ScriptedTransport
) -> Iterator[TestClient]:
    """A dashboard wired to a taxonomy service that talks to a scripted Vinted."""
    taxonomy = Taxonomy(SessionManager(db, transport), repo)
    with TestClient(create_app(web_settings, repo, taxonomy)) as test_client:
        test_client.cookies.set(SESSION_COOKIE, TOKEN)
        yield test_client


def _page_with_tree() -> Response:
    payload = {
        "CSRF_TOKEN": "11112222-3333-4444",
        "catalogTree": [{"id": 1904, "title": "Women", "catalogs": []}],
    }
    html = f"<script>self.__next_f.push([1,{json.dumps(json.dumps(payload))}])</script>"
    return Response(status_code=200, text=html, headers={}, cookies={"access_token_web": "t"})


def test_the_dashboard_offers_the_builder_when_the_service_is_wired(
    builder_client: TestClient, signed_in: TestClient
) -> None:
    assert "Build a search instead" in builder_client.get("/").text
    assert "Build a search instead" not in signed_in.get("/").text


def test_the_filter_endpoints_need_a_login(client: TestClient) -> None:
    for path in (
        "/api/filters/fr/categories",
        "/api/filters/fr/brands?q=nike",
        "/api/filters/fr/facets/status",
    ):
        assert client.get(path).status_code == 401, path


def test_without_the_service_the_builder_answers_503(signed_in: TestClient) -> None:
    assert signed_in.get("/api/filters/fr/categories").status_code == 503


def test_categories_come_back_as_a_tree(
    builder_client: TestClient, transport: ScriptedTransport
) -> None:
    transport.queue_root(_page_with_tree())  # session bootstrap
    transport.queue_root(_page_with_tree())  # the page that carries the tree

    response = builder_client.get("/api/filters/fr/categories")

    assert response.status_code == 200
    assert response.json() == {"categories": [{"id": 1904, "title": "Women", "children": []}]}


def test_an_unknown_site_is_refused_before_talking_to_vinted(
    builder_client: TestClient, transport: ScriptedTransport
) -> None:
    assert builder_client.get("/api/filters/xx/categories").status_code == 404
    assert transport.requests == []


def test_a_short_brand_query_is_answered_locally(
    builder_client: TestClient, transport: ScriptedTransport
) -> None:
    response = builder_client.get("/api/filters/fr/brands?q=n")

    assert response.json() == {"brands": []}
    assert transport.requests == []


def test_brands_pass_through_with_ids_and_counts(
    builder_client: TestClient, transport: ScriptedTransport
) -> None:
    transport.queue(
        Response(
            status_code=200,
            text=json.dumps({"brands": [{"id": 53, "title": "Nike", "item_count": 9}]}),
            headers={},
            cookies={},
        )
    )

    response = builder_client.get("/api/filters/fr/brands?q=nike")

    assert response.json() == {"brands": [{"id": 53, "title": "Nike", "count": 9}]}


def test_junk_in_catalog_ids_never_reaches_vinted(
    builder_client: TestClient, transport: ScriptedTransport
) -> None:
    transport.queue_root(_page_with_tree())
    transport.queue_root(_page_with_tree())
    transport.queue(
        Response(status_code=200, text=json.dumps({"options": []}), headers={}, cookies={})
    )

    builder_client.get("/api/filters/fr/brands?q=nike&catalog_ids=12,drop%20table,34")

    assert transport.requests[-1]["params"]["catalog_ids"] == "12,34"


def test_an_unknown_facet_is_a_404(builder_client: TestClient) -> None:
    assert builder_client.get("/api/filters/fr/facets/shoe_smell").status_code == 404


def test_a_refusal_from_vinted_surfaces_as_a_502_with_the_reason(
    builder_client: TestClient, transport: ScriptedTransport
) -> None:
    transport.queue_root(_page_with_tree())
    transport.queue_root(_page_with_tree())
    transport.queue_status(403, "blocked")

    response = builder_client.get("/api/filters/fr/facets/status")

    assert response.status_code == 502
    assert "error" in response.json()
