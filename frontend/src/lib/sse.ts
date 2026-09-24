/*
 * WHY THIS EXISTS
 * Reads the backend's live event stream (Server-Sent Events) piece by piece.
 * The browser's built-in reader (EventSource) only hands over events whose
 * names it was told about in advance and resumes in a way this backend
 * doesn't use, so the dashboard reads the raw stream itself with this small,
 * tested parser.
 *
 * FAILURE IT PREVENTS
 * - An event of a type the dashboard doesn't know about silently vanishing,
 *   which would leave a hole in the numbering and freeze the live view.
 * - A message split across two network packets being lost or garbled.
 */
export interface SseMessage { event: string; data: string; id: string | null }

export class SseParser {
  private buffer = "";
  private data: string[] = [];
  private event = "";
  private id: string | null = null;

  /** Feed a chunk of text; returns every message completed by it. */
  push(chunk: string): SseMessage[] {
    this.buffer += chunk;
    const out: SseMessage[] = [];
    let nl: number;
    while ((nl = this.buffer.search(/\r\n|\r|\n/)) !== -1) {
      const line = this.buffer.slice(0, nl);
      const width = this.buffer.startsWith("\r\n", nl) ? 2 : 1;
      // A lone "\r" at the very end may be the first half of "\r\n": wait for more.
      if (this.buffer[nl] === "\r" && width === 1 && nl === this.buffer.length - 1) break;
      this.buffer = this.buffer.slice(nl + width);
      const msg = this.line(line);
      if (msg) out.push(msg);
    }
    return out;
  }

  private line(line: string): SseMessage | null {
    if (line === "") {
      if (this.data.length === 0) { this.event = ""; return null; }
      const msg = { event: this.event || "message", data: this.data.join("\n"), id: this.id };
      this.data = [];
      this.event = "";
      return msg;
    }
    if (line.startsWith(":")) return null; // comment / keep-alive
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") this.data.push(value);
    else if (field === "event") this.event = value;
    else if (field === "id") this.id = value;
    return null;
  }
}

/** Wait before reconnect attempt n (1-based): 1 s, 2 s, 4 s ... capped at 10 s. */
export function reconnectDelayMs(attempt: number): number {
  const n = Math.max(1, Math.floor(attempt));
  return Math.min(10_000, 1000 * 2 ** (n - 1));
}
