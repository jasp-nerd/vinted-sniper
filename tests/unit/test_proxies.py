from __future__ import annotations

import time
from pathlib import Path

from tests.conftest import ScriptedTransport
from vinted_sniper.vinted.proxies import QUARANTINE_BLOCKED_S, ProxyRotation
from vinted_sniper.vinted.transport import TransportPool


def write_proxies(tmp_path: Path, *lines: str) -> Path:
    path = tmp_path / "proxies.txt"
    path.write_text("\n".join(lines))
    return path


def test_no_proxy_file_means_going_out_directly() -> None:
    rotation = ProxyRotation.from_file(None)

    assert rotation.enabled is False
    assert rotation.acquire() is None


def test_a_missing_file_is_a_warning_not_a_crash(tmp_path: Path) -> None:
    rotation = ProxyRotation.from_file(tmp_path / "nope.txt")

    assert rotation.enabled is False


def test_comments_and_blank_lines_are_ignored(tmp_path: Path) -> None:
    path = write_proxies(
        tmp_path,
        "# my proxies",
        "http://one.test:8080",
        "",
        "   ",
        "http://two.test:8080",
    )

    rotation = ProxyRotation.from_file(path)

    assert rotation.proxies == ["http://one.test:8080", "http://two.test:8080"]


def test_proxies_are_used_in_turn(tmp_path: Path) -> None:
    rotation = ProxyRotation.from_file(
        write_proxies(tmp_path, "http://one.test", "http://two.test")
    )

    assert [rotation.acquire() for _ in range(4)] == [
        "http://one.test",
        "http://two.test",
        "http://one.test",
        "http://two.test",
    ]


def test_a_benched_proxy_is_skipped(tmp_path: Path) -> None:
    rotation = ProxyRotation.from_file(
        write_proxies(tmp_path, "http://one.test", "http://two.test")
    )

    rotation.bench("http://one.test", QUARANTINE_BLOCKED_S, "refused")

    assert [rotation.acquire() for _ in range(3)] == ["http://two.test"] * 3
    assert rotation.available() == 1


def test_going_direct_beats_not_going_at_all(tmp_path: Path) -> None:
    """With every proxy benched, the direct address may still work — try it."""
    rotation = ProxyRotation.from_file(write_proxies(tmp_path, "http://one.test"))
    rotation.bench("http://one.test", QUARANTINE_BLOCKED_S, "refused")

    assert rotation.acquire() is None


def test_a_bench_expires(tmp_path: Path) -> None:
    rotation = ProxyRotation.from_file(write_proxies(tmp_path, "http://one.test"))
    rotation.bench("http://one.test", 60, "refused")

    assert rotation.available(now=0) == 0
    # Blocks lift on their own, so a benched proxy has to come back.
    assert rotation.available(now=time.time() + 61) == 1


def test_credentials_do_not_reach_the_logs(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    rotation = ProxyRotation.from_file(write_proxies(tmp_path, "http://user:hunter2@proxy.test"))
    rotation.bench("http://user:hunter2@proxy.test", 60, "refused")

    assert "hunter2" not in capsys.readouterr().out


def test_the_pool_reuses_one_client_per_route() -> None:
    built: list[str | None] = []

    def build(proxy: str | None) -> ScriptedTransport:
        built.append(proxy)
        return ScriptedTransport()

    pool = TransportPool(build)

    first = pool.get(None)
    assert pool.get(None) is first, "a new client per request would throw away the connection pool"

    pool.get("http://one.test")
    pool.get("http://one.test")

    assert built == [None, "http://one.test"]


async def test_closing_the_pool_closes_everything_in_it() -> None:
    transports: list[ScriptedTransport] = []

    def build(proxy: str | None) -> ScriptedTransport:
        transport = ScriptedTransport()
        transports.append(transport)
        return transport

    pool = TransportPool(build)
    pool.get(None)
    pool.get("http://one.test")

    await pool.aclose()

    assert all(transport.closed for transport in transports)
