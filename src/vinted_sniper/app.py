"""Starting everything up and shutting it down cleanly.

One process, one event loop, one SQLite file. A task per search, a dispatcher, a watchdog,
a heartbeat, and optionally the web UI and the Telegram bot. A supervisor keeps the running
searches in step with what is in the database, so adding one in the web UI takes effect
without a restart.

Shutdown matters more than it sounds. Docker sends SIGTERM and waits about ten seconds
before killing the process; a loop sitting in a sixty-second sleep will be killed mid-send
every single time. So nothing here sleeps — everything waits on a stop event with a
timeout, which comes back the instant a signal arrives.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from vinted_sniper.config import Settings
from vinted_sniper.db import Database, apply_pending
from vinted_sniper.db.repo import Query, Repo
from vinted_sniper.deliver.dispatcher import Dispatcher
from vinted_sniper.engine.health import Heartbeat
from vinted_sniper.engine.poller import Poller
from vinted_sniper.engine.watchdog import Watchdog
from vinted_sniper.log import get_logger
from vinted_sniper.vinted.client import VintedClient
from vinted_sniper.vinted.proxies import ProxyRotation
from vinted_sniper.vinted.session import SessionManager
from vinted_sniper.vinted.transport import Transport, TransportPool, build_transport

log = get_logger(__name__)

# How often to notice searches added, paused or edited elsewhere.
SUPERVISE_INTERVAL_S = 15.0

# Housekeeping: prune old listings and flush undeliverable notifications.
HOUSEKEEPING_INTERVAL_S = 3600.0


class Application:
    """The whole running system."""

    def __init__(
        self, settings: Settings, *, supervise_interval_s: float = SUPERVISE_INTERVAL_S
    ) -> None:
        self._settings = settings
        self._supervise_interval_s = supervise_interval_s
        self._stop = asyncio.Event()
        self._work_available = asyncio.Event()
        self._poller_tasks: dict[int, asyncio.Task[None]] = {}
        self._poller_signatures: dict[int, tuple[str, int]] = {}

    def request_stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        settings = self._settings

        async with Database(settings.db_path) as db:
            await apply_pending(db)
            repo = Repo(db)

            proxies = ProxyRotation.from_file(settings.proxy_file)
            mock_dir = settings.mock_scenario_dir if settings.fetch_mode == "mock" else None

            def build(proxy: str | None) -> Transport:
                return build_transport(
                    impersonate=settings.http_impersonate,
                    timeout=settings.request_timeout_s,
                    proxy=proxy,
                    mock_dir=mock_dir,
                )

            pool = TransportPool(build)
            try:
                sessions = SessionManager(
                    db,
                    pool,
                    rotate_after_minutes=settings.session_rotate_minutes,
                    proxies=proxies,
                )
                client = VintedClient(None, sessions, keep_raw=settings.keep_raw_json)
                dispatcher = Dispatcher(
                    repo=repo,
                    settings=settings,
                    stop=self._stop,
                    work_available=self._work_available,
                )
                watchdog = Watchdog(
                    repo=repo,
                    sessions=sessions,
                    settings=settings,
                    stop=self._stop,
                    announce=dispatcher.notify_status,
                )
                heartbeat = Heartbeat(repo, self._stop)

                self._install_signal_handlers()
                log.info(
                    "app.starting",
                    db=str(settings.db_path),
                    fetch_mode=settings.fetch_mode,
                    web=settings.web_enabled,
                )

                try:
                    async with asyncio.TaskGroup() as tg:
                        tg.create_task(dispatcher.run(), name="dispatcher")
                        tg.create_task(watchdog.run(), name="watchdog")
                        tg.create_task(heartbeat.run(), name="heartbeat")
                        tg.create_task(self._housekeeping(repo), name="housekeeping")
                        tg.create_task(
                            self._supervise(tg, repo, client, sessions), name="supervisor"
                        )

                        if settings.telegram_bot_token is not None:
                            tg.create_task(self._run_telegram_bot(repo), name="telegram-bot")
                        if settings.web_enabled:
                            tg.create_task(self._run_web(repo), name="web")
                except* Exception as group:
                    for error in group.exceptions:
                        log.exception("app.task_failed", error=str(error))
                    raise
            finally:
                await pool.aclose()

        log.info("app.stopped")

    # --- Supervision ---------------------------------------------------------------

    async def _supervise(
        self,
        tg: asyncio.TaskGroup,
        repo: Repo,
        client: VintedClient,
        sessions: SessionManager,
    ) -> None:
        """Keep one running task per active search."""
        while not self._stop.is_set():
            try:
                await self._reconcile(tg, repo, client, sessions)
            except Exception as exc:
                log.exception("supervisor.failed", error=str(exc))

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self._supervise_interval_s)

        for task in self._poller_tasks.values():
            task.cancel()

    async def _reconcile(
        self,
        tg: asyncio.TaskGroup,
        repo: Repo,
        client: VintedClient,
        sessions: SessionManager,
    ) -> None:
        queries = {query.id: query for query in await repo.list_queries(include_paused=False)}

        for query_id in list(self._poller_tasks):
            query = queries.get(query_id)
            task = self._poller_tasks[query_id]
            changed = query is not None and self._signature(query) != self._poller_signatures.get(
                query_id
            )
            if query is None or changed or task.done():
                task.cancel()
                del self._poller_tasks[query_id]
                self._poller_signatures.pop(query_id, None)
                if query is None:
                    log.info("poller.stopped", query_id=query_id)

        for query_id, query in queries.items():
            if query_id in self._poller_tasks:
                continue
            poller = Poller(
                query,
                repo=repo,
                client=client,
                sessions=sessions,
                settings=self._settings,
                stop=self._stop,
                work_available=self._work_available,
            )
            self._poller_tasks[query_id] = tg.create_task(
                self._guard(poller), name=f"poll:{query_id}"
            )
            self._poller_signatures[query_id] = self._signature(query)
            log.info(
                "poller.started",
                query_id=query_id,
                query=query.name,
                tld=query.tld,
                every_s=query.poll_interval_s,
            )

    @staticmethod
    def _signature(query: Query) -> tuple[str, int]:
        """What about a search would make its running task out of date."""
        return (query.url, query.poll_interval_s)

    async def _guard(self, poller: Poller) -> None:
        """Run a search's loop so that its failure cannot take the others down with it."""
        try:
            await poller.run()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("poller.crashed", query_id=poller.query.id, error=str(exc))

    # --- Background chores ---------------------------------------------------------

    async def _housekeeping(self, repo: Repo) -> None:
        while not self._stop.is_set():
            with contextlib.suppress(Exception):
                pruned = await repo.prune_items(self._settings.item_retention_days)
                if pruned:
                    log.debug("housekeeping.pruned_items", count=pruned)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=HOUSEKEEPING_INTERVAL_S)

    async def _run_telegram_bot(self, repo: Repo) -> None:
        from vinted_sniper.botctl.telegram_bot import run_bot  # noqa: PLC0415

        token = self._settings.telegram_bot_token
        if token is None:  # pragma: no cover - guarded by the caller
            return
        try:
            await run_bot(token.get_secret_value(), repo=repo, stop=self._stop)
        except Exception as exc:
            log.exception("telegram_bot.failed", error=str(exc))

    async def _run_web(self, repo: Repo) -> None:
        from vinted_sniper.web.server import serve  # noqa: PLC0415

        try:
            await serve(self._settings, repo, self._stop)
        except Exception as exc:
            log.exception("web.failed", error=str(exc))

    # --- Signals -------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self._on_signal, sig)

    def _on_signal(self, sig: signal.Signals) -> None:
        log.info("app.signal", signal=sig.name)
        self._stop.set()


async def run(settings: Settings) -> None:
    await Application(settings).run()
