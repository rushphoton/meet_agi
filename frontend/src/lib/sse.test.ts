/*
 * WHY THIS EXISTS
 * Proves the live-stream reader survives messages split across packets,
 * keep-alive pings, Windows line endings and event names it has never seen.
 */
import { describe, expect, it } from "vitest";
import { reconnectDelayMs, SseParser } from "./sse";

describe("failure paths", () => {
  it("a message split mid-line across chunks is reassembled", () => {
    const p = new SseParser();
    expect(p.push('id: 3\nevent: alert\ndata: {"se')).toEqual([]);
    expect(p.push('q":3}\n')).toEqual([]);
    expect(p.push("\n")).toEqual([{ event: "alert", data: '{"seq":3}', id: "3" }]);
  });

  it("keep-alive comments produce nothing", () => {
    const p = new SseParser();
    expect(p.push(": keep-alive\n\n: keep-alive\n\n")).toEqual([]);
  });

  it("CRLF line endings, including a CR split from its LF, still parse", () => {
    const p = new SseParser();
    expect(p.push("data: a\r")).toEqual([]);
    expect(p.push("\n\r\n")).toEqual([{ event: "message", data: "a", id: null }]);
  });

  it("an event name the dashboard doesn't know is still delivered (so numbering can't stall)", () => {
    const p = new SseParser();
    expect(p.push("event: brand.new\ndata: {}\n\n")[0].event).toBe("brand.new");
  });

  it("reconnect waits grow and are capped at 10 s, even for silly inputs", () => {
    expect([1, 2, 3, 4, 5, 50].map(reconnectDelayMs)).toEqual([1000, 2000, 4000, 8000, 10000, 10000]);
    expect(reconnectDelayMs(0)).toBe(1000);
    expect(reconnectDelayMs(-3)).toBe(1000);
  });
});

describe("normal stream", () => {
  it("parses several events in one chunk, with multi-line data", () => {
    const p = new SseParser();
    const out = p.push("id: 1\nevent: wake\ndata: {}\n\nid: 2\ndata: line1\ndata: line2\n\n");
    expect(out).toEqual([
      { event: "wake", data: "{}", id: "1" },
      { event: "message", data: "line1\nline2", id: "2" },
    ]);
  });
});
