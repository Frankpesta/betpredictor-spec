import { AliasApprove } from "@/components/alias-approve";
import { LegsTable } from "@/components/legs-table";
import { DbMissing, Empty, PageHeader } from "@/components/status-bits";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getDb } from "@/db/client";
import {
  failedBookings,
  lastSuccess,
  needsReview,
  recentJobRuns,
  teamsByLeague,
  unresolved,
  unsettledPastSlips,
} from "@/db/queries";
import { ago, formatDateTime, formatKickoff, hoursSince, toDbUtc } from "@/lib/format";
import { SLIP_TYPE_LABEL } from "@/lib/labels";

// Staleness thresholds for warnings. Odds: docs/07 §2.1 (3h). Fit: picks refits a model
// older than 7 days (docs/05 §1.1). Settle: an open slip whose last kickoff is > 1 day old.
const STALE = { oddsHours: 3, fitHours: 7 * 24, settleHours: 24, ingestHours: 7 * 24 };

// Non-FIFA sides SportyBet lists that are deliberately left unmapped (user decision
// 2026-09-27, docs/09): they are excluded, not missing.
const DELIBERATELY_UNMAPPED = new Set(["Bonaire", "Guadeloupe", "Martinique"]);

export default async function DataHealthPage() {
  const res = await getDb();
  if (!res.ok) return <DbMissing dbPath={res.dbPath} error={res.error} />;
  const db = res.db;
  const now = new Date();

  const jobs = recentJobRuns(db);
  const names = unresolved(db);
  const teams = teamsByLeague(db);
  const review = needsReview(db);
  const failed = failedBookings(db);
  const lastOdds = lastSuccess(db, "odds");
  const lastFit = lastSuccess(db, "fit");
  const lastIngest = lastSuccess(db, "ingest");
  const overdue = unsettledPastSlips(db, toDbUtc(new Date(now.getTime() - STALE.settleHours * 3_600_000)));

  const warnings: string[] = [];
  if (!lastOdds) warnings.push("Odds have never been fetched — run make odds (or Run daily).");
  else if (hoursSince(lastOdds, now) > STALE.oddsHours)
    warnings.push(`Odds are stale: last successful odds run ${ago(lastOdds, now)}.`);
  if (lastIngest && hoursSince(lastIngest, now) > STALE.ingestHours)
    warnings.push(`History/xG last ingested ${ago(lastIngest, now)} — run make ingest.`);
  if (lastFit && hoursSince(lastFit, now) > STALE.fitHours)
    warnings.push(`Last manual fit ${ago(lastFit, now)} (make picks refits stale models automatically).`);
  if (overdue > 0) warnings.push(`${overdue} open slip(s) finished more than a day ago — run make settle.`);
  const realUnresolved = names.filter((n) => !DELIBERATELY_UNMAPPED.has(n.rawName));
  if (realUnresolved.length > 0) warnings.push(`${realUnresolved.length} unresolved team name(s) — events are skipped until mapped.`);
  if (review.length > 0) warnings.push(`${review.length} match(es) need review — their legs stay unsettled.`);
  if (failed.length > 0) warnings.push(`${failed.length} open slip(s) failed to book — enter them manually.`);

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Pipeline" title="Data health">
        Job runs, unmapped names, disputed results and failed bookings.
      </PageHeader>

      <Card>
        <CardHeader>
          <CardTitle>Warnings</CardTitle>
        </CardHeader>
        <CardContent>
          {warnings.length === 0 ? (
            <p className="text-sm text-muted-foreground">No warnings.</p>
          ) : (
            <ul className="list-inside list-disc space-y-1 text-sm">
              {warnings.map((w) => (
                <li key={w}>{w}</li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Recent job runs</CardTitle>
          <CardDescription>Every scraper, fit and job writes a row here; failures keep their traceback.</CardDescription>
        </CardHeader>
        <CardContent>
          {jobs.length === 0 ? (
            <Empty title="No jobs have run yet">
              Start with <code>make ingest</code>, <code>make fit</code>, then <strong>Run daily</strong>.
            </Empty>
          ) : (
            <div className="divide-y overflow-hidden rounded-xl border text-sm">
              {jobs.map((j) => (
                <details key={j.id}>
                  <summary className="flex cursor-pointer flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 hover:bg-muted/50">
                    <span className="tabular-nums text-muted-foreground">#{j.id}</span>
                    <span className="font-medium">{j.jobName}</span>
                    <Badge
                      variant={j.status === "failed" ? "destructive" : j.status === "running" ? "outline" : "secondary"}
                    >
                      {j.status}
                    </Badge>
                    <span className="text-muted-foreground">{formatDateTime(j.startedAt)}</span>
                  </summary>
                  <div className="space-y-2 px-3 pb-3">
                    {j.summaryJson && (
                      <pre className="max-h-64 overflow-auto rounded bg-muted p-2 text-xs whitespace-pre-wrap">
                        {JSON.stringify(JSON.parse(j.summaryJson), null, 2)}
                      </pre>
                    )}
                    {j.error && (
                      <pre className="max-h-64 overflow-auto rounded bg-destructive/10 p-2 text-xs whitespace-pre-wrap text-destructive">
                        {j.error}
                      </pre>
                    )}
                  </div>
                </details>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Unresolved team names</CardTitle>
          <CardDescription>
            Pick the team each name refers to and approve it. Check carefully — fuzzy look-alikes are
            often wrong (e.g. Ireland vs Northern Ireland).
          </CardDescription>
        </CardHeader>
        <CardContent>
          {names.length === 0 ? (
            <p className="text-sm text-muted-foreground">All names resolved.</p>
          ) : (
            <div className="overflow-x-auto">
              <Table className="text-xs">
                <TableHeader>
                  <TableRow>
                    <TableHead>Source</TableHead>
                    <TableHead>League</TableHead>
                    <TableHead>Name</TableHead>
                    <TableHead>Last seen</TableHead>
                    <TableHead>Approve alias</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {names.map((n) => (
                    <TableRow key={n.id}>
                      <TableCell>{n.source}</TableCell>
                      <TableCell>{n.leagueKey ?? "—"}</TableCell>
                      <TableCell className="font-medium">{n.rawName}</TableCell>
                      <TableCell>{ago(n.lastSeen, now)}</TableCell>
                      <TableCell>
                        {DELIBERATELY_UNMAPPED.has(n.rawName) ? (
                          <span className="text-muted-foreground">non-FIFA side — deliberately unmapped</span>
                        ) : (
                          <AliasApprove
                            source={n.source}
                            rawName={n.rawName}
                            teams={n.leagueKey ? (teams.get(n.leagueKey) ?? []) : [...teams.values()].flat()}
                          />
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Matches needing review</CardTitle>
          <CardDescription>
            The two result sources disagreed, so these are not settled. Check the real score.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {review.length === 0 ? (
            <p className="text-sm text-muted-foreground">None.</p>
          ) : (
            <Table className="text-xs">
              <TableHeader>
                <TableRow>
                  <TableHead>League</TableHead>
                  <TableHead>Kickoff</TableHead>
                  <TableHead>Match</TableHead>
                  <TableHead>Stored score</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {review.map((r) => (
                  <TableRow key={r.matchId}>
                    <TableCell>{r.leagueKey}</TableCell>
                    <TableCell>{formatKickoff(r.kickoffUtc)}</TableCell>
                    <TableCell>
                      {r.homeName} v {r.awayName}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {r.homeGoals ?? "?"}–{r.awayGoals ?? "?"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Failed bookings</CardTitle>
          <CardDescription>
            Add these legs on SportyBet by hand, then use “Enter code manually” on the Today page.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {failed.length === 0 ? (
            <p className="text-sm text-muted-foreground">None.</p>
          ) : (
            failed.map((s) => (
              <div key={s.id} className="space-y-1">
                <p className="text-sm font-medium">
                  {s.slipDate} · {SLIP_TYPE_LABEL[s.slipType]} ({s.pool}) — slip #{s.id}
                </p>
                {s.bookingError && <p className="text-xs text-destructive">{s.bookingError}</p>}
                <LegsTable legs={s.legs} modelVersion={s.modelVersion} />
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
