"""How bytes get to Vinted and back.

Everything above this file works against the `Transport` protocol, which is what makes the
HTTP client a swappable detail. Plain httpx is the default and, at the polling rates this
app uses, it is enough. If that changes, `CurlCffiTransport` presents a browser's TLS
fingerprint instead, and nothing else in the codebase has to know.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self, runtime_checkable

import httpx


@dataclass(frozen=True, slots=True)
class Response:
    """Just the parts of a response this app cares about."""

    status_code: int
    text: str
    headers: dict[str, str]
    cookies: dict[str, str]

    def json(self) -> Any:
        return json.loads(self.text)


class TransportError(Exception):
    """The request never produced a response: DNS, TLS, timeout, connection reset."""


@runtime_checkable
class Transport(Protocol):
    """The whole surface the rest of the app needs from an HTTP client."""

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        cookies: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> Response: ...

    async def aclose(self) -> None: ...


class HttpxTransport:
    """The default client.

    One connection pool for the process, so TLS handshakes and HTTP/2 connections are
    reused rather than rebuilt on every poll.
    """

    def __init__(self, timeout: float = 15.0, proxy: str | None = None) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            proxy=proxy,
            follow_redirects=False,
            # Cookies are held by the session layer, which persists them across restarts.
            # Letting httpx keep its own jar as well would give us two sources of truth.
            cookies=None,
        )

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        cookies: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> Response:
        try:
            response = await self._client.get(
                url,
                headers=headers,
                cookies=cookies,
                params=params,
                follow_redirects=follow_redirects,
            )
        except httpx.HTTPError as exc:
            raise TransportError(str(exc)) from exc

        return Response(
            status_code=response.status_code,
            text=response.text,
            headers={k.lower(): v for k, v in response.headers.items()},
            cookies=dict(response.cookies),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class CurlCffiTransport:
    """Optional client that reproduces a real browser's TLS handshake.

    Only worth reaching for if plain requests start coming back 403 while the same search
    loads fine in your browser. Needs the `impersonate` extra.
    """

    def __init__(
        self, timeout: float = 15.0, proxy: str | None = None, impersonate: str = "chrome"
    ) -> None:
        try:
            from curl_cffi import requests as curl_requests  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - depends on optional install
            raise RuntimeError(
                "VINTED_SNIPER_HTTP_IMPERSONATE is on but curl_cffi is not installed. "
                "Install it with: uv sync --extra impersonate"
            ) from exc

        self._session = curl_requests.AsyncSession(
            timeout=timeout,
            proxy=proxy,
            impersonate=impersonate,
        )

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        cookies: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> Response:
        try:
            response = await self._session.get(
                url,
                headers=headers,
                cookies=cookies or {},
                params=params,
                allow_redirects=follow_redirects,
            )
        except Exception as exc:  # curl_cffi raises its own error hierarchy
            raise TransportError(str(exc)) from exc

        return Response(
            status_code=response.status_code,
            text=response.text,
            headers={k.lower(): v for k, v in response.headers.items() if v is not None},
            cookies=dict(response.cookies),
        )

    async def aclose(self) -> None:
        await self._session.close()


@dataclass
class MockTransport:
    """Replays responses from disk.

    Used by the test suite and by `FETCH_MODE=mock`, so you can develop against a recorded
    catalog — or a recorded 403 — without touching the real site.

    A scenario directory holds JSON files describing responses. `catalog.json` answers
    catalog requests, `root.json` answers homepage requests. Both may instead be a list,
    in which case successive calls walk through it and the last entry repeats.
    """

    scenario_dir: Path
    _cursors: dict[str, int] = field(default_factory=dict)

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        cookies: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> Response:
        del headers, cookies, params, follow_redirects
        name = "catalog" if "/api/v2/catalog/items" in url else "root"
        path = self.scenario_dir / f"{name}.json"
        if not path.exists():
            raise TransportError(f"mock scenario has no {path.name} for {url}")

        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            index = min(self._cursors.get(name, 0), len(payload) - 1)
            self._cursors[name] = index + 1
            payload = payload[index]

        body = payload.get("body", {})
        return Response(
            status_code=int(payload.get("status", 200)),
            text=body if isinstance(body, str) else json.dumps(body),
            headers={k.lower(): v for k, v in payload.get("headers", {}).items()},
            cookies=payload.get("cookies", {}),
        )

    async def aclose(self) -> None:
        return None


def build_transport(
    *,
    impersonate: bool,
    timeout: float,
    proxy: str | None = None,
    mock_dir: Path | None = None,
) -> Transport:
    """Pick the client to use, given how the app is configured."""
    if mock_dir is not None:
        return MockTransport(scenario_dir=mock_dir)
    if impersonate:
        return CurlCffiTransport(timeout=timeout, proxy=proxy)
    return HttpxTransport(timeout=timeout, proxy=proxy)


class TransportPool:
    """One transport per route out.

    Without proxies this is a single client, which is the common case. With proxies it is
    one per proxy, built the first time that proxy is used and kept afterwards, so a
    connection pool is not thrown away every time a session rotates.
    """

    def __init__(self, build: Callable[[str | None], Transport]) -> None:
        self._build = build
        self._transports: dict[str | None, Transport] = {}

    def get(self, proxy: str | None = None) -> Transport:
        transport = self._transports.get(proxy)
        if transport is None:
            transport = self._build(proxy)
            self._transports[proxy] = transport
        return transport

    async def aclose(self) -> None:
        for transport in self._transports.values():
            with contextlib.suppress(Exception):
                await transport.aclose()
        self._transports.clear()


class TransportSession:
    """Owns a transport for the lifetime of a `with` block."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport

    async def __aenter__(self) -> Transport:
        return self.transport

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.transport.aclose()

    @classmethod
    def build(
        cls,
        *,
        impersonate: bool,
        timeout: float,
        proxy: str | None = None,
        mock_dir: Path | None = None,
    ) -> Self:
        return cls(
            build_transport(
                impersonate=impersonate, timeout=timeout, proxy=proxy, mock_dir=mock_dir
            )
        )
