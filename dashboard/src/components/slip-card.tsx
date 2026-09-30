import { Layers, Rocket, Ticket } from "lucide-react";

import { LegsTable } from "@/components/legs-table";
import { SlipActions } from "@/components/slip-actions";
import { M, Stat, UnvalidatedBadge } from "@/components/status-bits";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { SlipWithLegs } from "@/db/queries";
import { odds2, pct1, pct4sig } from "@/lib/format";
import { SLIP_TYPE_LABEL } from "@/lib/labels";
import { cn } from "@/lib/utils";

export const BOOKING_LABEL: Record<string, string> = {
  pending: "Not booked yet",
  booked: "Booked",
  failed: "Booking failed",
  manual: "Booked manually",
};

/** Visual identity per slip type: accent bar + icon chip. */
const TYPE_STYLE: Record<string, { icon: typeof Ticket; bar: string; chip: string }> = {
  daily_2odds: {
    icon: Ticket,
    bar: "from-emerald-400 to-teal-500",
    chip: "bg-emerald-500/12 text-emerald-700 dark:text-emerald-300",
  },
  mid_acca: {
    icon: Layers,
    bar: "from-sky-400 to-indigo-500",
    chip: "bg-sky-500/12 text-sky-700 dark:text-sky-300",
  },
  mega_acca: {
    icon: Rocket,
    bar: "from-orange-400 to-rose-500",
    chip: "bg-orange-500/12 text-orange-700 dark:text-orange-300",
  },
};

type Props = {
  slipType: string;
  pool: string;
  slip?: SlipWithLegs;
  edgeValidated: boolean;
  /** docs/05 §8: the selection rule in settings (a slip carries its own `strategy`). */
  strategy: string;
};

export function SlipCard({ slipType, pool, slip, edgeValidated, strategy }: Props) {
  const mega = slipType === "mega_acca";
  const style = TYPE_STYLE[slipType] ?? TYPE_STYLE.daily_2odds;
  const Icon = style.icon;
  return (
    <Card className={cn("relative pt-6", !slip && "bg-card/60")}>
      <div className={cn("absolute inset-x-0 top-0 h-1 bg-gradient-to-r", style.bar, !slip && "opacity-30")} />
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2.5 text-lg">
          <span className={cn("grid size-9 place-items-center rounded-xl", style.chip)}>
            <Icon className="size-4.5" />
          </span>
          {SLIP_TYPE_LABEL[slipType]}
          {mega && <Badge variant="destructive">High risk</Badge>}
          {pool === "intl" && <UnvalidatedBadge />}
          {slip && (
            <Badge variant="outline" className="capitalize">
              {slip.mode}
            </Badge>
          )}
          {slip && (
            <Badge
              variant="secondary"
              title={
                slip.strategy === "likeliest"
                  ? "Built from the most probable legs (docs/05 §8), not from value"
                  : "Built from legs with estimated positive value (docs/05 §2.2)"
              }
            >
              {slip.strategy === "likeliest" ? "Likeliest" : "Value"}
            </Badge>
          )}
          {slip && slip.status !== "open" && (
            <Badge variant="secondary" className="capitalize">
              {slip.status}
            </Badge>
          )}
        </CardTitle>
        {!slip && (
          <CardDescription>
            {strategy === "value"
              ? "No qualifying slip today — the model found no value."
              : "No qualifying slip today — no combination met this slip's rules."}
          </CardDescription>
        )}
      </CardHeader>
      {slip && (
        <CardContent className="space-y-5">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <Stat label="Total odds" value={odds2(slip.totalOdds)} sub={`${slip.legs.length} legs`} />
            <Stat
              label="Win probability"
              value={<M v={slip.modelVersion}>{mega ? pct4sig(slip.pAllWin) : pct1(slip.pAllWin)}</M>}
              sub="model estimate"
            />
            <Stat
              label="Expected return"
              value={<M v={slip.modelVersion}>{odds2(slip.expectedMultiplier)}×</M>}
              sub="per unit staked"
              tone={slip.expectedMultiplier > 1 ? "positive" : "default"}
            />
          </div>
          <div className="overflow-hidden rounded-xl ring-1 ring-border/70">
            <LegsTable legs={slip.legs} modelVersion={slip.modelVersion} />
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed border-border bg-muted/40 px-4 py-3">
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <span className="text-[0.7rem] font-semibold tracking-wider text-muted-foreground uppercase">
                {BOOKING_LABEL[slip.bookingStatus] ?? slip.bookingStatus}
              </span>
              {slip.bookingCode && (
                <code className="rounded-lg bg-card px-3 py-1 font-mono text-lg font-semibold tracking-[0.2em] ring-1 ring-border">
                  {slip.bookingCode}
                </code>
              )}
            </div>
            {slip.bookingError && <p className="w-full text-xs text-destructive">{slip.bookingError}</p>}
          </div>
          <SlipActions
            slipId={slip.id}
            bookingCode={slip.bookingCode}
            bookingStatus={slip.bookingStatus}
            mode={slip.mode}
            status={slip.status}
            edgeValidated={edgeValidated}
          />
        </CardContent>
      )}
    </Card>
  );
}
