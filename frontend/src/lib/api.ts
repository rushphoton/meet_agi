/*
 * WHY THIS EXISTS
 * The one place the dashboard talks to the backend. Every call goes to a
 * relative path (/api/...), which this dashboard's own server forwards to the
 * backend (see next.config.ts). Errors come back as one plain sentence that
 * says what to do.
 *
 * FAILURE IT PREVENTS
 * - The browser refusing to talk to the backend because it lives on a
 *   different port (the backend doesn't allow cross-site calls; forwarding
 *   avoids needing it to).
 * - A stopped backend showing up as a blank screen or a cryptic
 *   "Failed to fetch" instead of "the backend isn't running - start it with ...".
 */
import type {
  DocumentInfo, FollowUp, FollowUpPatch, Health, MeetingEvent, MeetingListItem, MeetingRecord,
  MuteRequest, Ok, Settings, WakeRequest,
} from "./contract";

export const BACKEND_HINT = "Is the backend running? Start it with: python scripts/serve.py";

export class ApiError extends Error {
  constructor(message: string, readonly status: number | null) {
    super(message);
    this.name = "ApiError";
  }
}

/** URL of the live event stream, resuming after `since` (0 = from the start). */
export function eventsUrl(meetingId: string, since: number): string {
  const s = Number.isFinite(since) && since > 0 ? Math.floor(since) : 0;
  return `/api/meetings/${encodeURIComponent(meetingId)}/events?since=${s}`;
}

function detailOf(body: unknown): string | null {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((x) => (x && typeof x === "object" && "msg" in x ? String(x.msg) : String(x))).join("; ");
  }
  return null;
}

export async function apiFetch<T>(path: string, init?: RequestInit, fetchImpl: typeof fetch = fetch): Promise<T> {
  let res: Response;
  try {
    res = await fetchImpl(path, { cache: "no-store", ...init });
  } catch {
    throw new ApiError(`Can't reach the backend (${path}). ${BACKEND_HINT}`, null);
  }
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) {
    // The forwarding server answers 500 with an HTML page when the backend is down.
    const hint = res.status >= 500 && detailOf(body) === null ? ` ${BACKEND_HINT}` : "";
    throw new ApiError(`${res.status}: ${detailOf(body) ?? (res.statusText || "request failed")}.${hint}`, res.status);
  }
  if (body === null && text) {
    throw new ApiError(`The backend sent something that isn't JSON (${path}). ${BACKEND_HINT}`, res.status);
  }
  return body as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
});
const m = (id: string) => `/api/meetings/${encodeURIComponent(id)}`;

export const api = {
  health: () => apiFetch<Health>("/api/health"),
  listMeetings: () => apiFetch<MeetingListItem[]>("/api/meetings"),
  getMeeting: (id: string) => apiFetch<MeetingRecord>(m(id)),
  endMeeting: (id: string) => apiFetch<MeetingRecord>(`${m(id)}/end`, { method: "POST" }),
  wake: (id: string, body: WakeRequest) => apiFetch<MeetingEvent>(`${m(id)}/wake`, json("POST", body)),
  stop: (id: string) => apiFetch<MeetingEvent>(`${m(id)}/stop`, { method: "POST" }),
  mute: (id: string, body: MuteRequest) => apiFetch<MeetingEvent>(`${m(id)}/mute`, json("POST", body)),
  setFollowUp: (id: string, fid: string, body: FollowUpPatch) =>
    apiFetch<FollowUp>(`${m(id)}/follow-ups/${encodeURIComponent(fid)}`, json("PATCH", body)),
  getSettings: () => apiFetch<Settings>("/api/settings"),
  putSettings: (s: Settings) => apiFetch<Settings>("/api/settings", json("PUT", s)),
  listDocuments: () => apiFetch<DocumentInfo[]>("/api/documents"),
  uploadDocument: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return apiFetch<DocumentInfo>("/api/documents", { method: "POST", body: form });
  },
  deleteDocument: (name: string) =>
    apiFetch<Ok>(`/api/documents/${encodeURIComponent(name)}`, { method: "DELETE" }),
};
