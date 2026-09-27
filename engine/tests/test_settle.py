"""Settlement, CLV and views (docs/06 §5, docs/08 Phase 6). No network."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text

from engine.config import get_settings
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
from engine.db.session import session_scope
from engine.jobs import job_run, sync_leagues
from engine.settle import settle as st
from engine.settle.clv import clv, update_leg_clv
from engine.sportybet.results import ninety_minute_score, parse_results

FIX = Path(__file__).parent / "fixtures" / "sportybet"


# ---- pure rules --------------------------------------------------------------------


def test_docs_example_partial() -> None:
    # (win @1.5, half_win @1.8, void) -> 1.5 x 1.4 x 1 = 2.1 -> partial
    legs = [("win", 1.5), ("half_win", (1 + 1.8) / 2), ("void", 1.0)]
    out = st.slip_outcome(legs)
    assert out.status == "partial" and out.return_multiplier == pytest.approx(2.1)


@pytest.mark.parametrize(
    ("legs", "status", "mult"),
    [
        ([("win", 1.5), ("win", 1.4)], "won", 2.1),
        ([("win", 1.5), ("void", 1.0)], "won", 1.5),  # SportyBet 4.4: settled on the rest
        ([("void", 1.0), ("void", 1.0)], "void", 1.0),
        ([("win", 1.5), ("loss", 0.0)], "lost", 0.0),
        ([("loss", 0.0), ("pending", None)], "lost", 0.0),  # 4.11: lost at once
        ([("win", 1.5), ("pending", None)], "open", None),
        ([("win", 1.9), ("half_loss", 0.5)], "partial", 0.95),
        ([("push", 1.0), ("win", 1.7)], "partial", 1.7),
    ],
)
def test_slip_outcomes(
    legs: list[tuple[str, float | None]], status: str, mult: float | None
) -> None:
    out = st.slip_outcome(legs)
    assert out.status == status
    if mult is None:
        assert out.return_multiplier is None
    else:
        assert out.return_multiplier == pytest.approx(mult)


def test_settle_leg_uses_the_single_settlement_implementation() -> None:
    assert st.settle_leg("OU", 2.5, "over", 1.9, (2, 1)) == ("win", 1.9)
    assert st.settle_leg("AH", -0.5, "home", 1.8, (1, 1)) == ("loss", 0.0)
    assert st.settle_leg("AH", -0.25, "home", 2.0, (0, 0)) == ("half_loss", 0.5)
    assert st.settle_leg("OU", 2.5, "over", 1.9, None) == ("void", 1.0)


def test_reconcile_sources() -> None:
    assert st.reconcile((2, 1), (2, 1)) == ("ok", (2, 1))
    assert st.reconcile((2, 1), (2, 2)) == ("conflict", None)
    assert st.reconcile(None, (0, 0)) == ("ok", (0, 0))
    assert st.reconcile((1, 0), None) == ("ok", (1, 0))
    assert st.reconcile(None, None) == ("none", None)


def test_clv_example() -> None:
    assert clv(1.90, 1.80) == pytest.approx(0.0556, abs=1e-4)


def test_ninety_minute_score_on_real_results() -> None:
    res, total = parse_results(
        json.loads((FIX / "eventResultList_cases.json").read_text(encoding="utf-8"))
    )
    assert total == 1069
    assert res["sr:match:73600382"].score_90 == (4, 1)  # USA v Peru; regularTimeScore said 1:1
    assert res["sr:match:72477446"].score_90 == (1, 1)  # Tenerife v Cadiz; regularTimeScore 1:0
    assert res["sr:match:74062554"].score_90 == (1, 1)  # cup tie, setScore 3:4 incl. ET + pens
    assert res["sr:match:74737790"].score_90 is None  # no per-half scores: cannot settle
    assert res["sr:match:68932504"].score_90 == (2, 0)
    assert all(r.ended for r in res.values())


def test_halves_that_do_not_add_up_are_refused() -> None:
    assert ninety_minute_score({"gameScore": ["1:0", "1:0"], "setScore": "3:0"}) is None


# ---- make settle on a temp DB -------------------------------------------------------


def _mk_leg(
    s: Any,
    lg: League,
    run: ModelRun,
    slip: Slip,
    n: int,
    market: str,
    line: float,
    selection: str,
    odds: float,
    hours_ago: float,
    *,
    event: bool = True,
    db_score: tuple[int, int] | None = None,
    status: str = "scheduled",
) -> Match:
    h, a = (
        Team(league_id=lg.id, canonical_name=f"H{n}"),
        Team(league_id=lg.id, canonical_name=f"A{n}"),
    )
    s.add_all([h, a])
    s.flush()
    m = Match(
        league_id=lg.id,
        season="2026-27",
        kickoff_utc=utcnow() - timedelta(hours=hours_ago),
        home_team_id=h.id,
        away_team_id=a.id,
        status=status,
        home_goals=db_score[0] if db_score else None,
        away_goals=db_score[1] if db_score else None,
        sportybet_event_id=f"sr:match:{n}" if event else None,
    )
    s.add(m)
    s.flush()
    snap = OddsSnapshot(
        match_id=m.id,
        captured_at=utcnow() - timedelta(days=1),
        snapshot_kind="pick",
        market=market,
        line=line,
        selection=selection,
        odds=odds,
        sb_market_id="x",
        sb_specifier="x",
        sb_outcome_id="x",
        is_active=True,
    )
    pred = Prediction(
        match_id=m.id, model_run_id=run.id, lambda_home=1, lambda_away=1, score_matrix_json="[]"
    )
    s.add_all([snap, pred])
    s.flush()
    vl = ValueLeg(
        match_id=m.id,
        prediction_id=pred.id,
        odds_snapshot_id=snap.id,
        market=market,
        line=line,
        selection=selection,
        odds=odds,
        p_model=0.6,
        p_final=0.6,
        p_market_devig=0.55,
        p_win=0.6,
        p_half_win=0,
        p_push=0,
        p_half_loss=0,
        p_loss=0.4,
        expected_multiplier=0.6 * odds,
        edge=0.6 * odds - 1,
        sanity_status="ok",
    )
    s.add(vl)
    s.flush()
    slip.legs.append(SlipLeg(value_leg_id=vl.id, leg_order=len(slip.legs) + 1))
    return m


def _slip(s: Any, kind: str) -> Slip:
    slip = Slip(
        slip_type=kind,
        pool="club",
        slip_date=utcnow().date(),
        window_start_utc=utcnow(),
        window_end_utc=utcnow(),
        total_odds=2.0,
        p_all_win=0.4,
        expected_multiplier=1.05,
        model_version="t",
    )
    s.add(slip)
    return slip


def _results_payload(scores: dict[str, tuple[str, str]]) -> dict[str, Any]:
    events = [
        {
            "eventId": eid,
            "status": 4,
            "matchStatus": "Ended",
            "setScore": set_,
            "gameScore": halves.split(","),
        }
        for eid, (halves, set_) in scores.items()
    ]
    return {
        "bizCode": 10000,
        "message": "0#0",
        "data": {
            "totalNum": len(events),
            "tournaments": [{"id": "t", "name": "t", "events": events}],
        },
    }


class FakeResults:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0

    def get_json(self, path: str, params: dict[str, str], cache_name: str) -> dict[str, Any]:
        self.calls += 1
        assert path == "/factsCenter/eventResultList" and "tournamentId" in params
        return self.payload

    def post_json(self, path: str, body: Any, cache_name: str) -> dict[str, Any]:
        raise AssertionError("settle never POSTs")

    def close(self) -> None:
        pass


def test_make_settle_end_to_end(migrated_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with session_scope(migrated_db) as s:
        sync_leagues(s, get_settings())
        s.flush()
        lg = s.scalars(select(League).where(League.key == "EPL")).one()
        run = ModelRun(
            league_id=lg.id,
            model_version="t",
            fitted_at=utcnow(),
            train_from=utcnow().date(),
            train_to=utcnow().date(),
            params_json="{}",
            converged=True,
            neg_log_lik=0,
            n_matches=0,
        )
        s.add(run)
        s.flush()
        won, lost, void_won, review, recent = (
            _slip(s, k) for k in ("daily_2odds", "mid_acca", "mega_acca", "daily_2odds", "mid_acca")
        )
        _mk_leg(s, lg, run, won, 1, "OU", 2.5, "over", 1.5, 5)  # SB 3-1 -> win
        _mk_leg(s, lg, run, won, 2, "AH", -0.5, "home", 1.4, 5)  # SB 1-0 -> win
        _mk_leg(s, lg, run, lost, 3, "OU", 2.5, "over", 1.6, 5)  # SB 0-0 -> loss
        _mk_leg(s, lg, run, lost, 4, "OU", 2.5, "under", 1.9, 1)  # too recent: stays pending
        _mk_leg(
            s, lg, run, void_won, 5, "OU", 2.5, "under", 1.7, 5, event=False, status="postponed"
        )
        _mk_leg(
            s,
            lg,
            run,
            void_won,
            6,
            "OU",
            2.5,
            "under",
            1.8,
            5,
            event=False,
            db_score=(1, 0),
            status="finished",
        )
        _mk_leg(
            s, lg, run, review, 7, "OU", 2.5, "over", 1.9, 5, db_score=(2, 1), status="finished"
        )  # SB 2-2
        _mk_leg(s, lg, run, recent, 8, "OU", 2.5, "over", 1.9, 1)
        s.flush()
        ids = {
            k: v.id
            for k, v in {
                "won": won,
                "lost": lost,
                "void_won": void_won,
                "review": review,
                "recent": recent,
            }.items()
        }

    fake = FakeResults(
        _results_payload(
            {
                "sr:match:1": ("2:0,1:1", "3:1"),
                "sr:match:2": ("0:0,1:0", "1:0"),
                "sr:match:3": ("0:0,0:0", "0:0"),
                "sr:match:7": ("1:1,1:1", "2:2"),  # disagrees with the DB's 2-1
            }
        )
    )
    monkeypatch.setattr(st, "make_transport", lambda settings, kind="httpx": fake)
    with job_run("settle", get_settings(), migrated_db) as ctx:
        out = st.run_settle(ctx)
    assert "NEEDS REVIEW" in out

    with session_scope(migrated_db) as s:
        slips = {k: s.get_one(Slip, v) for k, v in ids.items()}
        assert (slips["won"].status, slips["won"].return_multiplier) == (
            "won",
            pytest.approx(1.5 * 1.4),
        )
        assert (slips["lost"].status, slips["lost"].return_multiplier) == (
            "lost",
            0.0,
        )  # 4.11 before leg 4 settles
        assert [leg.result for leg in slips["lost"].legs] == ["loss", "pending"]
        assert (slips["void_won"].status, slips["void_won"].return_multiplier) == (
            "won",
            pytest.approx(1.8),
        )
        assert [leg.result for leg in slips["void_won"].legs] == ["void", "win"]
        assert slips["review"].status == "open" and slips["review"].legs[0].result == "pending"
        review_match = s.get_one(ValueLeg, slips["review"].legs[0].value_leg_id).match_id
        assert s.get_one(Match, review_match).status == "needs_review"  # never settled
        assert slips["recent"].status == "open"
        # SportyBet-only results are written back as the match result
        m1 = s.get_one(Match, s.get_one(ValueLeg, slips["won"].legs[0].value_leg_id).match_id)
        assert (m1.home_goals, m1.away_goals, m1.status) == (3, 1, "finished")

        legs_view = s.execute(
            text("SELECT result, profit, league FROM v_leg_performance ORDER BY slip_leg_id")
        ).all()
        slips_view = s.execute(
            text("SELECT slip_type, status, profit FROM v_slip_performance ORDER BY slip_id")
        ).all()
    assert len(legs_view) == 5 and all(r[2] == "EPL" for r in legs_view)
    assert {r[1] for r in slips_view} == {"won", "lost"}
    assert len(slips_view) == 3
    assert ctx.summary["legs_settled"] == 5


def test_update_leg_clv_same_line_only(migrated_db: Path) -> None:
    with session_scope(migrated_db) as s:
        sync_leagues(s, get_settings())
        s.flush()
        lg = s.scalars(select(League).where(League.key == "EPL")).one()
        run = ModelRun(
            league_id=lg.id,
            model_version="t",
            fitted_at=utcnow(),
            train_from=utcnow().date(),
            train_to=utcnow().date(),
            params_json="{}",
            converged=True,
            neg_log_lik=0,
            n_matches=0,
        )
        s.add(run)
        s.flush()
        slip = _slip(s, "daily_2odds")
        m = _mk_leg(s, lg, run, slip, 1, "OU", 2.5, "over", 1.90, -1)  # kicks off in 1 h
        m2 = _mk_leg(s, lg, run, slip, 2, "OU", 2.5, "over", 2.00, -1)
        s.flush()
        for match, line, odds in ((m, 2.5, 1.80), (m2, 3.5, 1.50)):  # m2: only another line
            s.add(
                OddsSnapshot(
                    match_id=match.id,
                    captured_at=utcnow(),
                    snapshot_kind="close",
                    market="OU",
                    line=line,
                    selection="over",
                    odds=odds,
                    sb_market_id="18",
                    sb_specifier="x",
                    sb_outcome_id="12",
                    is_active=True,
                )
            )
        s.flush()
        assert update_leg_clv(s) == 1
        a, b = slip.legs
        assert a.closing_odds == 1.80 and a.clv == pytest.approx(0.0556, abs=1e-4)
        assert b.closing_odds is None and b.clv is None
