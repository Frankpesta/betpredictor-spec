"""`make picks`: predictions -> value legs -> slips (docs/05 §1-6, docs/09 §5).

Per enabled competition: make sure a fresh model run exists (refit if it is older than
MAX_MODEL_AGE or older than the newest finished match), store predictions for every
scheduled match in the next 72 h, price every (market, line) pair of the latest odds
snapshot with the shared value pipeline, and write every selection to `value_legs`.
Slips are then built per pool — club legs and INTL legs are never mixed.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from engine.config import LeagueDef, Settings
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
from engine.model.league import StoredModel
from engine.model.markets import Market, OutcomeProbs, Selection, price_selection
from engine.model.score_matrix import AbsurdRatesError, to_json_list
from engine.slips.builder import ChosenSlip, daily_2odds, mega_acca, mid_acca, weekend_window
from engine.slips.constraints import SPORTYBET_MAX_SELECTIONS, Leg
from engine.value.edge import evaluate_pair

if TYPE_CHECKING:
    from engine.jobs import JobContext

log = get_logger(__name__)

PICK_HORIZON = timedelta(hours=72)  # docs/05 §1.2
MAX_MODEL_AGE = timedelta(days=7)  # docs/05 §1.1
STALE_ODDS_AGE = timedelta(hours=3)  # docs/05 §1.3
PAIRS = {"AH": ("home", "away"), "OU": ("over", "under")}


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
    for (market, line), sides in sorted(by_key.items()):
        sels = PAIRS[market]
        if not all(s in sides for s in sels):
            report.skipped["missing_pair"] += len(sides)  # odds.py stores pairs; defensive
            continue
        a, b = sides[sels[0]], sides[sels[1]]
        legs = evaluate_pair(
            line,
            sels,
            (
                _price(mat, market, sels[0], line),
                _price(mat, market, sels[1], line),
            ),
            (a.odds, b.odds),
            low_confidence=low,
            minutes_to_kickoff=minutes,
            stale_prediction=stale_pred,
            shrink_weight=v.market_shrink_weight,
            min_edge=v.min_edge_leg,
            min_odds=v.min_odds,
            max_odds=v.max_odds,
            max_model_market_gap=v.max_model_market_gap,
        )
        for snap, lv in ((a, legs[0]), (b, legs[1])):
            probs = _price(mat, market, lv.selection, line)
            half = not lv.reasons or "non_half_line_v1" not in lv.reasons
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
            )
            session.add(row)
            session.flush()
            report.legs_priced += 1
            for r in lv.reasons:
                report.flagged[r] += 1
            report.qualifying += int(lv.qualifies)
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
                        qualifies=lv.qualifies,
                        sanity_ok=not lv.reasons,
                    ),
                    row.id,
                )
            )


def _replace_or_keep(session: Session, slip_type: str, pool: str, slip_date: Any) -> bool:
    """Delete this type/pool/date's pending+open slips.

    Returns True when a booked or settled slip remains (then no new slip is made).
    """
    session.execute(
        delete(Slip).where(
            Slip.slip_type == slip_type,
            Slip.pool == pool,
            Slip.slip_date == slip_date,
            Slip.booking_status == "pending",
            Slip.status == "open",
        )
    )
    session.flush()
    return (
        session.scalar(
            select(func.count())
            .select_from(Slip)
            .where(Slip.slip_type == slip_type, Slip.pool == pool, Slip.slip_date == slip_date)
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
                daily_2odds(legs, now, sc.daily_2odds, SPORTYBET_MAX_SELECTIONS),
            ),
            (
                "mid_acca",
                sc.mid_acca.enabled,
                today,
                (now, now + PICK_HORIZON),
                mid_acca(legs, now, sc.mid_acca),
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
                ),
            ),
        ]
        for slip_type, enabled, slip_date, window, chosen in plans:
            key = f"{pool}/{slip_type}"
            if not enabled:
                report.slips[key] = "disabled"
                continue
            if _replace_or_keep(session, slip_type, pool, slip_date):
                report.slips[key] = "kept existing booked/settled slip"
                continue
            if chosen is None:
                report.slips[key] = "none — no value"
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
                    Match.kickoff_utc <= now + PICK_HORIZON,
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
    ctx.note("qualifying", report.qualifying)
    ctx.note("slips", report.slips)
    for reason, n in report.skipped.items():
        ctx.skip(reason, n)
    if report.stale_odds_matches:
        ctx.warn(
            f"odds stale (> 3 h) for {len(report.stale_odds_matches)} matches — run `make odds`"
        )
    lines = [
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
