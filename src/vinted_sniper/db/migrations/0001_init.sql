-- Initial schema.
-- All timestamps are integer epoch seconds, UTC.

CREATE TABLE queries (
    id                   INTEGER PRIMARY KEY,
    name                 TEXT    NOT NULL,
    -- Normalised search URL. Normalising before storing is what makes this a usable
    -- uniqueness key: two pastes of the same search differing only in tracking
    -- parameters collapse to one row.
    url                  TEXT    NOT NULL UNIQUE,
    tld                  TEXT    NOT NULL,
    params_json          TEXT    NOT NULL,
    poll_interval_s      INTEGER NOT NULL DEFAULT 60 CHECK (poll_interval_s >= 10),
    paused               INTEGER NOT NULL DEFAULT 0 CHECK (paused IN (0, 1)),
    banned_keywords_json TEXT    NOT NULL DEFAULT '[]',
    max_total_price      REAL,
    conditions_json      TEXT,
    countries_json       TEXT,
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL
);

-- Runtime state lives apart from the definition so that editing a search never
-- disturbs its high-water mark, and so the health view is one cheap read.
CREATE TABLE query_state (
    query_id        INTEGER PRIMARY KEY REFERENCES queries(id) ON DELETE CASCADE,
    last_polled_at  INTEGER,
    last_success_at INTEGER,
    last_status     TEXT,
    last_error      TEXT,
    -- Photo timestamp of the newest listing we have actually notified about.
    -- NULL means the search has never completed a check, which is what triggers
    -- first-run seeding.
    newest_item_ts  INTEGER,
    -- Newest photo timestamp seen in the raw response, notified or not. The watchdog
    -- compares this across searches to tell a frozen catalog from a quiet one.
    newest_raw_ts   INTEGER,
    stale_cycles    INTEGER NOT NULL DEFAULT 0,
    items_seen_total INTEGER NOT NULL DEFAULT 0,
    count_403       INTEGER NOT NULL DEFAULT 0,
    count_429       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE destinations (
    id                 INTEGER PRIMARY KEY,
    kind               TEXT    NOT NULL CHECK (kind IN ('discord', 'telegram', 'webhook', 'ntfy')),
    name               TEXT    NOT NULL,
    config_json        TEXT    NOT NULL,
    active             INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    -- Health warnings and startup notices go to destinations flagged this way.
    notify_status      INTEGER NOT NULL DEFAULT 0 CHECK (notify_status IN (0, 1)),
    failure_count      INTEGER NOT NULL DEFAULT 0,
    deactivated_reason TEXT,
    created_at         INTEGER NOT NULL
);

-- Routing is data, not configuration: any search can feed any set of destinations.
CREATE TABLE query_destinations (
    query_id       INTEGER NOT NULL REFERENCES queries(id) ON DELETE CASCADE,
    destination_id INTEGER NOT NULL REFERENCES destinations(id) ON DELETE CASCADE,
    PRIMARY KEY (query_id, destination_id)
);

CREATE TABLE items (
    item_id       INTEGER PRIMARY KEY,
    query_id      INTEGER,
    tld           TEXT    NOT NULL,
    title         TEXT,
    brand         TEXT,
    size          TEXT,
    condition     TEXT,
    price         REAL,
    -- What the buyer actually pays, buyer protection included. This is the number
    -- filters and notifications lead with.
    total_price   REAL,
    currency      TEXT,
    url           TEXT    NOT NULL,
    photo_url     TEXT,
    photo_ts      INTEGER,
    seller_login  TEXT,
    seller_rating REAL,
    raw_json      TEXT,
    first_seen_at INTEGER NOT NULL
);

CREATE INDEX idx_items_first_seen ON items(first_seen_at);

-- Notifications are written here in the same transaction that records the item, so a
-- crash between "found it" and "sent it" cannot lose an alert or send it twice.
CREATE TABLE outbox (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id          INTEGER NOT NULL,
    query_id         INTEGER NOT NULL,
    destination_id   INTEGER NOT NULL REFERENCES destinations(id) ON DELETE CASCADE,
    status           TEXT    NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'sending', 'sent', 'failed', 'cancelled')),
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_attempt_at  INTEGER NOT NULL,
    -- Set while a worker holds the row. On startup, expired leases are returned to
    -- 'pending' so a killed process does not strand notifications.
    lease_expires_at INTEGER,
    last_error       TEXT,
    created_at       INTEGER NOT NULL,
    sent_at          INTEGER,
    UNIQUE (item_id, destination_id)
);

CREATE INDEX idx_outbox_claim ON outbox(destination_id, status, next_attempt_at);

CREATE TABLE sessions (
    tld           TEXT    PRIMARY KEY,
    cookies_json  TEXT    NOT NULL,
    -- Pinned for the session's lifetime. A cookie jar minted under one user agent and
    -- replayed under another is itself a giveaway.
    user_agent    TEXT    NOT NULL,
    created_at    INTEGER NOT NULL,
    last_used_at  INTEGER,
    request_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
