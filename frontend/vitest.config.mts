/*
 * WHY THIS EXISTS
 * Tells the test runner where the dashboard's tests are and how "@/..." paths
 * resolve, so `npm test` runs the same way on Windows and Linux.
 * FAILURE IT PREVENTS
 * Tests silently not running, or failing only because an import path differs.
 */
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: { include: ["src/**/*.test.ts"], environment: "node" },
});
