"""Documentation that drifts is worse than none, so a few claims are checked here."""

from __future__ import annotations

from pathlib import Path

import pytest

from vinted_sniper.cli import build_parser
from vinted_sniper.config import Settings

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DOC = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("field", sorted(Settings.model_fields))
def test_every_setting_is_documented(field: str) -> None:
    assert field.upper() in CONFIG_DOC, (
        f"{field} exists in Settings but is missing from docs/configuration.md"
    )


@pytest.mark.parametrize("field", sorted(Settings.model_fields))
def test_every_setting_appears_in_the_example_env(field: str) -> None:
    assert f"VINTED_SNIPER_{field.upper()}" in ENV_EXAMPLE, (
        f"{field} exists in Settings but is missing from .env.example"
    )


def test_documented_settings_all_exist() -> None:
    """The opposite direction: nothing documented that was removed from the code."""
    known = {f"VINTED_SNIPER_{name.upper()}" for name in Settings.model_fields}
    mentioned = {
        line.split("=")[0].removeprefix("# ").strip()
        for line in ENV_EXAMPLE.splitlines()
        if "VINTED_SNIPER_" in line and "=" in line
    }

    assert mentioned <= known, f"documented but gone from the code: {sorted(mentioned - known)}"


@pytest.mark.parametrize(
    "command",
    ["run", "check", "watch", "searches", "unwatch", "destination", "status", "heartbeat"],
)
def test_commands_named_in_the_docs_exist(command: str) -> None:
    parser = build_parser()
    known = {
        choice
        for action in parser._actions
        for choice in (action.choices or {})
        if isinstance(action.choices, dict)
    }

    assert command in known


def test_the_readme_does_not_promise_buying() -> None:
    """A deliberate non-feature. If this ever fails, the claim and the code disagree."""
    lowered = README.lower()

    assert "cannot buy" in lowered or "does not log into your" in lowered
