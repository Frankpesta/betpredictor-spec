import { ChevronRight } from "lucide-react";

import { LegsTable } from "@/components/legs-table";
import { BOOKING_LABEL } from "@/components/slip-card";
import { DbMissing, Empty, M, PageHeader, UnvalidatedBadge } from "@/components/status-bits";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getDb } from "@/db/client";
import { historySlips, type HistoryFilters } from "@/db/queries";
import { odds2, orDash, pct1, pct4sig } from "@/lib/format";
import { POOL_LABEL, SLIP_TYPE_LABEL, SLIP_TYPES } from "@/lib/labels";

const STATUSES = ["open", "won", "lost", "partial", "void"];
const MODES = ["paper", "placed"];
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function pick(v: string | string[] | undefined, allowed?: readonly string[]): string | undefined {
  const s = (Array.isArray(v) ? v[0] : v) ?? "";
  if (!s) return undefined;
  if (allowed && !allowed.includes(s)) return undefined;
  return s;
}

function Select({ name, value, options, labels }: {
  name: string;
  value?: string;
  options: readonly string[];
  labels?: Record<string, string>;
}) {
  return (
    <select name={name} defaultValue={value ?? ""} className="field">
      <option value="">All</option>
      {options.map((o) => (
        <option key={o} value={o}>
          {labels?.[o] ?? o}
        </option>
      ))}
    </select>
  );
}

export default async function HistoryPage(props: PageProps<"/history">) {
  const sp = await props.searchParams;
  const f: HistoryFilters = {
    type: pick(sp.type, SLIP_TYPES),
    status: pick(sp.status, STATUSES),
    mode: pick(sp.mode, MODES),
    pool: pick(sp.pool, ["club", "intl"]),
    from: pick(sp.from)?.match(DATE_RE) ? pick(sp.from) : undefined,
    to: pick(sp.to)?.match(DATE_RE) ? pick(sp.to) : undefined,
  };
  const res = await getDb();
  if (!res.ok) return <DbMissing dbPath={res.dbPath} error={res.error} />;
  const rows = historySlips(res.db, f);

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="Archive" title="History">
        All slips, newest first. Expand a slip for its legs, results and CLV.
      </PageHeader>

      <form method="get" className="flex flex-wrap items-end gap-3 rounded-2xl bg-card p-4 text-sm ring-1 ring-border">
        <label className="flex flex-col gap-1.5 text-xs font-medium text-muted-foreground">
          Type
          <Select name="type" value={f.type} options={SLIP_TYPES} labels={SLIP_TYPE_LABEL} />
        </label>
        <label className="flex flex-col gap-1.5 text-xs font-medium text-muted-foreground">
          Status
          <Select name="status" value={f.status} options={STATUSES} />
        </label>
        <label className="flex flex-col gap-1.5 text-xs font-medium text-muted-foreground">
          Mode
          <Select name="mode" value={f.mode} options={MODES} />
        </label>
        <label className="flex flex-col gap-1.5 text-xs font-medium text-muted-foreground">
          Pool
          <Select name="pool" value={f.pool} options={["club", "intl"]} labels={POOL_LABEL} />
        </label>
        <label className="flex flex-col gap-1.5 text-xs font-medium text-muted-foreground">
          From
          <input type="date" name="from" defaultValue={f.from} className="field" />
        </label>
        <label className="flex flex-col gap-1.5 text-xs font-medium text-muted-foreground">
          To
          <input type="date" name="to" defaultValue={f.to} className="field" />
        </label>
        <Button type="submit" size="sm" variant="outline">
          Filter
        </Button>
      </form>

      {rows.length === 0 ? (
        <Empty title="No slips yet">
          Slips appear after <code>make picks</code> (part of <strong>Run daily</strong>) finds qualifying legs.
        </Empty>
      ) : (
        <div className="divide-y divide-border/70 overflow-hidden rounded-2xl bg-card ring-1 ring-border">
          {rows.map((s) => {
            const mega = s.slipType === "mega_acca";
            return (
              <details key={s.id} className="group">
                <summary className="flex cursor-pointer list-none flex-wrap items-center gap-x-4 gap-y-1.5 px-5 py-3.5 text-sm transition-colors group-open:bg-muted/40 hover:bg-accent/40 [&::-webkit-details-marker]:hidden">
                  <ChevronRight className="size-4 text-muted-foreground transition-transform group-open:rotate-90" />
                  <span className="font-semibold tabular-nums">{s.slipDate}</span>
                  <span className="font-medium">{SLIP_TYPE_LABEL[s.slipType]}</span>
                  {s.pool === "intl" && <UnvalidatedBadge />}
                  <Badge
                    variant={s.status === "won" ? "default" : s.status === "lost" ? "destructive" : "secondary"}
                    className="capitalize"
                  >
                    {s.status}
                  </Badge>
                  <Badge variant="outline">{s.mode}</Badge>
                  <span className="text-muted-foreground">{s.legs.length} legs</span>
                  <span className="tabular-nums">@ {odds2(s.totalOdds)}</span>
                  <span>
                    p <M v={s.modelVersion}>{mega ? pct4sig(s.pAllWin) : pct1(s.pAllWin)}</M>
                  </span>
                  <span className="tabular-nums">return {orDash(s.returnMultiplier, (x) => `${odds2(x)}×`)}</span>
                  {s.mode === "placed" && s.stake !== null && (
                    <span className="tabular-nums">stake ₦{s.stake.toLocaleString("en-NG")}</span>
                  )}
                  <span className="text-muted-foreground">
                    {BOOKING_LABEL[s.bookingStatus] ?? s.bookingStatus}
                    {s.bookingCode && ` · ${s.bookingCode}`}
                  </span>
                </summary>
                <div className="bg-muted/20 px-5 pt-1 pb-5">
                  <LegsTable legs={s.legs} modelVersion={s.modelVersion} showResults />
                </div>
              </details>
            );
          })}
        </div>
      )}
    </div>
  );
}
