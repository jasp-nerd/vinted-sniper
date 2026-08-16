"""Optional: sending requests through somewhere else.

Most people never need this. It matters when the address you are on has been refused and
you cannot move the machine — a VPS whose whole range is challenged, typically.

Deliberately small. Proxies are tried in order, one at a time, and a proxy that gets
refused is set aside for a while rather than being hammered. There is no scoring, no
health-checking thread and no persistence: on restart everything is tried again, which is
the right default because blocks expire.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from vinted_sniper.log import get_logger

log = get_logger(__name__)

# How long a proxy sits out after each kind of refusal. A block is about the address, so it
# is worth a long pause; a rate limit is about pace, so it is short.
QUARANTINE_BLOCKED_S = 600
QUARANTINE_AUTH_FAILED_S = 1800
QUARANTINE_NETWORK_S = 60


@dataclass
class ProxyRotation:
    """A list of proxies, and which ones are currently in the doghouse."""

    proxies: list[str] = field(default_factory=list)
    _benched_until: dict[str, float] = field(default_factory=dict)
    _next: int = 0

    @classmethod
    def from_file(cls, path: Path | None) -> ProxyRotation:
        """Read proxy URLs, one per line. Blank lines and # comments are ignored."""
        if path is None:
            return cls()
        if not path.exists():
            log.warning("proxies.file_missing", path=str(path))
            return cls()

        proxies = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        log.info("proxies.loaded", count=len(proxies), path=str(path))
        return cls(proxies=proxies)

    @property
    def enabled(self) -> bool:
        return bool(self.proxies)

    def acquire(self, now: float | None = None) -> str | None:
        """Return the next usable proxy, or None to go out directly.

        None also means "everything is benched": going direct beats not going at all, and
        the direct address may well be fine.
        """
        if not self.proxies:
            return None

        moment = now if now is not None else time.time()
        for offset in range(len(self.proxies)):
            candidate = self.proxies[(self._next + offset) % len(self.proxies)]
            if self._benched_until.get(candidate, 0.0) <= moment:
                self._next = (self._next + offset + 1) % len(self.proxies)
                return candidate

        log.warning("proxies.all_benched", count=len(self.proxies))
        return None

    def bench(self, proxy: str | None, seconds: int, reason: str) -> None:
        """Set a proxy aside for a while."""
        if proxy is None:
            return
        self._benched_until[proxy] = time.time() + seconds
        log.info("proxies.benched", proxy=_redact(proxy), seconds=seconds, reason=reason)

    def available(self, now: float | None = None) -> int:
        moment = now if now is not None else time.time()
        return sum(1 for p in self.proxies if self._benched_until.get(p, 0.0) <= moment)


def _redact(proxy: str) -> str:
    """Proxy URLs often carry credentials, and logs get pasted into issues."""
    if "@" not in proxy:
        return proxy
    scheme, _, rest = proxy.partition("://")
    return f"{scheme}://***@{rest.rpartition('@')[2]}" if rest else "***"
