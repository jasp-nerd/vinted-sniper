"""The web UI.

On by default, bound to localhost unless you say otherwise. It exists
because editing a config file is where most people give up: pasting a Vinted URL into a box
is not.

It also answers the question the issue trackers of similar tools are full of — "why did it
stop?" — by showing, per search, when it last succeeded, what the last error was, and
whether the catalog has gone quiet. Nothing here is guessed from silence.

It listens on localhost and opens straight onto the dashboard — nothing to sign in to.
Setting VINTED_SNIPER_WEB_AUTH_TOKEN puts a password on it, which is the right move before
exposing it beyond your own machine: the database holds your webhook URLs and chat ids,
and an open dashboard on a public port would be a way to hand them out.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets
import time
from collections import Counter
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated, Any
from xml.sax.saxutils import escape as xml_escape

import uvicorn
from fastapi import Cookie, Depends, FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import SecretStr

from vinted_sniper.config import MIN_POLL_INTERVAL_S, Settings
from vinted_sniper.db.repo import Repo
from vinted_sniper.engine import health
from vinted_sniper.log import get_logger
from vinted_sniper.vinted import urls
from vinted_sniper.vinted.errors import VintedError
from vinted_sniper.vinted.taxonomy import FACET_CODES, Taxonomy

log = get_logger(__name__)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
SESSION_COOKIE = "vinted_sniper_session"


def _authorised(supplied: str | None, expected: SecretStr | None) -> bool:
    if expected is None:
        return True
    if not supplied:
        return False
    return secrets.compare_digest(supplied, expected.get_secret_value())


def create_app(settings: Settings, repo: Repo, taxonomy: Taxonomy | None = None) -> FastAPI:
    token = settings.web_auth_token  # None means no password: the dashboard just opens

    app = FastAPI(title="vinted-sniper", docs_url=None, redoc_url=None)

    async def require_login(
        session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> None:
        if not _authorised(session, token):
            raise HTTPException(status_code=401, detail="not signed in")

    guard = Depends(require_login)

    # --- Health, unauthenticated on purpose: the container check runs it ------------

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        alive = await health.is_alive(repo)
        return JSONResponse({"alive": alive}, status_code=200 if alive else 503)

    # --- Signing in ----------------------------------------------------------------

    @app.get("/login", response_class=HTMLResponse)
    async def login_form(request: Request) -> Response:
        if token is None:
            return RedirectResponse("/", status_code=303)
        return TEMPLATES.TemplateResponse(request, "login.html", {"error": None})

    @app.post("/login")
    async def login(request: Request, access_token: Annotated[str, Form()]) -> Response:
        if token is None:
            return RedirectResponse("/", status_code=303)
        if not _authorised(access_token, token):
            return TEMPLATES.TemplateResponse(
                request,
                "login.html",
                {"error": "That token does not match."},
                status_code=401,
            )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            SESSION_COOKIE,
            access_token,
            httponly=True,
            samesite="lax",
            max_age=30 * 86_400,
        )
        return response

    @app.post("/logout")
    async def logout() -> Response:
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE)
        return response

    # --- Dashboard -----------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(
        request: Request,
        session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> Response:
        if not _authorised(session, token):
            return RedirectResponse("/login", status_code=303)

        snapshot = await health.snapshot(repo)
        destinations = await repo.list_destinations()
        watched_tlds = [search.tld for search in snapshot.searches]
        return TEMPLATES.TemplateResponse(
            request,
            "dashboard.html",
            {
                "snapshot": snapshot,
                "destinations": destinations,
                "auth_enabled": token is not None,
                "recent": await repo.recent_items(limit=25),
                "now": int(time.time()),
                "min_interval": MIN_POLL_INTERVAL_S,
                "default_interval": settings.poll_default_interval_s,
                "builder_enabled": taxonomy is not None,
                "known_tlds": sorted(urls.KNOWN_TLDS),
                # Open the builder on the site the user already watches most.
                "default_tld": (
                    Counter(watched_tlds).most_common(1)[0][0] if watched_tlds else "fr"
                ),
            },
        )

    @app.get("/api/health")
    async def api_health(_: None = guard) -> JSONResponse:
        snapshot = await health.snapshot(repo)
        return JSONResponse(snapshot.as_dict())

    # --- Searches ------------------------------------------------------------------

    @app.post("/searches")
    async def add_search(
        url: Annotated[str, Form()],
        name: Annotated[str, Form()] = "",
        interval: Annotated[int, Form()] = 0,
        max_total_price: Annotated[str, Form()] = "",
        banned_keywords: Annotated[str, Form()] = "",
        destination_ids: Annotated[list[int] | None, Form()] = None,
        _: None = guard,
    ) -> Response:
        try:
            normalised = urls.normalise_search_url(url)
            tld = urls.extract_tld(normalised)
            params = urls.parse_search_params(normalised)
        except urls.InvalidSearchURLError as exc:
            return _redirect_with_error(str(exc))

        if await repo.find_query_by_url(normalised) is not None:
            return _redirect_with_error("that search is already being watched")

        query_id = await repo.add_query(
            name=name.strip() or _name_from(params, tld),
            url=normalised,
            tld=tld,
            params=params,
            poll_interval_s=max(interval or settings.poll_default_interval_s, MIN_POLL_INTERVAL_S),
            banned_keywords=[w.strip() for w in banned_keywords.split(",") if w.strip()],
            max_total_price=_decimal_or_none(max_total_price),
        )
        for destination_id in destination_ids or []:
            await repo.route(query_id, destination_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/searches/{query_id}/pause")
    async def pause_search(
        query_id: int, paused: Annotated[str, Form()], _: None = guard
    ) -> Response:
        await repo.set_paused(query_id, paused == "1")
        return RedirectResponse("/", status_code=303)

    @app.post("/searches/{query_id}/delete")
    async def delete_search(query_id: int, _: None = guard) -> Response:
        await repo.delete_query(query_id)
        return RedirectResponse("/", status_code=303)

    # --- Filter data for the advanced search builder -------------------------------
    # Thin JSON pass-throughs the dashboard's picker calls. The taxonomy service does
    # the talking to Vinted; a missing service (bare create_app in tests, or the web UI
    # run without the engine) answers 503 rather than pretending the picker can work.

    def _taxonomy_or_503() -> Taxonomy:
        if taxonomy is None:
            raise HTTPException(status_code=503, detail="the search builder is not available")
        return taxonomy

    def _known_tld(tld: str) -> str:
        if tld not in urls.KNOWN_TLDS:
            raise HTTPException(status_code=404, detail=f"vinted.{tld} is not a known site")
        return tld

    @app.get("/api/filters/{tld}/categories")
    async def filter_categories(tld: str, _: None = guard) -> JSONResponse:
        service = _taxonomy_or_503()
        try:
            tree = await service.categories(_known_tld(tld))
        except VintedError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse({"categories": tree})

    @app.get("/api/filters/{tld}/brands")
    async def filter_brands(
        tld: str, q: str = "", catalog_ids: str = "", _: None = guard
    ) -> JSONResponse:
        service = _taxonomy_or_503()
        if len(q.strip()) < 2:  # noqa: PLR2004 - an autocomplete needs two letters
            return JSONResponse({"brands": []})
        try:
            brands = await service.brands(_known_tld(tld), q, _id_list(catalog_ids))
        except VintedError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse({"brands": brands})

    @app.get("/api/filters/{tld}/facets/{code}")
    async def filter_facet(
        tld: str, code: str, catalog_ids: str = "", _: None = guard
    ) -> JSONResponse:
        service = _taxonomy_or_503()
        if code not in FACET_CODES:
            raise HTTPException(status_code=404, detail=f"no filter called {code!r}")
        try:
            options = await service.facet_options(_known_tld(tld), code, _id_list(catalog_ids))
        except VintedError as exc:
            return JSONResponse({"error": str(exc)}, status_code=502)
        return JSONResponse({"options": options})

    # --- Destinations --------------------------------------------------------------

    @app.post("/destinations")
    async def add_destination(
        kind: Annotated[str, Form()],
        name: Annotated[str, Form()] = "",
        target: Annotated[str, Form()] = "",
        _: None = guard,
    ) -> Response:
        target = target.strip()
        config: dict[str, Any]
        match kind:
            case "discord":
                if not target.startswith("https://"):
                    return _redirect_with_error("paste the full Discord webhook URL")
                config = {"webhook_url": target}
            case "telegram":
                if not target:
                    return _redirect_with_error(
                        "add the chat id, or use the pairing link from the command line"
                    )
                config = {"chat_id": target}
            case "webhook":
                config = {"url": target}
            case "ntfy":
                config = {"topic": target}
            case _:
                return _redirect_with_error(f"unknown destination type {kind!r}")

        await repo.add_destination(kind=kind, name=name.strip() or kind, config=config)
        return RedirectResponse("/", status_code=303)

    @app.post("/destinations/{destination_id}/delete")
    async def delete_destination(destination_id: int, _: None = guard) -> Response:
        await repo.deactivate_destination(destination_id, "removed from the dashboard")
        return RedirectResponse("/", status_code=303)

    @app.post("/searches/{query_id}/routes")
    async def set_routes(
        query_id: int,
        destination_ids: Annotated[list[int] | None, Form()] = None,
        _: None = guard,
    ) -> Response:
        wanted = set(destination_ids or [])
        current = set(await repo.destination_ids_for_query(query_id))
        for destination_id in current - wanted:
            await repo.unroute(query_id, destination_id)
        for destination_id in wanted - current:
            await repo.route(query_id, destination_id)
        return RedirectResponse("/", status_code=303)

    # --- RSS -----------------------------------------------------------------------

    @app.get("/rss/{query_id}.xml")
    async def rss(query_id: int, key: str = "") -> Response:
        # Feed readers cannot log in, so the token travels in the query string here.
        if not _authorised(key, token):
            raise HTTPException(status_code=401, detail="add ?key=<your token>")
        query = await repo.get_query(query_id)
        if query is None:
            raise HTTPException(status_code=404, detail="no such search")
        rows = [row for row in await repo.recent_items(limit=100) if row["query_id"] == query_id]
        return Response(content=_rss_feed(query.name, rows), media_type="application/rss+xml")

    return app


def _redirect_with_error(message: str) -> RedirectResponse:
    return RedirectResponse(f"/?error={message}", status_code=303)


def _id_list(raw: str) -> str:
    """Reduce user input to a comma-separated list of numeric ids, dropping the rest."""
    return ",".join(part.strip() for part in raw.split(",") if part.strip().isdigit())


def _decimal_or_none(raw: str) -> Decimal | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _name_from(params: dict[str, str], tld: str) -> str:
    if text := params.get("search_text"):
        return f"{text} ({tld})"
    return f"vinted.{tld} search"


def _rss_feed(title: str, rows: list[Any]) -> str:
    entries = []
    for row in rows:
        price = row["total_price"] or row["price"]
        description = f"{price} {row['currency'] or ''}".strip()
        entries.append(
            "<item>"
            f"<title>{xml_escape(row['title'] or 'Listing')}</title>"
            f"<link>{xml_escape(row['url'])}</link>"
            f"<guid isPermaLink='false'>{row['item_id']}</guid>"
            f"<description>{xml_escape(description)}</description>"
            "</item>"
        )
    return (
        "<?xml version='1.0' encoding='UTF-8'?>"
        "<rss version='2.0'><channel>"
        f"<title>{xml_escape(title)}</title>"
        "<description>Vinted listings matching this search</description>"
        "<link>https://www.vinted.com/</link>"
        f"{''.join(entries)}"
        "</channel></rss>"
    )


class _QuietServer(uvicorn.Server):
    """A server that leaves signal handling to the application.

    uvicorn would otherwise take over SIGTERM and SIGINT, and two sets of handlers means a
    shutdown that only half happens.
    """

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


async def serve(
    settings: Settings,
    repo: Repo,
    stop: asyncio.Event,
    taxonomy: Taxonomy | None = None,
) -> None:
    """Run the web UI until the app shuts down."""

    config = uvicorn.Config(
        create_app(settings, repo, taxonomy),
        host=settings.web_host,
        port=settings.web_port,
        log_config=None,
        access_log=False,
    )
    server = _QuietServer(config)
    serving = asyncio.create_task(server.serve())
    log.info("web.listening", host=settings.web_host, port=settings.web_port)
    if settings.web_is_exposed_without_a_password:
        log.warning(
            "web.no_password",
            host=settings.web_host,
            hint="the dashboard shows your webhook URLs; keep the published port on "
            "127.0.0.1 or set VINTED_SNIPER_WEB_AUTH_TOKEN",
        )
    await stop.wait()
    server.should_exit = True
    await serving
