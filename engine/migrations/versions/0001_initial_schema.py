"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-09-26 21:24:42.818346
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("started_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("metrics_json", sa.Text(), nullable=True),
        sa.Column("gate_passed", sa.Integer(), nullable=True),
        sa.Column("report_path", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint("gate_passed IN (0, 1)", name=op.f("ck_backtest_runs_gate_passed")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backtest_runs")),
    )
    op.create_table(
        "job_runs",
        sa.Column("job_name", sa.Text(), nullable=False),
        sa.Column("started_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("finished_at", sa.TIMESTAMP(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'success', 'failed')", name=op.f("ck_job_runs_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_runs")),
    )
    with op.batch_alter_table("job_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_job_runs_job_name"), ["job_name"], unique=False)

    op.create_table(
        "leagues",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("fd_code", sa.Text(), nullable=False),
        sa.Column("understat_key", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Integer(), server_default="0", nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint("enabled IN (0, 1)", name=op.f("ck_leagues_enabled")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leagues")),
        sa.UniqueConstraint("key", name=op.f("uq_leagues_key")),
    )
    op.create_table(
        "slips",
        sa.Column("slip_type", sa.Text(), nullable=False),
        sa.Column("slip_date", sa.Date(), nullable=False),
        sa.Column("window_start_utc", sa.TIMESTAMP(), nullable=False),
        sa.Column("window_end_utc", sa.TIMESTAMP(), nullable=False),
        sa.Column("total_odds", sa.REAL(), nullable=False),
        sa.Column("p_all_win", sa.REAL(), nullable=False),
        sa.Column("expected_multiplier", sa.REAL(), nullable=False),
        sa.Column("booking_code", sa.Text(), nullable=True),
        sa.Column("booking_status", sa.Text(), server_default="pending", nullable=False),
        sa.Column("booking_error", sa.Text(), nullable=True),
        sa.Column("mode", sa.Text(), server_default="paper", nullable=False),
        sa.Column("stake", sa.REAL(), nullable=True),
        sa.Column("status", sa.Text(), server_default="open", nullable=False),
        sa.Column("return_multiplier", sa.REAL(), nullable=True),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "booking_status IN ('pending', 'booked', 'failed', 'manual')",
            name=op.f("ck_slips_booking_status"),
        ),
        sa.CheckConstraint("mode IN ('paper', 'placed')", name=op.f("ck_slips_mode")),
        sa.CheckConstraint(
            "slip_type IN ('daily_2odds', 'mid_acca', 'mega_acca')", name=op.f("ck_slips_slip_type")
        ),
        sa.CheckConstraint(
            "status IN ('open', 'won', 'lost', 'void', 'partial')", name=op.f("ck_slips_status")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_slips")),
    )
    op.create_table(
        "unresolved_names",
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("raw_name", sa.Text(), nullable=False),
        sa.Column("league_key", sa.Text(), nullable=True),
        sa.Column("first_seen", sa.TIMESTAMP(), nullable=False),
        sa.Column("last_seen", sa.TIMESTAMP(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('football_data', 'understat', 'sportybet')",
            name=op.f("ck_unresolved_names_source"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_unresolved_names")),
        sa.UniqueConstraint("source", "raw_name", name=op.f("uq_unresolved_names_source_raw_name")),
    )
    op.create_table(
        "model_runs",
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.Text(), nullable=False),
        sa.Column("fitted_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("train_from", sa.Date(), nullable=False),
        sa.Column("train_to", sa.Date(), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("converged", sa.Integer(), nullable=False),
        sa.Column("neg_log_lik", sa.REAL(), nullable=False),
        sa.Column("n_matches", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint("converged IN (0, 1)", name=op.f("ck_model_runs_converged")),
        sa.ForeignKeyConstraint(
            ["league_id"],
            ["leagues.id"],
            name=op.f("fk_model_runs_league_id_leagues"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_runs")),
    )
    with op.batch_alter_table("model_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_model_runs_league_id"), ["league_id"], unique=False)

    op.create_table(
        "teams",
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["league_id"],
            ["leagues.id"],
            name=op.f("fk_teams_league_id_leagues"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_teams")),
        sa.UniqueConstraint("canonical_name", name=op.f("uq_teams_canonical_name")),
    )
    with op.batch_alter_table("teams", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_teams_league_id"), ["league_id"], unique=False)

    op.create_table(
        "matches",
        sa.Column("league_id", sa.Integer(), nullable=False),
        sa.Column("season", sa.Text(), nullable=False),
        sa.Column("kickoff_utc", sa.TIMESTAMP(), nullable=False),
        sa.Column("home_team_id", sa.Integer(), nullable=False),
        sa.Column("away_team_id", sa.Integer(), nullable=False),
        sa.Column("home_goals", sa.Integer(), nullable=True),
        sa.Column("away_goals", sa.Integer(), nullable=True),
        sa.Column("home_xg", sa.REAL(), nullable=True),
        sa.Column("away_xg", sa.REAL(), nullable=True),
        sa.Column("status", sa.Text(), server_default="scheduled", nullable=False),
        sa.Column("fd_row_hash", sa.Text(), nullable=True),
        sa.Column("sportybet_event_id", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('scheduled', 'finished', 'postponed', 'cancelled', 'abandoned', 'needs_review')",
            name=op.f("ck_matches_status"),
        ),
        sa.ForeignKeyConstraint(
            ["away_team_id"],
            ["teams.id"],
            name=op.f("fk_matches_away_team_id_teams"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["home_team_id"],
            ["teams.id"],
            name=op.f("fk_matches_home_team_id_teams"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["league_id"],
            ["leagues.id"],
            name=op.f("fk_matches_league_id_leagues"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_matches")),
        sa.UniqueConstraint("fd_row_hash", name=op.f("uq_matches_fd_row_hash")),
        sa.UniqueConstraint(
            "league_id",
            "home_team_id",
            "away_team_id",
            "season",
            name=op.f("uq_matches_league_id_home_team_id_away_team_id_season"),
        ),
        sa.UniqueConstraint("sportybet_event_id", name=op.f("uq_matches_sportybet_event_id")),
    )
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_matches_away_team_id"), ["away_team_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_matches_home_team_id"), ["home_team_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_matches_kickoff_utc"), ["kickoff_utc"], unique=False)
        batch_op.create_index(batch_op.f("ix_matches_league_id"), ["league_id"], unique=False)

    op.create_table(
        "team_aliases",
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("alias", sa.Text(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('football_data', 'understat', 'sportybet')",
            name=op.f("ck_team_aliases_source"),
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            name=op.f("fk_team_aliases_team_id_teams"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_team_aliases")),
        sa.UniqueConstraint("source", "alias", name=op.f("uq_team_aliases_source_alias")),
    )
    with op.batch_alter_table("team_aliases", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_team_aliases_team_id"), ["team_id"], unique=False)

    op.create_table(
        "historical_odds",
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("bookmaker", sa.Text(), nullable=False),
        sa.Column("timing", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("line", sa.REAL(), nullable=True),
        sa.Column("selection", sa.Text(), nullable=False),
        sa.Column("odds", sa.REAL(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint("market IN ('OU', 'AH', '1X2')", name=op.f("ck_historical_odds_market")),
        sa.CheckConstraint(
            "selection IN ('home', 'draw', 'away', 'over', 'under')",
            name=op.f("ck_historical_odds_selection"),
        ),
        sa.CheckConstraint("timing IN ('open', 'close')", name=op.f("ck_historical_odds_timing")),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name=op.f("fk_historical_odds_match_id_matches"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_historical_odds")),
    )
    with op.batch_alter_table("historical_odds", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_historical_odds_match_id"), ["match_id"], unique=False)
    # natural key (line is NULL for 1X2; NULLs never collide in UNIQUE, so coalesce)
    op.execute(
        "CREATE UNIQUE INDEX uq_historical_odds_natural_key ON historical_odds "
        "(match_id, bookmaker, timing, market, coalesce(line, -1000.0), selection)"
    )

    op.create_table(
        "odds_snapshots",
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("captured_at", sa.TIMESTAMP(), nullable=False),
        sa.Column("snapshot_kind", sa.Text(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("line", sa.REAL(), nullable=False),
        sa.Column("selection", sa.Text(), nullable=False),
        sa.Column("odds", sa.REAL(), nullable=False),
        sa.Column("sb_market_id", sa.Text(), nullable=False),
        sa.Column("sb_specifier", sa.Text(), nullable=False),
        sa.Column("sb_outcome_id", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Integer(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint("market IN ('OU', 'AH')", name=op.f("ck_odds_snapshots_market")),
        sa.CheckConstraint(
            "selection IN ('over', 'under', 'home', 'away')",
            name=op.f("ck_odds_snapshots_selection"),
        ),
        sa.CheckConstraint(
            "snapshot_kind IN ('pick', 'close')", name=op.f("ck_odds_snapshots_snapshot_kind")
        ),
        sa.CheckConstraint("is_active IN (0, 1)", name=op.f("ck_odds_snapshots_is_active")),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name=op.f("fk_odds_snapshots_match_id_matches"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_odds_snapshots")),
    )
    with op.batch_alter_table("odds_snapshots", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_odds_snapshots_match_id"), ["match_id"], unique=False)
        batch_op.create_index(
            "ix_odds_snapshots_match_id_captured_at", ["match_id", "captured_at"], unique=False
        )

    op.create_table(
        "predictions",
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("model_run_id", sa.Integer(), nullable=False),
        sa.Column("lambda_home", sa.REAL(), nullable=False),
        sa.Column("lambda_away", sa.REAL(), nullable=False),
        sa.Column("score_matrix_json", sa.Text(), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name=op.f("fk_predictions_match_id_matches"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["model_run_id"],
            ["model_runs.id"],
            name=op.f("fk_predictions_model_run_id_model_runs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_predictions")),
        sa.UniqueConstraint(
            "match_id", "model_run_id", name=op.f("uq_predictions_match_id_model_run_id")
        ),
    )
    with op.batch_alter_table("predictions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_predictions_match_id"), ["match_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_predictions_model_run_id"), ["model_run_id"], unique=False
        )

    op.create_table(
        "value_legs",
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("prediction_id", sa.Integer(), nullable=False),
        sa.Column("odds_snapshot_id", sa.Integer(), nullable=False),
        sa.Column("market", sa.Text(), nullable=False),
        sa.Column("line", sa.REAL(), nullable=False),
        sa.Column("selection", sa.Text(), nullable=False),
        sa.Column("odds", sa.REAL(), nullable=False),
        sa.Column("p_model", sa.REAL(), nullable=False),
        sa.Column("p_final", sa.REAL(), nullable=False),
        sa.Column("p_market_devig", sa.REAL(), nullable=False),
        sa.Column("p_win", sa.REAL(), nullable=False),
        sa.Column("p_half_win", sa.REAL(), nullable=False),
        sa.Column("p_push", sa.REAL(), nullable=False),
        sa.Column("p_half_loss", sa.REAL(), nullable=False),
        sa.Column("p_loss", sa.REAL(), nullable=False),
        sa.Column("expected_multiplier", sa.REAL(), nullable=False),
        sa.Column("edge", sa.REAL(), nullable=False),
        sa.Column("sanity_status", sa.Text(), nullable=False),
        sa.Column("sanity_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint("market IN ('OU', 'AH')", name=op.f("ck_value_legs_market")),
        sa.CheckConstraint(
            "sanity_status IN ('ok', 'flagged')", name=op.f("ck_value_legs_sanity_status")
        ),
        sa.CheckConstraint(
            "selection IN ('over', 'under', 'home', 'away')", name=op.f("ck_value_legs_selection")
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["matches.id"],
            name=op.f("fk_value_legs_match_id_matches"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["odds_snapshot_id"],
            ["odds_snapshots.id"],
            name=op.f("fk_value_legs_odds_snapshot_id_odds_snapshots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["prediction_id"],
            ["predictions.id"],
            name=op.f("fk_value_legs_prediction_id_predictions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_value_legs")),
    )
    with op.batch_alter_table("value_legs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_value_legs_match_id"), ["match_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_value_legs_odds_snapshot_id"), ["odds_snapshot_id"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_value_legs_prediction_id"), ["prediction_id"], unique=False
        )

    op.create_table(
        "slip_legs",
        sa.Column("slip_id", sa.Integer(), nullable=False),
        sa.Column("value_leg_id", sa.Integer(), nullable=False),
        sa.Column("leg_order", sa.Integer(), nullable=False),
        sa.Column("result", sa.Text(), server_default="pending", nullable=False),
        sa.Column("result_multiplier", sa.REAL(), nullable=True),
        sa.Column("closing_odds", sa.REAL(), nullable=True),
        sa.Column("clv", sa.REAL(), nullable=True),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "result IN ('pending', 'win', 'half_win', 'push', 'half_loss', 'loss', 'void')",
            name=op.f("ck_slip_legs_result"),
        ),
        sa.ForeignKeyConstraint(
            ["slip_id"], ["slips.id"], name=op.f("fk_slip_legs_slip_id_slips"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["value_leg_id"],
            ["value_legs.id"],
            name=op.f("fk_slip_legs_value_leg_id_value_legs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_slip_legs")),
        sa.UniqueConstraint("slip_id", "leg_order", name=op.f("uq_slip_legs_slip_id_leg_order")),
    )
    with op.batch_alter_table("slip_legs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_slip_legs_slip_id"), ["slip_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_slip_legs_value_leg_id"), ["value_leg_id"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("slip_legs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_slip_legs_value_leg_id"))
        batch_op.drop_index(batch_op.f("ix_slip_legs_slip_id"))

    op.drop_table("slip_legs")
    with op.batch_alter_table("value_legs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_value_legs_prediction_id"))
        batch_op.drop_index(batch_op.f("ix_value_legs_odds_snapshot_id"))
        batch_op.drop_index(batch_op.f("ix_value_legs_match_id"))

    op.drop_table("value_legs")
    with op.batch_alter_table("predictions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_predictions_model_run_id"))
        batch_op.drop_index(batch_op.f("ix_predictions_match_id"))

    op.drop_table("predictions")
    with op.batch_alter_table("odds_snapshots", schema=None) as batch_op:
        batch_op.drop_index("ix_odds_snapshots_match_id_captured_at")
        batch_op.drop_index(batch_op.f("ix_odds_snapshots_match_id"))

    op.drop_table("odds_snapshots")
    op.execute("DROP INDEX uq_historical_odds_natural_key")
    with op.batch_alter_table("historical_odds", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_historical_odds_match_id"))

    op.drop_table("historical_odds")
    with op.batch_alter_table("team_aliases", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_team_aliases_team_id"))

    op.drop_table("team_aliases")
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_matches_league_id"))
        batch_op.drop_index(batch_op.f("ix_matches_kickoff_utc"))
        batch_op.drop_index(batch_op.f("ix_matches_home_team_id"))
        batch_op.drop_index(batch_op.f("ix_matches_away_team_id"))

    op.drop_table("matches")
    with op.batch_alter_table("teams", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_teams_league_id"))

    op.drop_table("teams")
    with op.batch_alter_table("model_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_model_runs_league_id"))

    op.drop_table("model_runs")
    op.drop_table("unresolved_names")
    op.drop_table("slips")
    op.drop_table("leagues")
    with op.batch_alter_table("job_runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_job_runs_job_name"))

    op.drop_table("job_runs")
    op.drop_table("backtest_runs")
