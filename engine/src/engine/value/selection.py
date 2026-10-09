"""Leg selection strategies (docs/05 §8-9). Pure.

"value" is the original docs/05 §2.2 rule (see `sanity.qualifies`). "likeliest" ranks
legs by p_final alone: no edge or odds rules, sanity flags still exclude, and Asian
Handicap legs are only allowed on the favourite's side (backing the stronger team;
a big head start for the weaker team is not taken). "data_rule" (user decision
2026-10-08) needs a team-data reason for every leg: the model's chance each team
scores plus how often it failed to score recently, and an odds floor.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from engine.model.markets import classify_line

if TYPE_CHECKING:
    from engine.config import DataRuleCfg
    from engine.model.score_matrix import FloatArray

Strategy = Literal["data_rule", "likeliest", "value"]
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
    """docs/05 §8: sanity ok; an AH leg must be on the favourite's side (none known → no AH).
    Only the AH/OU markets it was defined for (the docs/05 §10 markets never qualify)."""
    if reasons or market not in ("AH", "OU"):
        return False
    return market != "AH" or (favourite is not None and selection == favourite)


@dataclass(frozen=True)
class TeamData:
    """docs/05 §9: model P(team scores) and its recent record (games it failed to score)."""

    p_score: float
    blanks: int
    games: int

    def text(self, name: str) -> str:
        return f"{name} scores {self.p_score:.0%}, blanked {self.blanks}/{self.games}"


def p_scores(mat: FloatArray) -> tuple[float, float]:
    """(P(home scores), P(away scores)) from a score matrix P[home goals][away goals]."""
    return 1.0 - float(mat[0, :].sum()), 1.0 - float(mat[:, 0].sum())


def team_data(p_score: float, goals_for: Sequence[int]) -> TeamData:
    """`goals_for`: the team's goals in its recent finished matches (newest first)."""
    return TeamData(p_score, sum(g == 0 for g in goals_for), len(goals_for))


def _strong(t: TeamData, cfg: DataRuleCfg) -> bool:
    return (
        t.p_score >= cfg.strong_score_p
        and t.blanks <= cfg.strong_max_blanks
        and t.games >= cfg.min_form_games
    )


def _weak(t: TeamData, cfg: DataRuleCfg) -> bool:
    return t.p_score <= cfg.weak_score_p and t.blanks >= cfg.weak_min_blanks


def data_reason(
    market: str, line: float, selection: str, home: TeamData, away: TeamData, cfg: DataRuleCfg
) -> str | None:
    """docs/05 §9.1 / §10.3: the team-data reason a selection may be backed, or None.

    AH lines are stored from the home side. Giving goals (handicap < 0) needs a strong
    attack on the backed side; taking goals needs a weak attack on the other side.
    Under needs at least one weak attack; Over needs both teams to score reliably.
    1X2 win = AH −0.5; double chance with the draw = AH +0.5; BTTS yes/no = Over/Under;
    one team's goals: Over needs that team strong, Under needs it weak. The draw and
    "home or away" have no data reason.
    """
    teams = {"home": home, "away": away}
    if market == "1X2":
        if selection == "draw":
            return None
        t = teams[selection]
        return f"to win: {t.text(selection)}" if _strong(t, cfg) else None
    if market == "DC":
        other = {"home_draw": "away", "draw_away": "home"}.get(selection)
        if other is None:
            return None
        o = teams[other]
        return f"vs weak attack: {o.text(other)}" if _weak(o, cfg) else None
    if market in ("OU_HOME", "OU_AWAY"):
        side = "home" if market == "OU_HOME" else "away"
        t = teams[side]
        if selection == "over":
            goals = int(line + 0.5)
            what = "to score" if goals == 1 else f"to score {goals}+"
            return f"{what}: {t.text(side)}" if _strong(t, cfg) else None
        return f"weak attack: {t.text(side)}" if _weak(t, cfg) else None
    if market == "BTTS":
        selection = "over" if selection == "yes" else "under"
    if market == "AH":
        other = "away" if selection == "home" else "home"
        handicap = line if selection == "home" else -line
        if handicap < 0:
            t = teams[selection]
            margin = int(-handicap + 0.5)  # −0.5 → win, −1.5 → win by 2+
            what = "to win" if margin == 1 else f"to win by {margin}+"
            return f"{what}: {t.text(selection)}" if _strong(t, cfg) else None
        o = teams[other]
        return f"vs weak attack: {o.text(other)}" if _weak(o, cfg) else None
    if selection == "under":
        weak = [k for k, t in teams.items() if _weak(t, cfg)]
        if not weak:
            return None
        return "weak attack: " + "; ".join(teams[k].text(k) for k in weak)
    if all(
        t.p_score >= cfg.over_score_p and t.blanks <= cfg.over_max_blanks for t in teams.values()
    ):
        return "both score: " + "; ".join(t.text(k) for k, t in teams.items())
    return None


def qualifies_data_rule(
    reasons: list[str] | tuple[str, ...],
    market: str,
    line: float,
    selection: str,
    odds: float,
    p_final: float,
    home: TeamData,
    away: TeamData,
    cfg: DataRuleCfg,
) -> str | None:
    """docs/05 §9.1: sanity ok, odds >= min_odds, p_final >= min_leg_probability and a
    data reason. Returns the reason when the leg qualifies, else None."""
    if reasons or odds < cfg.min_odds or p_final < cfg.min_leg_probability:
        return None
    return data_reason(market, line, selection, home, away, cfg)
