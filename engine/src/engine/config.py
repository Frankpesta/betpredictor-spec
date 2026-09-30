"""Configuration: `config/settings.toml` (+ optional `.env` overrides), strictly validated.

Every tunable number lives in settings.toml. Unknown keys anywhere are an error.
Env overrides use the prefix ``BP_`` and ``__`` as nested delimiter, e.g.
``BP_SCRAPING__HEADLESS=false``. Precedence: env > .env > settings.toml.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


def _find_root() -> Path:
    env_root = os.environ.get("BETPREDICTOR_ROOT")
    if env_root:
        return Path(env_root).resolve()
    # engine/src/engine/config.py -> repo root is three levels above the package dir
    return Path(__file__).resolve().parents[3]


ROOT: Path = _find_root()
SETTINGS_PATH: Path = ROOT / "config" / "settings.toml"
LOGS_DIR: Path = ROOT / "data" / "logs"


@dataclass(frozen=True)
class LeagueDef:
    key: str
    name: str
    fd_code: str | None  # football-data.co.uk division; None for INTL
    understat_key: str | None
    # SportyBet (Sportradar) tournament ids — docs/discovered/sportybet/2026-09-27/endpoints.md F2
    sb_tournaments: tuple[str, ...] = ()
    international: bool = False


# docs/01 §2 + docs/09 — the only competitions the system knows about.
LEAGUES: dict[str, LeagueDef] = {
    d.key: d
    for d in (
        LeagueDef("EPL", "Premier League", "E0", "EPL", ("sr:tournament:17",)),
        LeagueDef("LALIGA", "La Liga", "SP1", "La_liga", ("sr:tournament:8",)),
        LeagueDef("SERIEA", "Serie A", "I1", "Serie_A", ("sr:tournament:23",)),
        LeagueDef("BUNDES", "Bundesliga", "D1", "Bundesliga", ("sr:tournament:35",)),
        LeagueDef("LIGUE1", "Ligue 1", "F1", "Ligue_1", ("sr:tournament:34",)),
        LeagueDef("CHAMP", "Championship", "E1", None),
        LeagueDef("ERED", "Eredivisie", "N1", None),
        # Men's A internationals (docs/09). SportyBet category sr:category:4; women's,
        # youth and outright-only tournaments are deliberately not listed.
        LeagueDef(
            "INTL",
            "International (men's A)",
            None,
            None,
            (
                "sr:tournament:23755",  # UEFA Nations League
                "sr:tournament:1848",  # Africa Cup of Nations Qualification
                "sr:tournament:851",  # Int. Friendly Games
                "sr:tournament:27420",  # CONCACAF Nations League
                "sr:tournament:622",  # Gulf Cup
                "sr:tournament:53324",  # FIFA ASEAN Cup
            ),
            international=True,
        ),
    )
}

_SEASON_RE = re.compile(r"^(\d{4})-(\d{2})$")
_WEEKEND_RE = re.compile(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([01]\d|2[0-3]):([0-5]\d)$")


def _check_season(s: str) -> str:
    m = _SEASON_RE.match(s)
    if not m or (int(m.group(1)) + 1) % 100 != int(m.group(2)):
        raise ValueError(f"invalid season {s!r}; expected e.g. '2024-25'")
    return s


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GeneralCfg(_Strict):
    timezone: str
    db_path: str
    raw_dir: str
    reports_dir: str
    horizon_hours: int = Field(gt=0)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as e:
            raise ValueError(f"unknown timezone {v!r}") from e
        return v


class LeaguesCfg(_Strict):
    enabled: list[str]
    history_seasons: list[str]

    @field_validator("enabled")
    @classmethod
    def _known(cls, v: list[str]) -> list[str]:
        unknown = [k for k in v if k not in LEAGUES]
        if unknown:
            raise ValueError(f"unknown league keys: {unknown}")
        return v

    @field_validator("history_seasons")
    @classmethod
    def _seasons(cls, v: list[str]) -> list[str]:
        return [_check_season(s) for s in v]


class ScrapingCfg(_Strict):
    min_delay_seconds: float = Field(gt=0)
    max_delay_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=0)
    user_agent: str
    sportybet_country: str
    headless: bool

    @model_validator(mode="after")
    def _delays(self) -> ScrapingCfg:
        if self.min_delay_seconds > self.max_delay_seconds:
            raise ValueError("min_delay_seconds must be <= max_delay_seconds")
        return self


class ModelCfg(_Strict):
    version: str
    train_seasons_back: int = Field(ge=1)
    xi_per_day: float = Field(ge=0)
    ridge_lambda: float = Field(ge=0)
    max_goals: int = Field(ge=1)
    xg_blend_weight: float = Field(ge=0, le=1)
    rho_bounds: tuple[float, float]
    min_team_matches: int = Field(ge=0)

    @field_validator("rho_bounds")
    @classmethod
    def _rho(cls, v: tuple[float, float]) -> tuple[float, float]:
        if v[0] >= v[1]:
            raise ValueError("rho_bounds must be [low, high] with low < high")
        return v


class ValueCfg(_Strict):
    selection: Literal["likeliest", "value"]
    market_shrink_weight: float = Field(ge=0, le=1)
    min_edge_leg: float
    max_model_market_gap: float = Field(gt=0, le=1)
    min_odds: float = Field(gt=1)
    max_odds: float = Field(gt=1)
    allowed_lines: Literal["half_only"]

    @model_validator(mode="after")
    def _odds(self) -> ValueCfg:
        if self.min_odds >= self.max_odds:
            raise ValueError("min_odds must be < max_odds")
        return self


class Daily2OddsCfg(_Strict):
    enabled: bool
    min_legs: int = Field(ge=1)
    max_legs: int = Field(ge=1)
    target_odds_min: float = Field(gt=1)
    target_odds_max: float = Field(gt=1)
    min_leg_probability: float = Field(ge=0, le=1)
    min_slip_edge: float
    window_hours: int = Field(gt=0)

    @model_validator(mode="after")
    def _ranges(self) -> Daily2OddsCfg:
        if self.min_legs > self.max_legs:
            raise ValueError("min_legs must be <= max_legs")
        if self.target_odds_min > self.target_odds_max:
            raise ValueError("target_odds_min must be <= target_odds_max")
        return self


class MidAccaCfg(_Strict):
    enabled: bool
    min_legs: int = Field(ge=1)
    max_legs: int = Field(ge=1)
    min_leg_probability: float = Field(ge=0, le=1)
    min_slip_edge: float

    @model_validator(mode="after")
    def _ranges(self) -> MidAccaCfg:
        if self.min_legs > self.max_legs:
            raise ValueError("min_legs must be <= max_legs")
        return self


class MegaAccaCfg(_Strict):
    enabled: bool
    max_legs: int = Field(ge=2)
    min_leg_probability: float = Field(ge=0, le=1)
    min_leg_edge: float
    weekend_start: str
    weekend_end: str

    @field_validator("weekend_start", "weekend_end")
    @classmethod
    def _weekday_time(cls, v: str) -> str:
        if not _WEEKEND_RE.match(v):
            raise ValueError(f"expected 'Ddd HH:MM' (e.g. 'Fri 18:00'), got {v!r}")
        return v


class SlipsCfg(_Strict):
    daily_2odds: Daily2OddsCfg
    mid_acca: MidAccaCfg
    mega_acca: MegaAccaCfg


class BacktestCfg(_Strict):
    test_seasons: list[str] = Field(min_length=2)
    refit_every_days: int = Field(gt=0)
    odds_source: str
    closing_source: str
    closing_fallback: str
    tuning_refit_every_days: int = Field(gt=0)
    grid_xi_per_day: list[float] = Field(min_length=1)
    grid_xg_blend_weight: list[float] = Field(min_length=1)
    grid_market_shrink_weight: list[float] = Field(min_length=1)
    grid_min_edge_leg: list[float] = Field(min_length=1)

    @field_validator("test_seasons")
    @classmethod
    def _seasons(cls, v: list[str]) -> list[str]:
        return [_check_season(s) for s in v]


class GatesCfg(_Strict):
    max_calibration_ece: float = Field(gt=0)
    min_bets_for_roi: int = Field(ge=1)
    min_roi: float
    require_beats_market_logloss: bool


class BookingCfg(_Strict):
    max_odds_drift: float = Field(gt=0, lt=1)


class ApiCfg(_Strict):
    host: Literal["127.0.0.1"]  # local-only; never bind elsewhere
    port: int = Field(gt=0, lt=65536)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BP_",
        env_nested_delimiter="__",
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="forbid",
        frozen=True,
    )

    general: GeneralCfg
    leagues: LeaguesCfg
    scraping: ScrapingCfg
    model: ModelCfg
    value: ValueCfg
    slips: SlipsCfg
    backtest: BacktestCfg
    gates: GatesCfg
    booking: BookingCfg
    api: ApiCfg

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # TOML data is passed as init kwargs; give env/.env precedence over it.
        return (env_settings, dotenv_settings, init_settings)

    @model_validator(mode="after")
    def _cross(self) -> Settings:
        missing = [s for s in self.backtest.test_seasons if s not in self.leagues.history_seasons]
        if missing:
            raise ValueError(f"backtest.test_seasons not in leagues.history_seasons: {missing}")
        return self

    # ---- resolved paths (relative paths are relative to the repo root) ----
    def resolve(self, p: str) -> Path:
        path = Path(p)
        return path if path.is_absolute() else ROOT / path

    @property
    def db_file(self) -> Path:
        return self.resolve(self.general.db_path)

    @property
    def raw_path(self) -> Path:
        return self.resolve(self.general.raw_dir)

    @property
    def reports_path(self) -> Path:
        return self.resolve(self.general.reports_dir)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.general.timezone)

    def enabled_leagues(self) -> list[LeagueDef]:
        return [LEAGUES[k] for k in self.leagues.enabled]


def load_settings(path: Path | None = None) -> Settings:
    """Load and validate settings from a TOML file (default: config/settings.toml)."""
    path = path or SETTINGS_PATH
    with path.open("rb") as f:
        data: dict[str, Any] = tomllib.load(f)
    return Settings(**data)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


__all__ = [
    "LEAGUES",
    "LOGS_DIR",
    "ROOT",
    "SETTINGS_PATH",
    "LeagueDef",
    "Settings",
    "get_settings",
    "load_settings",
]
