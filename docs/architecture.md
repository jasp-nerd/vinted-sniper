# How it works

One process, one event loop, one SQLite file. There is no queue server, no second database
and no worker fleet, because a tool watching a handful of searches does not need any of that
and every extra moving part is another thing that can be broken at three in the morning.

```
                    ┌─ poller ─┐  one task per search
   Vinted ◀── HTTP ─┤ poller   │  fetch → filter → decide what is new
                    └─ poller ─┘
                          │  one transaction: record listings + queue notifications
                          ▼
                    ┌──────────┐
                    │  SQLite  │  searches, state, listings, outbox, sessions
                    └──────────┘
                          │  claim in order, one worker per destination
                          ▼
                  dispatcher ──▶ Discord · Telegram · ntfy · your webhook

   watchdog    reads state across searches, spots a frozen catalog
   heartbeat   writes a timestamp the health check reads
   web         optional dashboard over the same database
```

## The parts

**`vinted/`** is everything that touches Vinted. `transport.py` defines a small protocol so
the HTTP client is a swappable detail — plain httpx by default, curl_cffi with a browser TLS
fingerprint if you turn it on, or a replay-from-disk transport for tests and offline
development. `session.py` gets an anonymous session cookie by loading the homepage the way a
browser would, stores it so a restart does not need a new handshake, and replaces it on a
timer. `client.py` makes the one request this app makes and refuses to believe a 200 is a
catalog until it has checked. `urls.py` turns a pasted address-bar URL into a canonical form
plus API parameters.

**`engine/`** decides what to do with the results. `filters.py` applies your rules,
`dedup.py` works out what is genuinely new, `poller.py` runs the loop and maps failures to
actions, `watchdog.py` compares searches against each other, `health.py` assembles the status
view.

**`deliver/`** gets notifications out. `outbox.py`-backed workers in `dispatcher.py` claim
work in order, one destination at a time, through a token bucket in `ratelimit.py`. Each
channel is a small module implementing one protocol.

**`db/`** holds every SQL statement in `repo.py`, so a schema change has one place to look.

## Three decisions worth explaining

### Anonymous only

Vinted hands a session cookie to anyone who loads the site, and that cookie is all the
catalog needs. Logging in would unlock more, including buying — and would mean storing
someone's credentials, and would put a real account at risk of the restrictions Vinted has
been applying to suspected automation. Reading public pages as a logged-out visitor is both
safer and simpler, and it makes "we cannot buy for you" an honest statement rather than a
missing feature.

### Errors are not interchangeable

The single most common way tools in this space dig their own grave is retrying uniformly.
Each failure means something different:

| What happened | What it means | What we do |
|---|---|---|
| 401 | The anonymous token aged out | Get a new one and retry, quickly |
| 403 | This client is being refused | Back off hard, start a new session |
| 429 | Too fast | Wait exactly as long as we were told |
| 200 with the wrong shape | Something changed, or we got an interstitial | Log it loudly, do not retry, do not notify |
| Network error | Transient | Short backoff |

Retrying a 403 the way you would retry a timeout is how a temporary block becomes a long one.

### Notifications are written before they are sent

Finding a listing and queueing its notifications happens in one transaction. Nothing is
marked as sent until the far end has accepted it, and a process killed mid-send returns its
claimed rows to the queue at startup. The alternative — send as you go — loses alerts on a
crash or sends them twice, and both happen often enough to matter on a machine that reboots.

This is also where rate limits are respected. One worker per destination, sending one thing
at a time, in the order things were found. It is slower than firing everything at once, which
is the point: platforms remember who floods them.

## Deciding what is new

Three gates, because each covers a hole the others leave:

1. **A freshness window.** Anything whose photo is older than twenty minutes is ignored, so a
   restart or a slow first check cannot replay yesterday.
2. **A high-water mark per search.** The newest listing already announced. Results drift
   around in Vinted's ordering; this stops the same thing arriving twice.
3. **The set of listing ids already recorded.** Two overlapping searches will both see the
   same listing. You should hear about it once.

A search's first check is a special case: it records everything it finds and tells you about
none of it, unless you set `FIRST_RUN_MODE=newest`, which sends exactly one so you can confirm
delivery works.

## Testing

`tests/unit` covers the decisions — URL normalisation, filters, dedup, message building,
pacing. `tests/integration` runs the real poller, dispatcher and database against a scripted
transport, and covers the cases that decide whether this survives a week alone: empty
results, 403, 429, an expired token, a response that is not a catalog, a crash mid-delivery,
and a restart not resending anything.

Nothing in the suite reaches the network. A separate weekly job makes one real request
against the live site, so if Vinted changes something we hear about it from CI rather than
from an issue.

Run it offline the way the tests do:

```bash
VINTED_SNIPER_FETCH_MODE=mock VINTED_SNIPER_MOCK_SCENARIO_DIR=./scenario uv run vinted-sniper run
```

A scenario directory holds `root.json` and `catalog.json` describing responses. Either can be
a list, in which case successive calls walk through it — which is how the failure drills work.
