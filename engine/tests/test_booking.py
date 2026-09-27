"""Booking (docs/04 §3, docs/08 Phase 5): drift rule, echo check, job behaviour. No network."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

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
from engine.sportybet import booking as bk
from engine.sportybet.client import OUTCOMES, SHARE, SportyBetClient
from engine.sportybet.markets import LiveOutcome, parse_outcomes

FIX = Path(__file__).parent / "fixtures" / "sportybet"
NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)


def _leg(n: int, odds: float, hours: float = 5) -> bk.BookingLeg:
    return bk.BookingLeg(
        event_id=f"sr:match:{n}",
        market_id="18",
        specifier="total=2.5",
        outcome_id="12",
        odds_at_pick=odds,
        kickoff_utc=NOW + timedelta(hours=hours),
        label=f"M{n} OU +2.5 over @{odds}",
    )


def _outcomes(
    legs: list[bk.BookingLeg], odds: list[float], active: list[int] | None = None
) -> dict[str, Any]:
    active = active or [1] * len(legs)
    return {
        "bizCode": 10000,
        "message": "0#0",
        "data": [
            {
                "eventId": leg.event_id,
                "status": 0,
                "markets": [
                    {
                        "id": leg.market_id,
                        "specifier": leg.specifier,
                        "status": 0,
                        "outcomes": [{"id": leg.outcome_id, "odds": f"{o:.2f}", "isActive": a}],
                    }
                ],
            }
            for leg, o, a in zip(legs, odds, active, strict=True)
        ],
    }


def _share(legs: list[bk.BookingLeg], code: str = "ABC123") -> dict[str, Any]:
    return {
        "bizCode": 10000,
        "message": "Success",
        "data": {"shareCode": code, "ticket": {"selections": [leg.selection() for leg in legs]}},
    }


class FakeTransport:
    def __init__(self, replies: dict[str, list[dict[str, Any]]]) -> None:
        self.replies = replies
        self.calls: list[str] = []

    def post_json(self, path: str, body: Any, cache_name: str) -> dict[str, Any]:
        self.calls.append(path)
        return self.replies[path].pop(0)

    def get_json(self, path: str, params: dict[str, str], cache_name: str) -> dict[str, Any]:
        raise AssertionError("booking never GETs")

    def close(self) -> None:
        pass


def test_outcomes_parser_on_real_payload() -> None:
    live = parse_outcomes(
        json.loads((FIX / "outcomes_arsenal_leeds.json").read_text(encoding="utf-8"))
    )
    assert live[("sr:match:72221292", "16", "hcp=-0.5", "1714")] == LiveOutcome(1.41, True)


@pytest.mark.parametrize(
    ("now_odds", "active", "problem"),
    [
        (1.90, 1, None),
        (1.957, 1, None),
        (1.843, 1, None),
        (1.96, 1, "+3.2%"),
        (1.80, 1, "-5.3%"),
        (1.90, 0, "suspended"),
    ],
)
def test_drift_rule(now_odds: float, active: int, problem: str | None) -> None:
    leg = _leg(1, 1.90)
    live = {leg.key: LiveOutcome(now_odds, bool(active))}
    got = bk.drift_problems([leg], live, 0.03)
    if problem is None:
        assert got == []
    else:
        assert len(got) == 1 and problem in got[0]


def test_missing_selection_is_a_problem() -> None:
    assert "no longer offered" in bk.drift_problems([_leg(1, 1.9)], {}, 0.03)[0]


def test_book_legs_success_sends_exact_selections() -> None:
    legs = [_leg(1, 1.50), _leg(2, 1.45)]
    t = FakeTransport({OUTCOMES: [_outcomes(legs, [1.50, 1.46])], SHARE: [_share(legs)]})
    assert bk.book_legs(SportyBetClient(t), legs, "x", 0.03, NOW) == "ABC123"
    assert t.calls == [OUTCOMES, SHARE]


def test_drifted_leg_fails_without_booking_attempt() -> None:
    legs = [_leg(1, 1.50), _leg(2, 1.45)]
    t = FakeTransport({OUTCOMES: [_outcomes(legs, [1.50, 1.30])], SHARE: [_share(legs)]})
    with pytest.raises(bk.OddsDriftError, match=r"odds moved: M2 .* pick 1.45 now 1.30"):
        bk.book_legs(SportyBetClient(t), legs, "x", 0.03, NOW)
    assert t.calls == [OUTCOMES]  # orders/share never called


def test_echo_mismatch_is_refused() -> None:
    legs = [_leg(1, 1.50), _leg(2, 1.45)]
    t = FakeTransport({OUTCOMES: [_outcomes(legs, [1.50, 1.45])], SHARE: [_share(legs[:1])]})
    with pytest.raises(bk.BookingError, match="does not contain exactly"):
        bk.book_legs(SportyBetClient(t), legs, "x", 0.03, NOW)


def test_started_leg_fails_before_any_request() -> None:
    t = FakeTransport({})
    with pytest.raises(bk.BookingError, match="already started"):
        bk.book_legs(SportyBetClient(t), [_leg(1, 1.5, hours=0.1)], "x", 0.03, NOW)
    assert t.calls == []


# ---- make book on a temp DB -------------------------------------------------------


def _slip_with_legs(s: Any, league: League, n0: int, odds: list[float]) -> int:
    run = ModelRun(
        league_id=league.id,
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
    slip = Slip(
        slip_type="daily_2odds",
        pool="club",
        slip_date=utcnow().date(),
        window_start_utc=utcnow(),
        window_end_utc=utcnow() + timedelta(days=1),
        total_odds=1.0,
        p_all_win=0.5,
        expected_multiplier=1.1,
        model_version="t",
    )
    for i, o in enumerate(odds):
        h = Team(league_id=league.id, canonical_name=f"H{n0 + i}")
        a = Team(league_id=league.id, canonical_name=f"A{n0 + i}")
        s.add_all([h, a])
        s.flush()
        m = Match(
            league_id=league.id,
            season="2026-27",
            kickoff_utc=utcnow() + timedelta(hours=6),
            home_team_id=h.id,
            away_team_id=a.id,
            status="scheduled",
            sportybet_event_id=f"sr:match:{n0 + i}",
        )
        s.add(m)
        s.flush()
        snap = OddsSnapshot(
            match_id=m.id,
            captured_at=utcnow(),
            snapshot_kind="pick",
            market="OU",
            line=2.5,
            selection="over",
            odds=o,
            sb_market_id="18",
            sb_specifier="total=2.5",
            sb_outcome_id="12",
            is_active=True,
        )
        pred = Prediction(
            match_id=m.id,
            model_run_id=run.id,
            lambda_home=1.4,
            lambda_away=1.1,
            score_matrix_json="[]",
        )
        s.add_all([snap, pred])
        s.flush()
        vl = ValueLeg(
            match_id=m.id,
            prediction_id=pred.id,
            odds_snapshot_id=snap.id,
            market="OU",
            line=2.5,
            selection="over",
            odds=o,
            p_model=0.7,
            p_final=0.7,
            p_market_devig=0.65,
            p_win=0.7,
            p_half_win=0,
            p_push=0,
            p_half_loss=0,
            p_loss=0.3,
            expected_multiplier=0.7 * o,
            edge=0.7 * o - 1,
            sanity_status="ok",
        )
        s.add(vl)
        s.flush()
        slip.legs.append(SlipLeg(value_leg_id=vl.id, leg_order=i + 1))
    s.add(slip)
    s.flush()
    return slip.id


def test_make_book_books_good_slip_and_fails_drifted_one(
    migrated_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with session_scope(migrated_db) as s:
        sync_leagues(s, get_settings())
        s.flush()
        lg = s.scalars(select(League).where(League.key == "EPL")).one()
        good = _slip_with_legs(s, lg, 100, [1.50, 1.45])
        drifted = _slip_with_legs(s, lg, 200, [1.50, 1.45])

    def legs_of(n0: int) -> list[bk.BookingLeg]:
        return [_leg(n0, 1.50), _leg(n0 + 1, 1.45)]

    fake = FakeTransport(
        {
            OUTCOMES: [
                _outcomes(legs_of(100), [1.50, 1.45]),
                _outcomes(legs_of(200), [1.50, 1.20]),
            ],
            SHARE: [_share(legs_of(100), "GOOD01")],
        }
    )
    monkeypatch.setattr(bk, "make_transport", lambda settings, kind="httpx": fake)
    with job_run("book", get_settings(), migrated_db) as ctx:
        out = bk.run_book(ctx)
    assert "booked GOOD01" in out and "FAILED odds moved" in out
    assert fake.calls == [OUTCOMES, SHARE, OUTCOMES]  # no share call for the drifted slip
    with session_scope(migrated_db) as s:
        g, d = s.get_one(Slip, good), s.get_one(Slip, drifted)
        assert (g.booking_status, g.booking_code, g.booking_error) == ("booked", "GOOD01", None)
        assert d.booking_status == "failed" and d.booking_code is None
        assert (
            d.booking_error is not None
            and "odds moved" in d.booking_error
            and "1.45 now 1.20" in d.booking_error
        )
    assert (ctx.summary["booked"], ctx.summary["failed"]) == (1, 1)
