// Human labels shared by every page. No word here may imply certainty (docs/07 §2.3).

export const SLIP_TYPES = ["daily_2odds", "mid_acca", "mega_acca"] as const;
export type SlipType = (typeof SLIP_TYPES)[number];

export const SLIP_TYPE_LABEL: Record<string, string> = {
  daily_2odds: "Daily 2-odds",
  mid_acca: "Mid accumulator",
  mega_acca: "Mega accumulator",
};

export const POOL_LABEL: Record<string, string> = { club: "Club leagues", intl: "Internationals" };

export const NO_EDGE_COPY = "Model has not demonstrated an edge — paper mode recommended";

function signedLine(line: number): string {
  return line > 0 ? `+${line}` : `${line}`;
}

/** "Home −0.5", "Away +1.5" (line is always the home handicap, docs/discovered), "Over 2.5". */
export function selectionLabel(market: string, selection: string, line: number): string {
  if (market === "OU") return `${selection === "over" ? "Over" : "Under"} ${line}`;
  const own = selection === "home" ? line : -line;
  return `${selection === "home" ? "Home" : "Away"} ${signedLine(own)}`;
}

export function marketLabel(market: string): string {
  return market === "AH" ? "Asian handicap" : market === "OU" ? "Over/Under" : market;
}

export const RESULT_LABEL: Record<string, string> = {
  pending: "Pending",
  win: "Won",
  half_win: "Half won",
  push: "Push",
  half_loss: "Half lost",
  loss: "Lost",
  void: "Void",
};
