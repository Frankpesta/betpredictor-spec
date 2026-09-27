import Link from "next/link";

import { CalibrationChart, EquityChart, ExpectedVsActual, RoiBars } from "@/components/charts/perf-charts";
import { DbMissing, Empty, PageHeader, Stat } from "@/components/status-bits";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getDb } from "@/db/client";
import { legPerformance, slipPerformance, type PerfMode } from "@/db/queries";
import { ENGINE_URL } from "@/lib/engine";
import { orDash, pct1, pctSigned } from "@/lib/format";
import { SLIP_TYPE_LABEL, SLIP_TYPES } from "@/lib/labels";
import { equityCurve, groupLegs, legStats, oddsBand, slipStats } from "@/lib/perf";
import { cn } from "@/lib/utils";

const MODES: PerfMode[] = ["all", "paper", "placed"];

type Calibration = {
  n: number;
  ece: number | null;
  excluded: Record<string, number>;
  reliability: { lo: number; hi: number; count: number; mean_predicted: number | null; observed_rate: number | null }[];
};

/** Calibration reuses the engine's Python reliability_table (docs/06 §4), via the local API. */
async function fetchCalibration(mode: PerfMode): Promise<Calibration | null> {
  try {
    const res = await fetch(`${ENGINE_URL}/performance/calibration?mode=${mode}`, { cache: "no-store" });
    return res.ok ? ((await res.json()) as Calibration) : null;
  } catch {
    return null;
  }
}

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  const tone = value.startsWith("+") ? "positive" : /^[-−]/.test(value) ? "negative" : "default";
  return <Stat label={label} value={value} sub={sub} tone={tone} className="bg-card shadow-xs ring-border" />;
}

export default async function PerformancePage(props: PageProps<"/performance">) {
  const sp = await props.searchParams;
  const raw = Array.isArray(sp.mode) ? sp.mode[0] : sp.mode;
  const mode: PerfMode = MODES.includes(raw as PerfMode) ? (raw as PerfMode) : "all";

  const res = await getDb();
  if (!res.ok) return <DbMissing dbPath={res.dbPath} error={res.error} />;
  const legs = legPerformance(res.db, mode);
  const slips = slipPerformance(res.db, mode);
  const calibration = await fetchCalibration(mode);

  const all = legStats(legs);
  const perType = SLIP_TYPES.map((t) => slipStats(t, slips));
  const equity = equityCurve({
    legs: legs.map((l) => ({ date: l.kickoff_utc.slice(0, 10), profit: l.profit ?? 0 })),
    ...Object.fromEntries(
      SLIP_TYPES.map((t) => [
        t,
        slips.filter((s) => s.slip_type === t).map((s) => ({ date: s.slip_date, profit: s.profit ?? 0 })),
      ]),
    ),
  });
  const splits = [
    { title: "By market", rows: groupLegs(legs, (l) => l.market) },
    { title: "By league", rows: groupLegs(legs, (l) => l.league) },
    { title: "By odds band", rows: groupLegs(legs, (l) => oddsBand(l.odds)) },
    { title: "By month", rows: groupLegs(legs, (l) => l.kickoff_utc.slice(0, 7)) },
  ];
  const placedNaira = perType.reduce((a, s) => a + s.profitNaira, 0);
  const placedN = perType.reduce((a, s) => a + s.placedN, 0);

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Settled results"
        title="Performance"
        actions={
          <div className="flex gap-0.5 rounded-xl bg-muted/70 p-1 text-sm ring-1 ring-border/60" role="tablist">
          {MODES.map((m) => (
            <Link
              key={m}
              href={m === "all" ? "/performance" : `/performance?mode=${m}`}
              className={cn(
                "rounded-lg px-3 py-1.5 font-medium capitalize transition-colors",
                m === mode ? "bg-card shadow-sm ring-1 ring-border/70" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {m}
            </Link>
          ))}
          </div>
        }
      >
        Settled legs and slips, 1-unit flat stakes. Each value leg is counted once even when it sits in several
        slips. Small samples are mostly noise.
      </PageHeader>

      {legs.length === 0 && slips.length === 0 ? (
        <Empty title="Nothing settled yet">
          Performance appears once slips have results: run <code>make close</code> ~30–60 min before
          kickoffs (for CLV) and <code>make settle</code> the next day.
        </Empty>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Kpi label="Settled legs" value={String(all.n)} />
            <Kpi label="Leg hit rate" value={orDash(all.hitRate, pct1)} sub="half win = 0.5" />
            <Kpi label="Leg ROI (flat stake)" value={orDash(all.roi, pctSigned)} />
            <Kpi label="Mean CLV" value={orDash(all.meanClv, pctSigned)} sub={`${all.clvN} legs with closing odds`} />
            {perType.map((s) => (
              <Kpi
                key={s.type}
                label={`${SLIP_TYPE_LABEL[s.type]} ROI`}
                value={orDash(s.roi, pctSigned)}
                sub={`${s.n} slips · hit ${orDash(s.hitRate, pct1)}`}
              />
            ))}
            {placedN > 0 && (
              <Kpi
                label="Real profit (placed)"
                value={`${placedNaira < 0 ? "−" : placedNaira > 0 ? "+" : ""}₦${Math.abs(placedNaira).toLocaleString("en-NG", { maximumFractionDigits: 0 })}`}
                sub={`${placedN} placed slips with a stake`}
              />
            )}
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Equity curves</CardTitle>
              <CardDescription>Cumulative flat-stake profit in units, legs and each slip type.</CardDescription>
            </CardHeader>
            <CardContent>
              <EquityChart
                data={equity}
                series={[
                  { key: "legs", label: "All legs" },
                  ...SLIP_TYPES.map((t) => ({ key: t, label: SLIP_TYPE_LABEL[t] })),
                ]}
              />
            </CardContent>
          </Card>

          <div className="grid gap-4 md:grid-cols-2">
            {splits.map(({ title, rows }) => (
              <Card key={title}>
                <CardHeader>
                  <CardTitle>ROI {title.toLowerCase()}</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  <RoiBars data={rows.map((r) => ({ group: r.group, roi: r.stats.roi ?? 0, n: r.stats.n }))} />
                  <Table className="text-xs">
                    <TableHeader>
                      <TableRow>
                        <TableHead>Group</TableHead>
                        <TableHead className="text-right">Legs</TableHead>
                        <TableHead className="text-right">Hit</TableHead>
                        <TableHead className="text-right">ROI</TableHead>
                        <TableHead className="text-right">CLV</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {rows.map((r) => (
                        <TableRow key={r.group}>
                          <TableCell>{r.group}</TableCell>
                          <TableCell className="text-right tabular-nums">{r.stats.n}</TableCell>
                          <TableCell className="text-right tabular-nums">{orDash(r.stats.hitRate, pct1)}</TableCell>
                          <TableCell className="text-right tabular-nums">{orDash(r.stats.roi, pctSigned)}</TableCell>
                          <TableCell className="text-right tabular-nums">{orDash(r.stats.meanClv, pctSigned)}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </CardContent>
              </Card>
            ))}
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Calibration of settled legs</CardTitle>
                <CardDescription>
                  Predicted vs observed win rate per 10% bin; dashed line is perfect calibration (y = x).
                  Half lines only (binary outcome), as in the backtest.
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-2">
                {calibration === null ? (
                  <p className="text-sm text-muted-foreground">
                    Engine API not reachable — start it with <code>make api</code> (calibration reuses the
                    engine&apos;s Python code).
                  </p>
                ) : calibration.n === 0 ? (
                  <p className="text-sm text-muted-foreground">No settled half-line win/loss legs yet.</p>
                ) : (
                  <>
                    <p className="text-sm">
                      ECE <span className="tabular-nums">{orDash(calibration.ece, pct1)}</span> over{" "}
                      {calibration.n} legs
                      {Object.keys(calibration.excluded).length > 0 &&
                        ` · excluded: ${Object.entries(calibration.excluded)
                          .map(([k, v]) => `${v} ${k}`)
                          .join(", ")}`}
                    </p>
                    <CalibrationChart
                      bins={calibration.reliability.map((b) => ({
                        mid: (b.lo + b.hi) / 2,
                        predicted: b.mean_predicted,
                        observed: b.observed_rate,
                        count: b.count,
                      }))}
                    />
                    <Table className="text-xs">
                      <TableHeader>
                        <TableRow>
                          <TableHead>Bin</TableHead>
                          <TableHead className="text-right">Legs</TableHead>
                          <TableHead className="text-right">Predicted</TableHead>
                          <TableHead className="text-right">Observed</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {calibration.reliability
                          .filter((b) => b.count > 0)
                          .map((b) => (
                            <TableRow key={b.lo}>
                              <TableCell>
                                {pct1(b.lo)}–{pct1(b.hi)}
                              </TableCell>
                              <TableCell className="text-right tabular-nums">{b.count}</TableCell>
                              <TableCell className="text-right tabular-nums">{orDash(b.mean_predicted, pct1)}</TableCell>
                              <TableCell className="text-right tabular-nums">{orDash(b.observed_rate, pct1)}</TableCell>
                            </TableRow>
                          ))}
                      </TableBody>
                    </Table>
                  </>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Expected vs actual profit</CardTitle>
                <CardDescription>
                  Σ(expected multiplier − 1) vs Σ profit per slip type, in units. A large gap either way
                  over many slips points to the model, not luck.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <ExpectedVsActual
                  data={perType.map((s) => ({
                    type: SLIP_TYPE_LABEL[s.type],
                    expected: Number(s.expectedProfit.toFixed(3)),
                    actual: Number(s.actualProfit.toFixed(3)),
                  }))}
                />
              </CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}
