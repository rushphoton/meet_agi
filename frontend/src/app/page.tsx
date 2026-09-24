"use client";
/*
 * WHY THIS EXISTS
 * The sessions list: every meeting, newest first, with its date, who spoke,
 * how many follow-ups are still outstanding or resolved, and then how many
 * alerts reached the room. Each row opens the review or the live view. It
 * refreshes itself every few seconds so a new meeting appears on its own.
 * The form at the top sends the bot to a real Google Meet.
 *
 * FAILURE IT PREVENTS
 * Open follow-ups getting lost behind the more eye-catching alert count
 * (follow-ups come first on purpose, DESIGN.md §1).
 */
import Link from "next/link";
import { useEffect, useState } from "react";
import SendBotForm from "@/components/SendBotForm";
import { api, ApiError } from "@/lib/api";
import type { MeetingListItem } from "@/lib/contract";
import { SESSION_COLUMNS, sessionRow } from "@/lib/format";

const NUMERIC = new Set<string>(["Follow-ups outstanding", "Follow-ups resolved", "Alerts"]);

export default function SessionsPage() {
  const [items, setItems] = useState<MeetingListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () =>
      api.listMeetings()
        .then((x) => { if (alive) { setItems(x); setError(null); } })
        .catch((e) => { if (alive) setError(e instanceof ApiError ? e.message : String(e)); });
    load();
    const t = setInterval(load, 4000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  return (
    <>
      <h1>Sessions</h1>
      <p className="sub">Every meeting Meet AGI has joined or replayed. Newest first.</p>
      <SendBotForm />
      {error && <div className="error">{error}</div>}
      {items === null && !error && <p className="muted">Loading...</p>}
      {items && items.length === 0 && (
        <div className="panel">
          No meetings yet. Send the bot above, or run the fake meeting: <code>python scripts/replay.py</code>
        </div>
      )}
      {items && items.length > 0 && (
        <div className="panel">
          <table data-testid="sessions">
            <thead>
              <tr>
                {SESSION_COLUMNS.map((c) => <th key={c} className={NUMERIC.has(c) ? "num" : ""}>{c}</th>)}
                <th>Open</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={item.meeting_id}>
                  {sessionRow(item).map((cell, i) => (
                    <td key={i} className={NUMERIC.has(SESSION_COLUMNS[i]) ? "num" : ""}>
                      {i === 1 ? <Link href={`/meetings/${item.meeting_id}`}>{cell}</Link> : cell}
                    </td>
                  ))}
                  <td>
                    <Link href={`/meetings/${item.meeting_id}`}>Review</Link>
                    {" · "}
                    <Link href={`/meetings/${item.meeting_id}/live`}>Live</Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
