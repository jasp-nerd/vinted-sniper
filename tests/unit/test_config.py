from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from vinted_sniper.config import MIN_POLL_INTERVAL_S, Settings


def test_defaults_are_conservative() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.poll_default_interval_s >= MIN_POLL_INTERVAL_S
    assert settings.first_run_mode == "silent", "a new search must not replay the catalog"
    assert settings.keep_raw_json is False, "storing seller payloads should be opt-in"
    assert settings.web_host == "127.0.0.1", "the web UI must not listen publicly by default"
    assert settings.web_is_loopback


def test_no_password_is_allowed_on_localhost() -> None:
    """Anyone who can reach localhost is already on the machine."""
    settings = Settings(_env_file=None, web_enabled=True)  # type: ignore[call-arg]

    assert settings.web_auth_token is None
    assert settings.web_is_exposed_without_a_password is False


def test_a_passwordless_dashboard_on_a_public_address_is_flagged() -> None:
    """Not fatal — a container always binds 0.0.0.0 — but it must be visible in the logs."""
    settings = Settings(_env_file=None, web_enabled=True, web_host="0.0.0.0")  # type: ignore[call-arg]

    assert settings.web_is_exposed_without_a_password is True


def test_a_token_clears_the_exposure_warning() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, web_enabled=True, web_host="0.0.0.0", web_auth_token=SecretStr("s")
    )

    assert settings.web_is_exposed_without_a_password is False


def test_web_ui_starts_when_token_is_supplied() -> None:
    settings = Settings(_env_file=None, web_enabled=True, web_auth_token=SecretStr("s3cret"))  # type: ignore[call-arg]

    assert settings.web_enabled is True
    assert settings.web_auth_token is not None
    assert "s3cret" not in repr(settings), "secrets must not leak through repr"


def test_mock_mode_requires_a_scenario_directory() -> None:
    with pytest.raises(ValidationError, match="MOCK_SCENARIO_DIR"):
        Settings(_env_file=None, fetch_mode="mock")  # type: ignore[call-arg]


def test_poll_interval_below_floor_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, poll_default_interval_s=1)  # type: ignore[call-arg]


def test_unknown_environment_keys_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, wat_is_this=True)  # type: ignore[call-arg]
