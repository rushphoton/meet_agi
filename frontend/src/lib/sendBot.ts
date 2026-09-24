/*
 * WHY THIS EXISTS
 * The rules behind the "Send Meet AGI to a meeting" form: check the Meet link
 * before sending it, and turn the backend's refusals into one plain sentence
 * that says what to do next. For example: another meeting is still live, so
 * end it first; the Recall key isn't set; Recall itself said no.
 *
 * FAILURE IT PREVENTS
 * Getting stuck on stage with a bare "409" or a blank screen when the bot
 * can't be sent (review B, items 3 and 4).
 */
import { ApiError, BACKEND_HINT } from "./api";
import type { CreateMeetingRequest } from "./contract";

export type Checked = { ok: true; request: CreateMeetingRequest } | { ok: false; message: string };

/** Validate the form. The backend makes the final call; this only catches obvious typos. */
export function checkSendForm(meetingUrl: string, title: string): Checked {
  const raw = meetingUrl.trim();
  if (!raw) return { ok: false, message: "Paste the Google Meet link first." };
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return { ok: false, message: "That doesn't look like a link. It should start with https://meet.google.com/" };
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") {
    return { ok: false, message: "The link must start with https://" };
  }
  const t = title.trim();
  return { ok: true, request: { meeting_url: raw, title: t || null } };
}

export interface SendFailure {
  message: string;
  /** True when another meeting is still live; the page then links to /live so it can be ended. */
  offerLiveLink: boolean;
}

/** Turn a failed POST /api/meetings into what the page shows. Never throws. */
export function describeSendFailure(err: unknown): SendFailure {
  if (!(err instanceof ApiError)) {
    return { message: `Couldn't send the bot: ${String(err)}`, offerLiveLink: false };
  }
  // ApiError messages look like "503: <backend detail>." - keep the backend's words, drop the code prefix.
  const detail = err.message.replace(/^\d{3}:\s*/, "");
  switch (err.status) {
    case null:
      return { message: `Couldn't send the bot. ${BACKEND_HINT}`, offerLiveLink: false };
    case 409:
      return { message: `${detail} Open the live meeting and press "End meeting", then send again.`, offerLiveLink: true };
    case 503:
      return { message: `The backend isn't set up to send a real bot yet: ${detail}`, offerLiveLink: false };
    case 502:
      return { message: `Recall refused to send the bot: ${detail}`, offerLiveLink: false };
    case 501:
      return { message: `Sending a real bot isn't built in this backend yet: ${detail}`, offerLiveLink: false };
    default:
      return { message: `Couldn't send the bot (${err.status}): ${detail}`, offerLiveLink: false };
  }
}
