/*
 * WHY THIS EXISTS
 * Shows one alert with everything behind it: what was claimed and by whom,
 * what the documents say, how confident the bot is, its full reasoning, and
 * whether it actually reached the meeting chat or was held back.
 *
 * FAILURE IT PREVENTS
 * A chat message in the meeting with no explanation anyone can check - the
 * dashboard is where the "Details in the dashboard" promise is kept.
 */
import type { Alert } from "@/lib/contract";
import { formatConfidence, formatDate } from "@/lib/format";

export default function AlertCard({ alert, firedAt }: { alert: Alert; firedAt?: string }) {
  return (
    <article className={`alertcard${alert.gated ? " gated" : ""}`}>
      <h3>
        {alert.kind}: {alert.topic}
        {alert.canned && <span className="badge canned" title="Made by a placeholder, not a real model">CANNED</span>}
        {alert.gated
          ? <span className="badge muted">held back - dashboard only</span>
          : alert.delivered_to_chat
            ? <span className="badge ok">posted to chat</span>
            : <span className="badge muted">not posted (muted)</span>}
      </h3>
      <dl>
        <dt>Finding</dt><dd><strong>{alert.finding}</strong></dd>
        <dt>Claim</dt><dd>{alert.claim}</dd>
        <dt>Said by</dt><dd>{alert.said_by.length ? alert.said_by.join(", ") : "-"}</dd>
        <dt>Confidence</dt><dd>{formatConfidence(alert.confidence)}</dd>
        {alert.gated && alert.gate_reason && (<><dt>Held back because</dt><dd>{alert.gate_reason}</dd></>)}
        <dt>Reasoning</dt><dd className="reasoning">{alert.reasoning}</dd>
        <dt>Evidence</dt>
        <dd>
          {alert.evidence.length === 0 && <span className="muted">none cited</span>}
          {alert.evidence.map((ev, i) => (
            <div className="evidence" key={i}>
              <strong>{ev.document}</strong>{ev.locator ? ` · ${ev.locator}` : ""}
              <div>{ev.passage}</div>
            </div>
          ))}
        </dd>
        <dt>Models</dt><dd className="small">{alert.models_used.length ? alert.models_used.join(", ") : "-"}</dd>
        {firedAt && (<><dt>Fired at</dt><dd className="small">{formatDate(firedAt)}</dd></>)}
      </dl>
    </article>
  );
}
