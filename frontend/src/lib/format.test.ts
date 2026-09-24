/*
 * WHY THIS EXISTS
 * Proves bad dates and numbers never crash a screen, and that the sessions
 * list shows follow-ups before the alert count (DESIGN.md §1).
 */
import { describe, expect, it } from "vitest";
import { formatClock, formatConfidence, formatDate, SESSION_COLUMNS, sessionRow } from "./format";

describe("failure paths", () => {
  it("missing or broken values show a dash, not 'Invalid Date' or 'NaN'", () => {
    expect(formatDate(null)).toBe("-");
    expect(formatDate("not a date")).toBe("-");
    expect(formatClock(Number.NaN)).toBe("-:--");
    expect(formatClock(-1)).toBe("-:--");
    expect(formatConfidence(undefined)).toBe("-");
    expect(formatConfidence(7)).toBe("100%");
  });

  it("a meeting with no participants yet says so", () => {
    const row = sessionRow({ meeting_id: "m", title: "", started_at: "2026-09-24T10:00:00Z", participants: [],
      follow_ups_outstanding: 0, follow_ups_resolved: 0, alert_count: 0 });
    expect(row[1]).toBe("(untitled)");
    expect(row[2]).toBe("none yet");
  });
});

describe("sessions list", () => {
  it("columns: date, participants, follow-ups outstanding / resolved, then alerts", () => {
    const i = (name: string) => SESSION_COLUMNS.indexOf(name as (typeof SESSION_COLUMNS)[number]);
    expect(i("Date")).toBe(0);
    expect(i("Participants")).toBeLessThan(i("Follow-ups outstanding"));
    expect(i("Follow-ups outstanding")).toBeLessThan(i("Follow-ups resolved"));
    expect(i("Follow-ups resolved")).toBeLessThan(i("Alerts"));
    const row = sessionRow({ meeting_id: "m", title: "Q3", started_at: "2026-09-24T10:00:00Z",
      participants: ["Dana", "Lee"], follow_ups_outstanding: 2, follow_ups_resolved: 1, alert_count: 5 });
    expect(row.slice(2)).toEqual(["Dana, Lee", "2", "1", "5"]);
  });

  it("meeting clock", () => {
    expect(formatClock(65.4)).toBe("1:05");
    expect(formatClock(3725)).toBe("1:02:05");
  });
});
