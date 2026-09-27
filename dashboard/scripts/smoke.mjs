// docs/07 §3: every page must render against an empty DB and the seeded demo DB.
//
//   node scripts/smoke.mjs <db> [<db> ...]     (after `pnpm build`)
//
// For each DB: start `next start` with DB_PATH pointing at it, fetch every page,
// and fail on a non-200 status, a Next.js error page, or a word the UI must never
// use (docs/07 §2.3) in the visible text.
import { spawn } from "node:child_process";
import { resolve } from "node:path";

import Database from "better-sqlite3";

const PORT = 3107;
const BASE = `http://localhost:${PORT}`;
const FORBIDDEN = /\b(sure|guaranteed|banker|fixed)\b/i;
const ERROR_MARKERS = ["Application error", "Internal Server Error", 'id="__next_error__"'];

function firstMatchId(db) {
  const conn = new Database(db, { readonly: true, fileMustExist: true });
  try {
    return conn.prepare("SELECT min(id) AS id FROM matches").get()?.id ?? 1;
  } finally {
    conn.close();
  }
}

function visibleText(html) {
  return html
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/&[a-z#0-9]+;/gi, " ");
}

async function waitReady(child) {
  for (let i = 0; i < 120; i++) {
    if (child.exitCode !== null) throw new Error(`next start exited with ${child.exitCode}`);
    try {
      const r = await fetch(`${BASE}/model`);
      if (r.status > 0) return;
    } catch {
      // not listening yet
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("next start did not become ready");
}

async function checkDb(db) {
  const matchId = firstMatchId(db);
  const pages = [
    "/",
    "/fixtures",
    "/fixtures?q=1",
    `/fixtures/${matchId}`,
    "/fixtures/999999",
    "/history",
    "/history?type=mid_acca&status=lost&mode=paper",
    "/performance",
    "/performance?mode=placed",
    "/model",
    "/data-health",
  ];
  const child = spawn(process.execPath, ["node_modules/next/dist/bin/next", "start", "-p", String(PORT)], {
    env: { ...process.env, DB_PATH: db },
    stdio: ["ignore", "pipe", "pipe"],
  });
  let log = "";
  child.stdout.on("data", (d) => (log += d));
  child.stderr.on("data", (d) => (log += d));
  const failures = [];
  try {
    await waitReady(child);
    for (const page of pages) {
      const res = await fetch(`${BASE}${page}`);
      const html = await res.text();
      const text = visibleText(html);
      const problems = [];
      if (res.status !== 200) problems.push(`status ${res.status}`);
      for (const m of ERROR_MARKERS) if (html.includes(m)) problems.push(`contains "${m}"`);
      const bad = text.match(FORBIDDEN);
      if (bad) problems.push(`forbidden word "${bad[0]}"`);
      console.log(`${problems.length ? "FAIL" : "ok  "} ${page}${problems.length ? ` — ${problems.join("; ")}` : ""}`);
      if (problems.length) failures.push(page);
    }
  } finally {
    child.kill();
  }
  if (/\b(Error|TypeError):/.test(log)) {
    console.log("server log contained errors:\n" + log);
    failures.push("server-log");
  }
  return failures;
}

const dbs = process.argv.slice(2).map((p) => resolve(p));
if (dbs.length === 0) {
  console.error("usage: node scripts/smoke.mjs <db> [<db> ...]");
  process.exit(2);
}
let failed = 0;
for (const db of dbs) {
  console.log(`\n== ${db}`);
  failed += (await checkDb(db)).length;
}
console.log(failed ? `\n${failed} check(s) failed` : "\nall pages rendered");
process.exit(failed ? 1 : 0);
