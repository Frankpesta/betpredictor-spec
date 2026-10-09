import { CircleCheck, FlaskConical, Inbox, TriangleAlert } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { NO_EDGE_COPY } from "@/lib/labels";
import { cn } from "@/lib/utils";

export function GateBadge({ gatePassed }: { gatePassed: number | null }) {
  if (gatePassed === 1) {
    return (
      <Badge
        className="h-6 gap-1.5 bg-emerald-500/15 px-2.5 text-emerald-700 ring-1 ring-emerald-500/30 dark:text-emerald-300"
        title="Latest backtest passed the gate (docs/03 §10.5)"
      >
        <CircleCheck />
        Edge validated
      </Badge>
    );
  }
  return (
    <Badge
      className="h-6 gap-1.5 bg-amber-400/20 px-2.5 text-amber-800 ring-1 ring-amber-500/35 dark:text-amber-200"
      title={gatePassed === null ? "No backtest has run yet" : "Latest backtest failed the gate"}
    >
      <FlaskConical />
      Paper mode recommended
    </Badge>
  );
}

export function NoEdgeBanner() {
  return (
    <div className="mx-auto w-full max-w-7xl px-4 pt-4">
      <div
        role="status"
        className="flex items-center gap-3 rounded-xl border border-amber-400/40 bg-amber-50/80 px-4 py-2.5 text-sm text-amber-950 backdrop-blur dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-100"
      >
        <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-amber-400/25 text-amber-700 dark:text-amber-300">
          <TriangleAlert className="size-4" />
        </span>
        <p className="font-medium">{NO_EDGE_COPY}</p>
      </div>
    </div>
  );
}

export function UnvalidatedBadge({
  title = "Internationals have no historical odds: ROI/CLV were never backtested (docs/09 §4)",
}: { title?: string } = {}) {
  return (
    <Badge
      variant="outline"
      className="border-amber-500/50 bg-amber-400/10 text-amber-700 dark:text-amber-300"
      title={title}
    >
      Unvalidated
    </Badge>
  );
}

/** A model-derived number; hovering shows the model version (docs/07 §2.3). */
export function M({ v, children }: { v: string | null | undefined; children: React.ReactNode }) {
  return (
    <span title={`model ${v ?? "unknown"}`} className="cursor-help tabular-nums">
      {children}
    </span>
  );
}

/** Consistent page title block: eyebrow, title, description and optional right-hand actions. */
export function PageHeader({
  eyebrow,
  title,
  children,
  actions,
}: {
  eyebrow?: string;
  title: React.ReactNode;
  children?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="max-w-3xl space-y-1.5">
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1 className="text-3xl font-bold">{title}</h1>
        {children && <div className="text-sm leading-relaxed text-muted-foreground">{children}</div>}
      </div>
      {actions}
    </div>
  );
}

/** Big-number tile used for slip and performance summaries. */
export function Stat({
  label,
  value,
  sub,
  tone = "default",
  className,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "default" | "positive" | "negative";
  className?: string;
}) {
  return (
    <div className={cn("rounded-xl bg-muted/60 px-4 py-3 ring-1 ring-border/60", className)}>
      <p className="text-[0.7rem] font-semibold tracking-wider text-muted-foreground uppercase">{label}</p>
      <p
        className={cn(
          "mt-1 text-2xl font-bold tracking-tight tabular-nums",
          tone === "positive" && "text-emerald-600 dark:text-emerald-400",
          tone === "negative" && "text-red-600 dark:text-red-400",
        )}
      >
        {value}
      </p>
      {sub && <p className="mt-0.5 text-xs text-muted-foreground">{sub}</p>}
    </div>
  );
}

export function Empty({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="flex items-start gap-4 rounded-2xl border border-dashed border-border bg-card/50 p-6 text-sm">
      <span className="grid size-10 shrink-0 place-items-center rounded-xl bg-accent text-accent-foreground">
        <Inbox className="size-5" />
      </span>
      <div>
        <p className="font-semibold">{title}</p>
        {children && <div className="mt-1 leading-relaxed text-muted-foreground">{children}</div>}
      </div>
    </div>
  );
}

export function DbMissing({ dbPath, error }: { dbPath: string; error: string }) {
  return (
    <Empty title="Database not found">
      Run <code>make migrate</code> to create it. Expected at <code>{dbPath}</code> ({error}).
    </Empty>
  );
}
