import { config } from "dotenv";
import { defineConfig } from "drizzle-kit";

config({ path: ".env.local", quiet: true });

// Schema is owned by Alembic (engine/migrations). This config is only used by
// `drizzle-kit pull` to generate TypeScript types from the live database;
// never run `drizzle-kit push`/`migrate` against it.
export default defineConfig({
  dialect: "sqlite",
  out: "./src/db/generated",
  dbCredentials: { url: process.env.DB_PATH ?? "../data/betpredictor.db" },
  tablesFilter: ["!alembic_version"],
});
