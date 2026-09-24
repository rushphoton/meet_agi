/*
 * WHY THIS EXISTS
 * Proves the backend address and the live-stream relay URL are always sane,
 * whatever the environment variable or the browser sends.
 */
import { describe, expect, it } from "vitest";
import { backendBase, upstreamEventsUrl } from "./backendBase";

describe("failure paths", () => {
  it("unset, blank or trailing-slash settings still give a usable address", () => {
    expect(backendBase("")).toBe("http://localhost:8000");
    expect(backendBase("   ")).toBe("http://localhost:8000");
    expect(backendBase("http://127.0.0.1:8301//")).toBe("http://127.0.0.1:8301");
  });

  it("junk ?since from the browser becomes 0 (full history), never passed through", () => {
    const b = "http://x";
    expect(upstreamEventsUrl(b, "mtg_1", null)).toBe("http://x/api/meetings/mtg_1/events?since=0");
    expect(upstreamEventsUrl(b, "mtg_1", "abc")).toBe("http://x/api/meetings/mtg_1/events?since=0");
    expect(upstreamEventsUrl(b, "mtg_1", "-4")).toBe("http://x/api/meetings/mtg_1/events?since=0");
    expect(upstreamEventsUrl(b, "mtg_1", "5&x=1")).toBe("http://x/api/meetings/mtg_1/events?since=0");
    expect(upstreamEventsUrl(b, "../settings", "1")).toBe("http://x/api/meetings/..%2Fsettings/events?since=1");
  });
});

describe("normal", () => {
  it("passes a real resume point through", () => {
    expect(upstreamEventsUrl("http://x", "mtg_1", "33")).toBe("http://x/api/meetings/mtg_1/events?since=33");
  });
});
