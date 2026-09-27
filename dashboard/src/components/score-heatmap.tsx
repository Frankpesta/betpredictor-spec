"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";

type Props = { matrix: number[][]; home: string; away: string; modelVersion: string };

const DEFAULT_SIZE = 6; // docs/07 §2.2: show up to 6×6 by default
const FULL_SIZE = 11;

/** Scoreline probabilities: rows = home goals, columns = away goals. */
export function ScoreHeatmap({ matrix, home, away, modelVersion }: Props) {
  const [size, setSize] = useState(DEFAULT_SIZE);
  const n = Math.min(size, matrix.length);
  const cells = matrix.slice(0, n).map((row) => row.slice(0, n));
  const peak = Math.max(...cells.flat(), 1e-12);
  const shown = cells.flat().reduce((a, b) => a + b, 0);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-muted-foreground">
          Rows: {home} goals · columns: {away} goals · {(shown * 100).toFixed(1)}% of probability shown
        </span>
        <Button
          size="xs"
          variant="outline"
          onClick={() => setSize(size === DEFAULT_SIZE ? FULL_SIZE : DEFAULT_SIZE)}
        >
          {size === DEFAULT_SIZE ? `Show ${FULL_SIZE}×${FULL_SIZE}` : `Show ${DEFAULT_SIZE}×${DEFAULT_SIZE}`}
        </Button>
      </div>
      <div className="overflow-x-auto">
        <table className="border-collapse text-xs tabular-nums" title={`model ${modelVersion}`}>
          <thead>
            <tr>
              <th className="p-1" />
              {cells[0]?.map((_, j) => (
                <th key={j} className="w-12 p-1 text-center font-medium text-muted-foreground">
                  {j}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {cells.map((row, i) => (
              <tr key={i}>
                <th className="p-1 pr-2 text-right font-medium text-muted-foreground">{i}</th>
                {row.map((p, j) => {
                  const t = p / peak;
                  return (
                    <td
                      key={j}
                      className="h-9 w-12 border border-background text-center"
                      style={{
                        backgroundColor: `color-mix(in oklab, var(--series-1) ${Math.round(t * 85)}%, transparent)`,
                      }}
                      title={`${i}–${j}: ${(p * 100).toFixed(1)}% (model ${modelVersion})`}
                    >
                      {(p * 100).toFixed(1)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
