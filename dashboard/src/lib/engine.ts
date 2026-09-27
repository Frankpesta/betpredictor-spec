// Client-side calls to the FastAPI engine (docs/07 §1). The API listens on 127.0.0.1
// only, so these work in a browser on this computer, not from a phone on the LAN.

export const ENGINE_URL = process.env.NEXT_PUBLIC_ENGINE_URL ?? "http://127.0.0.1:8765";

export type EngineResult<T> = { ok: true; data: T } | { ok: false; status: number; error: string };

export type JobRun = {
  id: number;
  job_name: string;
  status: "running" | "success" | "failed";
  started_at: string;
  finished_at: string | null;
  summary: Record<string, unknown> | null;
  error: string | null;
};

function detail(body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      return d
        .map((e) => (e && typeof e === "object" && "msg" in e ? String((e as { msg: unknown }).msg) : String(e)))
        .join("; ");
    }
  }
  return "request failed";
}

export async function engine<T>(path: string, init?: RequestInit): Promise<EngineResult<T>> {
  try {
    const res = await fetch(`${ENGINE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
    const body: unknown = await res.json().catch(() => null);
    if (!res.ok) return { ok: false, status: res.status, error: detail(body) };
    return { ok: true, data: body as T };
  } catch {
    return {
      ok: false,
      status: 0,
      error: `Engine API not reachable at ${ENGINE_URL}. Start it with \`make api\` (works only on this computer).`,
    };
  }
}
