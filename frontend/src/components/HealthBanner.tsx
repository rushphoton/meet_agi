"use client";
/*
 * WHY THIS EXISTS
 * Strips under the top bar. They show, in plain words: live problems the
 * backend reports (in red, e.g. a vendor failing), which parts are still
 * placeholders (from GET /api/health), or that the backend can't be reached
 * and how to start it.
 *
 * FAILURE IT PREVENTS
 * Canned alerts and answers being mistaken for real ones (CLAUDE.md rule 6),
 * real problems hiding among the expected placeholder notes, and empty
 * screens that are really "the backend is off".
 */
import { useHealth } from "@/lib/useHealth";

export default function HealthBanner() {
  const { health, error } = useHealth(10_000);

  if (error) return <div className="banner danger" role="alert">Backend not reachable. {error}</div>;
  if (!health) return null;
  const warnings = health.warnings ?? [];
  return (
    <>
      {warnings.length > 0 && (
        <div className="banner danger" role="alert" data-testid="health-warnings">
          <strong>Problem{warnings.length > 1 ? "s" : ""} right now:</strong>{" "}
          {warnings.join(" · ")}
        </div>
      )}
      {health.placeholders.length > 0 && (
        <div className="banner">
          <strong>PLACEHOLDER parts still running (their output is marked CANNED):</strong>{" "}
          {health.placeholders.join("; ")}
          {health.offline ? " · OFFLINE mode" : ""}
        </div>
      )}
    </>
  );
}
