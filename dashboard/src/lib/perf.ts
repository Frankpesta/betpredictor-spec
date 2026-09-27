// Pure aggregations over the performance views (docs/06 §4). No I/O.

import type { LegPerf, SlipPerf } from "@/db/queries";

export type LegStats = { n: number; hitRate: number | null; roi: number | null; meanClv: number | null; clvN: number };

export function legStats(legs: LegPerf[]): LegStats {
  const n = legs.length;
  const clvs = legs.flatMap((l) => (l.clv === null ? [] : [l.clv]));
  const profit = legs.reduce((a, l) => a + (l.profit ?? 0), 0);
  const wins = legs.reduce((a, l) => a + l.win_units, 0);
  return {
    n,
    hitRate: n ? wins / n : null,
    roi: n ? profit / n : null,
    meanClv: clvs.length ? clvs.reduce((a, b) => a + b, 0) / clvs.length : null,
    clvN: clvs.length,
  };
}

/** docs/06 §4 odds bands; odds outside every band are reported as "other". */
export const ODDS_BANDS: [number, number][] = [
  [1.2, 1.5],
  [1.5, 1.8],
  [1.8, 2.2],
  [2.2, 2.6],
];

export function oddsBand(odds: number): string {
  for (const [i, [lo, hi]] of ODDS_BANDS.entries()) {
    const last = i === ODDS_BANDS.length - 1;
    if (odds >= lo && (last ? odds <= hi : odds < hi)) return `${lo.toFixed(2)}–${hi.toFixed(2)}`;
  }
  return "other";
}

export function groupLegs(legs: LegPerf[], key: (l: LegPerf) => string): { group: string; stats: LegStats }[] {
  const m = new Map<string, LegPerf[]>();
  for (const l of legs) m.set(key(l), [...(m.get(key(l)) ?? []), l]);
  return [...m.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([group, ls]) => ({ group, stats: legStats(ls) }));
}

export type SlipStats = {
  type: string;
  n: number;
  hitRate: number | null;
  roi: number | null;
  expectedProfit: number;
  actualProfit: number;
  placedN: number;
  profitNaira: number;
};

/** A slip "hits" when it returned more than its stake (won, or a partial above 1×). */
export function slipStats(type: string, slips: SlipPerf[]): SlipStats {
  const own = slips.filter((s) => s.slip_type === type);
  const n = own.length;
  const actual = own.reduce((a, s) => a + (s.profit ?? 0), 0);
  const placed = own.filter((s) => s.mode === "placed");
  return {
    type,
    n,
    hitRate: n ? own.filter((s) => (s.return_multiplier ?? 0) > 1).length / n : null,
    roi: n ? actual / n : null,
    expectedProfit: own.reduce((a, s) => a + (s.expected_multiplier - 1), 0),
    actualProfit: actual,
    placedN: placed.length,
    profitNaira: placed.reduce((a, s) => a + (s.profit_naira ?? 0), 0),
  };
}

export type EquityPoint = { date: string } & Record<string, number | string>;

/**
 * Cumulative flat-stake profit per series by date. Every series carries its last value
 * forward so lines stay continuous.
 */
export function equityCurve(series: Record<string, { date: string; profit: number }[]>): EquityPoint[] {
  const dates = [...new Set(Object.values(series).flatMap((s) => s.map((p) => p.date)))].sort();
  const running: Record<string, number> = {};
  const byDate: Record<string, Map<string, number>> = {};
  for (const [name, pts] of Object.entries(series)) {
    const m = new Map<string, number>();
    for (const p of pts) m.set(p.date, (m.get(p.date) ?? 0) + p.profit);
    byDate[name] = m;
    running[name] = 0;
  }
  return dates.map((date) => {
    const point: EquityPoint = { date };
    for (const name of Object.keys(series)) {
      running[name] += byDate[name].get(date) ?? 0;
      point[name] = Number(running[name].toFixed(4));
    }
    return point;
  });
}
