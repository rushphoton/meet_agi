/*
 * WHY THIS EXISTS
 * Settings for the dashboard's own small web server. Its one real job: pass
 * every /api/... request from the browser on to the backend
 * (NEXT_PUBLIC_API_BASE, default http://localhost:8000).
 *
 * FAILURE IT PREVENTS
 * The browser blocking the dashboard from reading the backend because they
 * run on different ports (the backend does not send cross-site permission
 * headers, and only the integrate step may change the backend). Forwarding
 * makes every call same-site, so no permission is needed.
 *
 * INFRASTRUCTURE NOTE (CLAUDE.md rule 4): forwarding costs one extra local
 * hop per request (well under a millisecond). Without it every screen fails
 * with a cross-site error. It could go if the backend ever serves the
 * dashboard itself or adds CORS headers for http://localhost:3000.
 * Compression is off because a compressing server holds back the live event
 * stream in chunks, which would make alerts appear late; on localhost the
 * bandwidth saving is irrelevant.
 *
 * The live event stream (/api/meetings/{id}/events) is NOT forwarded by this
 * rule: forwarding holds back the stream's opening until the backend sends its
 * first event, so a quiet meeting would look "not connected" for up to 15 s.
 * A small relay (src/app/api/meetings/[id]/events/route.ts) handles it and
 * takes precedence over this rule.
 *
 * The backend address is read when `npm run dev` or `npm run build` starts;
 * after changing NEXT_PUBLIC_API_BASE, restart dev or rebuild.
 */
import type { NextConfig } from "next";
import { backendBase } from "./src/lib/backendBase";

const apiBase = backendBase();

const nextConfig: NextConfig = {
  compress: false,
  async rewrites() {
    // "fallback" runs after the dashboard's own routes (including the dynamic
    // live-stream relay); plain rewrites would run before dynamic routes and swallow it.
    return {
      beforeFiles: [],
      afterFiles: [],
      fallback: [{ source: "/api/:path*", destination: `${apiBase}/api/:path*` }],
    };
  },
};

export default nextConfig;
