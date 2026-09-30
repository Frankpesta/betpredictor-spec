"""ORM models for every table in docs/01 §3.

Alembic migrations are the only thing that creates/alters tables; these models
must stay in sync with them (tests/test_migrations.py enforces it).
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import REAL, Date, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from engine.db.base import Base, IdTimestampMixin, IntBool, bool_check, enum_check

# ---- enum value sets (single source of truth for CHECK constraints) ----
ALIAS_SOURCES = ("football_data", "understat", "sportybet")
MATCH_STATUSES = ("scheduled", "finished", "postponed", "cancelled", "abandoned", "needs_review")
ODDS_TIMINGS = ("open", "close")
HIST_MARKETS = ("OU", "AH", "1X2")
HIST_SELECTIONS = ("home", "draw", "away", "over", "under")
SNAPSHOT_KINDS = ("pick", "close")
MARKETS = ("OU", "AH")
SELECTIONS = ("over", "under", "home", "away")
SANITY_STATUSES = ("ok", "flagged")
SLIP_TYPES = ("daily_2odds", "mid_acca", "mega_acca")
SLIP_POOLS = ("club", "intl")  # docs/09 §5: INTL legs only in INTL-only slips
SLIP_STRATEGIES = ("value", "likeliest")  # docs/05 §8: which selection rule built the slip
BOOKING_STATUSES = ("pending", "booked", "failed", "manual")
SLIP_MODES = ("paper", "placed")
SLIP_STATUSES = ("open", "won", "lost", "void", "partial")
LEG_RESULTS = ("pending", "win", "half_win", "push", "half_loss", "loss", "void")
JOB_STATUSES = ("running", "success", "failed")


def _fk(target: str) -> ForeignKey:
    return ForeignKey(target, ondelete="RESTRICT")


class League(IdTimestampMixin, Base):
    __tablename__ = "leagues"
    __table_args__ = (bool_check("enabled"),)

    key: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    fd_code: Mapped[str | None] = mapped_column(Text)  # NULL for INTL (docs/09)
    understat_key: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(IntBool, default=False, server_default="0")


class Team(IdTimestampMixin, Base):
    __tablename__ = "teams"

    league_id: Mapped[int] = mapped_column(_fk("leagues.id"), index=True)
    canonical_name: Mapped[str] = mapped_column(Text, unique=True)

    league: Mapped[League] = relationship()


class TeamAlias(IdTimestampMixin, Base):
    __tablename__ = "team_aliases"
    __table_args__ = (
        UniqueConstraint("source", "alias"),
        enum_check("source", ALIAS_SOURCES),
    )

    team_id: Mapped[int] = mapped_column(_fk("teams.id"), index=True)
    source: Mapped[str] = mapped_column(Text)
    alias: Mapped[str] = mapped_column(Text)


class Match(IdTimestampMixin, Base):
    __tablename__ = "matches"
    __table_args__ = (
        # Clubs meet once at home per season (fixture_key ''); national teams can meet
        # more often, so INTL rows set fixture_key = match date (docs/09 §3a).
        UniqueConstraint("league_id", "home_team_id", "away_team_id", "season", "fixture_key"),
        enum_check("status", MATCH_STATUSES),
        bool_check("neutral"),
    )

    league_id: Mapped[int] = mapped_column(_fk("leagues.id"), index=True)
    season: Mapped[str] = mapped_column(Text)
    kickoff_utc: Mapped[datetime] = mapped_column(index=True)
    home_team_id: Mapped[int] = mapped_column(_fk("teams.id"), index=True)
    away_team_id: Mapped[int] = mapped_column(_fk("teams.id"), index=True)
    home_goals: Mapped[int | None] = mapped_column(Integer)
    away_goals: Mapped[int | None] = mapped_column(Integer)
    home_xg: Mapped[float | None] = mapped_column(REAL)
    away_xg: Mapped[float | None] = mapped_column(REAL)
    status: Mapped[str] = mapped_column(Text, default="scheduled", server_default="scheduled")
    fd_row_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    neutral: Mapped[bool] = mapped_column(IntBool, default=False, server_default="0")
    fixture_key: Mapped[str] = mapped_column(Text, default="", server_default="")
    sportybet_event_id: Mapped[str | None] = mapped_column(Text, unique=True)

    league: Mapped[League] = relationship()
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])


class HistoricalOdds(IdTimestampMixin, Base):
    __tablename__ = "historical_odds"
    __table_args__ = (
        enum_check("timing", ODDS_TIMINGS),
        enum_check("market", HIST_MARKETS),
        enum_check("selection", HIST_SELECTIONS),
    )

    match_id: Mapped[int] = mapped_column(_fk("matches.id"), index=True)
    bookmaker: Mapped[str] = mapped_column(Text)
    timing: Mapped[str] = mapped_column(Text)
    market: Mapped[str] = mapped_column(Text)
    line: Mapped[float | None] = mapped_column(REAL)
    selection: Mapped[str] = mapped_column(Text)
    odds: Mapped[float] = mapped_column(REAL)


# Natural key for idempotent upsert. `line` is NULL for 1X2 and NULLs never collide
# in a UNIQUE index, so the key uses coalesce(line, sentinel).
_NO_LINE_SENTINEL = -1000.0
Index(
    "uq_historical_odds_natural_key",
    HistoricalOdds.match_id,
    HistoricalOdds.bookmaker,
    HistoricalOdds.timing,
    HistoricalOdds.market,
    func.coalesce(HistoricalOdds.line, _NO_LINE_SENTINEL),
    HistoricalOdds.selection,
    unique=True,
)


class ModelRun(IdTimestampMixin, Base):
    __tablename__ = "model_runs"
    __table_args__ = (bool_check("converged"),)

    league_id: Mapped[int] = mapped_column(_fk("leagues.id"), index=True)
    model_version: Mapped[str] = mapped_column(Text)
    fitted_at: Mapped[datetime]
    train_from: Mapped[date] = mapped_column(Date)
    train_to: Mapped[date] = mapped_column(Date)
    params_json: Mapped[str] = mapped_column(Text)
    converged: Mapped[bool] = mapped_column(IntBool)
    neg_log_lik: Mapped[float] = mapped_column(REAL)
    n_matches: Mapped[int] = mapped_column(Integer)


class Prediction(IdTimestampMixin, Base):
    __tablename__ = "predictions"
    __table_args__ = (UniqueConstraint("match_id", "model_run_id"),)

    match_id: Mapped[int] = mapped_column(_fk("matches.id"), index=True)
    model_run_id: Mapped[int] = mapped_column(_fk("model_runs.id"), index=True)
    lambda_home: Mapped[float] = mapped_column(REAL)
    lambda_away: Mapped[float] = mapped_column(REAL)
    score_matrix_json: Mapped[str] = mapped_column(Text)


class OddsSnapshot(IdTimestampMixin, Base):
    __tablename__ = "odds_snapshots"
    __table_args__ = (
        enum_check("snapshot_kind", SNAPSHOT_KINDS),
        enum_check("market", MARKETS),
        enum_check("selection", SELECTIONS),
        bool_check("is_active"),
        Index("ix_odds_snapshots_match_id_captured_at", "match_id", "captured_at"),
    )

    match_id: Mapped[int] = mapped_column(_fk("matches.id"), index=True)
    captured_at: Mapped[datetime]
    snapshot_kind: Mapped[str] = mapped_column(Text)
    market: Mapped[str] = mapped_column(Text)
    line: Mapped[float] = mapped_column(REAL)
    selection: Mapped[str] = mapped_column(Text)
    odds: Mapped[float] = mapped_column(REAL)
    sb_market_id: Mapped[str] = mapped_column(Text)
    sb_specifier: Mapped[str] = mapped_column(Text)
    sb_outcome_id: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(IntBool)


class ValueLeg(IdTimestampMixin, Base):
    __tablename__ = "value_legs"
    __table_args__ = (
        enum_check("market", MARKETS),
        enum_check("selection", SELECTIONS),
        enum_check("sanity_status", SANITY_STATUSES),
        bool_check("qualifies"),
    )

    match_id: Mapped[int] = mapped_column(_fk("matches.id"), index=True)
    prediction_id: Mapped[int] = mapped_column(_fk("predictions.id"), index=True)
    odds_snapshot_id: Mapped[int] = mapped_column(_fk("odds_snapshots.id"), index=True)
    market: Mapped[str] = mapped_column(Text)
    line: Mapped[float] = mapped_column(REAL)
    selection: Mapped[str] = mapped_column(Text)
    odds: Mapped[float] = mapped_column(REAL)
    p_model: Mapped[float] = mapped_column(REAL)
    p_final: Mapped[float] = mapped_column(REAL)
    p_market_devig: Mapped[float] = mapped_column(REAL)
    p_win: Mapped[float] = mapped_column(REAL)
    p_half_win: Mapped[float] = mapped_column(REAL)
    p_push: Mapped[float] = mapped_column(REAL)
    p_half_loss: Mapped[float] = mapped_column(REAL)
    p_loss: Mapped[float] = mapped_column(REAL)
    expected_multiplier: Mapped[float] = mapped_column(REAL)
    edge: Mapped[float] = mapped_column(REAL)
    sanity_status: Mapped[str] = mapped_column(Text)
    sanity_reason: Mapped[str | None] = mapped_column(Text)
    # docs/05 §8: slip eligibility under the strategy active at pick time (NULL before 0006)
    qualifies: Mapped[bool | None] = mapped_column(IntBool)


class Slip(IdTimestampMixin, Base):
    __tablename__ = "slips"
    __table_args__ = (
        enum_check("slip_type", SLIP_TYPES),
        enum_check("pool", SLIP_POOLS),
        enum_check("strategy", SLIP_STRATEGIES),
        enum_check("booking_status", BOOKING_STATUSES),
        enum_check("mode", SLIP_MODES),
        enum_check("status", SLIP_STATUSES),
    )

    slip_type: Mapped[str] = mapped_column(Text)
    pool: Mapped[str] = mapped_column(Text, default="club", server_default="club")
    strategy: Mapped[str] = mapped_column(Text, default="value", server_default="value")
    slip_date: Mapped[date] = mapped_column(Date)
    window_start_utc: Mapped[datetime]
    window_end_utc: Mapped[datetime]
    total_odds: Mapped[float] = mapped_column(REAL)
    p_all_win: Mapped[float] = mapped_column(REAL)
    expected_multiplier: Mapped[float] = mapped_column(REAL)
    booking_code: Mapped[str | None] = mapped_column(Text)
    booking_status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    booking_error: Mapped[str | None] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(Text, default="paper", server_default="paper")
    stake: Mapped[float | None] = mapped_column(REAL)
    status: Mapped[str] = mapped_column(Text, default="open", server_default="open")
    return_multiplier: Mapped[float | None] = mapped_column(REAL)
    model_version: Mapped[str] = mapped_column(Text)

    legs: Mapped[list[SlipLeg]] = relationship(
        back_populates="slip", order_by="SlipLeg.leg_order", cascade="all, delete-orphan"
    )


class SlipLeg(IdTimestampMixin, Base):
    __tablename__ = "slip_legs"
    __table_args__ = (
        UniqueConstraint("slip_id", "leg_order"),
        enum_check("result", LEG_RESULTS),
    )

    slip_id: Mapped[int] = mapped_column(ForeignKey("slips.id", ondelete="CASCADE"), index=True)
    value_leg_id: Mapped[int] = mapped_column(_fk("value_legs.id"), index=True)
    leg_order: Mapped[int] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    result_multiplier: Mapped[float | None] = mapped_column(REAL)
    closing_odds: Mapped[float | None] = mapped_column(REAL)
    clv: Mapped[float | None] = mapped_column(REAL)

    slip: Mapped[Slip] = relationship(back_populates="legs")


class BacktestRun(IdTimestampMixin, Base):
    __tablename__ = "backtest_runs"
    __table_args__ = (bool_check("gate_passed"),)

    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    config_json: Mapped[str] = mapped_column(Text)
    metrics_json: Mapped[str | None] = mapped_column(Text)
    gate_passed: Mapped[bool | None] = mapped_column(IntBool)
    report_path: Mapped[str | None] = mapped_column(Text)


class JobRun(IdTimestampMixin, Base):
    __tablename__ = "job_runs"
    __table_args__ = (enum_check("status", JOB_STATUSES),)

    job_name: Mapped[str] = mapped_column(Text, index=True)
    started_at: Mapped[datetime]
    finished_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(Text)
    summary_json: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)


class UnresolvedName(IdTimestampMixin, Base):
    __tablename__ = "unresolved_names"
    __table_args__ = (
        UniqueConstraint("source", "raw_name"),
        enum_check("source", ALIAS_SOURCES),
    )

    source: Mapped[str] = mapped_column(Text)
    raw_name: Mapped[str] = mapped_column(Text)
    league_key: Mapped[str | None] = mapped_column(Text)
    first_seen: Mapped[datetime]
    last_seen: Mapped[datetime]
