"""Calibration of settled legs (docs/06 §4), computed with `calibration.reliability_table`.

The dashboard reads every other performance number straight from the SQL views;
this is the one aggregate that must reuse the engine's Python implementation.
As in the backtest (docs/03 §10), only half lines are calibrated: they are the
only ones with a binary outcome. Other settled legs are counted, not dropped silently.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, Request
from sqlalchemy import select

from engine.api.routes_jobs import runner_of
from engine.db.models import Slip, SlipLeg, ValueLeg
from engine.db.session import session_scope
from engine.model.calibration import ece, reliability_table
from engine.model.markets import is_binary

router = APIRouter()


@router.get("/performance/calibration")
def calibration(
    request: Request, mode: Literal["all", "paper", "placed"] = "all"
) -> dict[str, Any]:
    with session_scope(runner_of(request).db_path) as s:
        query = (
            select(ValueLeg.id, ValueLeg.market, ValueLeg.line, ValueLeg.p_final, SlipLeg.result)
            .join(SlipLeg, SlipLeg.value_leg_id == ValueLeg.id)
            .join(Slip, Slip.id == SlipLeg.slip_id)
            .where(SlipLeg.result != "pending")
            .distinct()
        )
        if mode != "all":
            query = query.where(Slip.mode == mode)
        rows = s.execute(query).all()

    # A value leg can sit in several slips; calibrate each leg once.
    legs: dict[int, tuple[str, float, float, str]] = {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}
    probs: list[float] = []
    outcomes: list[float] = []
    excluded: dict[str, int] = {}
    for market, line, p_final, result in legs.values():
        if not is_binary(market, line):
            excluded["not_half_line"] = excluded.get("not_half_line", 0) + 1
        elif result not in ("win", "loss"):
            excluded[f"result_{result}"] = excluded.get(f"result_{result}", 0) + 1
        else:
            probs.append(p_final)
            outcomes.append(1.0 if result == "win" else 0.0)

    p, o = np.array(probs, dtype=float), np.array(outcomes, dtype=float)
    return {
        "mode": mode,
        "n": int(p.size),
        "excluded": excluded,
        "ece": ece(p, o) if p.size else None,
        "reliability": [asdict(b) for b in reliability_table(p, o)],
    }
