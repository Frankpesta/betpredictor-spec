"""Devig, market shrinkage, edge (docs/05 §2).

Pure functions shared by `make picks` and the backtest (docs/03 §10.2 step 4).
"""

from __future__ import annotations

from dataclasses import dataclass

from engine.model.markets import LINELESS_MARKETS, OutcomeProbs, base_market, is_binary
from engine.value.sanity import SanityInput, qualifies, sanity_reasons


def devig_pair(odds_a: float, odds_b: float) -> tuple[float, float]:
    """Multiplicative devig: q = 1/o, p_a = q_a / (q_a + q_b)."""
    if odds_a <= 1.0 or odds_b <= 1.0:
        raise ValueError(f"decimal odds must be > 1, got {odds_a}, {odds_b}")
    qa, qb = 1.0 / odds_a, 1.0 / odds_b
    return qa / (qa + qb), qb / (qa + qb)


def overround(odds_a: float, odds_b: float) -> float:
    return 1.0 / odds_a + 1.0 / odds_b


def devig_group(odds: tuple[float, ...], total: float = 1.0) -> tuple[float, ...]:
    """Multiplicative devig over a market's outcomes: p_i = total·q_i / Σq.

    `total` is what the true probabilities sum to: 1 for 2-/3-way markets, 2 for double
    chance (each scoreline wins two of its three selections)."""
    if any(o <= 1.0 for o in odds):
        raise ValueError(f"decimal odds must be > 1, got {odds}")
    q = [1.0 / o for o in odds]
    return tuple(total * x / sum(q) for x in q)


def group_overround(odds: tuple[float, ...], total: float = 1.0) -> float:
    """Σ(1/o) scaled to a 2-way-comparable figure (double chance: divided by 2)."""
    return sum(1.0 / o for o in odds) / total


# docs/05 §10: double chance outcomes overlap — their probabilities sum to 2.
GROUP_TOTAL = {"DC": 2.0}


def shrink(p_model: float, p_market_devig: float, weight: float) -> float:
    """p_final = w·p_model + (1−w)·p_market_devig."""
    if not 0.0 <= weight <= 1.0:
        raise ValueError(f"shrink weight must be in [0, 1], got {weight}")
    return weight * p_model + (1.0 - weight) * p_market_devig


def half_line_expected_multiplier(p_final: float, odds: float) -> float:
    """Half lines: p_win = p_final, everything else loses (docs/05 §2 step 5-6)."""
    return p_final * odds


def edge(expected_multiplier: float) -> float:
    return expected_multiplier - 1.0


@dataclass(frozen=True)
class LegValue:
    selection: str
    odds: float
    p_model: float
    p_market_devig: float
    p_final: float
    expected_multiplier: float
    edge: float
    reasons: tuple[str, ...]
    qualifies: bool


def evaluate_group(
    market: str,
    line: float,
    selections: tuple[str, ...],
    model: tuple[OutcomeProbs, ...],
    odds: tuple[float, ...],
    *,
    low_confidence: bool,
    minutes_to_kickoff: float | None,
    stale_prediction: bool,
    shrink_weight: float,
    min_edge: float,
    min_odds: float,
    max_odds: float,
    max_model_market_gap: float,
) -> tuple[LegValue, ...]:
    """docs/05 §2 for every selection of one (match, market, line); docs/05 §10 markets.

    Win-or-lose selections (half lines, 1X2/DC/BTTS): p_win = p_final, EM = p_final·o.
    Other lines are priced from the model's full outcome distribution for information only;
    the sanity step flags them.
    """
    total = GROUP_TOTAL.get(base_market(market), 1.0)
    devig = devig_group(odds, total)
    ovr = group_overround(odds, total)
    binary = is_binary(market, line)
    out: list[LegValue] = []
    for k in range(len(selections)):
        p_model = model[k].p_win
        p_final = shrink(p_model, devig[k], shrink_weight)
        em = (
            half_line_expected_multiplier(p_final, odds[k])
            if binary
            else model[k].expected_multiplier(odds[k])
        )
        reasons = sanity_reasons(
            SanityInput(
                line=line,
                p_model=p_model,
                p_market_devig=devig[k],
                overround=ovr,
                low_confidence=low_confidence,
                minutes_to_kickoff=minutes_to_kickoff,
                stale_prediction=stale_prediction,
                lineless=base_market(market) in LINELESS_MARKETS,
            ),
            max_model_market_gap,
        )
        e = edge(em)
        out.append(
            LegValue(
                selection=selections[k],
                odds=odds[k],
                p_model=p_model,
                p_market_devig=devig[k],
                p_final=p_final,
                expected_multiplier=em,
                edge=e,
                reasons=tuple(reasons),
                qualifies=qualifies(reasons, e, odds[k], min_edge, min_odds, max_odds),
            )
        )
    return tuple(out)


def evaluate_pair(
    line: float,
    selections: tuple[str, str],
    model: tuple[OutcomeProbs, OutcomeProbs],
    odds: tuple[float, float],
    *,
    low_confidence: bool,
    minutes_to_kickoff: float | None,
    stale_prediction: bool,
    shrink_weight: float,
    min_edge: float,
    min_odds: float,
    max_odds: float,
    max_model_market_gap: float,
) -> tuple[LegValue, LegValue]:
    """docs/05 §2 for both selections of one AH/OU (match, line). Used live and in backtests.

    Half lines: p_win = p_final, EM = p_final·o. Other lines are priced from the model's
    full outcome distribution for information only; the sanity step flags them.
    """
    a, b = evaluate_group(
        "OU",  # AH and OU share the line rules; only lineless markets differ
        line,
        selections,
        model,
        odds,
        low_confidence=low_confidence,
        minutes_to_kickoff=minutes_to_kickoff,
        stale_prediction=stale_prediction,
        shrink_weight=shrink_weight,
        min_edge=min_edge,
        min_odds=min_odds,
        max_odds=max_odds,
        max_model_market_gap=max_model_market_gap,
    )
    return a, b
