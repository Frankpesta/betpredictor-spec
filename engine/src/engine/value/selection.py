"""Leg selection strategies (docs/05 §8, user decision 2026-09-30). Pure.

"value" is the original docs/05 §2.2 rule (see `sanity.qualifies`). "likeliest" ranks
legs by p_final alone: no edge or odds rules, sanity flags still exclude, and Asian
Handicap legs are only allowed on the favourite's side (backing the stronger team;
a big head start for the weaker team is not taken).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from engine.model.markets import classify_line

Strategy = Literal["likeliest", "value"]
Side = Literal["home", "away"]

_TIE_TOL = 1e-9  # float noise: 1 − 0.7 != 0.3 exactly


def favourite_side(ah_home_p: Mapping[float, float]) -> Side | None:
    """The favourite from blended AH probabilities.

    `ah_home_p` maps a half line (home perspective) to p_final of the *home* selection.
    With both ±0.5 lines: P(home win) = p(−0.5), P(away win) = 1 − p(+0.5); the larger
    wins. Otherwise the fair handicap is the line whose home probability is closest to
    0.5: a negative fair line means home gives goals (home favourite), positive → away.
    None when there is no AH half line or the comparison is an exact tie.
    """
    half = {line: p for line, p in ah_home_p.items() if classify_line(line) == "half"}
    if not half:
        return None
    if -0.5 in half and 0.5 in half:
        home_win, away_win = half[-0.5], 1.0 - half[0.5]
        if abs(home_win - away_win) < _TIE_TOL:
            return None
        return "home" if home_win > away_win else "away"
    fair = min(half, key=lambda line: (abs(half[line] - 0.5), line))
    return "home" if fair < 0 else "away"


def qualifies_likeliest(
    reasons: list[str] | tuple[str, ...], market: str, selection: str, favourite: Side | None
) -> bool:
    """docs/05 §8: sanity ok; an AH leg must be on the favourite's side (none known → no AH)."""
    if reasons:
        return False
    return market != "AH" or (favourite is not None and selection == favourite)
