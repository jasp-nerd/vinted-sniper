"""The dashboard.

Worth testing properly because it is the part most people will actually touch, and because
it holds webhook URLs and chat ids — so "is it locked" is a correctness question.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from vinted_sniper.config import Settings
from vinted_sniper.db.repo import Repo
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


def test_a_dashboard_without_a_token_refuses_to_be_built(tmp_path: Path, repo: Repo) -> None:
    with pytest.raises(ValueError, match="WEB_AUTH_TOKEN"):
        Settings(_env_file=None, db_path=tmp_path / "a.db", web_enabled=True)  # type: ignore[call-arg]


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
