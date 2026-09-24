"use client";
/*
 * WHY THIS EXISTS
 * The "Send Meet AGI to a meeting" form: paste a Google Meet link (and an
 * optional title), press Send, and the dashboard opens the live view of the
 * new meeting. If the backend refuses, it shows why in plain words. If
 * another meeting is still live, it links to it so it can be ended.
 *
 * FAILURE IT PREVENTS
 * Having to use the API docs page or PowerShell on stage to send the bot
 * (review B, items 3 and 4).
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { checkSendForm, describeSendFailure, type SendFailure } from "@/lib/sendBot";

export default function SendBotForm() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<SendFailure | null>(null);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    const checked = checkSendForm(url, title);
    if (!checked.ok) {
      setFailure({ message: checked.message, offerLiveLink: false });
      return;
    }
    setBusy(true);
    setFailure(null);
    try {
      const record = await api.createMeeting(checked.request);
      router.push(`/meetings/${record.meeting_id}/live`);
    } catch (err) {
      setFailure(describeSendFailure(err));
      setBusy(false);
    }
  }

  return (
    <form className="panel" onSubmit={send} data-testid="send-bot">
      <h2>Send Meet AGI to a meeting</h2>
      <div className="controls">
        <input type="text" style={{ flex: "2 1 320px" }} placeholder="https://meet.google.com/abc-defg-hij"
          aria-label="Google Meet link" value={url} onChange={(e) => setUrl(e.target.value)} disabled={busy} />
        <input type="text" style={{ flex: "1 1 180px" }} placeholder="Title (optional)"
          aria-label="Meeting title" value={title} onChange={(e) => setTitle(e.target.value)} disabled={busy} />
        <button className="primary" type="submit" disabled={busy}>{busy ? "Sending..." : "Send Meet AGI"}</button>
      </div>
      <p className="small muted">The host must admit &quot;Meet AGI&quot; from the waiting room.</p>
      {failure && (
        <div className="error" role="alert" data-testid="send-error">
          {failure.message}
          {failure.offerLiveLink && <> <Link href="/live">Open the live meeting</Link></>}
        </div>
      )}
    </form>
  );
}
