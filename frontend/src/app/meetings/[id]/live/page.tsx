"use client";
/*
 * WHY THIS EXISTS
 * The live meeting view: the transcript with speakers as it happens, every
 * alert with its full reasoning the moment it fires, what the bot said out
 * loud and posted in chat, and the controls - wake the bot by hand, stop it
 * talking, mute it, or end the meeting.
 *
 * FAILURE IT PREVENTS
 * Being unable to see why the bot posted something, or to silence it fast,
 * while the meeting is still going. It also turns red when the bot is in the
 * call but no transcript has arrived for over 30 s (a dead laptop, VPN or
 * ngrok tunnel looks just like a quiet room otherwise).
 */
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import AlertCard from "@/components/AlertCard";
import AnswerList from "@/components/AnswerList";
import Transcript from "@/components/Transcript";
import { api, ApiError } from "@/lib/api";
import { silenceSeconds } from "@/lib/silence";
import { useHealth } from "@/lib/useHealth";
import { useMeetingStream } from "@/lib/useMeetingStream";

const STATUS_TEXT = {
  loading: "Connecting...",
  live: "Live",
  reconnecting: "Connection lost - reconnecting (nothing will be missed)",
  off: "Not streaming",
  error: "Error",
} as const;

export default function LivePage() {
  const { id } = useParams<{ id: string }>();
  const { state, status, error } = useMeetingStream(id, { live: true });
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const { health } = useHealth(5000);
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const record = state?.record;
  const flagged = useMemo(() => new Set(record?.alerts.flatMap((a) => a.segment_ids) ?? []), [record?.alerts]);
  const ended = Boolean(record?.ended_at);

  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setActionError(null);
    try {
      await fn();
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (!record) {
    return (
      <>
        <h1>Live meeting</h1>
        {error ? <div className="error">{error}</div> : <p className="muted">Loading...</p>}
      </>
    );
  }

  return (
    <>
      <h1>{record.title}</h1>
      <p className="sub">
        <span className={`status-dot ${status}`} />
        <span data-testid="stream-status">{ended ? "Meeting ended" : STATUS_TEXT[status]}</span>
        {" · "}bot: {record.bot_status ?? "unknown"}
        {record.source === "replay" && <span className="badge canned">FAKE MEETING (replay)</span>}
        {record.muted && <span className="badge alert">MUTED</span>}
        {" · "}<Link href={`/meetings/${record.meeting_id}`}>Open review</Link>
      </p>

      {(() => {
        const silent = silenceSeconds({
          ended, botStatus: record.bot_status, lastSegmentAt: state.lastSegmentAt,
          lastWebhookAt: health?.last_webhook_at, meetingStartedAt: record.started_at, nowMs: now,
        });
        return silent === null ? null : (
          <div className="notice-danger" role="alert" data-testid="silence-warning">
            No transcript for {silent} s - check the laptop, VPN and ngrok.
          </div>
        );
      })()}

      <div className="panel">
        <div className="controls">
          <input
            type="text" style={{ maxWidth: 360 }} placeholder="Question for the bot (optional)"
            value={question} onChange={(e) => setQuestion(e.target.value)} disabled={ended}
          />
          <button className="primary" disabled={busy || ended}
            onClick={() => act(async () => { await api.wake(id, { question: question.trim() || null }); setQuestion(""); })}>
            Wake (ask Meet AGI)
          </button>
          <button disabled={busy || ended} onClick={() => act(() => api.stop(id))}>Stop talking</button>
          <button disabled={busy || ended} className={record.muted ? "primary" : ""}
            onClick={() => act(() => api.mute(id, { muted: !record.muted }))}>
            {record.muted ? "Unmute" : "Mute"}
          </button>
          <button className="danger" disabled={busy || ended}
            onClick={() => { if (confirm("End this meeting? The bot leaves and the summary is written.")) void act(() => api.endMeeting(id)); }}>
            End meeting
          </button>
          <span className="small muted">
            wakes: {state.wakeCount} · stops: {state.stopCount} · events: {state.lastSeq}
          </span>
        </div>
        {actionError && <div className="error" style={{ marginTop: 10 }}>{actionError}</div>}
        {record.muted && <p className="small muted">Muted: the bot won&apos;t speak or post in chat. Alerts still appear here.</p>}
      </div>

      <div className="grid2">
        <section className="panel">
          <h2>Transcript ({record.segments.length} lines)</h2>
          <Transcript segments={record.segments} flagged={flagged} follow />
        </section>
        <section>
          <div className="panel">
            <h2>Alerts ({record.alerts.length})</h2>
            {record.alerts.length === 0 && <p className="muted">No alerts yet.</p>}
            <div data-testid="alerts">
              {[...record.alerts].reverse().map((a) => (
                <AlertCard key={a.alert_id} alert={a} firedAt={state.alertAt[a.alert_id]} />
              ))}
            </div>
          </div>
          <div className="panel">
            <h2>Spoken answers</h2>
            <AnswerList answers={record.answers} />
          </div>
          <div className="panel">
            <h2>Chat posts</h2>
            {record.chat_posts.length === 0 && <p className="muted">Nothing posted yet.</p>}
            <ul className="plain small">
              {record.chat_posts.map((c) => (
                <li key={c.chat_id}>{c.text} <span className="badge muted">{c.status}</span></li>
              ))}
            </ul>
          </div>
        </section>
      </div>
    </>
  );
}
