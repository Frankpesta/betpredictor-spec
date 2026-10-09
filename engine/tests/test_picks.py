"""`make picks` end to end on a temp DB (docs/05 §7 integration bullet, docs/08 Phase 4)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.config import Settings, get_settings
from engine.db.base import utcnow
from engine.db.models import (
    League,
    Match,
    ModelRun,
    OddsSnapshot,
    Prediction,
    Slip,
    SlipLeg,
    Team,
    ValueLeg,
)
from engine.db.picks import run_picks
from engine.db.session import session_scope
from engine.jobs import job_run, sync_leagues


def _settings() -> Settings:
    base = get_settings()
    return base.model_copy(
        update={
            "leagues": base.leagues.model_copy(update={"enabled": ["EPL", "INTL"]}),
            # make the synthetic value visible: no shrink, generous gap; the slip-flow
            # tests below use "likeliest" (data_rule has its own test)
            "value": base.value.model_copy(
                update={
                    "selection": "likeliest",
                    "market_shrink_weight": 1.0,
                    "max_model_market_gap": 0.3,
                }
            ),
        }
    )


def _league(s: Session, key: str, prefix: str, n_upcoming: int) -> None:
    lg = s.scalars(select(League).where(League.key == key)).one()
    rng = np.random.default_rng(5)
    teams = []
    for i in range(10):
        t = Team(league_id=lg.id, canonical_name=f"{prefix}{i}")
        s.add(t)
        s.flush()
        teams.append(t.id)
    now = utcnow()
    k = 0
    for i in teams:
        for j in teams:
            if i == j:
                continue
            k += 1
            hg, ag = int(rng.poisson(1.6)), int(rng.poisson(1.2))
            s.add(
                Match(
                    league_id=lg.id,
                    season="2025-26",
                    kickoff_utc=now - timedelta(days=200) + timedelta(days=k * 2),
                    home_team_id=i,
                    away_team_id=j,
                    home_goals=hg,
                    away_goals=ag,
                    status="finished",
                    fixture_key=f"f{k}" if key == "INTL" else "",
                )
            )
    s.flush()
    for u in range(n_upcoming):
        m = Match(
            league_id=lg.id,
            season="2026-27",
            kickoff_utc=now + timedelta(hours=3 + u),
            home_team_id=teams[u],
            away_team_id=teams[9 - u],
            status="scheduled",
            fixture_key=f"u{u}" if key == "INTL" else "",
        )
        s.add(m)
        s.flush()
        for sel, odds in (("over", 1.45), ("under", 2.80)):
            s.add(
                OddsSnapshot(
                    match_id=m.id,
                    captured_at=now,
                    snapshot_kind="pick",
                    market="OU",
                    line=1.5,
                    selection=sel,
                    odds=odds,
                    sb_market_id="18",
                    sb_specifier="total=1.5",
                    sb_outcome_id="12" if sel == "over" else "13",
                    is_active=True,
                )
            )
        for sel, odds in (("over", 1.90), ("under", 1.90)):  # a whole line: priced, flagged
            s.add(
                OddsSnapshot(
                    match_id=m.id,
                    captured_at=now,
                    snapshot_kind="pick",
                    market="OU",
                    line=3.0,
                    selection=sel,
                    odds=odds,
                    sb_market_id="18",
                    sb_specifier="total=3",
                    sb_outcome_id="12" if sel == "over" else "13",
                    is_active=True,
                )
            )
    s.flush()


def _slip_signature(
    db: Path,
) -> list[tuple[str, str, tuple[tuple[int, str, float, str, float], ...]]]:
    with session_scope(db) as s:
        out = []
        for slip in s.scalars(select(Slip).order_by(Slip.pool, Slip.slip_type)):
            legs = []
            for sl in slip.legs:
                vl = s.get_one(ValueLeg, sl.value_leg_id)
                legs.append((vl.match_id, vl.market, vl.line, vl.selection, vl.odds))
            out.append((slip.pool, slip.slip_type, tuple(legs)))
        return out


def test_picks_end_to_end_rerun_identical_and_booked_slip_kept(migrated_db: Path) -> None:
    settings = _settings()
    with session_scope(migrated_db) as s:
        sync_leagues(s, settings)
        s.flush()
        _league(s, "EPL", "Club", 6)
        _league(s, "INTL", "Nation", 6)

    with job_run("picks", settings, migrated_db) as ctx:
        run_picks(ctx)
    summary = ctx.summary
    assert summary["legs_priced"] == 2 * 6 * 4  # 2 pools x 6 matches x (1.5 pair + 3.0 pair)
    assert summary["flagged_by_reason"]["non_half_line_v1"] == 24  # whole line 3.0 flagged
    assert summary["qualifying"] > 0
    assert set(summary["refits"]) == {"EPL", "INTL"}  # no model run existed

    first = _slip_signature(migrated_db)
    pools = {p for p, _, _ in first}
    assert pools <= {"club", "intl"} and first, first
    with session_scope(migrated_db) as s:
        assert s.scalar(select(func.count()).select_from(Prediction)) == 12
        # never mixed: every leg of a slip belongs to that slip's pool
        for slip in s.scalars(select(Slip)):
            keys = {
                s.get_one(
                    League,
                    s.get_one(Match, s.get_one(ValueLeg, sl.value_leg_id).match_id).league_id,
                ).key
                for sl in slip.legs
            }
            assert keys == ({"INTL"} if slip.pool == "intl" else {"EPL"})
            assert len({s.get_one(ValueLeg, sl.value_leg_id).match_id for sl in slip.legs}) == len(
                slip.legs
            )
            for sl in slip.legs:
                vl = s.get_one(ValueLeg, sl.value_leg_id)
                assert vl.sanity_status == "ok" and vl.line == 1.5

    # run twice: identical slips, old pending ones replaced (not duplicated)
    with job_run("picks", settings, migrated_db) as ctx2:
        run_picks(ctx2)
    assert _slip_signature(migrated_db) == first
    assert ctx2.summary["refits"] == []  # the run from the first pass is fresh

    # a booked slip is kept and blocks a new one of the same type/pool/date
    with session_scope(migrated_db) as s:
        booked = s.scalars(select(Slip).order_by(Slip.id)).first()
        assert booked is not None
        booked.booking_status, booked.booking_code = "booked", "TEST01"
        booked_id, booked_key = booked.id, f"{booked.pool}/{booked.slip_type}"
    with job_run("picks", settings, migrated_db) as ctx3:
        run_picks(ctx3)
    assert ctx3.summary["slips"][booked_key] == "kept existing booked/settled slip"
    with session_scope(migrated_db) as s:
        assert s.get(Slip, booked_id) is not None
        assert s.scalar(select(func.count()).select_from(Slip)) == len(first)
        assert s.scalar(select(func.count()).select_from(SlipLeg)) > 0


def _add_ah(s: Session) -> None:
    """Every upcoming match gets AH ±0.5 and +1.5: the home side is the market favourite."""
    now = utcnow()
    for m in s.scalars(select(Match).where(Match.status == "scheduled")):
        for line, home_odds, away_odds in ((-0.5, 1.55, 2.45), (0.5, 1.12, 6.00), (1.5, 1.04, 9.0)):
            for sel, odds in (("home", home_odds), ("away", away_odds)):
                s.add(
                    OddsSnapshot(
                        match_id=m.id,
                        captured_at=now,
                        snapshot_kind="pick",
                        market="AH",
                        line=line,
                        selection=sel,
                        odds=odds,
                        sb_market_id="16",
                        sb_specifier=f"hcp={line}",
                        sb_outcome_id="1714" if sel == "home" else "1715",
                        is_active=True,
                    )
                )
    s.flush()


def test_picks_likeliest_backs_the_favourite_and_tags_slips(migrated_db: Path) -> None:
    """docs/05 §8: AH legs qualify only on the favourite's side; slips carry the strategy."""
    base = _settings()
    settings = base.model_copy(
        update={
            "value": base.value.model_copy(
                update={"selection": "likeliest", "market_shrink_weight": 0.3}
            )
        }
    )
    with session_scope(migrated_db) as s:
        sync_leagues(s, settings)
        s.flush()
        _league(s, "INTL", "Nation", 6)
        _add_ah(s)
    with job_run("picks", settings, migrated_db) as ctx:
        run_picks(ctx)
    assert ctx.summary["selection"] == "likeliest"
    with session_scope(migrated_db) as s:
        legs = s.scalars(select(ValueLeg)).all()
        assert legs and all(vl.qualifies is not None for vl in legs)
        for vl in legs:
            if vl.market == "AH" and vl.selection == "away":
                assert vl.qualifies is False  # the weaker side's head start is never taken
            if vl.sanity_status == "ok" and vl.market == "OU":
                assert vl.qualifies is True  # no edge / odds floor under "likeliest"
        assert any(vl.qualifies for vl in legs if vl.market == "AH" and vl.selection == "home")
        slips = s.scalars(select(Slip)).all()
        assert slips and {sl.strategy for sl in slips} == {"likeliest"}
        for slip in slips:
            for sl in slip.legs:
                assert s.get_one(ValueLeg, sl.value_leg_id).qualifies is True


def test_picks_value_strategy_tags_slips_value(migrated_db: Path) -> None:
    base = _settings()
    settings = base.model_copy(
        update={"value": base.value.model_copy(update={"selection": "value"})}
    )
    with session_scope(migrated_db) as s:
        sync_leagues(s, settings)
        s.flush()
        _league(s, "EPL", "Club", 6)
    with job_run("picks", settings, migrated_db) as ctx:
        run_picks(ctx)
    with session_scope(migrated_db) as s:
        slips = s.scalars(select(Slip)).all()
        assert slips and {sl.strategy for sl in slips} == {"value"}
        for vl in s.scalars(select(ValueLeg)):
            # value rule: qualifies == sanity ok + edge + odds bounds (docs/05 §2.2)
            v = settings.value
            expected = (
                vl.sanity_status == "ok"
                and vl.edge >= v.min_edge_leg
                and v.min_odds <= vl.odds <= v.max_odds
            )
            assert vl.qualifies is expected


def test_picks_data_rule_stores_reasons_and_tags_slips(migrated_db: Path) -> None:
    """docs/05 §9: qualifying legs carry their team-data reason; slips tagged data_rule."""
    base = _settings()
    rule = base.value.data_rule.model_copy(update={"over_score_p": 0.5, "over_max_blanks": 10})
    settings = base.model_copy(
        update={
            "value": base.value.model_copy(update={"selection": "data_rule", "data_rule": rule})
        }
    )
    with session_scope(migrated_db) as s:
        sync_leagues(s, settings)
        s.flush()
        _league(s, "EPL", "Club", 6)
    with job_run("picks", settings, migrated_db) as ctx:
        run_picks(ctx)
    assert ctx.summary["selection"] == "data_rule"
    with session_scope(migrated_db) as s:
        legs = s.scalars(select(ValueLeg)).all()
        assert any(vl.qualifies for vl in legs)
        for vl in legs:
            assert (vl.qualify_reason is not None) is bool(vl.qualifies)
            if vl.qualifies:
                assert vl.sanity_status == "ok"
                assert vl.odds >= rule.min_odds and vl.p_final >= rule.min_leg_probability
                assert vl.qualify_reason.startswith("both score: home scores ")
            if vl.market == "OU" and vl.selection == "under":
                assert not vl.qualifies  # synthetic attacks are not weak
        slips = s.scalars(select(Slip)).all()
        assert {sl.strategy for sl in slips} <= {"data_rule"}
        for slip in slips:
            for sl in slip.legs:
                assert s.get_one(ValueLeg, sl.value_leg_id).qualifies is True


def test_picks_half_time_markets_priced_from_half_matrices(migrated_db: Path) -> None:
    """docs/10: club half-time legs use the stored half-time model; reasons name the half."""
    base = _settings()
    loose = base.value.data_rule_half.model_copy(
        update={"over_score_p": 0.3, "over_max_blanks": 10, "min_odds": 1.01}
    )
    settings = base.model_copy(
        update={
            "half_time": base.half_time.model_copy(update={"min_matches": 20}),
            "value": base.value.model_copy(
                update={"selection": "data_rule", "data_rule_half": loose}
            ),
        }
    )
    with session_scope(migrated_db) as s:
        sync_leagues(s, settings)
        s.flush()
        _league(s, "EPL", "Club", 3)
        for m in s.scalars(select(Match).where(Match.status == "finished")):
            m.ht_home_goals, m.ht_away_goals = (m.home_goals or 0) // 2, (m.away_goals or 0) // 2
        now = utcnow()
        for m in s.scalars(select(Match).where(Match.status == "scheduled")):
            for sel, odds in (("over", 1.45), ("under", 2.70)):
                s.add(
                    OddsSnapshot(
                        match_id=m.id,
                        captured_at=now,
                        snapshot_kind="pick",
                        market="OU_1H",
                        line=0.5,
                        selection=sel,
                        odds=odds,
                        sb_market_id="68",
                        sb_specifier="total=0.5",
                        sb_outcome_id="12" if sel == "over" else "13",
                        is_active=True,
                    )
                )
    with job_run("picks", settings, migrated_db) as ctx:
        run_picks(ctx)
    with session_scope(migrated_db) as s:
        run = s.scalars(select(ModelRun)).one()
        assert '"half_time": {' in run.params_json
        ht_legs = s.scalars(select(ValueLeg).where(ValueLeg.market == "OU_1H")).all()
        assert len(ht_legs) == 6
        assert all(0 < vl.p_model < 1 for vl in ht_legs)
        assert any(vl.qualifies for vl in ht_legs)
        for vl in ht_legs:
            if vl.qualifies:
                assert vl.qualify_reason is not None
                assert vl.qualify_reason.startswith("1st half: both score")
