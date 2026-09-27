/*
 * WHY THIS EXISTS
 * Decides when the live view should turn red because no transcript has
 * arrived for a while even though the bot is in the call. A quiet room and a
 * broken connection look the same on screen. After 30 seconds of nothing, a
 * sleeping laptop, a dropped VPN or a dead ngrok tunnel is the likelier
 * cause, and Recall stops retrying after about 60 seconds.
 *
 * FAILURE IT PREVENTS
 * The dashboard going quietly blank mid-demo while nobody notices the
 * pipeline has died (review B, item 8).
 */
import type { BotStatusValue } from "./contract";

export const SILENCE_LIMIT_SECONDS = 30;
/**
 * Gate v0.1, item 8: Attendee sends nothing while the room is quiet (no heartbeat), so 30 s of quiet
 * turned the projector red mid-demo. Between 30 s and this limit the notice is a grey "quiet"; only
 * after it does it turn red. Recall keeps retrying webhooks for about 60 s, Attendee's captions come
 * every few seconds while anyone speaks, so 2 minutes of nothing is worth an alarm.
 */
export const SILENCE_ALARM_SECONDS = 120;

/** "alarm" (red: check the laptop, VPN, ngrok) or "quiet" (grey: probably nobody is speaking). */
export function silenceLevel(seconds: number): "quiet" | "alarm" {
  return seconds >= SILENCE_ALARM_SECONDS ? "alarm" : "quiet";
}

export interface SilenceInput {
  ended: boolean;
  botStatus: BotStatusValue | null | undefined;
  /** Event time of the last transcript.segment seen on the stream (null if none this session). */
  lastSegmentAt: string | null;
  /** health.last_webhook_at - when the backend last heard anything from Recall. */
  lastWebhookAt: string | null | undefined;
  /** Fallback starting point when nothing has arrived yet. */
  meetingStartedAt: string;
  nowMs: number;
}

function ms(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isNaN(t) ? null : t;
}

/**
 * Seconds of silence to warn about, or null when there is nothing to warn about
 * (meeting ended, bot not in the call, or the last sign of life is recent).
 */
export function silenceSeconds(i: SilenceInput): number | null {
  if (i.ended || i.botStatus !== "in_call") return null;
  const candidates = [ms(i.lastSegmentAt), ms(i.lastWebhookAt), ms(i.meetingStartedAt)]
    .filter((x): x is number => x !== null);
  if (candidates.length === 0) return null;
  const lastSign = Math.max(...candidates);
  const seconds = Math.floor((i.nowMs - lastSign) / 1000);
  return seconds > SILENCE_LIMIT_SECONDS ? seconds : null;
}
