import "server-only";

import { readFileSync } from "node:fs";
import path from "node:path";

import { parse } from "smol-toml";

// Tunables live in config/settings.toml (CLAUDE.md §5). The dashboard reads the few
// it needs to label things the same way the engine does; it never changes them.

/** docs/05 §8-9: which leg-selection rule `make picks` uses (value.selection). */
export type Strategy = "data_rule" | "likeliest" | "value";

const STRATEGY_VALUES: readonly Strategy[] = ["data_rule", "likeliest", "value"];

export type EngineSettings = {
  modelVersion: string;
  selection: Strategy;
  /** general.horizon_hours: look-ahead of odds, picks and the fixtures page. */
  horizonHours: number;
  minEdgeLeg: number;
  minOdds: number;
  maxOdds: number;
  /** value.data_rule.min_odds (docs/05 §9). */
  dataRuleMinOdds: number;
};

export function settingsPath(): string {
  // Runtime path outside the app: keep Turbopack from tracing it into the build.
  return path.resolve(/*turbopackIgnore: true*/ process.cwd(), process.env.SETTINGS_PATH ?? "../config/settings.toml");
}

function num(v: unknown, key: string): number {
  if (typeof v !== "number") throw new Error(`settings.toml: ${key} must be a number`);
  return v;
}

export function loadSettings(): EngineSettings {
  const raw = parse(readFileSync(settingsPath(), "utf8")) as Record<string, Record<string, unknown>>;
  const model = raw.model ?? {};
  const general = raw.general ?? {};
  const value = raw.value ?? {};
  return {
    modelVersion: String(model.version ?? "unknown"),
    selection: STRATEGY_VALUES.includes(value.selection as Strategy) ? (value.selection as Strategy) : "value",
    horizonHours: num(general.horizon_hours, "general.horizon_hours"),
    minEdgeLeg: num(value.min_edge_leg, "value.min_edge_leg"),
    minOdds: num(value.min_odds, "value.min_odds"),
    maxOdds: num(value.max_odds, "value.max_odds"),
    dataRuleMinOdds: num(
      (value.data_rule as Record<string, unknown> | undefined)?.min_odds,
      "value.data_rule.min_odds",
    ),
  };
}

/**
 * The engine stores its decision in value_legs.qualifies (docs/05 §8; it depends on the
 * match favourite). Legs priced before migration 0006 have NULL: they were priced under
 * the docs/05 §2.2 value rule — no sanity flag, edge ≥ min_edge_leg, odds within bounds.
 */
export function qualifies(
  leg: { sanityStatus: string; edge: number; odds: number; qualifies: number | null },
  s: EngineSettings,
): boolean {
  if (leg.qualifies !== null) return leg.qualifies === 1;
  return (
    leg.sanityStatus === "ok" && leg.edge >= s.minEdgeLeg && leg.odds >= s.minOdds && leg.odds <= s.maxOdds
  );
}
