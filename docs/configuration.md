# Configuration

Two kinds of settings, kept apart deliberately.

**Environment variables** control the process: where the database is, how often to check,
how to log. They are read once at startup and are listed below.

**Searches and destinations** live in the database and are managed with the CLI or the
dashboard. There is no config file listing them, so there is nothing to keep in sync and no
question about which copy wins.

## Environment variables

Every variable is prefixed `VINTED_SNIPER_`. All of them are optional except where noted.
[`.env.example`](../.env.example) has the same list with comments, ready to copy.

### Storage

| Variable | Default | What it does |
|---|---|---|
| `DB_PATH` | `./data/app.db` | Where the SQLite file lives. Already set to `/data/app.db` in the container. |
| `ITEM_RETENTION_DAYS` | `30` | Delete stored listings older than this. Does not cause anything to be re-sent. |
| `KEEP_RAW_JSON` | `false` | Keep each listing's full API payload. Handy when debugging a parsing problem; stores more seller data than notifications need. |

### Checking

| Variable | Default | What it does |
|---|---|---|
| `POLL_DEFAULT_INTERVAL_S` | `60` | Seconds between checks for a newly added search. Per-search values override it. Anything under 10 is refused. |
| `FRESHNESS_WINDOW_MIN` | `20` | Ignore listings whose photo is older than this. Stops a restart from replaying old results. |
| `FIRST_RUN_MODE` | `silent` | What a brand-new search does first time: `silent` notifies nothing, `newest` sends exactly one listing so you can confirm delivery works. |
| `REQUEST_TIMEOUT_S` | `15` | How long to wait for Vinted before giving up on one request. |

### Staying unblocked

| Variable | Default | What it does |
|---|---|---|
| `SESSION_ROTATE_MINUTES` | `60` | Start a fresh anonymous session after this long. Blocks track session age more than request rate. |
| `HTTP_IMPERSONATE` | `false` | Make requests present a real browser's TLS fingerprint. Needs the `impersonate` extra. Only worth turning on if you are being blocked while the same search loads fine in a browser. |
| `PROXY_FILE` | unset | Path to a text file of proxy URLs, one per line. Rarely needed. |

### Noticing problems

| Variable | Default | What it does |
|---|---|---|
| `WATCHDOG_STALE_CYCLES` | `10` | Checks with no new listing before a search is treated as stuck — but only if other searches on the same site are still finding things. |
| `WATCHDOG_ACTION` | `rotate` | `warn` logs it; `rotate` also starts a fresh session. |
| `OUTBOX_EXPIRY_MINUTES` | `60` | Discard notifications that could not be delivered within this window. |

### Telegram

| Variable | Default | What it does |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | unset | From [@BotFather](https://t.me/BotFather). Enables Telegram delivery and the pairing bot. |

### Dashboard

| Variable | Default | What it does |
|---|---|---|
| `WEB_ENABLED` | `false` | Turn the dashboard on. |
| `WEB_AUTH_TOKEN` | unset | **Required when the dashboard is on.** The app refuses to start without it. Generate with `openssl rand -hex 32`. |
| `WEB_HOST` | `127.0.0.1` | Loopback by default. Only widen behind a reverse proxy you trust. |
| `WEB_PORT` | `8000` | |

### Logging and development

| Variable | Default | What it does |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `LOG_FORMAT` | `console` | `json` when something else is collecting the logs. |
| `FETCH_MODE` | `live` | `mock` replays recorded responses from disk instead of calling Vinted. |
| `MOCK_SCENARIO_DIR` | unset | Required when `FETCH_MODE=mock`. |

## Commands

```
vinted-sniper run                      start watching (what the container runs)
vinted-sniper check --url <url>        fetch one search once and print the result
vinted-sniper watch <url> [options]    add a search
vinted-sniper searches                 list searches
vinted-sniper unwatch <id>             remove one
vinted-sniper destination <kind> <target>   add somewhere to send
vinted-sniper destinations             list them
vinted-sniper pair-telegram            print a link that connects a Telegram chat
vinted-sniper status                   how each search is doing
vinted-sniper migrate                  create or update the database, then exit
vinted-sniper heartbeat                exit 0 if the app is alive (the health check)
```

Options for `watch`:

| Option | Meaning |
|---|---|
| `--name` | What to call it. Defaults to the search text. |
| `--every N` | Seconds between checks for this search. |
| `--max-price N` | Skip anything above this **including buyer protection**. |
| `--exclude a,b,c` | Skip listings whose title contains any of these words. |
| `--to 1,2` | Destination ids to notify. Defaults to all active ones. |

## Destination settings

Stored per destination. The dashboard and `vinted-sniper destination` fill these in for you.

| Kind | Fields |
|---|---|
| `discord` | `webhook_url` |
| `telegram` | `chat_id`, optionally `message_thread_id` for a forum topic |
| `ntfy` | `topic`, optionally `server` and `token` |
| `webhook` | `url`, optionally `headers` |

## The webhook payload

A plain webhook destination receives a POST like this. The shape is treated as a contract:
it changes only with a version bump, because other people's automations depend on it.

```json
{
  "version": 1,
  "search": "nike air max",
  "items": [
    {
      "id": 9683334896,
      "site": "vinted.fr",
      "title": "Nike Air Max 90",
      "url": "https://www.vinted.fr/items/9683334896-nike-air-max-90",
      "brand": "Nike",
      "size": "42",
      "condition": "Very good",
      "price": "15.00",
      "total_price": "16.45",
      "currency": "EUR",
      "photo_url": "https://images.vinted.net/...",
      "listed_at": "2026-08-16T20:14:00+00:00",
      "seller": "someone",
      "seller_rating": 0.93,
      "links": {
        "message_seller": "https://www.vinted.fr/items/9683334896/want_it/new",
        "buy": "https://www.vinted.fr/transaction/buy/new?..."
      }
    }
  ]
}
```

`price` is what the seller asks. `total_price` is what you pay. Filters and displays use the
second one.

## RSS

Each search has a feed at `/rss/<search id>.xml`. Feed readers cannot sign in, so the token
goes in the URL:

```
http://localhost:8000/rss/1.xml?key=<your web auth token>
```

Anyone with that URL can read the feed, so treat it like a password.
