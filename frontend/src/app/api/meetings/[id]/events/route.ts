/*
 * WHY THIS EXISTS
 * Relays a meeting's live event stream from the backend to the browser. It
 * opens the stream at once with a harmless ": connected" line, then passes
 * every backend byte through as it arrives.
 *
 * FAILURE IT PREVENTS
 * The general /api forwarding rule (next.config.ts) holds the stream back
 * until the backend's first event or its 15-second keep-alive. On a quiet or
 * resumed meeting the live view then says "Connecting..." when it is really
 * connected, which is exactly when Ray needs to trust it.
 */
import { backendBase, upstreamEventsUrl } from "@/lib/backendBase";

export const dynamic = "force-dynamic";

export async function GET(req: Request, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const url = upstreamEventsUrl(backendBase(), id, new URL(req.url).searchParams.get("since"));
  let upstream: Response;
  try {
    upstream = await fetch(url, { signal: req.signal, cache: "no-store", headers: { Accept: "text/event-stream" } });
  } catch {
    return Response.json(
      { detail: "Can't reach the backend. Is it running? Start it with: python scripts/serve.py" }, { status: 502 });
  }
  if (!upstream.ok || !upstream.body) {
    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: { "Content-Type": upstream.headers.get("content-type") ?? "application/json" },
    });
  }
  const reader = upstream.body.getReader();
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      controller.enqueue(new TextEncoder().encode(": connected\n\n"));
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          controller.enqueue(value);
        }
      } catch {
        // backend went away or the browser left; the browser reconnects with ?since
      }
      try { controller.close(); } catch { /* already closed */ }
    },
    cancel() {
      void reader.cancel().catch(() => undefined);
    },
  });
  return new Response(body, {
    headers: { "Content-Type": "text/event-stream", "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no" },
  });
}
