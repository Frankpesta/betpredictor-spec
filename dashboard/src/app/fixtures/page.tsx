import Link from "next/link";

import { DbMissing, Empty, M, PageHeader, UnvalidatedBadge } from "@/components/status-bits";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getDb } from "@/db/client";
import { legsForPredictions, leagueKeys, upcomingFixtures, type ValueLegRow } from "@/db/queries";
import { formatKickoff, lagosToday, odds2, parseUtc, pctSigned, toDbUtc } from "@/lib/format";
import { selectionLabel } from "@/lib/labels";
import { loadSettings, qualifies } from "@/lib/settings";

const HORIZON_HOURS = 72; // docs/07 §2.2 and docs/05 §1.2

function one(v: string | string[] | undefined): string {
  return (Array.isArray(v) ? v[0] : v) ?? "";
}

/** Best qualifying leg: highest edge, ties by higher p_final (docs/05 §3 ordering). */
function bestLeg(legs: ValueLegRow[]): ValueLegRow | undefined {
  return [...legs].sort((a, b) => b.edge - a.edge || b.pFinal - a.pFinal || a.id - b.id)[0];
}

export default async function FixturesPage(props: PageProps<"/fixtures">) {
  const sp = await props.searchParams;
  const league = one(sp.league);
  const date = one(sp.date);
  const qualifyingOnly = one(sp.q) === "1";

  const res = await getDb();
  if (!res.ok) return <DbMissing dbPath={res.dbPath} error={res.error} />;
  const settings = loadSettings();
  const now = new Date();
  const until = new Date(now.getTime() + HORIZON_HOURS * 3_600_000);

  const all = upcomingFixtures(res.db, toDbUtc(now), toDbUtc(until));
  const legs = legsForPredictions(
    res.db,
    all.flatMap((f) => (f.predictionId ? [f.predictionId] : [])),
  );
  const byPred = new Map<number, ValueLegRow[]>();
  for (const l of legs) byPred.set(l.predictionId, [...(byPred.get(l.predictionId) ?? []), l]);

  const rows = all
    .map((f) => {
      const own = f.predictionId ? (byPred.get(f.predictionId) ?? []) : [];
      const q = own.filter((l) => qualifies(l, settings));
      return {
        ...f,
        best: bestLeg(q),
        nQualifying: q.length,
        nFlagged: own.filter((l) => l.sanityStatus === "flagged").length,
      };
    })
    .filter((f) => !league || f.leagueKey === league)
    .filter((f) => !date || lagosToday(parseUtc(f.kickoffUtc)) === date)
    .filter((f) => !qualifyingOnly || f.best);

  const leagues = leagueKeys(res.db);
  const dates = [...new Set(all.map((f) => lagosToday(parseUtc(f.kickoffUtc))))];

  return (
    <div className="space-y-6">
      <PageHeader eyebrow={`Next ${HORIZON_HOURS} hours`} title="Fixtures">
        Scheduled matches in the next {HORIZON_HOURS} hours. A leg qualifies with no sanity flag, edge ≥{" "}
        {pctSigned(settings.minEdgeLeg)} and odds {odds2(settings.minOdds)}–{odds2(settings.maxOdds)}.
      </PageHeader>

      <form className="flex flex-wrap items-end gap-3 rounded-2xl bg-card p-4 text-sm ring-1 ring-border" method="get">
        <label className="flex flex-col gap-1.5 text-xs font-medium text-muted-foreground">
          League
          <select name="league" defaultValue={league} className="field">
            <option value="">All</option>
            {leagues.map((l) => (
              <option key={l.key} value={l.key}>
                {l.name}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1.5 text-xs font-medium text-muted-foreground">
          Date
          <select name="date" defaultValue={date} className="field">
            <option value="">All</option>
            {dates.map((d) => (
              <option key={d} value={d}>
                {d}
              </option>
            ))}
          </select>
        </label>
        <label className="flex h-9 items-center gap-2 font-medium">
          <input type="checkbox" name="q" value="1" defaultChecked={qualifyingOnly} />
          Qualifying only
        </label>
        <Button type="submit" size="sm" variant="outline">
          Filter
        </Button>
      </form>

      {all.length === 0 ? (
        <Empty title="No upcoming fixtures">
          Run <code>make odds</code> (or <strong>Run daily</strong>) to fetch SportyBet fixtures, then{" "}
          <code>make picks</code> to price them.
        </Empty>
      ) : rows.length === 0 ? (
        <Empty title="No fixtures match these filters" />
      ) : (
        <div className="overflow-x-auto">
          <Table className="text-sm">
            <TableHeader>
              <TableRow>
                <TableHead>League</TableHead>
                <TableHead>Kickoff</TableHead>
                <TableHead>Match</TableHead>
                <TableHead className="text-right">λ</TableHead>
                <TableHead className="text-right">μ</TableHead>
                <TableHead>Best qualifying leg</TableHead>
                <TableHead className="text-right">Flagged</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((f) => (
                <TableRow key={f.matchId}>
                  <TableCell>
                    <span className="flex items-center gap-1">
                      {f.leagueKey}
                      {f.leagueKey === "INTL" && <UnvalidatedBadge />}
                    </span>
                  </TableCell>
                  <TableCell>{formatKickoff(f.kickoffUtc)}</TableCell>
                  <TableCell className="whitespace-normal">
                    <Link href={`/fixtures/${f.matchId}`} className="hover:underline">
                      {f.homeName} v {f.awayName}
                    </Link>
                  </TableCell>
                  <TableCell className="text-right">
                    {f.lambdaHome === null ? "—" : <M v={f.modelVersion}>{f.lambdaHome.toFixed(2)}</M>}
                  </TableCell>
                  <TableCell className="text-right">
                    {f.lambdaAway === null ? "—" : <M v={f.modelVersion}>{f.lambdaAway.toFixed(2)}</M>}
                  </TableCell>
                  <TableCell>
                    {f.best ? (
                      <M v={f.modelVersion}>
                        {f.best.market} {selectionLabel(f.best.market, f.best.selection, f.best.line)} @{" "}
                        {odds2(f.best.odds)} ({pctSigned(f.best.edge)})
                        {f.nQualifying > 1 && ` +${f.nQualifying - 1} more`}
                      </M>
                    ) : f.predictionId ? (
                      <span className="text-muted-foreground">none</span>
                    ) : (
                      <span className="text-muted-foreground">not priced — run make picks</span>
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{f.nFlagged}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </div>
  );
}
