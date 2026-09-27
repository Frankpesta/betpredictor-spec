// Display helpers. All stored times are UTC; display is Africa/Lagos (docs/07 §2).

export const DISPLAY_TZ = "Africa/Lagos";

/** Parse a SQLite UTC timestamp ("YYYY-MM-DD HH:MM:SS[.ffffff]") as UTC. */
export function parseUtc(ts: string): Date {
  const iso = ts.includes("T") ? ts : ts.replace(" ", "T");
  return new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
}

/** e.g. "Sat 27 Sep, 15:00" in Lagos time. */
export function formatKickoff(ts: string | Date): string {
  const d = typeof ts === "string" ? parseUtc(ts) : ts;
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: DISPLAY_TZ,
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(d);
  const get = (t: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("weekday")} ${get("day")} ${get("month")}, ${get("hour")}:${get("minute")}`;
}

/** Today's date in Lagos as YYYY-MM-DD (matches slips.slip_date). */
export function lagosToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: DISPLAY_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

/** Percent to 1 dp (docs/07 §2.3). */
export function pct1(p: number): string {
  return `${(p * 100).toFixed(1)}%`;
}

/** Mega acca probability: 4 significant figures, e.g. "0.01523%". */
export function pct4sig(p: number): string {
  return `${(p * 100).toPrecision(4)}%`;
}

/** Odds to 2 dp. */
export function odds2(o: number): string {
  return o.toFixed(2);
}

/** Signed percent to 1 dp, e.g. "+3.2%" (edges, ROI, CLV). */
export function pctSigned(p: number): string {
  const v = (p * 100).toFixed(1);
  return p > 0 ? `+${v}%` : `${v}%`;
}

/** Optional-number helper: "—" for null/undefined. */
export function orDash<T>(v: T | null | undefined, f: (x: T) => string): string {
  return v === null || v === undefined ? "—" : f(v);
}

/** e.g. "Sat 27 Sep 2026, 15:00" — job runs and fit dates. */
export function formatDateTime(ts: string | Date): string {
  const d = typeof ts === "string" ? parseUtc(ts) : ts;
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: DISPLAY_TZ,
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(d);
  const get = (t: Intl.DateTimeFormatPartTypes) => parts.find((p) => p.type === t)?.value ?? "";
  return `${get("weekday")} ${get("day")} ${get("month")} ${get("year")}, ${get("hour")}:${get("minute")}`;
}

/** "3h 12m ago" style age. */
export function ago(ts: string, now: Date = new Date()): string {
  const mins = Math.max(0, Math.round((now.getTime() - parseUtc(ts).getTime()) / 60000));
  if (mins < 60) return `${mins}m ago`;
  const h = Math.floor(mins / 60);
  if (h < 48) return `${h}h ${mins % 60}m ago`;
  return `${Math.floor(h / 24)}d ago`;
}

/** Hours since a UTC timestamp. */
export function hoursSince(ts: string, now: Date = new Date()): number {
  return (now.getTime() - parseUtc(ts).getTime()) / 3_600_000;
}

/** A JS Date as the engine's stored UTC text ("YYYY-MM-DD HH:MM:SS"), for SQL comparisons. */
export function toDbUtc(d: Date): string {
  return d.toISOString().slice(0, 19).replace("T", " ");
}
