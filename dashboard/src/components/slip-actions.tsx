"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { engine, type JobRun } from "@/lib/engine";
import { NO_EDGE_COPY } from "@/lib/labels";

type Props = {
  slipId: number;
  bookingCode: string | null;
  bookingStatus: string;
  mode: string;
  status: string;
  edgeValidated: boolean;
};

const CODE_RE = /^[A-Za-z0-9]{4,16}$/; // same rule as the engine API (routes_slips.BookingBody)
const REBOOK_POLL_MS = 2000;
const REBOOK_MAX_POLLS = 90;

export function SlipActions({ slipId, bookingCode, bookingStatus, mode, status, edgeValidated }: Props) {
  const router = useRouter();
  const [msg, setMsg] = useState<{ text: string; error: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [codeOpen, setCodeOpen] = useState(false);
  const [placeOpen, setPlaceOpen] = useState(false);
  const [code, setCode] = useState("");
  const [stake, setStake] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const open = status === "open";

  function done(text: string, error = false) {
    setMsg({ text, error });
    setBusy(false);
    if (!error) router.refresh();
  }

  async function copy() {
    if (!bookingCode) return;
    try {
      await navigator.clipboard.writeText(bookingCode);
      setMsg({ text: `Copied ${bookingCode}`, error: false });
    } catch {
      setMsg({ text: "Clipboard unavailable — select the code and copy it by hand.", error: true });
    }
  }

  async function rebook() {
    setBusy(true);
    setMsg({ text: "Rebooking…", error: false });
    const res = await engine<{ job_run_id: number }>(`/slips/${slipId}/rebook`, { method: "POST" });
    if (!res.ok) return done(res.error, true);
    for (let i = 0; i < REBOOK_MAX_POLLS; i++) {
      await new Promise((r) => setTimeout(r, REBOOK_POLL_MS));
      const job = await engine<JobRun>(`/jobs/${res.data.job_run_id}`);
      if (job.ok && job.data.status !== "running") {
        const ok = job.data.status === "success";
        return done(ok ? "Rebook finished." : "Rebook job failed — see Data health.", !ok);
      }
    }
    done("Rebook still running — refresh later.");
  }

  async function saveCode() {
    setBusy(true);
    const res = await engine(`/slips/${slipId}/booking`, {
      method: "PATCH",
      body: JSON.stringify({ booking_code: code.trim() }),
    });
    if (!res.ok) return done(res.error, true);
    setCodeOpen(false);
    setCode("");
    done("Booking code saved (manual).");
  }

  async function setMode(next: "paper" | "placed") {
    setBusy(true);
    const body: Record<string, unknown> = { mode: next };
    if (next === "placed") {
      if (stake.trim()) body.stake = Number(stake);
      body.confirm_no_edge = confirmed;
    }
    const res = await engine(`/slips/${slipId}/mode`, { method: "PATCH", body: JSON.stringify(body) });
    if (!res.ok) return done(res.error, true);
    setPlaceOpen(false);
    setConfirmed(false);
    done(next === "placed" ? "Marked as placed." : "Back to paper mode.");
  }

  const stakeOk = stake.trim() === "" || (Number.isFinite(Number(stake)) && Number(stake) > 0);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="outline" onClick={copy} disabled={!bookingCode}>
          Copy code
        </Button>
        <Button size="sm" variant="outline" onClick={rebook} disabled={!open || busy || mode === "placed"}>
          Rebook
        </Button>
        <Button size="sm" variant="outline" onClick={() => setCodeOpen(true)} disabled={!open || busy}>
          Enter code manually
        </Button>
        {mode === "placed" ? (
          <Button size="sm" variant="secondary" onClick={() => setMode("paper")} disabled={!open || busy}>
            Back to paper
          </Button>
        ) : (
          <Button size="sm" onClick={() => setPlaceOpen(true)} disabled={!open || busy}>
            Mark as placed
          </Button>
        )}
      </div>
      {bookingStatus === "failed" && open && (
        <p className="text-xs text-muted-foreground">
          Automatic booking failed: add the legs above on SportyBet by hand, then paste the code with
          &ldquo;Enter code manually&rdquo;.
        </p>
      )}
      {msg && (
        <p className={msg.error ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>{msg.text}</p>
      )}

      <Dialog open={codeOpen} onOpenChange={setCodeOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Enter booking code</DialogTitle>
            <DialogDescription>
              Paste the SportyBet booking code you created for these legs. It is stored as a manual
              booking.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1">
            <Label htmlFor={`code-${slipId}`}>Booking code</Label>
            <Input
              id={`code-${slipId}`}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="e.g. X6YP10"
              autoComplete="off"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCodeOpen(false)}>
              Cancel
            </Button>
            <Button onClick={saveCode} disabled={busy || !CODE_RE.test(code.trim())}>
              Save code
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={placeOpen} onOpenChange={setPlaceOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Mark slip as placed</DialogTitle>
            <DialogDescription>
              Record that you placed this slip yourself on SportyBet. This tool never places bets.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1">
            <Label htmlFor={`stake-${slipId}`}>Stake (₦, optional)</Label>
            <Input
              id={`stake-${slipId}`}
              inputMode="decimal"
              value={stake}
              onChange={(e) => setStake(e.target.value)}
              placeholder="e.g. 1000"
              aria-invalid={!stakeOk}
            />
          </div>
          {!edgeValidated && (
            <label className="flex items-start gap-2 rounded-md border border-amber-400 bg-amber-50 p-3 text-sm text-amber-950 dark:bg-amber-950 dark:text-amber-100">
              <input
                type="checkbox"
                className="mt-1"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
              />
              <span>{NO_EDGE_COPY}. I understand and want to record this slip as placed anyway.</span>
            </label>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setPlaceOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={() => setMode("placed")}
              disabled={busy || !stakeOk || (!edgeValidated && !confirmed)}
            >
              Mark as placed
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
