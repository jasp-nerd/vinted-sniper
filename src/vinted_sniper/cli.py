"""Command line entry points."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from vinted_sniper import __version__, log
from vinted_sniper.config import Settings
from vinted_sniper.db import Database, apply_pending
from vinted_sniper.vinted import urls
from vinted_sniper.vinted.client import VintedClient
from vinted_sniper.vinted.errors import BlockedError, VintedError
from vinted_sniper.vinted.session import SessionManager
from vinted_sniper.vinted.transport import TransportSession

TROUBLESHOOTING = "https://github.com/jasp/vinted-sniper/blob/main/docs/troubleshooting.md"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vinted-sniper",
        description="Watch Vinted searches and get notified when something matches.",
    )
    parser.add_argument("--version", action="version", version=f"vinted-sniper {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="Create or update the database, then exit.")

    check = sub.add_parser(
        "check",
        help="Fetch one search once and print what came back. Use this to prove the "
        "connection works before setting anything up.",
    )
    check.add_argument("--url", required=True, help="A Vinted search URL, copied from your browser.")

    return parser


async def _cmd_migrate(settings: Settings) -> int:
    async with Database(settings.db_path) as db:
        applied = await apply_pending(db)
    print(f"Database ready at {settings.db_path} ({applied} migration(s) applied).")
    return 0


async def _cmd_check(settings: Settings, url: str) -> int:
    try:
        normalised = urls.normalise_search_url(url)
        tld = urls.extract_tld(normalised)
        params = urls.parse_search_params(normalised)
    except urls.InvalidSearchURLError as exc:
        print(f"That URL will not work: {exc}", file=sys.stderr)
        return 2

    print(f"Site:   vinted.{tld}")
    print(f"Search: {normalised}")
    print(f"Params: {params}\n")

    async with Database(settings.db_path) as db:
        await apply_pending(db)
        async with TransportSession.build(
            impersonate=settings.http_impersonate,
            timeout=settings.request_timeout_s,
            mock_dir=settings.mock_scenario_dir if settings.fetch_mode == "mock" else None,
        ) as transport:
            sessions = SessionManager(
                db, transport, rotate_after_minutes=settings.session_rotate_minutes
            )
            client = VintedClient(transport, sessions)

            try:
                items = await client.search(tld, params)
            except BlockedError as exc:
                print(f"Blocked: {exc}\n", file=sys.stderr)
                print(
                    "This usually means the address you are connecting from is being "
                    f"challenged rather than anything about your search. Confirm it with:\n"
                    f"  curl -v -c - -L https://www.vinted.{tld}/ 2>&1 | grep access_token_web\n"
                    f"If that prints nothing, see {TROUBLESHOOTING}",
                    file=sys.stderr,
                )
                return 1
            except VintedError as exc:
                print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
                return 1

    if not items:
        print("Connected fine, but the search returned no listings.")
        print("Try a broader search to confirm everything works.")
        return 0

    print(f"Got {len(items)} listing(s). The newest few:\n")
    for item in items[:5]:
        listed = item.listed_at.strftime("%Y-%m-%d %H:%M UTC") if item.listed_at else "unknown"
        details = " · ".join(filter(None, [item.brand, item.size, item.condition]))
        print(f"  {item.title}")
        print(f"    {item.price_line()}")
        if details:
            print(f"    {details}")
        print(f"    listed {listed}")
        print(f"    {item.url}\n")
    return 0


async def _run(args: argparse.Namespace) -> int:
    settings = Settings()
    log.configure(level=settings.log_level, fmt=settings.log_format)

    if args.command == "migrate":
        return await _cmd_migrate(settings)
    if args.command == "check":
        return await _cmd_check(settings, args.url)
    raise AssertionError(f"unhandled command {args.command!r}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
