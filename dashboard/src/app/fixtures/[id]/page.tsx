import Link from "next/link";

import { ScoreHeatmap } from "@/components/score-heatmap";
import { DbMissing, Empty, M, UnvalidatedBadge } from "@/components/status-bits";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { getDb } from "@/db/client";
import { fixtureDetail, legsForPredictions } from "@/db/queries";
import { formatKickoff, odds2, pct1, pctSigned } from "@/lib/format";
import { marketLabel, selectionLabel } from "@/lib/labels";
import { loadSettings, qualifies } from "@/lib/settings";

export default async function FixturePage(props: PageProps<"/fixtures/[id]">) {
  const { id } = await props.params;
  const res = await getDb();
  if (!res.ok) return <DbMissing dbPath={res.dbPath} error={res.error} />;
  const matchId = Number(id);
  const f = Number.isInteger(matchId) ? fixtureDetail(res.db, matchId) : undefined;
  if (!f) {
    return (
      <Empty title="Match not found">
        <Link href="/fixtures" className="underline">
          Back to fixtures
        </Link>
      </Empty>
    );
  }
  const settings = loadSettings();
  const legs = f.predictionId ? legsForPredictions(res.db, [f.predictionId]) : [];
  let matrix: number[][] | null = null;
  if (f.scoreMatrixJson) {
    const parsed: unknown = JSON.parse(f.scoreMatrixJson);
    if (Array.isArray(parsed)) matrix = parsed as number[][];
  }
  const v = f.modelVersion ?? "unknown";

  return (
    <div className="space-y-6">
      <div className="space-y-1.5">
        <Link href="/fixtures" className="eyebrow hover:underline">
          ← Fixtures
        </Link>
        <h1 className="flex flex-wrap items-center gap-2 text-3xl font-bold">
          {f.homeName} v {f.awayName}
          {f.leagueKey === "INTL" && <UnvalidatedBadge />}
        </h1>
        <p className="text-sm text-muted-foreground">
          {f.leagueName} · {formatKickoff(f.kickoffUtc)} · {f.status}
          {f.neutral === 1 && " · neutral venue"}
          {f.homeGoals !== null && f.awayGoals !== null && ` · final ${f.homeGoals}–${f.awayGoals}`}
        </p>
        {f.lambdaHome !== null && f.lambdaAway !== null && (
          <p className="text-sm">
            Expected goals: λ <M v={v}>{f.lambdaHome.toFixed(2)}</M> · μ{" "}
            <M v={v}>{f.lambdaAway.toFixed(2)}</M>
          </p>
        )}
      </div>

      {!f.predictionId ? (
        <Empty title="No prediction for this match yet">
          Run <code>make picks</code> (or <strong>Run daily</strong>) to price it.
        </Empty>
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Scoreline probabilities (%)</CardTitle>
            </CardHeader>
            <CardContent>
              {matrix ? (
                <ScoreHeatmap matrix={matrix} home={f.homeName} away={f.awayName} modelVersion={v} />
              ) : (
                <p className="text-sm text-muted-foreground">Score matrix unavailable.</p>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Every priced line</CardTitle>
            </CardHeader>
            <CardContent>
              {legs.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No AH/OU lines were priced (no odds snapshot). Run <code>make odds</code> then{" "}
                  <code>make picks</code>.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <Table className="text-xs">
                    <TableHeader>
                      <TableRow>
                        <TableHead>Market</TableHead>
                        <TableHead>Selection</TableHead>
                        <TableHead className="text-right">Model %</TableHead>
                        <TableHead className="text-right">Market devig %</TableHead>
                        <TableHead className="text-right">Final %</TableHead>
                        <TableHead className="text-right">Odds</TableHead>
                        <TableHead className="text-right">Edge</TableHead>
                        <TableHead>Sanity</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {legs.map((l) => (
                        <TableRow key={l.id}>
                          <TableCell>{marketLabel(l.market)}</TableCell>
                          <TableCell>{selectionLabel(l.market, l.selection, l.line)}</TableCell>
                          <TableCell className="text-right">
                            <M v={v}>{pct1(l.pModel)}</M>
                          </TableCell>
                          <TableCell className="text-right tabular-nums">{pct1(l.pMarketDevig)}</TableCell>
                          <TableCell className="text-right">
                            <M v={v}>{pct1(l.pFinal)}</M>
                          </TableCell>
                          <TableCell className="text-right tabular-nums">{odds2(l.odds)}</TableCell>
                          <TableCell className="text-right">
                            <M v={v}>{pctSigned(l.edge)}</M>
                          </TableCell>
                          <TableCell className="whitespace-normal">
                            {l.sanityStatus === "flagged" ? (
                              <Badge variant="destructive" title="Flagged legs are stored but never used in a slip">
                                flagged: {l.sanityReason ?? "?"}
                              </Badge>
                            ) : qualifies(l, settings) ? (
                              <Badge className="bg-emerald-600 text-white">qualifies</Badge>
                            ) : (
                              <span className="text-muted-foreground">ok</span>
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
        </>
      )}
    </div>
  );
}
