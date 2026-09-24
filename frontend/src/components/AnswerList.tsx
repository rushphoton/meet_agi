/*
 * WHY THIS EXISTS
 * Lists what the bot said out loud after "Hey AGI" (question, answer, where
 * it came from, and whether it played, was stopped or was muted).
 *
 * FAILURE IT PREVENTS
 * Nobody being able to check afterwards what the bot told the room.
 */
import type { SpokenAnswer } from "@/lib/contract";

export default function AnswerList({ answers }: { answers: SpokenAnswer[] }) {
  if (answers.length === 0) return <p className="muted">No spoken answers yet.</p>;
  return (
    <ul className="plain">
      {answers.map((a) => (
        <li key={a.answer_id}>
          <div>
            <strong>Q{a.asked_by ? ` (${a.asked_by})` : ""}:</strong> {a.question}
            {a.canned && <span className="badge canned">CANNED</span>}
            <span className="badge muted">{a.status}</span>
          </div>
          <div><strong>A:</strong> {a.text}</div>
          {a.evidence.length > 0 && (
            <div className="small muted">Source: {a.evidence.map((e) => e.document).join(", ")}</div>
          )}
        </li>
      ))}
    </ul>
  );
}
