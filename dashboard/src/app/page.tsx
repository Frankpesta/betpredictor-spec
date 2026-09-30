import { SlipCard } from "@/components/slip-card";
import { DbMissing, PageHeader } from "@/components/status-bits";
import { getDb } from "@/db/client";
import { latestGate, todaysSlips } from "@/db/queries";
import { lagosToday, toDbUtc } from "@/lib/format";
import { POOL_LABEL, SLIP_TYPES } from "@/lib/labels";
import { loadSettings } from "@/lib/settings";

export default async function TodayPage() {
  const today = lagosToday();
  const res = await getDb();

  if (!res.ok) {
    return (
      <div className="space-y-6">
        <PageHeader eyebrow="Daily slips" title="Today" />
        <DbMissing dbPath={res.dbPath} error={res.error} />
      </div>
    );
  }

  const { selection } = loadSettings();
  const slips = todaysSlips(res.db, today, toDbUtc(new Date()), selection);
  const edgeValidated = latestGate(res.db).gatePassed === 1;
  const hasIntl = slips.some((s) => s.pool === "intl");

  return (
    <div className="space-y-10">
      <PageHeader eyebrow={`${today} · Africa/Lagos`} title="Today">
        Slips are paper bets unless you mark them placed.
        {slips.length === 0 && (
          <>
            {" "}
            No slips today. If today&apos;s run has not happened yet, press <strong>Run daily</strong> (or{" "}
            <code>make daily</code>); if it has,{" "}
            {selection === "value" ? "the model found no value" : "no combination met the slip rules"} — see
            Fixtures and Data health for details.
          </>
        )}
      </PageHeader>
      {(["club", ...(hasIntl ? ["intl"] : [])] as const).map((pool) => (
        <section key={pool} className="space-y-4">
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-bold">{POOL_LABEL[pool]}</h2>
            <div className="h-px flex-1 bg-gradient-to-r from-border to-transparent" />
          </div>
          {pool === "intl" && (
            <p className="text-sm text-muted-foreground">
              International slips use only international legs and are never mixed with club legs.
              The international model is unvalidated (no historical odds to backtest against).
            </p>
          )}
          <div className="grid gap-6">
            {SLIP_TYPES.map((type) => (
              <SlipCard
                key={type}
                slipType={type}
                pool={pool}
                slip={slips.find((s) => s.slipType === type && s.pool === pool)}
                edgeValidated={edgeValidated}
                strategy={selection}
              />
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}
