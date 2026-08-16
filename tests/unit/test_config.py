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


def test_web_ui_refuses_to_run_without_a_token() -> None:
    with pytest.raises(ValidationError, match="WEB_AUTH_TOKEN"):
        Settings(_env_file=None, web_enabled=True)  # type: ignore[call-arg]


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
