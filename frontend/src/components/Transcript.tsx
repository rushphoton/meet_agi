"use client";
/*
 * WHY THIS EXISTS
 * The running transcript: every finished sentence with its speaker and the
 * time into the meeting. Lines an alert is about are highlighted. In the live
 * view it keeps the newest line in sight unless you have scrolled up to read.
 *
 * FAILURE IT PREVENTS
 * Not being able to tell who said the thing an alert is about.
 */
import { useEffect, useRef } from "react";
import type { TranscriptSegment } from "@/lib/contract";
import { formatClock } from "@/lib/format";

export default function Transcript({ segments, flagged, follow }: {
  segments: TranscriptSegment[]; flagged: Set<string>; follow?: boolean;
}) {
  const box = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  useEffect(() => {
    const el = box.current;
    if (follow && el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [segments.length, follow]);

  if (segments.length === 0) return <p className="muted">No transcript yet.</p>;
  return (
    <div
      className={`transcript${follow ? "" : " full"}`}
      ref={box}
      onScroll={(e) => {
        const el = e.currentTarget;
        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      }}
    >
      {segments.map((s) => (
        <div key={s.segment_id} className={`line${flagged.has(s.segment_id) ? " flagged" : ""}`}>
          <span className="t">{formatClock(s.t_start)}</span>
          <span className="who">{s.speaker_name}</span>
          <span>{s.text}</span>
        </div>
      ))}
    </div>
  );
}
