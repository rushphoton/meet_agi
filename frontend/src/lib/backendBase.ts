/*
 * WHY THIS EXISTS
 * The one place that works out where the backend lives
 * (NEXT_PUBLIC_API_BASE, default http://localhost:8000). The forwarding rules
 * in next.config.ts and the live-stream relay both use it, so they can't
 * disagree.
 *
 * FAILURE IT PREVENTS
 * Half the dashboard talking to one backend and the live stream to another.
 */
import { eventsUrl } from "./api";

export const DEFAULT_API_BASE = "http://localhost:8000";

export function backendBase(env: string | undefined = process.env.NEXT_PUBLIC_API_BASE): string {
  const v = (env ?? "").trim();
  return (v || DEFAULT_API_BASE).replace(/\/+$/, "");
}

/** Backend URL of a meeting's live stream; `since` comes from the browser, so junk becomes 0. */
export function upstreamEventsUrl(base: string, meetingId: string, since: string | null): string {
  const n = since !== null && /^\d+$/.test(since) ? Number(since) : 0;
  return `${base}${eventsUrl(meetingId, n)}`;
}
