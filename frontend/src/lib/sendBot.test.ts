/*
 * WHY THIS EXISTS
 * Proves the "Send Meet AGI" form catches typos and shows each backend refusal
 * (409 already live, 503 key not set, 502 Recall said no, backend down) as a
 * plain sentence, and never crashes.
 */
import { describe, expect, it } from "vitest";
import { ApiError, BACKEND_HINT } from "./api";
import { checkSendForm, describeSendFailure } from "./sendBot";

describe("failure paths", () => {
  it("empty or junk links are refused before anything is sent", () => {
    expect(checkSendForm("  ", "x")).toMatchObject({ ok: false });
    expect(checkSendForm("meet.google.com/abc-defg-hij", "")).toMatchObject({ ok: false });
    expect(checkSendForm("ftp://meet.google.com/abc", "")).toMatchObject({ ok: false });
    expect(checkSendForm("javascript:alert(1)", "")).toMatchObject({ ok: false });
  });

  it("503 with the key missing shows the backend's own words", () => {
    const f = describeSendFailure(new ApiError("503: Cannot send the bot: RECALL_API_KEY not set in .env.", 503));
    expect(f.message).toContain("RECALL_API_KEY not set in .env");
    expect(f.message).not.toMatch(/^503/);
    expect(f.offerLiveLink).toBe(false);
  });

  it("409 another meeting live -> says so and offers the live view to end it", () => {
    const f = describeSendFailure(new ApiError("409: A meeting is already live: mtg_abc 'Rehearsal'; end it first.", 409));
    expect(f.message).toContain("mtg_abc");
    expect(f.message).toContain("End meeting");
    expect(f.offerLiveLink).toBe(true);
  });

  it("502 Recall refused -> names Recall", () => {
    const f = describeSendFailure(new ApiError("502: Recall did not create the bot: HTTP 400.", 502));
    expect(f.message).toMatch(/^Recall refused/);
    expect(f.message).toContain("HTTP 400");
  });

  it("backend down -> tells you how to start it", () => {
    expect(describeSendFailure(new ApiError("Can't reach the backend", null)).message).toContain(BACKEND_HINT);
  });

  it("an unexpected non-API error still yields a sentence", () => {
    expect(describeSendFailure(new TypeError("boom")).message).toContain("boom");
    expect(describeSendFailure(new ApiError("500: Internal Server Error.", 500)).message).toContain("500");
  });
});

describe("normal send", () => {
  it("trims the link and turns a blank title into null", () => {
    expect(checkSendForm(" https://meet.google.com/abc-defg-hij ", "  ")).toEqual({
      ok: true, request: { meeting_url: "https://meet.google.com/abc-defg-hij", title: null },
    });
    expect(checkSendForm("https://meet.google.com/abc-defg-hij", " Board ")).toMatchObject({
      ok: true, request: { title: "Board" },
    });
  });
});
