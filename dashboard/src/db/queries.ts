import "server-only";

import { and, asc, desc, eq, gte, inArray, lte, max, sql, type SQL } from "drizzle-orm";
import { alias } from "drizzle-orm/sqlite-core";

import type { Db } from "./client";
import {
  backtestRuns,
  jobRuns,
  leagues,
  matches,
  modelRuns,
  predictions,
  slipLegs,
  slips,
  teams,
  unresolvedNames,
  valueLegs,
} from "./generated/schema";

// Read-only queries for server components. Timestamps are stored as naive UTC text
// ("YYYY-MM-DD HH:MM:SS[.ffffff]"), so comparisons use the same text format (toDbUtc).

const home = alias(teams, "home_team");
const away = alias(teams, "away_team");

export type SlipRow = typeof slips.$inferSelect;
export type ValueLegRow = typeof valueLegs.$inferSelect;

// ---------------------------------------------------------------------------
// Global status
// ---------------------------------------------------------------------------

export function latestGate(db: Db): { gatePassed: number | null; finishedAt: string | null } {
  const row = db
    .select({ gatePassed: backtestRuns.gatePassed, finishedAt: backtestRuns.finishedAt })
    .from(backtestRuns)
    .where(sql`${backtestRuns.finishedAt} IS NOT NULL`)
    .orderBy(desc(backtestRuns.finishedAt), desc(backtestRuns.id))
    .limit(1)
    .get();
  return { gatePassed: row?.gatePassed ?? null, finishedAt: row?.finishedAt ?? null };
}

/** Latest successful run of a job (e.g. "odds"), or null. */
export function lastSuccess(db: Db, jobName: string): string | null {
  const row = db
    .select({ finishedAt: jobRuns.finishedAt })
    .from(jobRuns)
    .where(and(eq(jobRuns.jobName, jobName), eq(jobRuns.status, "success")))
    .orderBy(desc(jobRuns.finishedAt))
    .limit(1)
    .get();
  return row?.finishedAt ?? null;
}

// ---------------------------------------------------------------------------
// Slips + legs
// ---------------------------------------------------------------------------

export type LegView = {
  slipId: number;
  legOrder: number;
  result: string;
  resultMultiplier: number | null;
  closingOdds: number | null;
  clv: number | null;
  valueLegId: number;
  matchId: number;
  market: string;
  line: number;
  selection: string;
  odds: number;
  pFinal: number;
  pModel: number;
  edge: number;
  kickoffUtc: string;
  homeName: string;
  awayName: string;
  leagueKey: string;
};

export function legsForSlips(db: Db, slipIds: number[]): Map<number, LegView[]> {
  const out = new Map<number, LegView[]>();
  if (slipIds.length === 0) return out;
  const rows = db
    .select({
      slipId: slipLegs.slipId,
      legOrder: slipLegs.legOrder,
      result: slipLegs.result,
      resultMultiplier: slipLegs.resultMultiplier,
      closingOdds: slipLegs.closingOdds,
      clv: slipLegs.clv,
      valueLegId: valueLegs.id,
      matchId: matches.id,
      market: valueLegs.market,
      line: valueLegs.line,
      selection: valueLegs.selection,
      odds: valueLegs.odds,
      pFinal: valueLegs.pFinal,
      pModel: valueLegs.pModel,
      edge: valueLegs.edge,
      kickoffUtc: matches.kickoffUtc,
      homeName: home.canonicalName,
      awayName: away.canonicalName,
      leagueKey: leagues.key,
    })
    .from(slipLegs)
    .innerJoin(valueLegs, eq(valueLegs.id, slipLegs.valueLegId))
    .innerJoin(matches, eq(matches.id, valueLegs.matchId))
    .innerJoin(home, eq(home.id, matches.homeTeamId))
    .innerJoin(away, eq(away.id, matches.awayTeamId))
    .innerJoin(leagues, eq(leagues.id, matches.leagueId))
    .where(inArray(slipLegs.slipId, slipIds))
    .orderBy(asc(slipLegs.slipId), asc(slipLegs.legOrder))
    .all();
  for (const r of rows) {
    const list = out.get(r.slipId) ?? [];
    list.push(r);
    out.set(r.slipId, list);
  }
  return out;
}

export type SlipWithLegs = SlipRow & { legs: LegView[] };

function withLegs(db: Db, rows: SlipRow[]): SlipWithLegs[] {
  const legs = legsForSlips(
    db,
    rows.map((s) => s.id),
  );
  return rows.map((s) => ({ ...s, legs: legs.get(s.id) ?? [] }));
}

/**
 * Today's slips per type and pool: daily/mid slips dated today (Lagos); the mega acca
 * is one per weekend window, so show the latest mega whose window has not ended.
 */
export function todaysSlips(db: Db, today: string, nowUtc: string): SlipWithLegs[] {
  const dated = db.select().from(slips).where(eq(slips.slipDate, today)).all();
  const mega = db
    .select()
    .from(slips)
    .where(and(eq(slips.slipType, "mega_acca"), gte(slips.windowEndUtc, nowUtc)))
    .orderBy(desc(slips.slipDate), desc(slips.id))
    .all();
  const byKey = new Map<string, SlipRow>();
  for (const s of [...dated, ...mega]) {
    const key = `${s.pool}:${s.slipType}`;
    if (!byKey.has(key)) byKey.set(key, s);
  }
  return withLegs(db, [...byKey.values()]);
}

export type HistoryFilters = {
  type?: string;
  status?: string;
  mode?: string;
  pool?: string;
  from?: string;
  to?: string;
};

export function historySlips(db: Db, f: HistoryFilters, limit = 300): SlipWithLegs[] {
  const conds: SQL[] = [];
  if (f.type) conds.push(eq(slips.slipType, f.type));
  if (f.status) conds.push(eq(slips.status, f.status));
  if (f.mode) conds.push(eq(slips.mode, f.mode));
  if (f.pool) conds.push(eq(slips.pool, f.pool));
  if (f.from) conds.push(gte(slips.slipDate, f.from));
  if (f.to) conds.push(lte(slips.slipDate, f.to));
  const rows = db
    .select()
    .from(slips)
    .where(conds.length ? and(...conds) : undefined)
    .orderBy(desc(slips.slipDate), desc(slips.id))
    .limit(limit)
    .all();
  return withLegs(db, rows);
}

export function failedBookings(db: Db): SlipWithLegs[] {
  const rows = db
    .select()
    .from(slips)
    .where(and(eq(slips.bookingStatus, "failed"), eq(slips.status, "open")))
    .orderBy(desc(slips.slipDate), desc(slips.id))
    .all();
  return withLegs(db, rows);
}

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

export type FixtureRow = {
  matchId: number;
  kickoffUtc: string;
  leagueKey: string;
  leagueName: string;
  homeName: string;
  awayName: string;
  status: string;
  predictionId: number | null;
  lambdaHome: number | null;
  lambdaAway: number | null;
  modelVersion: string | null;
};

/** Latest prediction id per match (predictions are unique per match + model run). */
function latestPredictionIds(db: Db) {
  return db
    .select({ matchId: predictions.matchId, predictionId: max(predictions.id).as("prediction_id") })
    .from(predictions)
    .groupBy(predictions.matchId)
    .as("latest_pred");
}

export function upcomingFixtures(db: Db, fromUtc: string, toUtc: string): FixtureRow[] {
  const latest = latestPredictionIds(db);
  return db
    .select({
      matchId: matches.id,
      kickoffUtc: matches.kickoffUtc,
      leagueKey: leagues.key,
      leagueName: leagues.name,
      homeName: home.canonicalName,
      awayName: away.canonicalName,
      status: matches.status,
      predictionId: predictions.id,
      lambdaHome: predictions.lambdaHome,
      lambdaAway: predictions.lambdaAway,
      modelVersion: modelRuns.modelVersion,
    })
    .from(matches)
    .innerJoin(leagues, eq(leagues.id, matches.leagueId))
    .innerJoin(home, eq(home.id, matches.homeTeamId))
    .innerJoin(away, eq(away.id, matches.awayTeamId))
    .leftJoin(latest, eq(latest.matchId, matches.id))
    .leftJoin(predictions, eq(predictions.id, latest.predictionId))
    .leftJoin(modelRuns, eq(modelRuns.id, predictions.modelRunId))
    .where(
      and(
        gte(matches.kickoffUtc, fromUtc),
        lte(matches.kickoffUtc, toUtc),
        eq(matches.status, "scheduled"),
      ),
    )
    .orderBy(asc(matches.kickoffUtc), asc(matches.id))
    .all();
}

export function legsForPredictions(db: Db, predictionIds: number[]): ValueLegRow[] {
  if (predictionIds.length === 0) return [];
  return db
    .select()
    .from(valueLegs)
    .where(inArray(valueLegs.predictionId, predictionIds))
    .orderBy(asc(valueLegs.market), asc(valueLegs.line), asc(valueLegs.selection))
    .all();
}

export type FixtureDetail = FixtureRow & {
  homeGoals: number | null;
  awayGoals: number | null;
  neutral: number;
  scoreMatrixJson: string | null;
  modelRunId: number | null;
};

export function fixtureDetail(db: Db, matchId: number): FixtureDetail | undefined {
  const latest = latestPredictionIds(db);
  return db
    .select({
      matchId: matches.id,
      kickoffUtc: matches.kickoffUtc,
      leagueKey: leagues.key,
      leagueName: leagues.name,
      homeName: home.canonicalName,
      awayName: away.canonicalName,
      status: matches.status,
      homeGoals: matches.homeGoals,
      awayGoals: matches.awayGoals,
      neutral: matches.neutral,
      predictionId: predictions.id,
      lambdaHome: predictions.lambdaHome,
      lambdaAway: predictions.lambdaAway,
      scoreMatrixJson: predictions.scoreMatrixJson,
      modelRunId: predictions.modelRunId,
      modelVersion: modelRuns.modelVersion,
    })
    .from(matches)
    .innerJoin(leagues, eq(leagues.id, matches.leagueId))
    .innerJoin(home, eq(home.id, matches.homeTeamId))
    .innerJoin(away, eq(away.id, matches.awayTeamId))
    .leftJoin(latest, eq(latest.matchId, matches.id))
    .leftJoin(predictions, eq(predictions.id, latest.predictionId))
    .leftJoin(modelRuns, eq(modelRuns.id, predictions.modelRunId))
    .where(eq(matches.id, matchId))
    .get();
}

export function leagueKeys(db: Db): { key: string; name: string }[] {
  return db
    .select({ key: leagues.key, name: leagues.name })
    .from(leagues)
    .orderBy(asc(leagues.key))
    .all();
}

// ---------------------------------------------------------------------------
// Model page
// ---------------------------------------------------------------------------

export type ModelRunRow = typeof modelRuns.$inferSelect & { leagueKey: string; leagueName: string };

export function latestModelRuns(db: Db): ModelRunRow[] {
  const latest = db
    .select({ leagueId: modelRuns.leagueId, id: max(modelRuns.id).as("run_id") })
    .from(modelRuns)
    .groupBy(modelRuns.leagueId)
    .as("latest_run");
  return db
    .select({
      id: modelRuns.id,
      leagueId: modelRuns.leagueId,
      modelVersion: modelRuns.modelVersion,
      fittedAt: modelRuns.fittedAt,
      trainFrom: modelRuns.trainFrom,
      trainTo: modelRuns.trainTo,
      paramsJson: modelRuns.paramsJson,
      converged: modelRuns.converged,
      negLogLik: modelRuns.negLogLik,
      nMatches: modelRuns.nMatches,
      createdAt: modelRuns.createdAt,
      updatedAt: modelRuns.updatedAt,
      leagueKey: leagues.key,
      leagueName: leagues.name,
    })
    .from(modelRuns)
    .innerJoin(latest, eq(latest.id, modelRuns.id))
    .innerJoin(leagues, eq(leagues.id, modelRuns.leagueId))
    .orderBy(asc(leagues.key))
    .all();
}

export function latestBacktest(db: Db) {
  return db
    .select()
    .from(backtestRuns)
    .where(sql`${backtestRuns.finishedAt} IS NOT NULL`)
    .orderBy(desc(backtestRuns.finishedAt), desc(backtestRuns.id))
    .limit(1)
    .get();
}

// ---------------------------------------------------------------------------
// Data health
// ---------------------------------------------------------------------------

export function recentJobRuns(db: Db, limit = 30) {
  return db.select().from(jobRuns).orderBy(desc(jobRuns.id)).limit(limit).all();
}

export function unresolved(db: Db) {
  return db
    .select()
    .from(unresolvedNames)
    .orderBy(asc(unresolvedNames.leagueKey), asc(unresolvedNames.rawName))
    .all();
}

export function teamsByLeague(db: Db): Map<string, { id: number; name: string }[]> {
  const rows = db
    .select({ id: teams.id, name: teams.canonicalName, leagueKey: leagues.key })
    .from(teams)
    .innerJoin(leagues, eq(leagues.id, teams.leagueId))
    .orderBy(asc(teams.canonicalName))
    .all();
  const out = new Map<string, { id: number; name: string }[]>();
  for (const r of rows) {
    const list = out.get(r.leagueKey) ?? [];
    list.push({ id: r.id, name: r.name });
    out.set(r.leagueKey, list);
  }
  return out;
}

export function needsReview(db: Db) {
  return db
    .select({
      matchId: matches.id,
      kickoffUtc: matches.kickoffUtc,
      leagueKey: leagues.key,
      homeName: home.canonicalName,
      awayName: away.canonicalName,
      homeGoals: matches.homeGoals,
      awayGoals: matches.awayGoals,
    })
    .from(matches)
    .innerJoin(leagues, eq(leagues.id, matches.leagueId))
    .innerJoin(home, eq(home.id, matches.homeTeamId))
    .innerJoin(away, eq(away.id, matches.awayTeamId))
    .where(eq(matches.status, "needs_review"))
    .orderBy(desc(matches.kickoffUtc))
    .all();
}

/** Open slips whose last kickoff is more than `hours` ago — `make settle` is overdue. */
export function unsettledPastSlips(db: Db, beforeUtc: string): number {
  const row = db
    .select({ n: sql<number>`count(*)` })
    .from(slips)
    .where(and(eq(slips.status, "open"), lte(slips.windowEndUtc, beforeUtc)))
    .get();
  return row?.n ?? 0;
}

// ---------------------------------------------------------------------------
// Performance (docs/06 §4) — read from the SQL views shared with the engine.
// ---------------------------------------------------------------------------

export type PerfMode = "all" | "paper" | "placed";

export type LegPerf = {
  value_leg_id: number;
  slip_type: string;
  pool: string;
  mode: string;
  league: string;
  market: string;
  line: number;
  selection: string;
  odds: number;
  p_final: number;
  edge_at_pick: number;
  result: string;
  multiplier: number | null;
  profit: number | null;
  win_units: number;
  clv: number | null;
  pick_date: string;
  kickoff_utc: string;
  model_version: string;
};

export type SlipPerf = {
  slip_id: number;
  slip_type: string;
  pool: string;
  slip_date: string;
  legs: number;
  total_odds: number;
  p_all_win: number;
  expected_multiplier: number;
  status: string;
  return_multiplier: number | null;
  profit: number | null;
  mode: string;
  stake: number | null;
  profit_naira: number | null;
  model_version: string;
};

function modeWhere(mode: PerfMode): SQL {
  return mode === "all" ? sql`1 = 1` : sql`mode = ${mode}`;
}

/**
 * Settled legs, each value leg counted once (a leg can sit in several slips).
 * Pool/mode of the first slip it appeared in are kept for splits.
 */
export function legPerformance(db: Db, mode: PerfMode): LegPerf[] {
  return db.all<LegPerf>(sql`
    SELECT * FROM v_leg_performance
    WHERE slip_leg_id IN (
      SELECT min(slip_leg_id) FROM v_leg_performance WHERE ${modeWhere(mode)} GROUP BY value_leg_id
    )
    ORDER BY kickoff_utc, value_leg_id`);
}

export function slipPerformance(db: Db, mode: PerfMode): SlipPerf[] {
  return db.all<SlipPerf>(sql`
    SELECT * FROM v_slip_performance WHERE ${modeWhere(mode)} ORDER BY slip_date, slip_id`);
}
