"use client";
/*
 * WHY THIS EXISTS
 * A thin strip under the top bar that says, in plain words, which parts of
 * the backend are still placeholders (from GET /api/health), or that the
 * backend can't be reached and how to start it.
 *
 * FAILURE IT PREVENTS
 * Canned alerts and answers being mistaken for real ones (CLAUDE.md rule 6),
 * and empty screens that are really "the backend is off".
 */
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Health } from "@/lib/contract";

export default function HealthBanner() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.health()
        .then((h) => { if (alive) { setHealth(h); setError(null); } })
        .catch((e) => { if (alive) setError(e instanceof ApiError ? e.message : String(e)); });
    load();
    const t = setInterval(load, 10_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (error) return <div className="banner" role="alert">Backend not reachable. {error}</div>;
  if (!health || health.placeholders.length === 0) return null;
  return (
    <div className="banner">
      <strong>PLACEHOLDER parts still running (their output is marked CANNED):</strong>{" "}
      {health.placeholders.join("; ")}
      {health.offline ? " · OFFLINE mode" : ""}
    </div>
  );
}
