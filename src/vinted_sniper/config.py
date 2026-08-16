"""Process settings, read once from the environment at startup.

Everything a *user* owns — searches, destinations, routing, per-search filters — lives in
SQLite instead, managed through the CLI or web UI. Keeping those two apart means there is
never a question of which copy of a setting wins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Not configurable. Polling faster than this earns 403s without finding items any sooner,
# because Vinted's own catalog lags behind uploads by far more than a few seconds.
MIN_POLL_INTERVAL_S = 10

# Ceiling for the 403 backoff ladder.
MAX_BACKOFF_S = 900


class Settings(BaseSettings):
    """Static configuration. Every field is documented in docs/configuration.md."""

    model_config = SettingsConfigDict(
        env_prefix="VINTED_SNIPER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

    # --- Storage -------------------------------------------------------------------
    db_path: Path = Field(
        default=Path("./data/app.db"),
        description="Path to the SQLite database file. Its parent directory is created on start.",
    )
    item_retention_days: int = Field(
        default=30,
        ge=1,
        description="Delete stored items older than this. Does not cause re-notification.",
    )
    keep_raw_json: bool = Field(
        default=False,
        description="Store each item's raw API payload. Useful for debugging schema drift, "
        "but it keeps more seller data on disk than notifications need.",
    )

    # --- Logging -------------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["console", "json"] = "console"

    # --- Vinted transport ----------------------------------------------------------
    http_impersonate: bool = Field(
        default=False,
        description="Route Vinted requests through curl_cffi with a browser TLS fingerprint. "
        "Requires the 'impersonate' extra. Only needed if plain requests start getting 403s.",
    )
    proxy_file: Path | None = Field(
        default=None,
        description="Optional text file of proxy URLs, one per line. Off by default.",
    )
    session_rotate_minutes: int = Field(
        default=60,
        ge=1,
        description="Discard and re-bootstrap a site session once it reaches this age. "
        "Blocks correlate with session age more than with request rate.",
    )
    request_timeout_s: float = Field(default=15.0, gt=0)

    # --- Polling -------------------------------------------------------------------
    poll_default_interval_s: int = Field(
        default=60,
        ge=MIN_POLL_INTERVAL_S,
        description="Default seconds between checks for a new search. Per-search overrides "
        "live in the database.",
    )
    freshness_window_min: int = Field(
        default=20,
        ge=1,
        description="Ignore listings whose photo timestamp is older than this. Stops a restart "
        "or a slow first poll from replaying yesterday's catalog.",
    )
    first_run_mode: Literal["silent", "newest"] = Field(
        default="silent",
        description="What a brand-new search does on its first check: 'silent' notifies "
        "nothing, 'newest' sends exactly one item so you can confirm delivery works.",
    )

    # --- Watchdog ------------------------------------------------------------------
    watchdog_stale_cycles: int = Field(
        default=10,
        ge=2,
        description="Consecutive checks with no newer listing before a search is called stale.",
    )
    watchdog_action: Literal["warn", "rotate"] = Field(
        default="rotate",
        description="What to do about a stale search: log a warning, or also force a new session.",
    )

    # --- Delivery ------------------------------------------------------------------
    outbox_expiry_minutes: int = Field(
        default=60,
        ge=1,
        description="Give up on an undelivered notification after this long. A two-hour-old "
        "listing alert is not worth sending.",
    )
    telegram_bot_token: SecretStr | None = Field(
        default=None,
        description="Enables Telegram delivery and the /start binding bot when set.",
    )

    # --- Development ---------------------------------------------------------------
    fetch_mode: Literal["live", "mock"] = Field(
        default="live",
        description="'mock' replays recorded responses from disk instead of calling Vinted.",
    )
    mock_scenario_dir: Path | None = None

    # --- Web UI --------------------------------------------------------------------
    web_enabled: bool = False
    web_host: str = Field(
        default="127.0.0.1",
        description="Loopback by default. Only widen this behind a reverse proxy you trust.",
    )
    web_port: int = Field(default=8000, ge=1, le=65535)
    web_auth_token: SecretStr | None = Field(
        default=None,
        description="Required when the web UI is enabled. The app refuses to start without it.",
    )

    @model_validator(mode="after")
    def _check_coherent(self) -> Settings:
        if self.web_enabled and self.web_auth_token is None:
            raise ValueError(
                "VINTED_SNIPER_WEB_ENABLED is true but VINTED_SNIPER_WEB_AUTH_TOKEN is not set. "
                "The web UI exposes your searches and destinations, so it will not start "
                "unauthenticated. Generate one with: openssl rand -hex 32"
            )
        if self.fetch_mode == "mock" and self.mock_scenario_dir is None:
            raise ValueError(
                "VINTED_SNIPER_FETCH_MODE is 'mock' but VINTED_SNIPER_MOCK_SCENARIO_DIR is not set."
            )
        return self
