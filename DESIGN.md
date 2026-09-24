# Meet AGI — Design

Status: design frozen for milestone 0 (24 Sep 2026); contract as built at milestone 0 (tag `milestone-0`). Owner: Ray. Changes to this file and to the contract happen only on `main`, in the integrate step (CLAUDE.md rules 3 and 10).

---

## 1. What it is (one page)

Meet AGI is a meeting participant that you invite into a Google Meet. It shows up as "Meet AGI", posts a one-line notice in the chat saying it is listening, and then stays silent.

It hears everyone. Each person's audio arrives separately, so every sentence in its transcript carries the right speaker's name. It reads along with your own documents (for example, the board deck).

It speaks up in two ways only:

1. **Alert (silent, in the chat).** When the room disagrees about a fact, contradicts itself, or someone is unsure of a number that is in your documents, it posts a short chat message, for example:
   `Because you mentioned Q3 revenue was rising: the board deck says it fell 4%. Details in the dashboard.`
   The full reasoning (what was said, by whom, which document, how confident) appears in the web dashboard at the same moment.
2. **Speech mode (out loud).** When someone says "Hey AGI", it says a short filler line ("Sure, let me look that up") so the room knows it heard. It then answers the question out loud from your documents and posts a one-line summary in the chat that starts `Because you asked:`. Saying "AGI, stop talking" cuts it off.

The dashboard has three parts:

- **Settings:** documents, speaker names, which AI model does which job.
- **Live meeting view:** running transcript with speakers, alerts with reasoning as they fire, a manual "wake" button, and a mute switch.
- **Post-meeting review:** executive summary (key topics, takeaways, number of follow-ups), full transcript, every alert with its reasoning, and follow-ups you can tick resolved or outstanding. The sessions list shows follow-up counts before alert counts.

What it is **not**, for the demo:

- It doesn't remember across meetings.
- It doesn't recognize voices.
- It doesn't ask clarifying questions.
- It doesn't stream speech word by word.

These are on the cut list (§9).

How it gets built: after this document and milestone 0, three AI sub-agents each build one part in their own copy of the repo, with nobody watching. Each proves its part against a scripted fake meeting before anything touches a real call. Anything this document leaves open, they resolve with the rule in §10.

---

## 2. Verified Recall.ai capability matrix

Checked against docs.recall.ai on 24 Sep 2026. Region base URL: `https://us-west-2.recall.ai`. "Verified" means stated in Recall's docs. It does not mean we have exercised it live: nothing below has run against a real Recall account yet (see risk R1).

| # | Need | Recall feature (field / endpoint) | Google Meet | Status | Source |
|---|---|---|---|---|---|
| 1 | Join as a guest | `POST /api/v1/bot/` with `meeting_url`, `bot_name`. Signed-out bots land in the waiting room; **the host must admit them**. They cannot join meetings that require sign-in (`google_meet_sign_in_missing_login_credentials`) or that have "ask to join" disabled (`google_meet_knocking_disabled`). | Yes | Verified | docs.recall.ai/docs/google-meet-faq |
| 2 | Real-time transcript by webhook | `recording_config.transcript.provider.recallai_streaming` (`mode: "prioritize_low_latency"`, `language_code: "en"`) + `recording_config.realtime_endpoints: [{type:"webhook", url, events:["transcript.data"]}]` | Yes | Verified | docs.recall.ai/docs/real-time-transcription |
| 3 | Finalized vs partial | `transcript.data` = finalized; `transcript.partial_data` = interim. **We subscribe to `transcript.data` only.** | Yes | Verified | same |
| 4 | Speaker on every segment | `data.participant {id, name, is_host, email}` on each `transcript.data` event; `data.words[] {text, start_timestamp.relative, end_timestamp.relative}` | Yes | Verified | same |
| 5 | Separate audio track per speaker | `recording_config.transcript.diarization.use_separate_streams_when_available: true` ("perfect diarization"; up to ~1.8x transcription cost with overlapping speech). Raw per-participant PCM (`audio_separate_raw`, websocket only, 16 kHz mono s16le, 16 loudest speakers) exists, but **we don't use it**: the per-stream transcript is enough. | Yes | Verified | real-time-transcription; how-to-get-separate-audio-per-participant-realtime |
| 6 | Participant join/leave | Real-time events `participant_events.join`, `participant_events.leave`, `participant_events.update`, `speech_on/off`, `chat_message` | Yes | Verified (event names; payload schema not read) | docs.recall.ai/docs/real-time-webhook-endpoints |
| 7 | Speak into the meeting | `POST /api/v1/bot/{id}/output_audio/` body `{kind:"mp3", b64_data}`. **Requires `automatic_audio_output` in the create-bot request** (we pass a short silent mp3). | Yes (short clips) | Verified | docs.recall.ai/docs/output-audio-in-meetings |
| 8 | Stop speaking | `DELETE /api/v1/bot/{id}/output_audio/` exists; **the docs don't say whether it cuts audio that is already playing** | Unknown | Unverified | reference/bot_output_audio_destroy |
| 9 | Post to chat | `POST /api/v1/bot/{id}/send_chat_message/` body `{to:"everyone", message}`; Google Meet **500-character limit**; `pin` is Meet-only; only `"everyone"` supported on Meet | Yes | Verified | docs.recall.ai/docs/sending-chat-messages |
| 10 | Consent notice on join | `chat.on_bot_join` in create-bot request | Yes | Verified | same |
| 11 | Leave / list / status | `POST /api/v1/bot/{id}/leave_call/` (irreversible); `GET /api/v1/bot/`; `GET /api/v1/bot/{id}` | Yes | Verified | reference/bot_leave_call_create |
| 12 | Webhook authenticity | Signed with headers `webhook-id`, `webhook-timestamp`, `webhook-signature` (HMAC-SHA256, workspace secret starting `whsec_`). **The docs don't describe URL tokens.** | Yes | Verified | docs.recall.ai/docs/authenticating-requests-from-recallai |
| 13 | Retries | Non-2xx or timeout → retried every 1 s, up to 60 times, then endpoint marked `failed`. **Receiver must answer 2xx fast and deduplicate.** | Yes | Verified | real-time-webhook-endpoints |
| 14 | Rate limit | 300 requests/min/workspace on bot endpoints | n/a | Verified | reference pages |
| 15 | Price | $0.50/h recording + $0.15/h Recall transcription; first 5 h free; no extra charge for audio/chat | n/a | Verified (pricing page) | recall.ai/pricing |
| 16 | Room devices | Google Meet "adaptive audio" puts several in-room people on one stream, so they all get one name | Yes (limitation) | Verified | google-meet-faq |

Output Media (a web page as the bot's camera and mic) is **not used**: it can't be combined with `automatic_audio_output`, and it costs more per hour. It's the fallback if row 8 turns out to be unfixable (risk R3).

---

## 3. Architecture

### 3.1 Plain English first

Think of it as five desks in one office, passing notes through one shared in-tray (the **event bus**):

1. **The receiver (meeting lane).** Recall sends every finished sentence it hears to a public web address. The receiver checks the note is really from Recall, says "got it" within milliseconds (otherwise Recall resends it every second), and hands the sentence on. *Prevents:* duplicate or forged transcript lines, and Recall giving up on us.
2. **The thinker (engine lane).** It reads every finished sentence and decides whether it's someone calling "Hey AGI", someone saying "AGI, stop talking", or a factual dispute worth flagging. A cheap, fast model looks at every sentence. A careful model is called only when the cheap one thinks something is there. *Prevents:* paying for the careful model on every "yeah, sounds good".
3. **The librarian (engine lane).** It holds your documents, cut into short passages, and finds the passages relevant to a sentence or a question. *Prevents:* answers and alerts that aren't grounded in your documents.
4. **The mouth (meeting lane).** It turns text into speech (Inworld), plays one clip at a time into the meeting, posts chat messages, and throws away everything queued when told to stop or mute. *Prevents:* two answers talking over each other, and the bot speaking while muted.
5. **The dashboard (screens lane).** It reads the same in-tray live and shows everything with reasoning. It also sends the manual-wake and mute buttons back. *Prevents:* chat messages with no explanation behind them.

A **recorder** writes each meeting to one JSON file as it happens. The dashboard's review screen reads it back. A **fake meeting** can replay a scripted conversation through the receiver, so every desk can be tested without a real call, a real key or a real cost.

### 3.2 Components (technical)

| Component | Where | Owner | Why it exists |
|---|---|---|---|
| App entry, wiring, settings loader | `backend/app/main.py`, `backend/app/settings.py` | integrate step only | Single place where lanes are switched on; lanes can't break each other's wiring |
| Contract (all data shapes) | `backend/app/contract/` (Pydantic v2) | integrate step only | One definition of every shape; TypeScript is generated from it |
| Event bus | `backend/app/core/bus.py` | milestone 0 (integrate) | In-process publish/subscribe (asyncio). Decouples lanes; every event also goes to the recorder and the live stream |
| Store / recorder | `backend/app/core/store.py` | milestone 0 (integrate) | In-memory state plus one JSON file per meeting (`data/meetings/{meeting_id}.json`, atomic write). Postgres later |
| Live stream | `GET /api/meetings/{id}/events` (Server-Sent Events) | milestone 0 (integrate) | Dashboard live view; `?since={seq}` resumes without gaps |
| Engine entry (frozen) | `backend/app/pipeline/entry.py` | engine lane builds behind it | The one door transcript enters the thinker by (§4.4) |
| Pipeline | `backend/app/pipeline/` | engine lane | Wake/stop detection, dispute detection, gate, summary |
| Knowledge | `backend/app/knowledge/` | engine lane | Loads `knowledge/` files, chunks them, keyword (BM25-style) search. No vector database |
| LLM providers | `backend/app/providers/llm/` | engine lane | Gemini + Claude clients, plus a canned provider for tests |
| Recall client + receiver | `backend/app/integrations/` | meeting lane | Bot lifecycle, webhook receiver, chat posting, fake Recall for tests |
| Speech assembly + audio queue | `backend/app/speech/` | meeting lane | Sentence assembly from utterances; the one-clip-at-a-time queue; filler bank |
| Voice provider | `backend/app/providers/voice/` | meeting lane | Inworld TTS, plus a canned sample-clip provider that says it is canned |
| Dashboard | `frontend/` (Next.js, App Router, TypeScript) | screens lane | Settings, live view, review |
| Generated client | `frontend/src/contract/` | generated only | `openapi-typescript` output from `contract/openapi.json`; never hand-edited |
| Fake meeting | `fixtures/`, `backend/app/dev/fake_meeting.py`, `scripts/replay.py` | milestone 0 | Scripted conversation replayed through the real receiver |

### 3.3 Flows

**Hearing:**

1. Recall sends `transcript.data` to `POST /webhooks/recall/{RECALL_WEBHOOK_TOKEN}`.
2. The receiver checks the token (constant time), plus the Recall signature when `RECALL_WORKSPACE_SECRET` is set.
3. It deduplicates on `(participant.id, first word start_timestamp.relative)`, returns 200, then processes.
4. The sentence assembler splits utterances on `. ? !`. It flushes a sentence on a ≥1.2 s gap between words, at 40 words, or when the next utterance comes from a different speaker.
5. It maps the speaker name through the Settings speaker table.
6. It publishes `transcript.segment` and calls `process_segment(segment)`.

**Alert:**

1. `process_segment` runs the cheap check (Flash-Lite) on the last 8 segments plus the top 3 document passages. The check returns `{worth_a_look: bool, score: 0-1, topic}`.
2. If `worth_a_look` and `score ≥ gate.cheap_threshold` (0.5), the judge (Claude) returns a structured verdict.
3. The gate checks: confidence ≥ `gate.min_confidence` (0.75); no alert in the last `gate.cooldown_seconds` (90); fewer than `gate.max_alerts_per_meeting` (8); not muted-for-alerts.
4. If the gate passes, publish `alert` (full reasoning) and `chat.post` (`Because you mentioned {topic}: {finding} Details in the dashboard.`, ≤500 chars).
5. Blocked verdicts are still recorded as `alert` with `gated: true` and a reason. They show in the dashboard only, never in chat.

**Speech mode:**

1. A wake is detected (§4.5) or the manual button is pressed: publish `wake`.
2. The meeting lane plays a cached filler clip at once.
3. The engine takes the question: the rest of the same sentence after the wake phrase, or else the next segment from the same speaker within 8 s. If nothing comes, it answers "Sorry, I didn't catch a question."
4. It searches knowledge (top 5 passages) and Claude answers in ≤60 words, one clip, citing the document by name.
5. Publish `spoken.answer` and `chat.post` (`Because you asked: {one line}`).
6. The meeting lane synthesizes the answer (Inworld, MP3), queues it and plays it.

**Stop:**

1. The stop phrase is detected: publish `stop`.
2. The meeting lane empties the queue and calls `DELETE output_audio`.
3. The engine leaves speech mode and drops any answer still being generated.

**Mute:**

1. Dashboard mute: publish `mute {muted: true}`.
2. The meeting lane discards queued audio, then plays and posts nothing until unmuted.
3. The engine keeps detecting. Alerts still go to the dashboard, marked `delivered_to_chat: false`.

**End:**

1. The bot leaves or the call ends (bot status `call_ended`/`done`), or the dashboard presses "End".
2. Publish `meeting.ended`.
3. The engine builds `meeting.summary` and `follow_up` items (Claude, from transcript plus alerts).
4. The store writes the final JSON file.

### 3.4 Where it runs (demo)

- The backend runs on Ray's laptop: `uvicorn` on port 8000.
- Recall reaches it at `https://among-sanction-browsing.ngrok-free.dev` via `tools\ngrok.exe http 8000 --url=...`.
- The dashboard runs on `http://localhost:3000` and calls `NEXT_PUBLIC_API_BASE=http://localhost:8000`.
- There is one meeting at a time (see §10 default).

### 3.5 Models (each is a setting)

| Job | Default model ID (verified in account 24 Sep 2026) | Setting key | Fallback |
|---|---|---|---|
| Cheap "worth a closer look" check | `gemini-3.5-flash-lite` | `models.cheap_check` | `gemini-2.5-flash-lite` |
| Dispute judgement | `claude-haiku-4-5-20251001` | `models.judge` | `claude-sonnet-5` |
| Spoken answer | `claude-haiku-4-5-20251001` | `models.answer` | `claude-sonnet-5` |
| Summary + follow-ups | `claude-haiku-4-5-20251001` | `models.summary` | `claude-sonnet-5` |
| Voice | Inworld `inworld-tts-2`, voice `Grant`, MP3 | `voice.model`, `voice.voice_id` | canned sample clip (says so) |

Inworld endpoint, verified from their API reference:

- `POST https://api.inworld.ai/tts/v1/voice`
- Header `Authorization: Basic {INWORLD_API_KEY}`
- Body `{text ≤2000 chars, voiceId, modelId, audioConfig:{audioEncoding:"MP3"}}`
- The response field `audioContent` holds base64 audio.

---

## 4. Data contract

### 4.1 Rules

- Shapes are defined once, as Pydantic v2 models in `backend/app/contract/`.
- `scripts/export_openapi.py` writes `contract/openapi.json`, and `openapi-typescript` generates `frontend/src/contract/schema.d.ts`. Both outputs are committed. `scripts/verify.py` fails if they drift from the models.
- Only the integrate step on `main` may change `backend/app/contract/`, the exported OpenAPI file, or the generated client. A lane that needs a change records it under "Assumptions recorded by sub-agents" in STATE.md and builds against the contract as it is.
- All times are UTC ISO-8601 strings with a `Z` (`at`). Meeting-relative times are float seconds (`t_start`, `t_end`).
- IDs are strings: `mtg_…`, `seg_…`, `alr_…`, `ans_…`, `chat_…`, `fu_…` (prefix plus 12 hex characters).
- The event list is closed: a new event type is a contract change.
- `GET /api/contract/events` returns the `Event` union. It exists so the union appears in the OpenAPI file and TypeScript gets generated for the live stream.

### 4.2 Event envelope

```
Event {
  id: str            # evt_…
  meeting_id: str
  seq: int           # per-meeting, strictly increasing from 1; the live stream resumes by it
  at: datetime       # when published
  type: EventType    # discriminator, one of the literals below
  payload: <one of the payload models, matched to type>
}
```

### 4.3 Event types and payloads

| `type` | Payload model | Fields | Published by |
|---|---|---|---|
| `bot.status` | `BotStatus` | `status: "joining"\|"waiting_room"\|"in_call"\|"left"\|"failed"`, `detail: str\|None`, `recall_bot_id: str\|None` | meeting |
| `transcript.segment` | `TranscriptSegment` | `segment_id`, `speaker_id: str` (Recall participant id as str), `speaker_name: str` (after settings mapping; `"Unknown speaker"` if none), `text: str`, `t_start: float`, `t_end: float`, `source: "recall"\|"replay"\|"manual"` | meeting |
| `wake` | `Wake` | `trigger: "phrase"\|"button"`, `segment_id: str\|None`, `matched_variant: str\|None`, `question: str\|None` | engine (phrase), API (button) |
| `stop` | `Stop` | `trigger: "phrase"\|"button"`, `segment_id: str\|None` | engine / API |
| `mute` | `Mute` | `muted: bool`, `by: "dashboard"` | API |
| `alert` | `Alert` | `alert_id`, `kind: "contradiction"\|"disagreement"\|"uncertainty"`, `topic: str` (≤60 chars, fills "Because you mentioned ___"), `claim: str`, `said_by: list[str]`, `segment_ids: list[str]`, `finding: str`, `evidence: list[Evidence]`, `reasoning: str` (full, unbounded), `confidence: float 0-1`, `gated: bool`, `gate_reason: str\|None`, `delivered_to_chat: bool`, `models_used: list[str]`, `canned: bool` | engine |
| `spoken.answer` | `SpokenAnswer` | `answer_id`, `question: str`, `asked_by: str\|None`, `text: str` (≤60 words), `evidence: list[Evidence]`, `status: "queued"\|"playing"\|"played"\|"stopped"\|"muted"\|"failed"`, `canned: bool` | engine (queued) / meeting (status updates as new events with the same `answer_id`) |
| `chat.post` | `ChatPost` | `chat_id`, `text: str` (≤500, enforced), `reason: "alert"\|"answer"\|"consent"\|"system"`, `ref_id: str\|None`, `status: "pending"\|"sent"\|"suppressed_muted"\|"failed"` | engine (pending) / meeting (result) |
| `follow_up` | `FollowUp` | `follow_up_id`, `text: str`, `owner: str\|None`, `source_alert_id: str\|None`, `status: "outstanding"\|"resolved"` | engine at end; API on toggle |
| `meeting.summary` | `MeetingSummary` | `key_topics: list[str]`, `takeaways: list[str]`, `follow_up_count: int`, `alert_count: int`, `canned: bool` | engine |
| `meeting.ended` | `MeetingEnded` | `reason: "bot_left"\|"call_ended"\|"dashboard"\|"replay_finished"` | meeting / API |

`Evidence { document: str, passage: str (≤400 chars), locator: str|None }`

### 4.4 Records and API shapes

- `MeetingRecord` = `{meeting_id, title, meeting_url, source: "recall"|"replay", recall_bot_id|None, bot_status|None, muted: bool, started_at, ended_at|None, participants: list[Participant], segments: list[TranscriptSegment], alerts: list[Alert], answers: list[SpokenAnswer], chat_posts: list[ChatPost], follow_ups: list[FollowUp], summary: MeetingSummary|None, events_last_seq: int}`. This is what the JSON file holds.
- `Participant` = `{speaker_id, recall_name|None, display_name, role|None}`
- `MeetingListItem` = `{meeting_id, title, started_at, participants: list[str], follow_ups_outstanding: int, follow_ups_resolved: int, alert_count: int}`. The field order is the display order.
- `Settings` = `{bot_name: "Meet AGI", consent_text, speakers: list[SpeakerMapping{match_name, display_name, role|None}], models: {cheap_check, judge, answer, summary}, voice: {provider:"inworld", model, voice_id}, gate: {cheap_threshold, min_confidence, cooldown_seconds, max_alerts_per_meeting}, wake: {variants: list[str], max_word_position: int, question_wait_seconds: int}, stop_variants: list[str], answer_max_words: int, fillers: list[str]}`
- `DocumentInfo` = `{name, size_bytes, chunks: int, added_at}`

**Frozen engine entry point.** It is part of the contract. Its name and signature can't change:

```python
# backend/app/pipeline/entry.py
async def process_segment(segment: TranscriptSegment, ctx: MeetingContext) -> None
```

- `MeetingContext` = `{meeting_id, settings: Settings, bus: EventBus, store: Store, muted: bool, speech_mode: bool}`. It lives in `backend/app/contract/context.py`.
- The engine responds only by publishing events.
- Manual wake, stop button, mute and meeting-end reach the engine through bus subscriptions set up in its own registration module (§5).
- Milestone 0 ships a placeholder:
  - One CANNED `alert` plus its `chat.post` when a sentence claims Q3 revenue was rising.
  - One CANNED `wake`, `spoken.answer` and `chat.post` when a sentence starts with "Hey AGI", or when the manual wake button is pressed.
  - One CANNED `meeting.summary` at meeting end.
  - Everything it emits has `canned: true` or "CANNED" in its text.

### 4.5 Wake and stop detection (part of the contract's behavior, used by the tests)

- **Normalize** a sentence: lowercase, strip punctuation except spaces, collapse spaces.
- **Wake variants** (the `wake.variants` default): `hey agi`, `hey a g i`, `hey aji`, `hey age i`, `hey a gi`, `hey ag i`, `hi agi`, `hey agee`.
- **Positional guard:** the variant must start within the first `max_word_position` = 3 words. Only openers from the set {`ok`, `okay`, `so`, `um`, `uh`, `and`, `alright`} may come before it. It must not follow `say`, `said`, `called` or `word` anywhere earlier in the sentence.
  - "Hey AGI, what was Q3 revenue" fires.
  - "If you say hey AGI it answers" doesn't fire.
  - "The hey AGI thing is cool" doesn't fire.
- **Stop variants:** `agi stop talking`, `a g i stop talking`, `aji stop talking`, `agi stop`, `stop talking agi`. They may appear anywhere in the sentence, but only while an answer is `queued` or `playing`, or within 5 s after one.

### 4.6 REST endpoints (FastAPI)

| Method | Path | Body → Response | Purpose |
|---|---|---|---|
| GET | `/api/health` | → `{ok, offline, dev_mode, placeholders: list[str]}` | Liveness; lists anything still canned |
| GET | `/api/meetings` | → `list[MeetingListItem]` | Sessions list |
| POST | `/api/meetings` | `{meeting_url, title?}` → `MeetingRecord` | Send the bot (501 "not built yet" until the meeting lane fills `rt.launch_bot`) |
| GET | `/api/meetings/{id}` | → `MeetingRecord` | Review screen |
| POST | `/api/meetings/{id}/end` | → `MeetingRecord` | Bot leaves; triggers summary |
| POST | `/api/meetings/{id}/wake` | `{question?}` → `Event` | Manual wake |
| POST | `/api/meetings/{id}/stop` | → `Event` | Stop button |
| POST | `/api/meetings/{id}/mute` | `{muted}` → `Event` | Mute |
| PATCH | `/api/meetings/{id}/follow-ups/{fid}` | `{status}` → `FollowUp` | Toggle |
| GET | `/api/meetings/{id}/events?since=` | → SSE stream of `Event` | Live view |
| GET/PUT | `/api/settings` | `Settings` | Settings screen |
| GET | `/api/documents` | → `list[DocumentInfo]` | |
| POST | `/api/documents` | multipart file → `DocumentInfo` | Accepts `.md .txt .pdf` |
| DELETE | `/api/documents/{name}` | → `{ok}` | |
| GET | `/api/contract/events` | → `Event` | Schema exposure only |
| POST | `/api/dev/meetings` | `{title}` → `MeetingRecord` (with a `replay-…` bot id) | Creates a fake-meeting record; `scripts/replay.py` then posts Recall-shaped webhooks to the receiver. 404 unless `DEV_MODE=1` |
| POST | `/webhooks/recall/{token}` | Recall payload → 200 `{ok:true}` | Receiver; the route calls `rt.recall_webhook` (meeting lane) |

---

## 5. Lane map

| Lane | Owns (may edit) | Must not edit | Registers itself in | Proves itself with |
|---|---|---|---|---|
| **engine** (`lane-engine`) | `backend/app/pipeline/`, `backend/app/knowledge/`, `backend/app/providers/llm/`, their tests under `backend/tests/engine/` | everything else, esp. `main.py`, `api.py`, `settings.py`, `contract/`, `core/` | `backend/app/pipeline/register.py` → `def register(rt: Runtime) -> None` (subscribe on `rt.bus`) | `python -m pytest backend/tests/engine` including `test_fake_meeting_produces_exactly_one_alert_one_answer_one_summary` |
| **meeting** (`lane-meeting`) | `backend/app/integrations/`, `backend/app/speech/`, `backend/app/providers/voice/`, tests under `backend/tests/meeting/` | same | `backend/app/integrations/register.py` → `def register(rt: Runtime) -> None` (fills slots `rt.recall_webhook`, `rt.launch_bot`, `rt.end_bot`; subscribes on `rt.bus`) | `python -m pytest backend/tests/meeting` against `fixtures/recall/` and the fake Recall |
| **screens** (`lane-screens`) | `frontend/` except `frontend/src/contract/` | backend, generated client | `frontend/src/app/…` routes | `npm --prefix frontend run verify` (typecheck + tests + build) and 3 URLs on the replay |
| **integrate** (orchestrator, on `main`) | `backend/app/main.py`, `backend/app/api.py`, `backend/app/settings.py`, `backend/app/contract/`, `backend/app/core/`, `backend/app/dev/`, `contract/`, `frontend/src/contract/`, `scripts/`, `fixtures/`, root files, DESIGN.md, STATE.md | lane internals (except to merge) | calls each lane's `register` | `python scripts/verify.py` |

Lanes may read anything. The milestone-0 skeleton creates each `register.py` with a placeholder body, so every lane only fills in a file it already owns.

`Runtime` (`backend/app/core/runtime.py`) carries `bus`, `store`, `config`, `get_settings()` and the slots. Every REST route lives in `api.py` and calls a slot. A lane never adds a route, so the API description only changes on `main`. `rt.placeholders` names whatever is still canned; `/api/health` shows the list.

---

## 6. Milestone plan

| # | Milestone | Who | Done when (Ray can check) |
|---|---|---|---|
| 0 | **Contract + fake meeting.** Backend skeleton; contract models; bus; store (JSON files); SSE; placeholder engine; no-op registers; `fixtures/fake_meeting/script.json` (a ~3-minute scripted meeting with 4 named speakers containing exactly one fact dispute about a number in the sample docs, one "Hey AGI" question, one mention of the wake word that must NOT fire, and nothing else alert-worthy); `fixtures/knowledge_sample/` (labeled SAMPLE: "board deck" saying Q3 revenue fell 4%, plus a pricing note); `fixtures/recall/` synthesized from Recall's documented schema (labeled `synthesized_from_docs`); replay that posts the script through the real receiver path; canned LLM and voice providers; OpenAPI export + generated TS client; `scripts/verify.py`; README "Run it" | orchestrator | `python scripts/verify.py` prints `ALL CHECKS PASSED`; `/api/health` lists placeholders |
| 1 | **Three lanes in parallel** (engine, meeting, screens) in worktrees, each against the fake meeting only | 3 sub-agents | each lane's command green on its branch; report with assumptions |
| 2 | **Integrate.** Merge lanes into `main`, wire registers, apply recorded contract requests, regenerate client, end-to-end replay test: 1 alert in chat, 1 spoken answer, 1 summary, 0 extra posts, wake-word mention ignored | orchestrator | `python scripts/verify.py`; the replay shown live at `http://localhost:3000` |
| 3 | **Gate.** Hostile review (`reviewer`), fix every "Not ready", record conditions in STATE.md | orchestrator + reviewer | review report committed under `reviews/`; verify green |
| 4 | **Live dry run.** Real Recall bot in Ray's own Meet, real keys, ngrok up. Measure: wake-to-filler, wake-to-answer, alert latency, stop behavior (does DELETE cut a playing clip?), cost of a 15-minute run | orchestrator + Ray (host admits bot, speaks the script) | a filled latency/cost table in STATE.md; the recorded payloads replace the synthesized fixtures |
| 5 | **Demo hardening.** Fix live findings, rehearse twice, write the demo run sheet with fallbacks | orchestrator | two clean rehearsals logged |

Latency targets for milestone 4 (judged, not promised):

- Wake phrase to filler audible: ≤3 s.
- Wake to answer audible: ≤10 s.
- Disputed sentence to chat alert: ≤8 s.

---

## 7. Risks

| # | Risk | Likelihood | Impact | Mitigation / fallback |
|---|---|---|---|---|
| R1 | **No Recall.ai account yet.** Recall rejected sign-up with Gmail on 24 Sep. `RECALL_API_KEY` is empty; only an Attendee key exists | Certain today | Blocks milestone 4 only | Ray signs up with a non-Gmail address before milestone 4. Fallback: Attendee (open-source, similar API) behind the same `integrations` boundary; costs about half a day in the meeting lane |
| R2 | Fixtures synthesized from docs, not recorded, so field names or envelope may differ live | High | Receiver breaks on first live call | The receiver parses defensively and logs unknown shapes; milestone 4 records real payloads and replaces fixtures |
| R3 | `DELETE output_audio` may not cut a clip already playing | Medium | "AGI, stop talking" only stops the *next* clip | Answers ≤60 words (~20 s); queue cleared either way; fallback is Output Media (webpage audio, can be paused) |
| R4 | Guest bot needs host admit; org-only or sign-in-required meetings block it | Medium | Bot never joins | Demo meeting hosted from Ray's personal Google account, "anyone with link can ask to join"; host watches for the admit popup; `bot.status = waiting_room` shown in the dashboard |
| R5 | Mainland-China network: Gemini blocks CN IPs; Anthropic/Recall/Inworld reachability; ngrok tunnel stability | High in Beijing | Calls fail mid-meeting | VPN on for the whole demo; timeouts of 8 s on every vendor call; on cheap-check failure skip alerts rather than crash; on voice failure post the answer to chat only |
| R6 | Laptop sleep or network drops (seen repeatedly on 24 Sep) | Medium | Webhooks fail; Recall marks the endpoint failed after 60 s | Disable sleep for the demo; the receiver is idempotent; dashboard shows endpoint health |
| R7 | Wake word mis-transcribed ("hey edgy", "hey AJ") | Medium | Speech mode doesn't trigger | Variants list is a setting; manual wake button; milestone 4 logs the actual transcriptions |
| R8 | Alert spam or false positives | Medium | Room ignores or mutes the bot | Gate (confidence 0.75, 90 s cooldown, cap 8); gated verdicts visible in the dashboard only |
| R9 | 500-character chat limit exceeded | Low | Recall rejects the post | Enforced in the `ChatPost` model; truncate at a word boundary with "…" |
| R10 | Adaptive audio (room devices) merges speakers | Low for demo | Wrong names | Everyone joins from own laptop; documented limitation |
| R11 | Hooks (`.claude/settings.json`) untested on Windows | Medium | Sessions start without STATE.md, or regressions slip | Verified on Linux; first Windows session checks the banner |
| R12 | Cost overrun | Low | Budget | Recall first 5 h free; Haiku only on flagged sentences; Flash-Lite on every sentence (fractions of a cent) |

---

## 8. Decisions taken on questions the brief left open

Each is final for the lanes. Ray can override any of them on `main`.

1. **Webhook authentication.** Recall's documented method is a header signature with a workspace secret (`whsec_…`), not a URL token. The receiver requires the path token `RECALL_WEBHOOK_TOKEN` (constant-time compare). It *also* verifies the signature when `RECALL_WORKSPACE_SECRET` is set in `.env`.
2. **Documents.** Stored in `knowledge/` (git-ignored except `knowledge/README.md`). Accepts `.md`, `.txt`, `.pdf` (via `pypdf`). Chunks are ~120 words with 20 words of overlap. Search is BM25 over the chunks, no vector database; the top 3 go to the cheap check and the top 5 to answers.
3. **Follow-ups.** At meeting end the summary step extracts action items and open questions (with owner if named). Each non-gated alert whose dispute was not settled in the transcript becomes a follow-up with `source_alert_id`. All start `outstanding`.
4. **Mute** silences audio and chat only; detection and the dashboard continue. Consent notice default: `Meet AGI is listening to help with facts from our documents. It may post short notes here. Say "Hey AGI" to ask it something, or "AGI, stop talking" to stop it.`
5. **One meeting at a time.** Starting a second while one is live returns HTTP 409.
6. **Fillers** (voice lines, cached as MP3 at startup, with canned fallback): "Sure, let me look that up." / "One moment, checking the documents." / "Good question, give me a second."
7. **Speaker names:** the Settings mapping wins, then Recall's `participant.name`, then "Unknown speaker".
8. **No authentication on the dashboard or API** for the demo (localhost only). Only the webhook path is public, and it's protected by decision 1.
9. **Answer when documents are silent:** the bot says so ("I couldn't find that in our documents") rather than answering from general knowledge.

---

## 9. Cut list (in the order things get cut)

1. Clarifying questions (bot asks back when a question is ambiguous)
2. Cross-meeting memory
3. Sentence-by-sentence streaming speech (first thing to add after the demo)

**Never cut:** live hearing (transcript with speakers), speech mode (wake → filler → spoken answer → stop).

Already out of scope: voice-print identification, Postgres, multi-meeting concurrency, dashboard login.

---

## 10. When unsure

Sub-agents can't ask anyone. When this document, the contract and CLAUDE.md don't decide something:

1. **Choose the simplest option that keeps the fake meeting's expected result true.** That result is exactly one alert, one spoken answer, one summary, nothing else, and the wake-word mention ignored.
2. **Stay inside your lane's folders.** If the simplest option needs a contract or shared-file change, don't make it. Build against the contract as it is and record the change you needed.
3. **Record it** in your final report and in STATE.md under "Assumptions recorded by sub-agents", as one line: `lane · assumption · why · how to undo`.
4. **Keep going.** Never stop to wait. Never call a live vendor from a test. Never print a key.
5. **When two rules conflict:** safety (CLAUDE.md rule 9) first, then the contract, then this document, then your brief.
6. **Anything canned says so** in its own output (CLAUDE.md rule 6).
