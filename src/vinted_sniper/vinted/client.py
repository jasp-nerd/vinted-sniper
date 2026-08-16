"""Fetching a search's results.

One endpoint, one method. The care here goes into reading the response: a 200 from Vinted
is not a promise that the body is a catalog, and the tools that assumed it was are the ones
whose issue trackers fill up with tracebacks.
"""

from __future__ import annotations

import random
import time
from http import HTTPStatus
from typing import Any, Final

from vinted_sniper.log import get_logger
from vinted_sniper.vinted import headers as hdr
from vinted_sniper.vinted import urls
from vinted_sniper.vinted.errors import (
    AuthExpiredError,
    BlockedError,
    MalformedResponseError,
    NetworkError,
    RateLimitedError,
)
from vinted_sniper.vinted.models import Item, ParseError, parse_item
from vinted_sniper.vinted.session import SessionManager
from vinted_sniper.vinted.transport import Response, Transport, TransportError

log = get_logger(__name__)

# Vinted's own frontend sends a timestamp slightly behind the wall clock. Matching that,
# jitter included, is free and keeps our requests from looking machine-timed.
_TIME_SKEW_RANGE_S = (0, 180)

# Vinted's code for "your token is no longer valid", returned inside a 200 body as well
# as with a 401.
_INVALID_TOKEN_CODE: Final = 100

# The site caps this well below what you might ask for; 96 is what its own pages request.
PER_PAGE = 96


class VintedClient:
    """Reads search results from one or more Vinted country sites."""

    def __init__(
        self,
        transport: Transport,
        sessions: SessionManager,
        *,
        keep_raw: bool = False,
        rng: random.Random | None = None,
    ) -> None:
        self._transport = transport
        self._sessions = sessions
        self._keep_raw = keep_raw
        self._rng = rng or random.Random()

    async def search(self, tld: str, params: dict[str, str]) -> list[Item]:
        """Return the current first page of a search, newest first.

        Raises the error types in `errors.py`; the poller decides what each one means for
        the session and the schedule.
        """
        session = await self._sessions.get(tld)
        query = dict(params)
        query["per_page"] = str(PER_PAGE)
        query["time"] = str(int(time.time()) - self._rng.randint(*_TIME_SKEW_RANGE_S))

        try:
            response = await self._transport.get(
                urls.catalog_endpoint(tld),
                headers=hdr.api_headers(tld, session.identity),
                cookies=session.cookie_header,
                params=query,
            )
        except TransportError as exc:
            raise NetworkError(str(exc)) from exc

        _raise_for_status(response, tld)
        await self._sessions.note_request(session)
        await self._sessions.merge_cookies(session, response.cookies)

        return _parse_catalog(response, tld, keep_raw=self._keep_raw)


def _raise_for_status(response: Response, tld: str) -> None:
    """Turn an HTTP status into the error type that says what to do about it."""
    status = response.status_code

    if status == HTTPStatus.OK:
        return

    if status == HTTPStatus.UNAUTHORIZED:
        raise AuthExpiredError(f"vinted.{tld} rejected the session token")

    if status in (HTTPStatus.FORBIDDEN, HTTPStatus.PROXY_AUTHENTICATION_REQUIRED):
        raise BlockedError(f"vinted.{tld} refused the request with {status}")

    if status == HTTPStatus.TOO_MANY_REQUESTS:
        raise RateLimitedError(_retry_after(response))

    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        raise NetworkError(f"vinted.{tld} returned {status}")

    raise NetworkError(f"vinted.{tld} returned an unexpected {status}")


def _retry_after(response: Response) -> float | None:
    raw = response.headers.get("retry-after")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_catalog(response: Response, tld: str, *, keep_raw: bool) -> list[Item]:
    """Read a catalog response, refusing anything that is not one."""
    try:
        payload: Any = response.json()
    except ValueError as exc:
        # Most often this is an anti-bot interstitial served with a 200.
        preview = response.text[:200].replace("\n", " ")
        raise MalformedResponseError(f"vinted.{tld} did not return JSON: {preview!r}") from exc

    if not isinstance(payload, dict):
        raise MalformedResponseError(
            f"vinted.{tld} returned {type(payload).__name__}, expected an object"
        )

    if (message := payload.get("message")) and "items" not in payload:
        if payload.get("code") == _INVALID_TOKEN_CODE:
            raise AuthExpiredError(f"vinted.{tld} says: {message}")
        raise MalformedResponseError(f"vinted.{tld} says: {message}")

    raw_items = payload.get("items")
    if raw_items is None:
        raise MalformedResponseError(
            f"vinted.{tld} returned no items field; keys were {sorted(payload)[:8]}"
        )
    if not isinstance(raw_items, list):
        raise MalformedResponseError(
            f"vinted.{tld} returned items as {type(raw_items).__name__}, expected a list"
        )

    items: list[Item] = []
    for entry in raw_items:
        if not isinstance(entry, dict):
            continue
        try:
            items.append(parse_item(entry, tld, keep_raw=keep_raw))
        except ParseError as exc:
            # One malformed listing should not cost you the other ninety-five.
            log.warning("item.unparsed", tld=tld, error=str(exc))
    return items
