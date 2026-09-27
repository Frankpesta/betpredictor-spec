"""Seed a **synthetic demo** database for dashboard checks (docs/07 §3).

    uv run python tests/fixtures/seed_demo.py <demo.db>                  # seed a migrated DB
    uv run python tests/fixtures/seed_demo.py --fresh <demo.db>          # recreate, migrate, seed
    uv run python tests/fixtures/seed_demo.py --fresh --empty <x.db>     # recreate + migrate only

The target must be a freshly migrated, empty DB; the script refuses to seed a DB
that already has matches and `--fresh` refuses to delete the configured real DB,
so it can never pollute `data/betpredictor.db`. Everything is clearly marked demo:
team names start with "DEMO", model_version is "demo", booking codes start with "DEMO".

Probabilities come from the real engine maths (score_matrix, price_selection,
evaluate_pair) on made-up ratings, so the numbers are internally consistent — but
they describe no real match.
"""

from __future__ import annotations

import json
import random
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from engine.config import Settings, get_settings
from engine.db.models import (
    BacktestRun,
    JobRun,
    League,
    Match,
    ModelRun,
    OddsSnapshot,
    Prediction,
    Slip,
    SlipLeg,
    Team,
    UnresolvedName,
    ValueLeg,
)
from engine.db.session import session_scope
from engine.jobs import sync_leagues
from engine.model.markets import price_selection, result_multiplier, settle
from engine.model.score_matrix import score_matrix, to_json_list
from engine.settle.settle import slip_outcome
from engine.value.edge import evaluate_pair

DEMO_VERSION = "demo"
LAGOS = ZoneInfo("Africa/Lagos")
LINES = {"AH": (-1.5, -0.5, 0.5), "OU": (1.5, 2.5, 3.5)}
PAIRS = {"AH": ("home", "away"), "OU": ("over", "under")}
DEMO_TEAMS = {
    "EPL": ["DEMO Albion", "DEMO Rovers", "DEMO United", "DEMO City", "DEMO Athletic", "DEMO Town"],
    "INTL": ["DEMO Northland", "DEMO Southland", "DEMO Eastland", "DEMO Westland"],
}


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)


def _book_odds(p: float, rng: random.Random, margin: float = 0.06) -> tuple[float, float]:
    """A two-way price around a noisy 'true' probability, with a bookmaker margin."""
    q = min(max(p + rng.uniform(-0.06, 0.06), 0.05), 0.95)
    return round(1 / (q * (1 + margin / 2)), 2), round(1 / ((1 - q) * (1 + margin / 2)), 2)


def seed(
    db_path: Path, settings: Settings | None = None, now: datetime | None = None
) -> dict[str, int]:
    settings = settings or get_settings()
    now = now or datetime.now(UTC)
    rng = random.Random(20260927)
    with session_scope(db_path) as s:
        if s.scalar(select(func.count()).select_from(Match)):
            raise SystemExit(f"{db_path} already has matches; seed_demo only fills an empty DB")
        sync_leagues(s, settings)
        s.flush()
        counts = _seed(s, settings, rng, now)
    return counts


def _seed(s: Session, settings: Settings, rng: random.Random, now: datetime) -> dict[str, int]:
    counts = {"matches": 0, "value_legs": 0, "slips": 0}
    leagues = {lg.key: lg for lg in s.scalars(select(League))}
    runs: dict[str, tuple[ModelRun, dict[int, tuple[float, float]], float, float]] = {}

    for key, names in DEMO_TEAMS.items():
        lg = leagues[key]
        teams = [Team(league_id=lg.id, canonical_name=n) for n in names]
        s.add_all(teams)
        s.flush()
        ratings = {t.id: (rng.uniform(-0.4, 0.4), rng.uniform(-0.3, 0.3)) for t in teams}
        home_adv = 0.0 if key == "INTL" else 0.25
        rho = -0.08
        params = {
            "goals": {
                "att": [a for a, _ in ratings.values()],
                "def": [d for _, d in ratings.values()],
                "teams": list(ratings),
                "home_adv": home_adv,
                "rho": rho,
                "gamma": 0.1,
            },
            "xg": None,
            "team_names": {str(t.id): t.canonical_name for t in teams},
            "matches_per_team": {str(t.id): 60 for t in teams},
            "low_confidence": [],
            "demo": True,
        }
        run = ModelRun(
            league_id=lg.id,
            model_version=DEMO_VERSION,
            fitted_at=_utc(now - timedelta(days=1)),
            train_from=(now - timedelta(days=3 * 365)).date(),
            train_to=(now - timedelta(days=1)).date(),
            params_json=json.dumps(params),
            converged=True,
            neg_log_lik=1234.5,
            n_matches=540,
        )
        s.add(run)
        s.flush()
        runs[key] = (run, ratings, home_adv, rho)

    # Past (settled) and upcoming matches.
    for key, (run, ratings, home_adv, rho) in runs.items():
        ids = list(ratings)
        for slot, day in enumerate((-6, -4, -2, 1, 2)):
            for i in range(0, len(ids) - 1, 2):
                h, a = ids[(i + slot) % len(ids)], ids[(i + 1 + slot) % len(ids)]
                kickoff = (now + timedelta(days=day)).replace(minute=0, second=0, microsecond=0)
                kickoff = kickoff.replace(hour=15 + i % 3)
                _match(
                    s,
                    settings,
                    rng,
                    key,
                    leagues[key],
                    run,
                    ratings,
                    home_adv,
                    rho,
                    h,
                    a,
                    kickoff,
                    finished=day < 0,
                    counts=counts,
                )

    # A match whose two result sources disagreed (shown on Data health).
    first = s.scalars(select(Match).where(Match.status == "finished")).first()
    if first is not None:
        first.status = "needs_review"

    _slips(s, settings, now, counts)
    _bookkeeping(s, now)
    return counts


def _match(
    s: Session,
    settings: Settings,
    rng: random.Random,
    key: str,
    league: League,
    run: ModelRun,
    ratings: dict[int, tuple[float, float]],
    home_adv: float,
    rho: float,
    h: int,
    a: int,
    kickoff: datetime,
    *,
    finished: bool,
    counts: dict[str, int],
) -> None:
    lam = float(round(pow(2.718281828, 0.2 + ratings[h][0] + ratings[a][1] + home_adv), 3))
    mu = float(round(pow(2.718281828, 0.1 + ratings[a][0] + ratings[h][1]), 3))
    mat = score_matrix(lam, mu, rho, settings.model.max_goals)
    m = Match(
        league_id=league.id,
        season="2026-27",
        kickoff_utc=_utc(kickoff),
        home_team_id=h,
        away_team_id=a,
        status="scheduled",
        neutral=key == "INTL",
        fixture_key=kickoff.date().isoformat(),  # demo pairings may repeat within a season
        sportybet_event_id=f"demo:{key}:{h}:{a}:{kickoff:%Y%m%d}",
    )
    if finished:
        # Sample a plausible score from the model's own matrix.
        flat = [(i, j, float(mat[i, j])) for i in range(8) for j in range(8)]
        r, acc = rng.random() * sum(p for *_, p in flat), 0.0
        for i, j, p in flat:
            acc += p
            if acc >= r:
                m.home_goals, m.away_goals = i, j
                break
        m.status = "finished"
    s.add(m)
    s.flush()
    counts["matches"] += 1
    pred = Prediction(
        match_id=m.id,
        model_run_id=run.id,
        lambda_home=lam,
        lambda_away=mu,
        score_matrix_json=json.dumps(to_json_list(mat)),
    )
    s.add(pred)
    s.flush()
    captured = _utc(kickoff - timedelta(hours=20))
    v = settings.value
    for market, lines in LINES.items():
        sel_a, sel_b = PAIRS[market]
        for line in lines:
            pa = price_selection(mat, market, sel_a, line)  # type: ignore[arg-type]
            pb = price_selection(mat, market, sel_b, line)  # type: ignore[arg-type]
            oa, ob = _book_odds(pa.p_win, rng)
            snaps = []
            for sel, odds, spec_line in ((sel_a, oa, line), (sel_b, ob, line)):
                snap = OddsSnapshot(
                    match_id=m.id,
                    captured_at=captured,
                    snapshot_kind="pick",
                    market=market,
                    line=spec_line,
                    selection=sel,
                    odds=odds,
                    sb_market_id="16" if market == "AH" else "18",
                    sb_specifier=f"hcp={line}" if market == "AH" else f"total={line}",
                    sb_outcome_id=sel,
                    is_active=True,
                )
                s.add(snap)
                snaps.append(snap)
            s.flush()
            vals = evaluate_pair(
                line,
                (sel_a, sel_b),
                (pa, pb),
                (oa, ob),
                low_confidence=False,
                minutes_to_kickoff=None,
                stale_prediction=False,
                shrink_weight=v.market_shrink_weight,
                min_edge=v.min_edge_leg,
                min_odds=v.min_odds,
                max_odds=v.max_odds,
                max_model_market_gap=v.max_model_market_gap,
            )
            for lv, snap in zip(vals, snaps, strict=True):
                s.add(
                    ValueLeg(
                        match_id=m.id,
                        prediction_id=pred.id,
                        odds_snapshot_id=snap.id,
                        market=market,
                        line=snap.line,
                        selection=lv.selection,
                        odds=lv.odds,
                        p_model=lv.p_model,
                        p_final=lv.p_final,
                        p_market_devig=lv.p_market_devig,
                        p_win=lv.p_final,
                        p_half_win=0.0,
                        p_push=0.0,
                        p_half_loss=0.0,
                        p_loss=1 - lv.p_final,
                        expected_multiplier=lv.expected_multiplier,
                        edge=lv.edge,
                        sanity_status="flagged" if lv.reasons else "ok",
                        sanity_reason=",".join(lv.reasons) or None,
                    )
                )
                counts["value_legs"] += 1


def _slips(s: Session, settings: Settings, now: datetime, counts: dict[str, int]) -> None:
    """One slip of each type per pool/day, built naively from the best legs (demo only)."""
    legs = list(
        s.execute(
            select(ValueLeg, Match, League.key)
            .join(Match, Match.id == ValueLeg.match_id)
            .join(League, League.id == Match.league_id)
            .where(ValueLeg.sanity_status == "ok")
            .order_by(ValueLeg.p_final.desc(), ValueLeg.id)
        )
    )
    by_day: dict[tuple[date, str], list[tuple[ValueLeg, Match]]] = {}
    for vl, m, key in legs:
        pool = "intl" if key == "INTL" else "club"
        d = m.kickoff_utc.astimezone(LAGOS).date()
        by_day.setdefault((d, pool), []).append((vl, m))
    today = now.astimezone(LAGOS).date()
    booking_cycle = ["booked", "failed", "manual", "pending"]
    n = 0
    for (d, pool), day_legs in sorted(by_day.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        seen: set[int] = set()
        best: list[tuple[ValueLeg, Match]] = []
        for vl, m in day_legs:
            if m.id not in seen and 1.2 <= vl.odds <= 2.6:
                seen.add(m.id)
                best.append((vl, m))
        for slip_type, k in (("daily_2odds", 2), ("mid_acca", 4), ("mega_acca", 6)):
            chosen = best[:k]
            if len(chosen) < 2:
                continue
            total = p_all = em = 1.0
            for vl, _ in chosen:
                total *= vl.odds
                p_all *= vl.p_final
                em *= vl.expected_multiplier
            start = min(m.kickoff_utc for _, m in chosen)
            end = max(m.kickoff_utc for _, m in chosen)
            status = booking_cycle[n % len(booking_cycle)] if d >= today else "booked"
            slip = Slip(
                slip_type=slip_type,
                pool=pool,
                slip_date=d,
                window_start_utc=start,
                window_end_utc=end,
                total_odds=round(total, 4),
                p_all_win=p_all,
                expected_multiplier=em,
                booking_status=status,
                booking_code=f"DEMO{n:02d}" if status in ("booked", "manual") else None,
                booking_error="demo: SportyBet rejected a selection"
                if status == "failed"
                else None,
                mode="placed" if (n % 5 == 0 and d < today) else "paper",
                stake=1000.0 if (n % 5 == 0 and d < today) else None,
                model_version=DEMO_VERSION,
            )
            s.add(slip)
            s.flush()
            leg_results = []
            for order, (vl, m) in enumerate(chosen, start=1):
                sl = SlipLeg(slip_id=slip.id, value_leg_id=vl.id, leg_order=order, result="pending")
                if m.status == "finished" and m.home_goals is not None and m.away_goals is not None:
                    res = settle(vl.market, vl.selection, vl.line, m.home_goals, m.away_goals)  # type: ignore[arg-type]
                    sl.result, sl.result_multiplier = res, result_multiplier(res, vl.odds)
                    sl.closing_odds = round(vl.odds * (0.97 + 0.05 * ((vl.id * 7) % 10) / 10), 2)
                    sl.clv = vl.odds / sl.closing_odds - 1
                leg_results.append((sl.result, sl.result_multiplier))
                s.add(sl)
            out = slip_outcome(leg_results)
            slip.status, slip.return_multiplier = out.status, out.return_multiplier
            n += 1
            counts["slips"] += 1


def _bookkeeping(s: Session, now: datetime) -> None:
    started = _utc(now - timedelta(hours=1))
    s.add(
        BacktestRun(
            started_at=started,
            finished_at=started + timedelta(seconds=40),
            config_json=json.dumps({"demo": True}),
            metrics_json=json.dumps(
                {
                    "demo": True,
                    "gate": {
                        "passed": False,
                        "bets": 34,
                        "ece": 0.013,
                        "roi": -0.08,
                        "mean_clv": 0.009,
                        "failures": ["demo: bets 34 < 400", "demo: ROI -0.08 <= 0.0"],
                    },
                    "holdout": {
                        "overall": {
                            "ALL": {
                                "n_priced": 900,
                                "bets": 34,
                                "log_loss": 0.69,
                                "baseline_log_loss_open": 0.688,
                                "ece": 0.013,
                                "roi": -0.08,
                                "mean_clv": 0.009,
                            }
                        }
                    },
                }
            ),
            gate_passed=False,
            report_path="data/reports/DEMO_backtest.md",
        )
    )
    for i, (name, status) in enumerate(
        [("odds", "success"), ("picks", "success"), ("book", "failed"), ("settle", "success")]
    ):
        t = _utc(now - timedelta(minutes=50 - i * 10))
        s.add(
            JobRun(
                job_name=name,
                started_at=t,
                finished_at=t + timedelta(seconds=30),
                status=status,
                summary_json=json.dumps({"demo": True, "count": i}),
                error="demo: Traceback (most recent call last): ...\nRuntimeError: demo failure"
                if status == "failed"
                else None,
            )
        )
    s.add(
        UnresolvedName(
            source="sportybet",
            raw_name="DEMO Rovrs FC",
            league_key="EPL",
            first_seen=_utc(now),
            last_seen=_utc(now),
        )
    )


def recreate(db_path: Path) -> None:
    """Delete `db_path` (never the configured real DB) and migrate it to head."""
    from alembic import command
    from alembic.config import Config

    from engine.config import ROOT
    from engine.db.session import get_engine, sqlite_url

    if db_path.resolve() == get_settings().db_file.resolve():
        raise SystemExit(f"refusing to recreate the real database {db_path}")
    get_engine(db_path).dispose()
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = Config(str(ROOT / "engine" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "engine" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", sqlite_url(db_path))
    cfg.attributes["configure_logger"] = False
    command.upgrade(cfg, "head")


def main(argv: list[str]) -> int:
    flags = {a for a in argv if a.startswith("--")}
    paths = [a for a in argv if not a.startswith("--")]
    if len(paths) != 1 or not flags <= {"--fresh", "--empty"}:
        print(__doc__)
        return 2
    db_path = Path(paths[0]).resolve()
    if "--fresh" in flags:
        recreate(db_path)
    if "--empty" in flags:
        print(f"migrated empty DB {paths[0]}")
        return 0
    counts = seed(db_path)
    print(f"seeded demo DB {paths[0]}: {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
