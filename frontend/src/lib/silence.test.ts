/*
 * WHY THIS EXISTS
 * Proves the red "No transcript for N s" notice appears when the pipe is dead
 * and stays away when the room is simply between sentences, the bot isn't in
 * the call yet, or the meeting is over.
 */
import { describe, expect, it } from "vitest";
import { silenceSeconds, type SilenceInput } from "./silence";

const T0 = Date.parse("2026-09-24T10:00:00Z");
const base: SilenceInput = {
  ended: false, botStatus: "in_call", lastSegmentAt: null, lastWebhookAt: null,
  meetingStartedAt: "2026-09-24T10:00:00Z", nowMs: T0,
};
const at = (s: number) => new Date(T0 + s * 1000).toISOString();

describe("failure paths", () => {
  it("in the call, last line 45 s ago -> warns with 45 s", () => {
    expect(silenceSeconds({ ...base, lastSegmentAt: at(0), nowMs: T0 + 45_000 })).toBe(45);
  });

  it("in the call, nothing ever arrived for 31 s since the meeting started -> warns", () => {
    expect(silenceSeconds({ ...base, nowMs: T0 + 31_000 })).toBe(31);
  });

  it("a stale stream but a fresh webhook (backend still hears Recall) -> no warning", () => {
    expect(silenceSeconds({ ...base, lastSegmentAt: at(0), lastWebhookAt: at(50), nowMs: T0 + 60_000 })).toBeNull();
  });

  it("garbled timestamps don't crash and don't count as a sign of life", () => {
    expect(silenceSeconds({ ...base, lastSegmentAt: "nonsense", lastWebhookAt: "", nowMs: T0 + 40_000 })).toBe(40);
    expect(silenceSeconds({ ...base, meetingStartedAt: "bad", nowMs: T0 + 40_000 })).toBeNull();
  });
});

describe("no warning when there is nothing wrong", () => {
  it("exactly 30 s is still fine", () => {
    expect(silenceSeconds({ ...base, lastSegmentAt: at(0), nowMs: T0 + 30_999 })).toBeNull();
  });

  it("meeting ended, or bot not in the call (joining, waiting room, left, unknown)", () => {
    const late = { ...base, nowMs: T0 + 600_000 };
    expect(silenceSeconds({ ...late, ended: true })).toBeNull();
    for (const s of ["joining", "waiting_room", "left", "failed", null, undefined] as const) {
      expect(silenceSeconds({ ...late, botStatus: s })).toBeNull();
    }
  });
});
