import "server-only";

import { readFileSync } from "node:fs";
import path from "node:path";

import { parse } from "smol-toml";

// Tunables live in config/settings.toml (CLAUDE.md §5). The dashboard reads the few
// it needs to label things the same way the engine does; it never changes them.

export type EngineSettings = {
  modelVersion: string;
  minEdgeLeg: number;
  minOdds: number;
  maxOdds: number;
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
  const value = raw.value ?? {};
  return {
    modelVersion: String(model.version ?? "unknown"),
    minEdgeLeg: num(value.min_edge_leg, "value.min_edge_leg"),
    minOdds: num(value.min_odds, "value.min_odds"),
    maxOdds: num(value.max_odds, "value.max_odds"),
  };
}

/** docs/05 §2.2: no sanity flag, edge ≥ min_edge_leg, odds within [min_odds, max_odds]. */
export function qualifies(
  leg: { sanityStatus: string; edge: number; odds: number },
  s: EngineSettings,
): boolean {
  return (
    leg.sanityStatus === "ok" && leg.edge >= s.minEdgeLeg && leg.odds >= s.minOdds && leg.odds <= s.maxOdds
  );
}
