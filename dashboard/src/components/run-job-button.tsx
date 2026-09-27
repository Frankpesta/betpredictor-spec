"use client";

import { LoaderCircle, Play } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { engine, type JobRun } from "@/lib/engine";

const POLL_MS = 2000; // docs/07 §2.1

type Props = { job: string; label: string; variant?: "default" | "outline" | "secondary" };

/** POST /jobs/{job}, then poll GET /jobs/{id} every 2 s until it finishes. */
export function RunJobButton({ job, label, variant = "default" }: Props) {
  const router = useRouter();
  const [runId, setRunId] = useState<number | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (runId === null) return;
    timer.current = setInterval(async () => {
      const res = await engine<JobRun>(`/jobs/${runId}`);
      if (!res.ok) {
        setError(res.error);
        return;
      }
      setStatus(res.data.status);
      if (res.data.status !== "running") {
        if (timer.current) clearInterval(timer.current);
        if (res.data.status === "failed") {
          setError(`${job} failed — see Data health for the error.`);
        }
        setRunId(null);
        router.refresh();
      }
    }, POLL_MS);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [runId, job, router]);

  async function start() {
    setError(null);
    setStatus("starting");
    const res = await engine<{ job_run_id: number }>(`/jobs/${job}`, { method: "POST" });
    if (!res.ok) {
      setStatus(null);
      setError(res.status === 409 ? "Another job is already running." : res.error);
      return;
    }
    setStatus("running");
    setRunId(res.data.job_run_id);
  }

  const running = runId !== null;
  return (
    <div className="flex flex-col items-end gap-1">
      <Button size="sm" variant={variant} onClick={start} disabled={running}>
        {running ? <LoaderCircle className="animate-spin" /> : <Play />}
        {running ? `${label}: running…` : label}
      </Button>
      {!running && status && status !== "starting" && !error && (
        <span className="text-xs text-muted-foreground">last run: {status}</span>
      )}
      {error && <span className="max-w-xs text-right text-xs text-destructive">{error}</span>}
    </div>
  );
}
