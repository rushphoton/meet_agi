"use client";
/*
 * WHY THIS EXISTS
 * A fixed address for "the meeting happening now": it opens the live view of
 * the newest meeting. If there is none yet it waits and jumps as soon as one
 * starts (for example when scripts/replay.py begins).
 *
 * FAILURE IT PREVENTS
 * Having to copy a random meeting id into the address bar in the middle of a
 * demo.
 */
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";

export default function LatestLivePage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const look = async () => {
      try {
        const items = await api.listMeetings();
        if (!alive) return;
        setError(null);
        if (items.length > 0) {
          router.replace(`/meetings/${items[0].meeting_id}/live`);
          return;
        }
      } catch (e) {
        if (alive) setError(e instanceof ApiError ? e.message : String(e));
      }
      if (alive) setTimeout(look, 2000);
    };
    look();
    return () => { alive = false; };
  }, [router]);

  return (
    <>
      <h1>Live meeting</h1>
      {error && <div className="error">{error}</div>}
      <p className="muted">
        Waiting for a meeting to start... (for the fake meeting run <code>python scripts/replay.py</code>)
      </p>
    </>
  );
}
