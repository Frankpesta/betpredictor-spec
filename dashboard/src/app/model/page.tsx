import { RunJobButton } from "@/components/run-job-button";
import { DbMissing, Empty, GateBadge, M, PageHeader, UnvalidatedBadge } from "@/components/status-bits";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getDb } from "@/db/client";
import { latestBacktest, latestModelRuns, type ModelRunRow } from "@/db/queries";
import { formatDateTime, orDash, pct1, pctSigned } from "@/lib/format";

type Params = {
  goals?: { att: number[]; def: number[]; teams: number[]; home_adv: number; rho: number };
  team_names?: Record<string, string>;
  matches_per_team?: Record<string, number>;
  low_confidence?: number[];
};

function parse<T>(json: string | null | undefined): T | null {
  if (!json) return null;
  try {
    return JSON.parse(json) as T;
  } catch {
    return null;
  }
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function Ratings({ run }: { run: ModelRunRow }) {
  const p = parse<Params>(run.paramsJson);
  const g = p?.goals;
  if (!g) return <p className="text-sm text-muted-foreground">Parameters unavailable.</p>;
  const low = new Set(p.low_confidence ?? []);
  const rows = g.teams
    .map((id, i) => ({
      id,
      name: p.team_names?.[String(id)] ?? `#${id}`,
      att: g.att[i],
      def: g.def[i],
      n: p.matches_per_team?.[String(id)],
    }))
    .sort((a, b) => b.att - b.def - (a.att - a.def));
  const v = run.modelVersion;
  return (
    <div className="max-h-96 overflow-auto">
      <Table className="text-xs">
        <TableHeader>
          <TableRow>
            <TableHead>#</TableHead>
            <TableHead>Team</TableHead>
            <TableHead className="text-right">Attack</TableHead>
            <TableHead className="text-right">Defence</TableHead>
            <TableHead className="text-right">Att − def</TableHead>
            <TableHead className="text-right">Matches</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r, i) => (
            <TableRow key={r.id}>
              <TableCell className="tabular-nums">{i + 1}</TableCell>
              <TableCell>
                {r.name}
                {low.has(r.id) && (
                  <Badge variant="outline" className="ml-1" title="Fewer matches than model.min_team_matches">
                    low confidence
                  </Badge>
                )}
              </TableCell>
              <TableCell className="text-right">
                <M v={v}>{r.att.toFixed(3)}</M>
              </TableCell>
              <TableCell className="text-right">
                <M v={v}>{r.def.toFixed(3)}</M>
              </TableCell>
              <TableCell className="text-right">
                <M v={v}>{(r.att - r.def).toFixed(3)}</M>
              </TableCell>
              <TableCell className="text-right tabular-nums">{r.n ?? "—"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

type Metrics = {
  demo?: boolean;
  gate?: { passed?: boolean; bets?: number; ece?: number; roi?: number; mean_clv?: number; failures?: string[] };
  holdout?: { overall?: { ALL?: Record<string, unknown> } };
};

export default async function ModelPage() {
  const res = await getDb();
  if (!res.ok) return <DbMissing dbPath={res.dbPath} error={res.error} />;
  const runs = latestModelRuns(res.db);
  const bt = latestBacktest(res.db);
  const m = parse<Metrics>(bt?.metricsJson);
  const all = m?.holdout?.overall?.ALL ?? {};

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Dixon–Coles + xG blend" title="Model">
        Latest fit per competition and the latest backtest.
      </PageHeader>

      <Card>
        <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <CardTitle className="flex items-center gap-2">
              Latest backtest <GateBadge gatePassed={bt?.gatePassed ?? null} />
            </CardTitle>
            <CardDescription>
              {bt
                ? `Run #${bt.id}, finished ${formatDateTime(bt.finishedAt ?? bt.startedAt)}`
                : "No backtest has run yet."}
            </CardDescription>
          </div>
          <RunJobButton job="backtest" label="Run backtest" variant="outline" />
        </CardHeader>
        {bt && (
          <CardContent className="space-y-3 text-sm">
            <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
              <div>
                <p className="text-muted-foreground">Holdout bets</p>
                <p className="tabular-nums">{orDash(num(m?.gate?.bets), String)}</p>
              </div>
              <div>
                <p className="text-muted-foreground">ROI</p>
                <p className="tabular-nums">{orDash(num(m?.gate?.roi), pctSigned)}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Mean CLV</p>
                <p className="tabular-nums">{orDash(num(m?.gate?.mean_clv), pctSigned)}</p>
              </div>
              <div>
                <p className="text-muted-foreground">ECE</p>
                <p className="tabular-nums">{orDash(num(m?.gate?.ece), pct1)}</p>
              </div>
              <div>
                <p className="text-muted-foreground">Log loss (model / market)</p>
                <p className="tabular-nums">
                  {orDash(num(all.log_loss), (x) => x.toFixed(4))} /{" "}
                  {orDash(num(all.baseline_log_loss_open), (x) => x.toFixed(4))}
                </p>
              </div>
            </div>
            {m?.gate?.failures && m.gate.failures.length > 0 && (
              <ul className="list-inside list-disc text-muted-foreground">
                {m.gate.failures.map((f) => (
                  <li key={f}>Gate failed: {f}</li>
                ))}
              </ul>
            )}
            {bt.reportPath && (
              <p className="text-muted-foreground">
                Full report: <code className="break-all">{bt.reportPath}</code>
              </p>
            )}
            <p className="text-muted-foreground">
              Internationals are calibration-only in the backtest (no historical odds): ROI, CLV and the
              gate do not cover them.
            </p>
          </CardContent>
        )}
      </Card>

      {runs.length === 0 ? (
        <Empty title="No fitted models">
          Run <code>make ingest</code> then <code>make fit</code>.
        </Empty>
      ) : (
        runs.map((run) => {
          const p = parse<Params>(run.paramsJson);
          return (
            <Card key={run.id}>
              <CardHeader>
                <CardTitle className="flex flex-wrap items-center gap-2">
                  {run.leagueName}
                  {run.leagueKey === "INTL" && <UnvalidatedBadge />}
                  {run.converged === 1 ? (
                    <Badge variant="secondary">converged</Badge>
                  ) : (
                    <Badge variant="destructive">not converged</Badge>
                  )}
                </CardTitle>
                <CardDescription className="flex flex-wrap gap-x-4">
                  <span>Fitted {formatDateTime(run.fittedAt)}</span>
                  <span>
                    Training {run.trainFrom} → {run.trainTo}
                  </span>
                  <span>{run.nMatches} matches</span>
                  <span>
                    Home advantage{" "}
                    {p?.goals ? <M v={run.modelVersion}>{p.goals.home_adv.toFixed(3)}</M> : "—"}
                  </span>
                  <span>ρ {p?.goals ? <M v={run.modelVersion}>{p.goals.rho.toFixed(3)}</M> : "—"}</span>
                  <span>version {run.modelVersion}</span>
                </CardDescription>
              </CardHeader>
              <CardContent>
                <details>
                  <summary className="cursor-pointer text-sm">Team ratings (sorted by attack − defence)</summary>
                  <div className="mt-2">
                    <Ratings run={run} />
                  </div>
                </details>
              </CardContent>
            </Card>
          );
        })
      )}
    </div>
  );
}
