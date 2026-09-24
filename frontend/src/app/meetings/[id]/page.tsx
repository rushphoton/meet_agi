"use client";
/*
 * WHY THIS EXISTS
 * The post-meeting review: the executive summary (key topics, takeaways,
 * follow-up and alert counts), the follow-ups you can tick resolved or
 * outstanding, every alert with its full reasoning (including ones held back
 * from the chat), what the bot said aloud, and the full transcript.
 *
 * FAILURE IT PREVENTS
 * Decisions and open questions from a meeting evaporating once the call ends.
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";
import AlertCard from "@/components/AlertCard";
import AnswerList from "@/components/AnswerList";
import Transcript from "@/components/Transcript";
import { api, ApiError } from "@/lib/api";
import type { FollowUp } from "@/lib/contract";
import { formatDate } from "@/lib/format";
import { followUpCounts, visibleAlertCount } from "@/lib/meetingState";
import { useMeetingStream } from "@/lib/useMeetingStream";

export default function ReviewPage() {
  const { id } = useParams<{ id: string }>();
  const { state, error, setFollowUp } = useMeetingStream(id, { live: true });
  const [pending, setPending] = useState<string | null>(null);
  const [toggleError, setToggleError] = useState<string | null>(null);

  const record = state?.record;
  const flagged = useMemo(() => new Set(record?.alerts.flatMap((a) => a.segment_ids) ?? []), [record?.alerts]);

  async function toggle(fu: FollowUp) {
    setPending(fu.follow_up_id);
    setToggleError(null);
    try {
      const next = fu.status === "outstanding" ? "resolved" : "outstanding";
      setFollowUp(await api.setFollowUp(id, fu.follow_up_id, { status: next }));
    } catch (e) {
      setToggleError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setPending(null);
    }
  }

  if (!record) {
    return (
      <>
        <h1>Meeting review</h1>
        {error ? <div className="error">{error}</div> : <p className="muted">Loading...</p>}
      </>
    );
  }

  const counts = followUpCounts(record);
  const summary = record.summary;

  return (
    <>
      <h1>{record.title}</h1>
      <p className="sub">
        {formatDate(record.started_at)}
        {record.ended_at ? ` - ended ${formatDate(record.ended_at)}` : " - still live"}
        {" · "}{record.participants.map((p) => p.display_name).join(", ") || "no participants yet"}
        {record.source === "replay" && <span className="badge canned">FAKE MEETING (replay)</span>}
        {" · "}<Link href={`/meetings/${record.meeting_id}/live`}>Open live view</Link>
      </p>

      <section className="panel" data-testid="summary">
        <h2>
          Executive summary
          {summary?.canned && <span className="badge canned">CANNED</span>}
        </h2>
        {!summary && (
          <p className="muted">
            {record.ended_at ? "The summary is being written..." : "The summary appears when the meeting ends."}
          </p>
        )}
        {summary && (
          <>
            <p>
              <strong>{counts.outstanding}</strong> follow-ups outstanding, <strong>{counts.resolved}</strong> resolved
              {" · "}<strong>{visibleAlertCount(record)}</strong> alerts
              <span className="small muted"> (summary counted {summary.follow_up_count} follow-ups, {summary.alert_count} alerts)</span>
            </p>
            <h3>Key topics</h3>
            {summary.key_topics.length ? <ul>{summary.key_topics.map((t, i) => <li key={i}>{t}</li>)}</ul> : <p className="muted">none</p>}
            <h3>Takeaways</h3>
            {summary.takeaways.length ? <ul>{summary.takeaways.map((t, i) => <li key={i}>{t}</li>)}</ul> : <p className="muted">none</p>}
          </>
        )}
      </section>

      <section className="panel" data-testid="follow-ups">
        <h2>Follow-ups ({counts.outstanding} outstanding, {counts.resolved} resolved)</h2>
        {toggleError && <div className="error">{toggleError}</div>}
        {record.follow_ups.length === 0 && <p className="muted">No follow-ups for this meeting.</p>}
        <ul className="plain">
          {record.follow_ups.map((fu) => (
            <li key={fu.follow_up_id}>
              <label>
                <input
                  type="checkbox" checked={fu.status === "resolved"} disabled={pending === fu.follow_up_id}
                  onChange={() => void toggle(fu)}
                />{" "}
                <span style={fu.status === "resolved" ? { textDecoration: "line-through" } : undefined}>{fu.text}</span>
              </label>
              {fu.owner && <span className="small muted"> · owner: {fu.owner}</span>}
              {fu.source_alert_id && <span className="small muted"> · from an alert</span>}
              <span className={`badge ${fu.status === "resolved" ? "ok" : "alert"}`}>{fu.status}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="panel">
        <h2>Alerts ({record.alerts.length})</h2>
        {record.alerts.length === 0 && <p className="muted">No alerts in this meeting.</p>}
        <div data-testid="alerts">
          {record.alerts.map((a) => <AlertCard key={a.alert_id} alert={a} firedAt={state.alertAt[a.alert_id]} />)}
        </div>
      </section>

      <section className="panel">
        <h2>Spoken answers</h2>
        <AnswerList answers={record.answers} />
      </section>

      <section className="panel">
        <h2>Transcript ({record.segments.length} lines)</h2>
        <Transcript segments={record.segments} flagged={flagged} />
      </section>
    </>
  );
}
