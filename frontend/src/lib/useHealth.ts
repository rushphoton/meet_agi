"use client";
/*
 * WHY THIS EXISTS
 * Asks the backend "are you OK?" (GET /api/health) on a timer, so screens can
 * show placeholders, live warnings and when Recall was last heard from.
 *
 * FAILURE IT PREVENTS
 * A broken or sleeping backend looking exactly like a quiet meeting.
 */
import { useEffect, useState } from "react";
import { api, ApiError } from "./api";
import type { Health } from "./contract";

export function useHealth(intervalMs: number) {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.health()
        .then((h) => { if (alive) { setHealth(h); setError(null); } })
        .catch((e) => { if (alive) setError(e instanceof ApiError ? e.message : String(e)); });
    load();
    const t = setInterval(load, intervalMs);
    return () => { alive = false; clearInterval(t); };
  }, [intervalMs]);

  return { health, error };
}
