"""Markdown backtest report (docs/03 §10.4-10.5). Pure string building."""

from __future__ import annotations

from typing import Any

from engine.backtest.tuning import GateResult, TuningResult

HONEST_FRAMING = (
    "No model can guarantee wins. Bookmaker odds are efficient; this report measures "
    "honestly whether a small positive expected value exists. Past results on historical "
    "odds do not guarantee future results."
)


def _f(v: Any, fmt: str = ".4f") -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        return format(v, fmt)
    return str(v)


def _pct(v: Any) -> str:
    return "–" if v is None else f"{v:+.2%}"


def summary_table(metrics: dict[str, Any], primary_closing: str) -> str:
    head = (
        "| scope | market | priced | log loss | market open LL | market close LL (subset) "
        "| ECE | bets | hit rate | ROI | profit (u) | max DD (u) | lose streak "
        f"| mean CLV | CLV {primary_closing}-only |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    rows = [head]
    for scope, by_market in metrics.items():
        for market in ("ALL", "OU", "AH"):
            m = by_market[market]
            base_close, ours = m.get("baseline_log_loss_close"), m.get("log_loss_on_closing_subset")
            close = (
                f"{_f(base_close)} vs {_f(ours)} (n={m.get('closing_subset_n', 0)})"
                if m.get("closing_subset_n")
                else "–"
            )
            rows.append(
                f"| {scope} | {market} | {m.get('n_priced', 0)} | {_f(m.get('log_loss'))} "
                f"| {_f(m.get('baseline_log_loss_open'))} | {close} | {_f(m.get('ece'))} "
                f"| {m.get('bets', 0)} | {_pct(m.get('hit_rate'))} | {_pct(m.get('roi'))} "
                f"| {_f(m.get('profit_units'), '+.2f')} | {_f(m.get('max_drawdown_units'), '.2f')} "
                f"| {m.get('longest_losing_streak', '–')} | {_pct(m.get('mean_clv'))} "
                f"| {_pct(m.get(f'mean_clv_{primary_closing}_only'))} |"
            )
    return "\n".join(rows)


def tuning_tables(t: TuningResult) -> str:
    s1 = ["| xi_per_day | xg_blend_weight | O/U 2.5 log loss (p_model) | n |", "|---|---|---|---|"]
    for r in sorted(t.stage1, key=lambda r: r.ou_log_loss):
        mark = (
            " **←**"
            if (r.xi_per_day, r.xg_blend_weight) == (t.xi_per_day, t.xg_blend_weight)
            else ""
        )
        s1.append(f"| {r.xi_per_day} | {r.xg_blend_weight} | {r.ou_log_loss:.5f}{mark} | {r.n} |")
    s2 = ["| market_shrink_weight | log loss (p_final, O/U + half-line AH) | n |", "|---|---|---|"]
    for r2 in t.stage2:
        mark = " **←**" if r2.market_shrink_weight == t.market_shrink_weight else ""
        s2.append(f"| {r2.market_shrink_weight} | {r2.log_loss:.5f}{mark} | {r2.n} |")
    s3 = ["| min_edge_leg | bets | ROI | profit (u) | ≥ min bets |", "|---|---|---|---|---|"]
    for r3 in t.stage3:
        mark = " **←**" if r3.min_edge_leg == t.min_edge_leg else ""
        s3.append(
            f"| {r3.min_edge_leg}{mark} | {r3.bets} | {_pct(r3.roi)} | {r3.profit_units:+.2f} "
            f"| {'yes' if r3.eligible else 'no'} |"
        )
    note = (
        "\n\n**No min_edge_leg setting reached the minimum bet count; "
        "the configured value was kept.**"
        if t.min_edge_fallback
        else ""
    )
    return (
        "### Stage 1 — xi and xG blend (lowest O/U 2.5 log loss)\n\n"
        + "\n".join(s1)
        + "\n\n### Stage 2 — market shrink (lowest log loss)\n\n"
        + "\n".join(s2)
        + "\n\n### Stage 3 — min edge (highest ROI with enough bets)\n\n"
        + "\n".join(s3)
        + note
    )


def reliability_md(rel: list[dict[str, Any]]) -> str:
    rows = ["| bin | n | mean predicted | observed |", "|---|---|---|---|"]
    for b in rel:
        if b["count"]:
            rows.append(
                f"| {b['lo']:.1f}–{b['hi']:.1f} | {b['count']} | {b['mean_predicted']:.3f} "
                f"| {b['observed_rate']:.3f} |"
            )
    return "\n".join(rows)


def render(
    *,
    started: str,
    tuning_seasons: list[str],
    holdout_season: str,
    tuning: TuningResult,
    holdout_metrics: dict[str, Any],
    tuning_metrics: dict[str, Any],
    slips: dict[str, Any],
    gate_result: GateResult,
    primary_closing: str,
    notes: list[str],
    intl: dict[str, Any] | None = None,
) -> str:
    verdict = "PASSED" if gate_result.passed else "FAILED"
    g = gate_result
    lines = [
        f"# Backtest report — {started}",
        "",
        f"> {HONEST_FRAMING}",
        "",
        f"## Gate: **{verdict}** (holdout season {holdout_season})",
        "",
        f"- ECE: {_f(g.ece)}  ·  qualifying bets: {g.bets}  ·  ROI: {_pct(g.roi)}  ·  "
        f"mean CLV: {_pct(g.mean_clv)}",
    ]
    if g.failures:
        lines += ["- Failed checks: " + "; ".join(g.failures)]
        lines += [
            "",
            "**Model has not demonstrated an edge — paper mode recommended.**",
        ]
    lines += [
        "",
        "## Chosen parameters (from tuning seasons " + ", ".join(tuning_seasons) + ")",
        "",
        f"- xi_per_day = {tuning.xi_per_day}",
        f"- xg_blend_weight = {tuning.xg_blend_weight}",
        f"- market_shrink_weight = {tuning.market_shrink_weight}",
        f"- min_edge_leg = {tuning.min_edge_leg}"
        + (
            " (configured value; no grid value had enough bets)" if tuning.min_edge_fallback else ""
        ),
        "",
        f"## Holdout {holdout_season} (run once, refit every 7 days)",
        "",
        summary_table(holdout_metrics, primary_closing),
        "",
        "### Simulated daily 2-odds slips (holdout)",
        "",
        f"- days with qualifying legs: {slips['days_with_qualifying_legs']}; "
        f"slips: {slips['slips']}; "
        f"hit rate: {_pct(slips['hit_rate'])}; mean p_all_win: {_pct(slips['mean_p_all_win'])}; "
        f"ROI: {_pct(slips['roi'])}; profit: {slips['profit_units']:+.2f} u",
        "",
        "### Reliability (holdout, all markets)",
        "",
        reliability_md(holdout_metrics["overall"]["ALL"].get("reliability", [])),
        "",
        *intl_section(intl or {}),
        "## Tuning seasons with the chosen parameters (refit every "
        "tuning_refit_every_days — not a clean out-of-sample result)",
        "",
        summary_table(tuning_metrics, primary_closing),
        "",
        "## Tuning grid",
        "",
        tuning_tables(tuning),
        "",
        "## Notes",
        "",
        *(f"- {n}" for n in notes),
        "",
    ]
    return "\n".join(lines)


def intl_section(intl: dict[str, Any]) -> list[str]:
    if not intl:
        return []
    lines = [
        "## International — UNVALIDATED (docs/09)",
        "",
        "No historical odds exist for internationals, so ROI, CLV and the gate cannot be "
        "computed. Only the quality of the O/U 2.5 probabilities is measured, against a "
        "constant base-rate forecast.",
        "",
        "| competition | seasons | priced | log loss | base-rate LL | ECE | Brier "
        "| observed over-2.5 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for key, m in intl.items():
        if not m.get("n_priced"):
            lines.append(f"| {key} | – | 0 | – | – | – | – | – |")
            continue
        lines.append(
            f"| {key} | {', '.join(m['seasons'])} | {m['n_priced']} | {m['log_loss']:.4f} "
            f"| {m['baseline_log_loss_base_rate']:.4f} | {m['ece']:.4f} | {m['brier']:.4f} "
            f"| {m['observed_over_rate']:.1%} |"
        )
    for key, m in intl.items():
        absurd = m.get("skipped_absurd_rates") or []
        if absurd:
            lines += [
                "",
                f"{key}: {len(absurd)} predictions skipped — the model produced absurd rates "
                "(tail check), typically between weakly connected sides:",
                *(f"- {a}" for a in absurd[:15]),
            ]
    return [*lines, ""]
