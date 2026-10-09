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

const LINELESS_LABEL: Record<string, string> = {
  home: "Home",
  draw: "Draw",
  away: "Away",
  home_draw: "Home or draw",
  home_away: "Home or away",
  draw_away: "Draw or away",
  yes: "Yes",
  no: "No",
};

/**
 * "Home −0.5", "Away +1.5" (line is always the home handicap, docs/discovered), "Over 2.5";
 * docs/05 §10: "Home or draw", "Yes"; team goals read like totals ("Over 1.5").
 */
export function selectionLabel(code: string, selection: string, line: number): string {
  const market = splitPeriod(code)[0];
  if (market === "OU" || market === "OU_HOME" || market === "OU_AWAY")
    return `${selection === "over" ? "Over" : "Under"} ${line}`;
  if (market !== "AH") return LINELESS_LABEL[selection] ?? selection;
  const own = selection === "home" ? line : -line;
  return `${selection === "home" ? "Home" : "Away"} ${signedLine(own)}`;
}

const MARKET_LABEL: Record<string, string> = {
  AH: "Asian handicap",
  OU: "Over/Under",
  OU_HOME: "Home team goals",
  OU_AWAY: "Away team goals",
  "1X2": "1X2",
  DC: "Double chance",
  BTTS: "Both teams to score",
};

const HALF_LABEL: Record<string, string> = { "1H": "1st half", "2H": "2nd half" };

/** docs/10 §3: "OU_1H" -> ["OU", "1H"]; full-time codes -> [code, "FT"]. */
export function splitPeriod(code: string): [string, string] {
  const m = /^(.*)_(1H|2H)$/.exec(code);
  return m ? [m[1], m[2]] : [code, "FT"];
}

export function marketLabel(code: string): string {
  const [market, period] = splitPeriod(code);
  const label = MARKET_LABEL[market] ?? market;
  return period === "FT" ? label : `${HALF_LABEL[period]} · ${label}`;
}

/**
 * Markets with no historical odds to backtest — judged on paper only: docs/05 §10 full-time
 * markets and every half-time market (docs/10: calibration-gated, never ROI-tested).
 */
export function isUnvalidatedMarket(code: string): boolean {
  const [market, period] = splitPeriod(code);
  return period !== "FT" || ["OU_HOME", "OU_AWAY", "1X2", "DC", "BTTS"].includes(market);
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

/** docs/05 §8-9: slips.strategy / value.selection. */
export const STRATEGY_LABEL: Record<"data_rule" | "likeliest" | "value", string> = {
  data_rule: "Data rule",
  likeliest: "Likeliest",
  value: "Value",
};
