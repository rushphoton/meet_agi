/*
 * WHY THIS EXISTS
 * The live screen hears about a meeting as a stream of numbered events
 * ("someone said X", "an alert fired", "the meeting ended"). This file folds
 * that stream into one picture of the meeting, the same way the backend's
 * recorder does (backend/app/core/store.py), so the live view and the review
 * screen always show the same thing.
 *
 * FAILURE IT PREVENTS
 * - The same line appearing twice after a reconnect (duplicates are dropped by
 *   their sequence number).
 * - Lines lost or shown in the wrong order when the connection hiccups (an
 *   event that arrives early is held until the ones before it arrive; the
 *   screen then asks the backend to resend from the last one it has).
 * - One unexpected or garbled message breaking the whole screen (it is
 *   counted and skipped).
 *
 * It has no network or React code, so every rule above is tested on its own
 * (meetingState.test.ts).
 */
import type {
  Alert, ChatPost, FollowUp, MeetingEvent, MeetingEventType, MeetingRecord, SpokenAnswer,
} from "./contract";

export interface LiveState {
  record: MeetingRecord;
  /** Highest sequence number folded in so far; the stream resumes with ?since=lastSeq. */
  lastSeq: number;
  /** Events that arrived ahead of a missing one, keyed by seq, waiting for the gap to fill. */
  held: Record<number, MeetingEvent>;
  /** Wall-clock time each alert first arrived (for "fired at" on the live view). */
  alertAt: Record<string, string>;
  wakeCount: number;
  stopCount: number;
  /** Messages that were dropped: duplicates, garbled, wrong meeting, or an event type this build doesn't know. */
  duplicates: number;
  ignored: number;
}

const KNOWN_TYPES: ReadonlySet<MeetingEventType> = new Set<MeetingEventType>([
  "bot.status", "transcript.segment", "wake", "stop", "mute", "alert", "spoken.answer",
  "chat.post", "follow_up", "meeting.summary", "meeting.ended",
]);

export function initialState(record: MeetingRecord): LiveState {
  return {
    record,
    lastSeq: record.events_last_seq ?? 0,
    held: {},
    alertAt: {},
    wakeCount: 0,
    stopCount: 0,
    duplicates: 0,
    ignored: 0,
  };
}

/** True while an event is being held because an earlier one hasn't arrived yet. */
export function hasGap(state: LiveState): boolean {
  return Object.keys(state.held).length > 0;
}

/** Parse one SSE `data:` line. Returns null for anything that isn't a well-formed event object. */
export function parseEventData(data: string): unknown | null {
  try {
    const value: unknown = JSON.parse(data);
    return typeof value === "object" && value !== null ? value : null;
  } catch {
    return null;
  }
}

interface Envelope { seq: number; type: string; meeting_id: string; payload: unknown; at: string }

function asEnvelope(raw: unknown): Envelope | null {
  if (typeof raw !== "object" || raw === null) return null;
  const e = raw as Record<string, unknown>;
  if (typeof e.seq !== "number" || !Number.isInteger(e.seq) || e.seq < 1) return null;
  if (typeof e.type !== "string" || typeof e.meeting_id !== "string") return null;
  if (typeof e.payload !== "object" || e.payload === null) return null;
  return { seq: e.seq, type: e.type, meeting_id: e.meeting_id, payload: e.payload, at: String(e.at ?? "") };
}

/**
 * Fold one incoming message (already JSON-parsed, shape not yet trusted) into the state.
 * Never throws; returns the same state object when nothing changed.
 */
export function applyEvent(state: LiveState, raw: unknown): LiveState {
  const env = asEnvelope(raw);
  if (!env || env.meeting_id !== state.record.meeting_id) {
    return { ...state, ignored: state.ignored + 1 };
  }
  if (env.seq <= state.lastSeq || state.held[env.seq]) {
    return { ...state, duplicates: state.duplicates + 1 };
  }
  if (env.seq > state.lastSeq + 1) {
    // Arrived early: hold it until the missing ones come (the hook then resyncs with ?since).
    return { ...state, held: { ...state.held, [env.seq]: raw as MeetingEvent } };
  }
  let next = foldOne(state, env);
  // Drain anything that was waiting on this one.
  while (next.held[next.lastSeq + 1]) {
    const waiting = next.held[next.lastSeq + 1];
    const rest = { ...next.held };
    delete rest[next.lastSeq + 1];
    next = foldOne({ ...next, held: rest }, asEnvelope(waiting) as Envelope);
  }
  return next;
}

/** Apply several messages in order (used for tests and for a resync batch). */
export function applyEvents(state: LiveState, raws: unknown[]): LiveState {
  return raws.reduce<LiveState>((s, r) => applyEvent(s, r), state);
}

function upsert<T, K extends keyof T>(items: T[], item: T, key: K): T[] {
  const i = items.findIndex((x) => x[key] === item[key]);
  if (i === -1) return [...items, item];
  const copy = items.slice();
  copy[i] = item;
  return copy;
}

/** Apply a follow-up the API returned directly (after a toggle), without touching the sequence. */
export function applyFollowUp(state: LiveState, fu: FollowUp): LiveState {
  return { ...state, record: { ...state.record, follow_ups: upsert(state.record.follow_ups, fu, "follow_up_id") } };
}

function foldOne(state: LiveState, env: Envelope): LiveState {
  const advanced: LiveState = { ...state, lastSeq: env.seq };
  if (!KNOWN_TYPES.has(env.type as MeetingEventType)) {
    // A newer backend may add event types; skip them but keep the sequence moving.
    return { ...advanced, ignored: advanced.ignored + 1 };
  }
  const r = { ...state.record, events_last_seq: env.seq };
  // The shape check above is deliberately shallow; the backend validates payloads before publishing.
  const e = { ...env, id: "" } as unknown as MeetingEvent;
  switch (e.type) {
    case "bot.status":
      r.bot_status = e.payload.status;
      if (e.payload.recall_bot_id) r.recall_bot_id = e.payload.recall_bot_id;
      break;
    case "transcript.segment": {
      const seg = e.payload;
      if (!r.segments.some((s) => s.segment_id === seg.segment_id)) r.segments = [...r.segments, seg];
      if (!r.participants.some((p) => p.speaker_id === seg.speaker_id)) {
        r.participants = [...r.participants, { speaker_id: seg.speaker_id, display_name: seg.speaker_name }];
      }
      break;
    }
    case "mute":
      r.muted = e.payload.muted;
      break;
    case "alert": {
      const alert: Alert = e.payload;
      r.alerts = upsert(r.alerts, alert, "alert_id");
      const alertAt = advanced.alertAt[alert.alert_id]
        ? advanced.alertAt : { ...advanced.alertAt, [alert.alert_id]: env.at };
      return { ...advanced, record: r, alertAt };
    }
    case "spoken.answer":
      r.answers = upsert<SpokenAnswer, "answer_id">(r.answers, e.payload, "answer_id");
      break;
    case "chat.post":
      r.chat_posts = upsert<ChatPost, "chat_id">(r.chat_posts, e.payload, "chat_id");
      break;
    case "follow_up":
      r.follow_ups = upsert<FollowUp, "follow_up_id">(r.follow_ups, e.payload, "follow_up_id");
      break;
    case "meeting.summary":
      r.summary = e.payload;
      break;
    case "meeting.ended":
      r.ended_at = r.ended_at ?? env.at;
      break;
    case "wake":
      return { ...advanced, record: r, wakeCount: advanced.wakeCount + 1 };
    case "stop":
      return { ...advanced, record: r, stopCount: advanced.stopCount + 1 };
  }
  return { ...advanced, record: r };
}

/** Alerts that reached (or would have reached) the chat, i.e. not held back by the gate. */
export function visibleAlertCount(record: MeetingRecord): number {
  return record.alerts.filter((a) => !a.gated).length;
}

export function followUpCounts(record: MeetingRecord): { outstanding: number; resolved: number } {
  return {
    outstanding: record.follow_ups.filter((f) => f.status === "outstanding").length,
    resolved: record.follow_ups.filter((f) => f.status === "resolved").length,
  };
}
