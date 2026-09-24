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
 * The backend address is read when `npm run dev` or `npm run build` starts;
 * after changing NEXT_PUBLIC_API_BASE, restart dev or rebuild.
 */
import type { NextConfig } from "next";

const apiBase = (process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000").replace(/\/+$/, "");

const nextConfig: NextConfig = {
  compress: false,
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiBase}/api/:path*` }];
  },
};

export default nextConfig;
