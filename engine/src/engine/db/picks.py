"""`make picks`: predictions -> value legs -> slips (docs/05 §1-6, docs/09 §5).

Per enabled competition: make sure a fresh model run exists (refit if it is older than
MAX_MODEL_AGE or older than the newest finished match), store predictions for every
scheduled match within `general.horizon_hours`, price every (market, line) pair of the latest odds
snapshot with the shared value pipeline, and write every selection to `value_legs`.
Slips are then built per pool — club legs and INTL legs are never mixed.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from engine.config import LEAGUES, LeagueDef, Settings
from engine.db.base import utcnow
from engine.db.model_runs import fit_and_store
from engine.db.models import (
    League,
    Match,
    ModelRun,
    OddsSnapshot,
    Prediction,
    Slip,
    SlipLeg,
    ValueLeg,
)
from engine.db.session import session_scope
from engine.logging import get_logger
from engine.model.half_time import HalfTimeParams, half_matrix
from engine.model.league import StoredModel
from engine.model.markets import (
    SELECTIONS_BY_MARKET,
    Market,
    OutcomeProbs,
    Period,
    Selection,
    is_binary,
    price_selection,
    split_period,
)
from engine.model.score_matrix import AbsurdRatesError, to_json_list
from engine.slips.builder import (
    MID_WINDOW,
    ChosenSlip,
    daily_2odds,
    mega_acca,
    mid_acca,
    weekend_window,
)
from engine.slips.constraints import SPORTYBET_MAX_SELECTIONS, Leg
from engine.value.edge import LegValue, evaluate_group
from engine.value.selection import (
    TeamData,
    favourite_side,
    p_scores,
    qualifies_data_rule,
    qualifies_likeliest,
    team_data,
)

if TYPE_CHECKING:
    from engine.jobs import JobContext

log = get_logger(__name__)

MAX_MODEL_AGE = timedelta(days=7)  # docs/05 §1.1
HALF_LABEL = {"1H": "1st half", "2H": "2nd half"}
STALE_ODDS_AGE = timedelta(hours=3)  # docs/05 §1.3
PAIRS = SELECTIONS_BY_MARKET  # every selection of a market, in group order (docs/05 §10)


@dataclass
class PicksReport:
    refits: list[str] = field(default_factory=list)
    predictions_created: int = 0
    legs_priced: int = 0
    flagged: Counter[str] = field(default_factory=Counter)
    qualifying: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    stale_odds_matches: list[str] = field(default_factory=list)
    legs_by_pool: dict[str, list[tuple[Leg, int]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    slips: dict[str, str] = field(default_factory=dict)
    # (team, period) -> the team's goals in that period of its recent matches
    goals_for: dict[tuple[int, str], list[int]] = field(default_factory=dict)


def recent_goals_for(
    session: Session,
    team_id: int,
    n: int,
    before: datetime,
    report: PicksReport,
    period: Period = "FT",
) -> list[int]:
    """docs/05 §9 / docs/10: the team's goals in its last `n` finished matches (newest
    first), full time or in one half (then only matches with half-time goals), cached."""
    key = (team_id, period)
    if key not in report.goals_for:
        q = select(
            Match.home_team_id,
            Match.home_goals,
            Match.away_goals,
            Match.ht_home_goals,
            Match.ht_away_goals,
        ).where(
            or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
            Match.status == "finished",
            Match.home_goals.is_not(None),
            Match.away_goals.is_not(None),
            Match.kickoff_utc < before,
        )
        if period != "FT":
            q = q.where(Match.ht_home_goals.is_not(None), Match.ht_away_goals.is_not(None))
        rows = session.execute(q.order_by(Match.kickoff_utc.desc(), Match.id.desc()).limit(n)).all()
        goals: list[int] = []
        for home, hg, ag, hth, hta in rows:
            ft, ht = (hg, hth) if home == team_id else (ag, hta)
            assert ft is not None  # filtered in the query
            if period == "FT":
                goals.append(ft)
            else:
                assert ht is not None
                goals.append(ht if period == "1H" else ft - ht)
        report.goals_for[key] = goals
    return report.goals_for[key]


def _price(mat: Any, market: str, selection: str, line: float) -> OutcomeProbs:
    """Stored market/selection strings are validated by DB CHECKs; narrow them for pricing."""
    return price_selection(mat, cast(Market, market), cast(Selection, selection), line)


def latest_run(session: Session, league_id: int) -> ModelRun | None:
    return session.scalars(
        select(ModelRun)
        .where(ModelRun.league_id == league_id, ModelRun.converged.is_(True))
        .order_by(ModelRun.fitted_at.desc(), ModelRun.id.desc())
        .limit(1)
    ).one_or_none()


def ensure_fresh_run(
    session: Session, league: League, settings: Settings, now: datetime, report: PicksReport
) -> ModelRun:
    run = latest_run(session, league.id)
    newest = session.scalar(
        select(func.max(Match.kickoff_utc)).where(
            Match.league_id == league.id, Match.status == "finished"
        )
    )
    stale = (
        run is None
        or run.fitted_at < now - MAX_MODEL_AGE
        or (newest is not None and run.fitted_at < newest)
        # docs/10: club runs saved before the half-time model have no "half_time" key
        or (not LEAGUES[league.key].international and "half_time" not in run.params_json)
    )
    if stale:
        run = fit_and_store(session, league, settings, now).run
        report.refits.append(league.key)
        log.info(
            "picks: refitted stale model", extra={"fields": {"league": league.key, "run": run.id}}
        )
    assert run is not None
    return run


def _prediction(
    session: Session,
    match: Match,
    run: ModelRun,
    model: StoredModel,
    settings: Settings,
    report: PicksReport,
) -> tuple[Prediction, Any] | None:
    if not (model.has_team(match.home_team_id) and model.has_team(match.away_team_id)):
        report.skipped["no_model_team"] += 1
        return None
    try:
        mat = model.matrix(
            match.home_team_id,
            match.away_team_id,
            settings.model.xg_blend_weight,
            settings.model.max_goals,
            match.neutral,
        )
    except AbsurdRatesError as exc:
        report.skipped["absurd_rates"] += 1
        log.warning(
            "picks: absurd rates, match skipped",
            extra={"fields": {"match_id": match.id, "error": str(exc)}},
        )
        return None
    pred = session.scalars(
        select(Prediction).where(Prediction.match_id == match.id, Prediction.model_run_id == run.id)
    ).one_or_none()
    if pred is None:
        lam, mu = model.rates(
            match.home_team_id, match.away_team_id, settings.model.xg_blend_weight, match.neutral
        )
        pred = Prediction(
            match_id=match.id,
            model_run_id=run.id,
            lambda_home=lam,
            lambda_away=mu,
            score_matrix_json=json.dumps(to_json_list(mat)),
        )
        session.add(pred)
        session.flush()
        report.predictions_created += 1
    return pred, mat


def price_match(
    session: Session,
    lg: LeagueDef,
    match: Match,
    run: ModelRun,
    model: StoredModel,
    settings: Settings,
    now: datetime,
    report: PicksReport,
) -> None:
    latest = session.scalar(
        select(func.max(OddsSnapshot.captured_at)).where(
            OddsSnapshot.match_id == match.id, OddsSnapshot.snapshot_kind == "pick"
        )
    )
    if latest is None:
        report.skipped["no_odds"] += 1
        return
    if latest < now - STALE_ODDS_AGE:
        report.stale_odds_matches.append(f"{match.id}")
    got = _prediction(session, match, run, model, settings, report)
    if got is None:
        return
    pred, mat = got
    snaps = session.scalars(
        select(OddsSnapshot).where(
            OddsSnapshot.match_id == match.id,
            OddsSnapshot.snapshot_kind == "pick",
            OddsSnapshot.captured_at == latest,
        )
    ).all()
    by_key: dict[tuple[str, float], dict[str, OddsSnapshot]] = defaultdict(dict)
    for sn in snaps:
        by_key[(sn.market, sn.line)][sn.selection] = sn
    low = match.home_team_id in model.low_confidence or match.away_team_id in model.low_confidence
    minutes = (match.kickoff_utc - now).total_seconds() / 60.0
    stale_pred = pred.model_run_id != run.id
    v = settings.value
    pool = "intl" if lg.international else "club"
    priced: list[tuple[str, float, OddsSnapshot, LegValue]] = []
    mats: dict[Period, Any] = {"FT": mat}
    ht = None if model.half_time is None else HalfTimeParams.from_json(model.half_time)
    if ht is not None:
        lam, mu = model.rates(
            match.home_team_id, match.away_team_id, settings.model.xg_blend_weight, match.neutral
        )
        mats["1H"] = half_matrix(lam, mu, ht, 1, settings.model.max_goals)
        mats["2H"] = half_matrix(lam, mu, ht, 2, settings.model.max_goals)
    for (market, line), sides in sorted(by_key.items()):
        if market not in v.markets:
            report.skipped["market_not_enabled"] += len(sides)
            continue
        if split_period(market)[1] not in mats:
            report.skipped["no_half_time_model"] += len(sides)  # INTL (docs/10) or too few
            continue
        sels = PAIRS[market]
        if not all(s in sides for s in sels):
            report.skipped["missing_pair"] += len(sides)  # odds.py stores groups; defensive
            continue
        snaps_k = tuple(sides[s] for s in sels)
        legs = evaluate_group(
            market,
            line,
            sels,
            tuple(_price(mats[split_period(market)[1]], market, s, line) for s in sels),
            tuple(sn.odds for sn in snaps_k),
            low_confidence=low,
            minutes_to_kickoff=minutes,
            stale_prediction=stale_pred,
            shrink_weight=v.market_shrink_weight,
            min_edge=v.min_edge_leg,
            min_odds=v.min_odds,
            max_odds=v.max_odds,
            max_model_market_gap=v.max_model_market_gap,
        )
        priced += [(market, line, sn, lv) for sn, lv in zip(snaps_k, legs, strict=True)]
    # docs/05 §8: the favourite comes from the blended AH probabilities of this batch
    favourite = favourite_side(
        {
            line: lv.p_final
            for market, line, _, lv in priced
            if market == "AH" and lv.selection == "home"
        }
    )
    teams: dict[Period, dict[str, TeamData]] = {}
    if v.selection == "data_rule":
        for period in sorted({split_period(m)[1] for m, *_ in priced}):
            cfg = v.data_rule if period == "FT" else v.data_rule_half
            pmat = mats[period]
            teams[period] = {
                side: team_data(
                    p, recent_goals_for(session, tid, cfg.form_games, now, report, period)
                )
                for side, tid, p in zip(
                    ("home", "away"),
                    (match.home_team_id, match.away_team_id),
                    p_scores(pmat),
                    strict=True,
                )
            }
    for market, line, snap, lv in priced:
        why: str | None = None
        base, period = split_period(market)
        if v.selection == "data_rule":
            why = qualifies_data_rule(
                lv.reasons,
                base,
                line,
                lv.selection,
                lv.odds,
                lv.p_final,
                teams[period]["home"],
                teams[period]["away"],
                v.data_rule if period == "FT" else v.data_rule_half,
            )
            if why is not None and period != "FT":
                why = f"{HALF_LABEL[period]}: {why}"
            ok = why is not None
        elif v.selection == "likeliest":
            ok = qualifies_likeliest(lv.reasons, market, lv.selection, favourite)
        else:
            ok = lv.qualifies
        probs = _price(mats[period], market, lv.selection, line)
        half = is_binary(market, line)
        row = ValueLeg(
            match_id=match.id,
            prediction_id=pred.id,
            odds_snapshot_id=snap.id,
            market=market,
            line=line,
            selection=lv.selection,
            odds=lv.odds,
            p_model=lv.p_model,
            p_final=lv.p_final,
            p_market_devig=lv.p_market_devig,
            # half lines: outcome distribution is (p_final, 0, 0, 0, 1 - p_final)
            p_win=lv.p_final if half else probs.p_win,
            p_half_win=0.0 if half else probs.p_half_win,
            p_push=0.0 if half else probs.p_push,
            p_half_loss=0.0 if half else probs.p_half_loss,
            p_loss=1.0 - lv.p_final if half else probs.p_loss,
            expected_multiplier=lv.expected_multiplier,
            edge=lv.edge,
            sanity_status="flagged" if lv.reasons else "ok",
            sanity_reason=",".join(lv.reasons) or None,
            qualifies=ok,
            qualify_reason=why,
        )
        session.add(row)
        session.flush()
        report.legs_priced += 1
        for r in lv.reasons:
            report.flagged[r] += 1
        report.qualifying += int(ok)
        report.legs_by_pool[pool].append(
            (
                Leg(
                    match_id=match.id,
                    market=market,
                    line=line,
                    selection=lv.selection,
                    odds=lv.odds,
                    p_final=lv.p_final,
                    expected_multiplier=lv.expected_multiplier,
                    kickoff_utc=match.kickoff_utc,
                    qualifies=ok,
                    sanity_ok=not lv.reasons,
                ),
                row.id,
            )
        )


def _replace_or_keep(
    session: Session, slip_type: str, pool: str, slip_date: Any, strategy: str
) -> bool:
    """Delete this type/pool/date/strategy's pending+open slips.

    Returns True when a booked or settled slip remains (then no new slip is made).
    """
    session.execute(
        delete(Slip).where(
            Slip.slip_type == slip_type,
            Slip.pool == pool,
            Slip.slip_date == slip_date,
            Slip.strategy == strategy,
            Slip.booking_status == "pending",
            Slip.status == "open",
        )
    )
    session.flush()
    return (
        session.scalar(
            select(func.count())
            .select_from(Slip)
            .where(
                Slip.slip_type == slip_type,
                Slip.pool == pool,
                Slip.slip_date == slip_date,
                Slip.strategy == strategy,
            )
        )
        or 0
    ) > 0


def store_slip(
    session: Session,
    slip_type: str,
    pool: str,
    slip_date: Any,
    window: tuple[datetime, datetime],
    chosen: ChosenSlip,
    leg_ids: dict[Leg, int],
    settings: Settings,
) -> Slip:
    slip = Slip(
        slip_type=slip_type,
        pool=pool,
        strategy=settings.value.selection,
        slip_date=slip_date,
        window_start_utc=window[0],
        window_end_utc=window[1],
        total_odds=chosen.totals.total_odds,
        p_all_win=chosen.totals.p_all_win,
        expected_multiplier=chosen.totals.expected_multiplier,
        model_version=settings.model.version,
    )
    for i, leg in enumerate(chosen.legs, 1):
        slip.legs.append(SlipLeg(value_leg_id=leg_ids[leg], leg_order=i))
    session.add(slip)
    session.flush()
    return slip


def build_slips(session: Session, settings: Settings, now: datetime, report: PicksReport) -> None:
    tz = settings.tz
    today = now.astimezone(tz).date()
    sc = settings.slips
    strategy = settings.value.selection
    mega_window = weekend_window(now, sc.mega_acca.weekend_start, sc.mega_acca.weekend_end, tz)
    for pool in ("club", "intl"):
        entries = report.legs_by_pool.get(pool, [])
        legs = [leg for leg, _ in entries]
        ids = {leg: vid for leg, vid in entries}
        plans: list[tuple[str, bool, Any, tuple[datetime, datetime], ChosenSlip | None]] = [
            (
                "daily_2odds",
                sc.daily_2odds.enabled,
                today,
                (now, now + timedelta(hours=sc.daily_2odds.window_hours)),
                daily_2odds(legs, now, sc.daily_2odds, SPORTYBET_MAX_SELECTIONS, strategy),
            ),
            (
                "mid_acca",
                sc.mid_acca.enabled,
                today,
                (now, now + MID_WINDOW),
                mid_acca(legs, now, sc.mid_acca, strategy=strategy),
            ),
            (
                # one mega slip per weekend window: keyed on the window's end date (Monday)
                "mega_acca",
                sc.mega_acca.enabled,
                mega_window[1].astimezone(tz).date(),
                mega_window,
                mega_acca(
                    legs,
                    mega_window,
                    sc.mega_acca,
                    settings.value.min_odds,
                    settings.value.max_odds,
                    strategy=strategy,
                ),
            ),
        ]
        for slip_type, enabled, slip_date, window, chosen in plans:
            key = f"{pool}/{slip_type}"
            if not enabled:
                report.slips[key] = "disabled"
                continue
            if _replace_or_keep(session, slip_type, pool, slip_date, strategy):
                report.slips[key] = "kept existing booked/settled slip"
                continue
            if chosen is None:
                report.slips[key] = {
                    "value": "none — no value",
                    "data_rule": "none — too few legs pass the data rule",
                }.get(strategy, "none — no feasible combination")
                continue
            slip = store_slip(session, slip_type, pool, slip_date, window, chosen, ids, settings)
            t = chosen.totals
            report.slips[key] = (
                f"slip #{slip.id}: {len(chosen.legs)} legs, odds {t.total_odds:.2f}, "
                f"p_all_win {t.p_all_win:.4%}, EM {t.expected_multiplier:.3f}"
            )


def run_picks(ctx: JobContext) -> str:
    s = ctx.settings
    now = utcnow()
    report = PicksReport()
    with session_scope(ctx.db_path) as sess:
        for lg in s.enabled_leagues():
            league = sess.scalars(select(League).where(League.key == lg.key)).one()
            matches = sess.scalars(
                select(Match)
                .where(
                    Match.league_id == league.id,
                    Match.status == "scheduled",
                    Match.kickoff_utc > now,
                    Match.kickoff_utc <= now + timedelta(hours=s.general.horizon_hours),
                )
                .order_by(Match.kickoff_utc, Match.id)
            ).all()
            if not matches:
                continue
            run = ensure_fresh_run(sess, league, s, now, report)
            model = StoredModel.from_json(json.loads(run.params_json))
            for m in matches:
                price_match(sess, lg, m, run, model, s, now, report)
        build_slips(sess, s, now, report)

    ctx.note("refits", report.refits)
    ctx.note("predictions_created", report.predictions_created)
    ctx.note("legs_priced", report.legs_priced)
    ctx.note("flagged_by_reason", dict(report.flagged))
    ctx.note("selection", s.value.selection)
    ctx.note("qualifying", report.qualifying)
    ctx.note("slips", report.slips)
    for reason, n in report.skipped.items():
        ctx.skip(reason, n)
    if report.stale_odds_matches:
        ctx.warn(
            f"odds stale (> 3 h) for {len(report.stale_odds_matches)} matches — run `make odds`"
        )
    lines = [
        f"selection: {s.value.selection}   "
        f"legs priced: {report.legs_priced}   qualifying: {report.qualifying}   "
        f"predictions created: {report.predictions_created}   refits: {report.refits or 'none'}",
        "flagged by reason: "
        + (", ".join(f"{k} {v}" for k, v in sorted(report.flagged.items())) or "none"),
        "skipped: " + (", ".join(f"{k} {v}" for k, v in sorted(report.skipped.items())) or "none"),
        *(f"{k:<22} {v}" for k, v in report.slips.items()),
    ]
    if report.stale_odds_matches:
        lines.append(
            f"WARNING odds stale for {len(report.stale_odds_matches)} matches — run `make odds`"
        )
    return "\n".join(lines)
