"use client";
/*
 * WHY THIS EXISTS
 * Keeps one meeting on screen up to date: it loads the meeting once, then
 * listens to the live event stream and folds each event in (meetingState.ts).
 * If the connection drops, or an event arrives with one missing before it,
 * it reconnects asking only for what it hasn't seen yet (?since=<last>).
 *
 * FAILURE IT PREVENTS
 * A laptop wobble or backend restart mid-meeting leaving the live view frozen,
 * or showing lines twice, until someone reloads the page.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, eventsUrl } from "./api";
import type { FollowUp } from "./contract";
import { applyEvent, applyFollowUp, hasGap, initialState, parseEventData, type LiveState } from "./meetingState";
import { reconnectDelayMs, SseParser } from "./sse";

export type StreamStatus = "loading" | "live" | "reconnecting" | "off" | "error";

/** How long an out-of-order event may wait for the missing one before we resync. */
const GAP_RESYNC_MS = 2000;

export function useMeetingStream(meetingId: string, opts: { live: boolean }) {
  const [state, setState] = useState<LiveState | null>(null);
  const [status, setStatus] = useState<StreamStatus>("loading");
  const [error, setError] = useState<string | null>(null);
  const stateRef = useRef<LiveState | null>(null);

  // The ref is the source of truth (read synchronously for ?since and gap checks); React state mirrors it.
  const update = useCallback((fn: (s: LiveState) => LiveState) => {
    const prev = stateRef.current;
    if (!prev) return;
    const next = fn(prev);
    if (next === prev) return;
    stateRef.current = next;
    setState(next);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    async function connect(): Promise<void> {
      if (cancelled || !stateRef.current) return;
      controller = new AbortController();
      const since = stateRef.current.lastSeq;
      let gapTimer: ReturnType<typeof setTimeout> | null = null;
      try {
        const res = await fetch(eventsUrl(meetingId, since), {
          signal: controller.signal, cache: "no-store", headers: { Accept: "text/event-stream" },
        });
        if (!res.ok || !res.body) throw new Error(`stream HTTP ${res.status}`);
        setStatus("live");
        setError(null);
        attempt = 0;
        const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
        const parser = new SseParser();
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          for (const msg of parser.push(value)) {
            const raw = parseEventData(msg.data);
            update((s) => applyEvent(s, raw));
          }
          const current = stateRef.current;
          if (current && hasGap(current) && !gapTimer) {
            gapTimer = setTimeout(() => controller?.abort(), GAP_RESYNC_MS);
          } else if (current && !hasGap(current) && gapTimer) {
            clearTimeout(gapTimer);
            gapTimer = null;
          }
        }
      } catch {
        // fall through to reconnect
      } finally {
        if (gapTimer) clearTimeout(gapTimer);
      }
      if (cancelled) return;
      attempt += 1;
      setStatus("reconnecting");
      timer = setTimeout(connect, reconnectDelayMs(attempt));
    }

    (async () => {
      try {
        const record = await api.getMeeting(meetingId);
        if (cancelled) return;
        const s = initialState(record);
        stateRef.current = s;
        setState(s);
        if (opts.live) {
          void connect();
        } else {
          setStatus("off");
        }
      } catch (e) {
        if (cancelled) return;
        setStatus("error");
        setError(e instanceof ApiError ? e.message : String(e));
      }
    })();

    return () => {
      cancelled = true;
      controller?.abort();
      if (timer) clearTimeout(timer);
    };
  }, [meetingId, opts.live, update]);

  const setFollowUp = useCallback((fu: FollowUp) => update((s) => applyFollowUp(s, fu)), [update]);

  return { state, status, error, setFollowUp };
}
