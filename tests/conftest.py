from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

import pytest

from vinted_sniper.config import Settings
from vinted_sniper.db import Database, apply_pending
from vinted_sniper.db.repo import Repo
from vinted_sniper.vinted.transport import Response


class ScriptedTransport:
    """A transport that answers with whatever the test lined up.

    Records the requests it received so tests can assert on headers, cookies and paging
    rather than on mocks calling each other.
    """

    def __init__(self) -> None:
        # Homepage and catalog requests are queued separately: a test that lines up a
        # catalog response should not have it swallowed by the session bootstrap.
        self.responses: list[Response] = []
        self.root_responses: list[Response] = []
        self.requests: list[dict[str, Any]] = []
        self.closed = False
        self._default_cookies = {"access_token_web": "test-token"}

    def queue(self, response: Response) -> None:
        self.responses.append(response)

    def queue_root(self, response: Response) -> None:
        self.root_responses.append(response)

    def queue_catalog(self, items: list[dict[str, Any]], **kwargs: Any) -> None:
        self.queue(
            Response(
                status_code=200,
                text=json.dumps({"items": items, **kwargs}),
                headers={"content-type": "application/json"},
                cookies={},
            )
        )

    def queue_status(self, status_code: int, body: str = "", **headers: str) -> None:
        self.queue(Response(status_code=status_code, text=body, headers=headers, cookies={}))

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        cookies: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> Response:
        self.requests.append({"url": url, "headers": headers, "cookies": cookies, "params": params})

        # Homepage requests succeed with a session cookie unless a test says otherwise, so
        # that tests about the catalog do not have to set one up.
        if "/api/v2/" not in url:
            if self.root_responses:
                return self.root_responses.pop(0)
            return Response(
                status_code=200, text="<html></html>", headers={}, cookies=self._default_cookies
            )

        if not self.responses:
            return Response(
                status_code=200,
                text=json.dumps({"items": []}),
                headers={},
                cookies={},
            )
        return self.responses.pop(0)

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def transport() -> ScriptedTransport:
    return ScriptedTransport()


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "test.db")
    await database.connect()
    await apply_pending(database)
    yield database
    await database.close()


@pytest.fixture
def repo(db: Database) -> Repo:
    return Repo(db)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        db_path=tmp_path / "test.db",
        poll_default_interval_s=60,
        freshness_window_min=20,
        first_run_mode="silent",
    )


@pytest.fixture
def make_item() -> Callable[..., dict[str, Any]]:
    """Builds catalog entries shaped like the real thing."""

    def _build(item_id: int, *, photo_ts: int, price: str = "10.0", **overrides: Any) -> Any:
        entry: dict[str, Any] = {
            "id": item_id,
            "title": f"Item {item_id}",
            "url": f"https://www.vinted.fr/items/{item_id}",
            "brand_title": "Nike",
            "size_title": "M",
            "status": "Very good",
            "price": {"amount": price, "currency_code": "EUR"},
            "total_item_price": {
                "amount": str(round(float(price) * 1.1 + 0.7, 2)),
                "currency_code": "EUR",
            },
            "photo": {
                "full_size_url": f"https://images.vinted.net/{item_id}.jpeg",
                "high_resolution": {"id": str(item_id), "timestamp": photo_ts},
            },
            "user": {"id": 1, "login": "seller", "feedback_reputation": 0.9},
            "promoted": False,
        }
        entry.update(overrides)
        return entry

    return _build
