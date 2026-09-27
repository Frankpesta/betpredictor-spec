"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { engine } from "@/lib/engine";

type Props = { source: string; rawName: string; teams: { id: number; name: string }[] };

/** Approve an alias for an unresolved team name: POST /aliases (docs/07 §1). */
export function AliasApprove({ source, rawName, teams }: Props) {
  const router = useRouter();
  const [teamId, setTeamId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function approve() {
    setBusy(true);
    setError(null);
    const res = await engine("/aliases", {
      method: "POST",
      body: JSON.stringify({ source, alias: rawName, team_id: Number(teamId) }),
    });
    setBusy(false);
    if (!res.ok) {
      setError(res.error);
      return;
    }
    router.refresh();
  }

  if (teams.length === 0) return <span className="text-xs text-muted-foreground">no teams in this league</span>;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select
        value={teamId}
        onChange={(e) => setTeamId(e.target.value)}
        className="h-7 max-w-56 rounded-lg border bg-background px-2 text-xs"
        aria-label={`Team for ${rawName}`}
      >
        <option value="">Choose team…</option>
        {teams.map((t) => (
          <option key={t.id} value={t.id}>
            {t.name}
          </option>
        ))}
      </select>
      <Button size="xs" variant="outline" onClick={approve} disabled={!teamId || busy}>
        Approve alias
      </Button>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  );
}
