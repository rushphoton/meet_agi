/*
 * WHY THIS EXISTS
 * Proves that when the backend is down or says no, the dashboard shows one
 * plain sentence saying what to do, not a crash or a blank page.
 */
import { describe, expect, it } from "vitest";
import { ApiError, apiFetch, BACKEND_HINT, eventsUrl } from "./api";

const fakeFetch = (impl: () => Promise<Response>) => (() => impl()) as unknown as typeof fetch;

describe("failure paths", () => {
  it("backend not running -> tells you how to start it", async () => {
    const err = await apiFetch<never>("/api/meetings", undefined, fakeFetch(() => Promise.reject(new TypeError("Failed to fetch"))))
      .catch((e: ApiError) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBeNull();
    expect(err.message).toContain(BACKEND_HINT);
  });

  it("forwarding server's HTML 500 page (backend down behind the proxy) -> same hint", async () => {
    const err = await apiFetch<never>("/api/meetings", undefined,
      fakeFetch(async () => new Response("<html>Internal Server Error</html>", { status: 500 }))).catch((e: ApiError) => e);
    expect(err.status).toBe(500);
    expect(err.message).toContain(BACKEND_HINT);
  });

  it("backend refusal shows the backend's own reason", async () => {
    const err = await apiFetch<never>("/api/meetings/x", undefined,
      fakeFetch(async () => Response.json({ detail: "No meeting x" }, { status: 404 }))).catch((e: ApiError) => e);
    expect(err.message).toBe("404: No meeting x.");
  });

  it("validation errors (a list of problems) are joined into one sentence", async () => {
    const err = await apiFetch<never>("/api/settings", undefined,
      fakeFetch(async () => Response.json({ detail: [{ msg: "field required" }, { msg: "too long" }] }, { status: 422 })))
      .catch((e: ApiError) => e);
    expect(err.message).toBe("422: field required; too long.");
  });

  it("a 200 that isn't JSON is reported, not passed on as data", async () => {
    const err = await apiFetch<never>("/api/meetings", undefined,
      fakeFetch(async () => new Response("<html>oops</html>", { status: 200 }))).catch((e: ApiError) => e);
    expect(err).toBeInstanceOf(ApiError);
  });

  it("stream URL never asks for a negative or fractional starting point, and escapes the id", () => {
    expect(eventsUrl("mtg_1", -5)).toBe("/api/meetings/mtg_1/events?since=0");
    expect(eventsUrl("mtg_1", Number.NaN)).toBe("/api/meetings/mtg_1/events?since=0");
    expect(eventsUrl("mtg_1", 7.9)).toBe("/api/meetings/mtg_1/events?since=7");
    expect(eventsUrl("a/b", 1)).toBe("/api/meetings/a%2Fb/events?since=1");
  });
});

describe("normal calls", () => {
  it("returns the parsed body", async () => {
    const body = await apiFetch<{ ok: boolean }>("/api/health", undefined, fakeFetch(async () => Response.json({ ok: true })));
    expect(body.ok).toBe(true);
  });
});
