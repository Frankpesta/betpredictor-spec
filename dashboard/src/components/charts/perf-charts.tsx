"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

// Colours are the validated reference palette in globals.css (--series-*), in fixed
// order; text stays in the theme's ink colours. One y-axis per chart, always.

const SERIES = ["var(--series-1)", "var(--series-2)", "var(--series-3)", "var(--series-4)"];
const AXIS = { stroke: "var(--muted-foreground)", fontSize: 11, tickLine: false } as const;
const GRID = <CartesianGrid stroke="var(--border)" strokeDasharray="0" vertical={false} />;
const TOOLTIP_STYLE = {
  contentStyle: {
    background: "var(--popover)",
    border: "1px solid var(--border)",
    borderRadius: 8,
    color: "var(--popover-foreground)",
    fontSize: 12,
  },
  labelStyle: { color: "var(--popover-foreground)" },
};

// Value axes always include 0 so bars and curves are read against the break-even line.
const WITH_ZERO: [(min: number) => number, (max: number) => number] = [
  (min) => Math.min(0, min),
  (max) => Math.max(0, max),
];

// Legend text stays in ink colour; the swatch beside it carries the series identity.
const legendText = (value: string) => <span style={{ color: "var(--foreground)" }}>{value}</span>;

function units(v: unknown): string {
  return typeof v === "number" ? `${v > 0 ? "+" : ""}${v.toFixed(2)} u` : String(v);
}
function pct(v: unknown): string {
  return typeof v === "number" ? `${(v * 100).toFixed(1)}%` : String(v);
}

export function EquityChart({
  data,
  series,
}: {
  data: Record<string, number | string>[];
  series: { key: string; label: string }[];
}) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
        {GRID}
        <XAxis dataKey="date" {...AXIS} minTickGap={24} />
        <YAxis {...AXIS} width={48} domain={WITH_ZERO} tickFormatter={(v: number) => v.toFixed(1)} />
        <ReferenceLine y={0} stroke="var(--series-ref)" />
        <Tooltip {...TOOLTIP_STYLE} formatter={(v) => units(v)} />
        {series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} formatter={legendText} />}
        {series.map((s, i) => (
          <Line
            key={s.key}
            type="linear"
            dataKey={s.key}
            name={s.label}
            stroke={SERIES[i % SERIES.length]}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

/** Single-series ROI bars by group (market / league / odds band / month). */
export function RoiBars({ data }: { data: { group: string; roi: number; n: number }[] }) {
  return (
    <ResponsiveContainer width="100%" height={Math.max(160, data.length * 34 + 40)}>
      <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, bottom: 0, left: 8 }}>
        <CartesianGrid stroke="var(--border)" horizontal={false} />
        <XAxis
          type="number"
          {...AXIS}
          domain={WITH_ZERO}
          tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
        />
        <YAxis type="category" dataKey="group" {...AXIS} width={90} />
        <ReferenceLine x={0} stroke="var(--series-ref)" />
        <Tooltip
          {...TOOLTIP_STYLE}
          formatter={(v, _n, item) => [`${pct(v)} (${(item.payload as { n: number }).n} legs)`, "ROI"]}
        />
        <Bar dataKey="roi" name="ROI" fill={SERIES[0]} radius={4} barSize={16} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

export function CalibrationChart({
  bins,
}: {
  bins: { mid: number; predicted: number | null; observed: number | null; count: number }[];
}) {
  const points = bins
    .filter((b) => b.predicted !== null && b.observed !== null)
    .map((b) => ({ predicted: b.predicted, observed: b.observed, count: b.count }));
  return (
    <ResponsiveContainer width="100%" height={280}>
      <ComposedChart margin={{ top: 8, right: 16, bottom: 16, left: 0 }}>
        {GRID}
        <XAxis
          type="number"
          dataKey="predicted"
          domain={[0, 1]}
          ticks={[0, 0.2, 0.4, 0.6, 0.8, 1]}
          {...AXIS}
          tickFormatter={(v: number) => `${v * 100}%`}
          label={{ value: "Predicted", position: "insideBottom", offset: -8, fontSize: 11 }}
        />
        <YAxis
          type="number"
          dataKey="observed"
          domain={[0, 1]}
          ticks={[0, 0.2, 0.4, 0.6, 0.8, 1]}
          {...AXIS}
          width={44}
          tickFormatter={(v: number) => `${v * 100}%`}
        />
        <ReferenceLine
          segment={[
            { x: 0, y: 0 },
            { x: 1, y: 1 },
          ]}
          stroke="var(--series-ref)"
          strokeDasharray="4 4"
          ifOverflow="visible"
        />
        <Tooltip
          {...TOOLTIP_STYLE}
          formatter={(v, name) => [pct(v), name === "observed" ? "Observed" : "Predicted"]}
          labelFormatter={() => ""}
        />
        <Scatter data={points} dataKey="observed" name="observed" fill={SERIES[0]} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function ExpectedVsActual({
  data,
}: {
  data: { type: string; expected: number; actual: number }[];
}) {
  return (
    <ResponsiveContainer width="100%" height={240}>
      <BarChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }} barGap={2}>
        {GRID}
        <XAxis dataKey="type" {...AXIS} />
        <YAxis {...AXIS} width={48} domain={WITH_ZERO} tickFormatter={(v: number) => v.toFixed(1)} />
        <ReferenceLine y={0} stroke="var(--series-ref)" />
        <Tooltip {...TOOLTIP_STYLE} formatter={(v) => units(v)} />
        <Legend wrapperStyle={{ fontSize: 12 }} formatter={legendText} />
        <Bar dataKey="expected" name="Expected profit" fill={SERIES[0]} radius={4} isAnimationActive={false} />
        <Bar dataKey="actual" name="Actual profit" fill={SERIES[1]} radius={4} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
