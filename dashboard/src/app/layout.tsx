import type { Metadata } from "next";
import { Geist_Mono, Plus_Jakarta_Sans } from "next/font/google";
import { Crosshair } from "lucide-react";
import Link from "next/link";
import "./globals.css";

import { NavLinks } from "@/components/nav-links";
import { RunJobButton } from "@/components/run-job-button";
import { GateBadge, NoEdgeBanner } from "@/components/status-bits";
import { ThemeProvider, ThemeToggle } from "@/components/theme";
import { getDb } from "@/db/client";
import { lastSuccess, latestGate } from "@/db/queries";
import { ago, hoursSince } from "@/lib/format";
import { loadSettings } from "@/lib/settings";
import { cn } from "@/lib/utils";

const jakarta = Plus_Jakarta_Sans({
  variable: "--font-jakarta",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "BetPredictor",
  description: "Personal football value-betting model — local only, paper mode by default.",
};

const STALE_ODDS_HOURS = 3; // docs/07 §2.1: last odds run shown red when older than 3h

async function TopBar() {
  const res = await getDb();
  const settings = loadSettings();
  const gate = res.ok ? latestGate(res.db) : { gatePassed: null, finishedAt: null };
  const lastOdds = res.ok ? lastSuccess(res.db, "odds") : null;
  const stale = lastOdds === null || hoursSince(lastOdds) > STALE_ODDS_HOURS;
  return (
    <>
      <header className="sticky top-0 z-40 border-b border-border/70 bg-background/75 backdrop-blur-xl supports-[backdrop-filter]:bg-background/60">
        <div className="mx-auto flex w-full max-w-7xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
            <Link href="/" className="group flex items-center gap-2.5">
              <span className="grid size-8 place-items-center rounded-lg bg-gradient-to-br from-emerald-400 to-teal-600 text-white shadow-sm shadow-emerald-600/30 transition-transform group-hover:scale-105">
                <Crosshair className="size-4.5" strokeWidth={2.5} />
              </span>
              <span className="text-[1.05rem] font-bold tracking-tight">
                Bet<span className="text-primary">Predictor</span>
              </span>
            </Link>
            <NavLinks />
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span
              className={cn(
                "inline-flex h-6 items-center gap-1.5 rounded-full px-2.5 font-medium ring-1",
                stale
                  ? "bg-red-500/10 text-red-700 ring-red-500/30 dark:text-red-300"
                  : "bg-muted text-muted-foreground ring-border",
              )}
            >
              <span className={cn("size-1.5 rounded-full", stale ? "bg-red-500" : "bg-emerald-500")} />
              Odds {lastOdds ? ago(lastOdds) : "never fetched"}
            </span>
            <span
              className="inline-flex h-6 items-center rounded-full bg-muted px-2.5 font-medium text-muted-foreground ring-1 ring-border"
              title="config/settings.toml [model].version"
            >
              Model {settings.modelVersion}
            </span>
            <GateBadge gatePassed={gate.gatePassed} />
            <RunJobButton job="daily" label="Run daily" />
            <ThemeToggle />
          </div>
        </div>
      </header>
      {gate.gatePassed !== 1 && <NoEdgeBanner />}
    </>
  );
}

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${jakarta.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-background text-foreground">
        <ThemeProvider>
          <TopBar />
          <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-8">{children}</main>
          <footer className="mt-8 border-t border-border/70">
            <p className="mx-auto max-w-7xl px-4 py-5 text-xs leading-relaxed text-muted-foreground">
              No model can guarantee wins. Bookmaker odds are efficient; this tool looks for small
              positive expected value and measures honestly whether it exists. It never places bets.
            </p>
          </footer>
        </ThemeProvider>
      </body>
    </html>
  );
}
