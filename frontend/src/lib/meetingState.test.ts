/*
 * WHY THIS EXISTS
 * Proves the live view can't double-count, lose or scramble what happened in a
 * meeting when the connection misbehaves. Failure paths come first.
 * The sample data below is test-only (it is not shown anywhere in the app).
 */
import { describe, expect, it } from "vitest";
import type { Alert, MeetingEvent, MeetingRecord } from "./contract";
import {
  applyEvent, applyEvents, applyFollowUp, followUpCounts, hasGap, initialState, parseEventData,
  visibleAlertCount,
} from "./meetingState";

const MID = "mtg_000000000001";

function record(over: Partial<MeetingRecord> = {}): MeetingRecord {
  return {
    meeting_id: MID, title: "Test", source: "replay", started_at: "2026-09-24T10:00:00Z",
    alerts: [], answers: [], chat_posts: [], follow_ups: [], participants: [], segments: [],
    muted: false, events_last_seq: 0, ...over,
  };
}

let n = 0;
function seg(seq: number, speaker = "Dana", text = `line ${seq}`): MeetingEvent {
  n += 1;
  return {
    id: `evt_${seq}`, meeting_id: MID, seq, at: "2026-09-24T10:00:01Z", type: "transcript.segment",
    payload: { segment_id: `seg_${seq}_${n}`, speaker_id: speaker.toLowerCase(), speaker_name: speaker,
      text, t_start: seq, t_end: seq + 1, source: "replay" },
  };
}

function alert(seq: number, over: Partial<Alert> = {}): MeetingEvent {
  return {
    id: `evt_${seq}`, meeting_id: MID, seq, at: "2026-09-24T10:00:05Z", type: "alert",
    payload: {
      alert_id: "alr_1", kind: "contradiction", topic: "Q3 revenue", claim: "Q3 revenue rose",
      said_by: ["Dana"], segment_ids: [], finding: "The deck says it fell 4%.", evidence: [],
      reasoning: "because", confidence: 0.9, gated: false, delivered_to_chat: true, models_used: [],
      canned: true, ...over,
    },
  };
}

describe("failure paths", () => {
  it("duplicate seq is ignored (the same line never shows twice)", () => {
    const e = seg(1);
    const s = applyEvents(initialState(record()), [e, e, { ...e }]);
    expect(s.record.segments).toHaveLength(1);
    expect(s.duplicates).toBe(2);
    expect(s.lastSeq).toBe(1);
  });

  it("events already in the loaded record are ignored when the stream replays them", () => {
    const s = applyEvent(initialState(record({ events_last_seq: 5 })), seg(3));
    expect(s.record.segments).toHaveLength(0);
    expect(s.duplicates).toBe(1);
  });

  it("out-of-order seq is held, then applied in order once the gap fills", () => {
    let s = initialState(record());
    s = applyEvent(s, seg(1, "A"));
    s = applyEvent(s, seg(3, "C"));
    expect(hasGap(s)).toBe(true);
    expect(s.record.segments.map((x) => x.speaker_name)).toEqual(["A"]);
    expect(s.lastSeq).toBe(1);
    s = applyEvent(s, seg(2, "B"));
    expect(hasGap(s)).toBe(false);
    expect(s.record.segments.map((x) => x.speaker_name)).toEqual(["A", "B", "C"]);
    expect(s.lastSeq).toBe(3);
  });

  it("a held event arriving twice is counted once", () => {
    let s = applyEvents(initialState(record()), [seg(1), seg(3)]);
    s = applyEvent(s, seg(3));
    expect(s.duplicates).toBe(1);
    expect(Object.keys(s.held)).toEqual(["3"]);
  });

  it("unknown event type is ignored but keeps the numbering moving", () => {
    const unknown = { id: "evt_2", meeting_id: MID, seq: 2, at: "", type: "brand.new", payload: { x: 1 } };
    const s = applyEvents(initialState(record()), [seg(1), unknown, seg(3)]);
    expect(s.ignored).toBe(1);
    expect(s.lastSeq).toBe(3);
    expect(s.record.segments).toHaveLength(2);
    expect(hasGap(s)).toBe(false);
  });

  it("garbled or foreign messages are counted and skipped, never thrown", () => {
    const start = initialState(record());
    const junk = [null, 42, "text", {}, { seq: "1", type: "alert", meeting_id: MID, payload: {} },
      { seq: 0, type: "alert", meeting_id: MID, payload: {} }, { ...seg(1), meeting_id: "mtg_other" },
      { ...seg(1), payload: null }];
    const s = applyEvents(start, junk);
    expect(s.ignored).toBe(junk.length);
    expect(s.lastSeq).toBe(0);
    expect(parseEventData("{not json")).toBeNull();
    expect(parseEventData("7")).toBeNull();
  });

  it("reconnect with ?since: resent history after the last seen seq merges without duplicates", () => {
    // First connection saw 1-2, dropped; second connection (since=2) sends 3-4; a stray resend of 2 is dropped.
    let s = applyEvents(initialState(record()), [seg(1), seg(2)]);
    expect(s.lastSeq).toBe(2);
    s = applyEvents(s, [seg(2), seg(3), seg(4)]);
    expect(s.record.segments).toHaveLength(4);
    expect(s.duplicates).toBe(1);
  });

  it("a second meeting.ended keeps the first end time", () => {
    const end = (seq: number, at: string): MeetingEvent => ({
      id: `e${seq}`, meeting_id: MID, seq, at, type: "meeting.ended", payload: { reason: "dashboard" },
    });
    const s = applyEvents(initialState(record()), [end(1, "2026-09-24T11:00:00Z"), end(2, "2026-09-24T12:00:00Z")]);
    expect(s.record.ended_at).toBe("2026-09-24T11:00:00Z");
  });
});

describe("folding the stream", () => {
  it("adds speakers as participants once, and an updated alert replaces the old one", () => {
    const s = applyEvents(initialState(record()), [
      seg(1, "Dana"), seg(2, "Dana"), seg(3, "Lee"), alert(4), alert(5, { confidence: 0.5 }),
    ]);
    expect(s.record.participants.map((p) => p.display_name)).toEqual(["Dana", "Lee"]);
    expect(s.record.alerts).toHaveLength(1);
    expect(s.record.alerts[0].confidence).toBe(0.5);
    expect(s.alertAt.alr_1).toBe("2026-09-24T10:00:05Z");
  });

  it("mute, wake, stop, summary and follow-ups update the picture", () => {
    const base = { meeting_id: MID, at: "2026-09-24T10:00:00Z" };
    const s = applyEvents(initialState(record()), [
      { ...base, id: "1", seq: 1, type: "mute", payload: { muted: true, by: "dashboard" } },
      { ...base, id: "2", seq: 2, type: "wake", payload: { trigger: "button" } },
      { ...base, id: "3", seq: 3, type: "stop", payload: { trigger: "button" } },
      { ...base, id: "4", seq: 4, type: "follow_up", payload: { follow_up_id: "fu_1", text: "Check", status: "outstanding" } },
      { ...base, id: "5", seq: 5, type: "follow_up", payload: { follow_up_id: "fu_1", text: "Check", status: "resolved" } },
      { ...base, id: "6", seq: 6, type: "meeting.summary", payload: { key_topics: ["Q3"], takeaways: [], follow_up_count: 1, alert_count: 1, canned: true } },
    ]);
    expect(s.record.muted).toBe(true);
    expect(s.wakeCount).toBe(1);
    expect(s.stopCount).toBe(1);
    expect(followUpCounts(s.record)).toEqual({ outstanding: 0, resolved: 1 });
    expect(s.record.summary?.canned).toBe(true);
  });

  it("a follow-up toggled through the API shows at once and the later event doesn't duplicate it", () => {
    const fu = { follow_up_id: "fu_1", text: "Check", status: "resolved" as const };
    let s = initialState(record({ follow_ups: [{ ...fu, status: "outstanding" }], events_last_seq: 3 }));
    s = applyFollowUp(s, fu);
    s = applyEvent(s, { id: "4", meeting_id: MID, seq: 4, at: "", type: "follow_up", payload: fu });
    expect(s.record.follow_ups).toEqual([fu]);
  });

  it("gated alerts don't count as alerts in the room", () => {
    const s = applyEvents(initialState(record()), [alert(1), alert(2, { alert_id: "alr_2", gated: true })]);
    expect(visibleAlertCount(s.record)).toBe(1);
  });
});
