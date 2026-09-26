import { eq } from "drizzle-orm";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { getDb } from "@/db/client";
import { slips } from "@/db/generated/schema";
import { lagosToday, odds2, pct1, pct4sig } from "@/lib/format";

type SlipRow = typeof slips.$inferSelect;

const SLIP_CARDS = [
  { type: "daily_2odds", title: "Daily 2-odds", highRisk: false },
  { type: "mid_acca", title: "Mid accumulator", highRisk: false },
  { type: "mega_acca", title: "Mega accumulator", highRisk: true },
] as const;

export default async function TodayPage() {
  const today = lagosToday();
  const res = await getDb();

  if (!res.ok) {
    return (
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold">Today</h1>
        <Card>
          <CardHeader>
            <CardTitle>Database not found</CardTitle>
            <CardDescription>
              Run <code>make migrate</code> to create it. Expected at <code>{res.dbPath}</code>.
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  const todays: SlipRow[] = res.db.select().from(slips).where(eq(slips.slipDate, today)).all();

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold">Today</h1>
        <p className="text-sm text-muted-foreground">Slips for {today} (Africa/Lagos)</p>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        {SLIP_CARDS.map(({ type, title, highRisk }) => {
          const slip = todays.find((s) => s.slipType === type);
          return (
            <Card key={type}>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  {title}
                  {highRisk && <Badge variant="destructive">High risk</Badge>}
                </CardTitle>
                {slip ? (
                  <CardDescription>
                    Total odds {odds2(slip.totalOdds)} · win probability{" "}
                    {type === "mega_acca" ? pct4sig(slip.pAllWin) : pct1(slip.pAllWin)} ·{" "}
                    {slip.mode}
                  </CardDescription>
                ) : (
                  <CardDescription>
                    No qualifying slip today — the model found no value, or picks have not been
                    run yet. Run <code>make daily</code>.
                  </CardDescription>
                )}
              </CardHeader>
              {slip && (
                <CardContent className="text-sm">
                  Booking: {slip.bookingStatus}
                  {slip.bookingCode ? ` · ${slip.bookingCode}` : ""}
                </CardContent>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
}
