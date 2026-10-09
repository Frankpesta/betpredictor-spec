import Link from "next/link";

import { M, UnvalidatedBadge } from "@/components/status-bits";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import type { LegView } from "@/db/queries";
import { formatKickoff, odds2, orDash, pct1, pctSigned } from "@/lib/format";
import { isUnvalidatedMarket, marketLabel, RESULT_LABEL, selectionLabel } from "@/lib/labels";

type Props = { legs: LegView[]; modelVersion: string; showResults?: boolean };

/** Full leg list — always shown so a failed booking can be entered by hand (docs/04 §3.5). */
export function LegsTable({ legs, modelVersion, showResults = false }: Props) {
  return (
    <div className="overflow-x-auto">
      <Table className="text-xs">
        <TableHeader>
          <TableRow>
            <TableHead>Match</TableHead>
            <TableHead>Kickoff</TableHead>
            <TableHead>Market</TableHead>
            <TableHead>Selection</TableHead>
            <TableHead className="text-right">Odds</TableHead>
            <TableHead className="text-right">Model %</TableHead>
            <TableHead className="text-right">Edge</TableHead>
            {showResults && (
              <>
                <TableHead>Result</TableHead>
                <TableHead className="text-right">Closing</TableHead>
                <TableHead className="text-right">CLV</TableHead>
              </>
            )}
          </TableRow>
        </TableHeader>
        <TableBody>
          {legs.map((l) => (
            <TableRow key={`${l.slipId}-${l.legOrder}`}>
              <TableCell className="whitespace-normal">
                <Link href={`/fixtures/${l.matchId}`} className="hover:underline">
                  {l.homeName} v {l.awayName}
                </Link>
              </TableCell>
              <TableCell>{formatKickoff(l.kickoffUtc)}</TableCell>
              <TableCell className="whitespace-normal">
                {marketLabel(l.market)}
                {isUnvalidatedMarket(l.market) && (
                  <span className="ml-1.5">
                    <UnvalidatedBadge title="No historical odds for this market: never ROI-backtested, judged on paper (docs/05 §10, docs/10)" />
                  </span>
                )}
              </TableCell>
              <TableCell className="whitespace-normal">
                {selectionLabel(l.market, l.selection, l.line)}
                {l.qualifyReason && (
                  <span className="block text-[0.7rem] text-muted-foreground">{l.qualifyReason}</span>
                )}
              </TableCell>
              <TableCell className="text-right tabular-nums">{odds2(l.odds)}</TableCell>
              <TableCell className="text-right">
                <M v={modelVersion}>{pct1(l.pFinal)}</M>
              </TableCell>
              <TableCell className="text-right">
                <M v={modelVersion}>{pctSigned(l.edge)}</M>
              </TableCell>
              {showResults && (
                <>
                  <TableCell>{RESULT_LABEL[l.result] ?? l.result}</TableCell>
                  <TableCell className="text-right tabular-nums">{orDash(l.closingOdds, odds2)}</TableCell>
                  <TableCell className="text-right tabular-nums">{orDash(l.clv, pctSigned)}</TableCell>
                </>
              )}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}
