import "server-only";

import path from "node:path";

import Database from "better-sqlite3";
import { drizzle, type BetterSQLite3Database } from "drizzle-orm/better-sqlite3";
import { connection } from "next/server";

import * as relations from "./generated/relations";
import * as schema from "./generated/schema";

const fullSchema = { ...schema, ...relations };
export type Db = BetterSQLite3Database<typeof fullSchema>;

export type DbResult = { ok: true; db: Db } | { ok: false; error: string; dbPath: string };

const globalForDb = globalThis as unknown as { __bpDb?: Db };

export function dbPath(): string {
  return path.resolve(process.cwd(), process.env.DB_PATH ?? "../data/betpredictor.db");
}

/**
 * Read-only DB handle for server components / route handlers.
 * The dashboard never writes: every mutation goes through the FastAPI engine.
 * Calls `connection()` so synchronous better-sqlite3 queries run per request,
 * never at build/prerender time.
 */
export async function getDb(): Promise<DbResult> {
  await connection();
  if (globalForDb.__bpDb) return { ok: true, db: globalForDb.__bpDb };
  const file = dbPath();
  try {
    const sqlite = new Database(file, { readonly: true, fileMustExist: true });
    sqlite.pragma("busy_timeout = 5000");
    const db = drizzle(sqlite, { schema: fullSchema });
    globalForDb.__bpDb = db;
    return { ok: true, db };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { ok: false, error: message, dbPath: file };
  }
}
