from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from pydantic import ValidationError

from engine.config import SETTINGS_PATH, load_settings


def _write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "settings.toml"
    p.write_text(text, encoding="utf-8")
    return p


def _base_text() -> str:
    return SETTINGS_PATH.read_text(encoding="utf-8")


def test_real_settings_load() -> None:
    s = load_settings()
    assert s.general.timezone == "Africa/Lagos"
    assert s.leagues.enabled == ["EPL", "LALIGA", "SERIEA", "BUNDES", "LIGUE1", "INTL"]  # docs/09
    assert s.model.rho_bounds == (-0.2, 0.2)
    assert s.slips.daily_2odds.target_odds_max == 2.30
    assert s.slips.mega_acca.weekend_start == "Fri 18:00"
    assert s.api.host == "127.0.0.1"
    assert s.db_file.name == "betpredictor.db"


def test_settings_file_has_exactly_the_documented_sections() -> None:
    data = tomllib.loads(_base_text())
    assert set(data) == {
        "general",
        "leagues",
        "scraping",
        "model",
        "value",
        "slips",
        "half_time",  # docs/10 §2
        "backtest",
        "gates",
        "booking",  # docs/04 §3.4 drift threshold (docs/discovered/sportybet/2026-09-27/booking.md)
        "api",
    }
    assert set(data["slips"]) == {"daily_2odds", "mid_acca", "mega_acca"}


def test_unknown_top_level_key_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, _base_text() + "\n[surprise]\nx = 1\n")
    with pytest.raises(ValidationError, match="surprise"):
        load_settings(p)


def test_unknown_nested_key_rejected(tmp_path: Path) -> None:
    text = _base_text().replace("[model]\n", "[model]\nmystery_knob = 3\n")
    with pytest.raises(ValidationError, match="mystery_knob"):
        load_settings(_write(tmp_path, text))


def test_missing_key_rejected(tmp_path: Path) -> None:
    text = _base_text().replace('version = "dc-1.0.0"\n', "")
    with pytest.raises(ValidationError, match="version"):
        load_settings(_write(tmp_path, text))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('weekend_start = "Fri 18:00"', 'weekend_start = "Friday 6pm"'),
        ('enabled = ["EPL",', 'enabled = ["UCL", "EPL",'),
        ("rho_bounds = [-0.2, 0.2]", "rho_bounds = [0.2, -0.2]"),
        ('host = "127.0.0.1"', 'host = "0.0.0.0"'),
        ('timezone = "Africa/Lagos"', 'timezone = "Mars/Olympus"'),
    ],
)
def test_invalid_values_rejected(tmp_path: Path, old: str, new: str) -> None:
    text = _base_text()
    assert old in text
    with pytest.raises(ValidationError):
        load_settings(_write(tmp_path, text.replace(old, new, 1)))


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BP_SCRAPING__HEADLESS", "false")
    assert load_settings().scraping.headless is False
