/*
 * WHY THIS EXISTS
 * Small, tested rules for how things read on screen: dates, meeting clock
 * times, confidence, and the column order of the sessions list (follow-ups
 * come before alerts, as DESIGN.md §1 asks).
 *
 * FAILURE IT PREVENTS
 * A bad date or a missing number crashing a whole screen ("Invalid Date",
 * "NaN%"), and the sessions list drifting from the agreed column order.
 */
import type { MeetingListItem } from "./contract";

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleString(undefined, {
    year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
  });
}

/** Meeting-relative seconds as m:ss (or h:mm:ss). */
export function formatClock(seconds: number | null | undefined): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return "-:--";
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const mm = Math.floor((total % 3600) / 60);
  const ss = String(total % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(mm).padStart(2, "0")}:${ss}` : `${mm}:${ss}`;
}

export function formatConfidence(c: number | null | undefined): string {
  if (typeof c !== "number" || !Number.isFinite(c)) return "-";
  return `${Math.round(Math.min(1, Math.max(0, c)) * 100)}%`;
}

/** The sessions list columns, in display order. Follow-ups before the alert count. */
export const SESSION_COLUMNS = [
  "Date", "Meeting", "Participants", "Follow-ups outstanding", "Follow-ups resolved", "Alerts",
] as const;

export function sessionRow(item: MeetingListItem): string[] {
  return [
    formatDate(item.started_at),
    item.title || "(untitled)",
    item.participants.length ? item.participants.join(", ") : "none yet",
    String(item.follow_ups_outstanding),
    String(item.follow_ups_resolved),
    String(item.alert_count),
  ];
}
