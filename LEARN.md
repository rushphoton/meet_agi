# Learning from Meet AGI

A guide for someone who has been sent this repository and wants to understand how it works, run it, and take it apart. If you only want to run it, the first section is enough.

**What it is:** a bot that joins a Google Meet and listens to the live transcript.
- It answers out loud when someone says "Hey AGI", from your own documents first.
- It posts a short note in the meeting chat when the room contradicts itself or doubts a fact. The full reasoning shows on a small web dashboard.
- It writes a summary with follow-ups when the call ends.

It was built in a few days by AI coding agents, directed by a non-programmer, using the process described in "How it was built" below. That process is as much the point of the repo as the code.

---

## 1. Run it in 5 minutes, with no accounts and no keys

You need Python 3.11 or newer and Node.js 20 or newer. Windows, macOS and Linux all work. Commands below say `python`; on macOS and Linux use `python3`.

```
git clone https://github.com/rushphoton/meet_agi.git
cd meet_agi
python scripts/verify.py
```

- `verify.py` creates a private Python environment (`.venv`) the first time. It then runs every backend test, checks the data contract, starts the server once, and plays a scripted fake meeting through the real code.
- It should end with `REPLAY OK` and `ALL CHECKS PASSED`.
- With no `.env` file, every AI and voice call uses a **canned** stand-in that labels its own output CANNED. Nothing is sent to any vendor.

**To watch it work, use three terminals:**
1. `python scripts/serve.py` starts the backend on http://localhost:8000.
2. `npm --prefix frontend install`, then `npm --prefix frontend run build`, then `npm --prefix frontend run start` starts the dashboard on http://localhost:3000.
3. Open http://localhost:3000/live, then run `python scripts/replay.py --speed 2`. Lines appear, one alert fires with its reasoning, "Hey AGI" gets an answer, and a summary appears at the end.

**For a real meeting:**
1. Copy `.env.example` to `.env` and fill in the keys (see section 5).
2. Run `python scripts/go.py`. It starts the backend, an ngrok tunnel and the dashboard, then checks each one.
3. Paste a Meet link into the dashboard and admit the bot when it asks to join.

---

## 2. The architecture in one picture

```
 Google Meet ──(meeting-bot vendor: Attendee or Recall.ai)──► webhook: one finished caption line at a time
                                                              │
                     ngrok tunnel (only POST /webhooks/recall/<secret token> may pass)
                                                              ▼
 ┌──────────────────────────── backend (Python, FastAPI) ─────────────────────────────┐
 │ integrations/receiver.py  check token → reply 200 at once → drop repeats           │
 │ speech/assembler.py       join fragments into sentences with speaker names          │
 │        │  publishes transcript.segment                                              │
 │        ▼                                                                            │
 │ core/bus.py  ── one in-process event bus; every event is also saved and streamed ──│
 │        │                                                                            │
 │ pipeline/engine.py        per sentence: stop? wake ("Hey AGI")? dispute worth a look?│
 │   pipeline/phrases.py       wake/stop matching (exact, then "sounds like", confirmed) │
 │   knowledge/index.py        BM25 keyword search over the knowledge/ folder          │
 │   providers/llm/            Gemini/Claude (real.py), canned stand-in (canned.py)    │
 │   pipeline/gate.py          confidence, cooldown, cap: does an alert reach chat?    │
 │        │  publishes alert / wake / spoken.answer / chat.post / stop / summary       │
 │        ▼                                                                            │
 │ speech/audio_out.py       speaks answers sentence by sentence; stop drops the rest  │
 │ providers/voice/          Inworld text-to-speech (MP3), canned clip for tests       │
 │ integrations/chat.py      posts to Meet chat (500-character limit)                  │
 │ core/store.py             one JSON file per meeting in data/meetings/              │
 │ api.py                    REST + live event stream (Server-Sent Events)             │
 └─────────────────────────────────────────────────────────────────────────────────────┘
                                                              │
                                    frontend/ (Next.js): sessions, live view, review, settings
```

The one idea that holds it together: **every part talks only through events on the bus** (`core/bus.py`), and every event shape is defined once, in `backend/app/contract/`. The dashboard's TypeScript types are generated from those definitions (`contract/openapi.json` → `frontend/src/contract/schema.d.ts`), never written by hand. `scripts/verify.py` fails if they drift apart.

---

## 3. Follow one sentence through the code

Suppose someone says: "Hey AGI, what was Q3 revenue according to the board deck?"

1. **The vendor sends the caption to the webhook.**
   - `integrations/receiver.py` checks the secret token in the URL and replies 200 straight away, because vendors resend anything slow.
   - It drops duplicates and hands the words to `speech/assembler.py`.
2. **The assembler builds a sentence and publishes `transcript.segment`.** It closes the sentence on a pause, at 40 words, or when the speaker changes. The sentence then enters the engine through the one frozen door, `pipeline/entry.py: process_segment()`.
3. **The engine checks, in order:**
   - Is this a stop phrase while the bot is talking?
   - Is it a wake? `phrases.py` handles exact spellings, plus caption mistakes like "Hey GI" or "Hey giant", which are confirmed by one quick model call.
   - Otherwise, does it need a dispute check?
4. **For a wake, the engine publishes `wake`.**
   - The meeting side hears that and plays a filler ("Sure, let me look that up").
   - The engine searches `knowledge/`, asks the answer model for 2–5 spoken sentences, and publishes `spoken.answer` plus a `chat.post` that starts "Because you asked:".
5. **`speech/audio_out.py` speaks the answer.**
   - It splits the answer into sentences, turns each into an MP3 and plays them one at a time.
   - Saying "stop talking" publishes `stop`, which drops the sentences not yet played.
6. **The rest happens automatically.**
   - Every event is saved to `data/meetings/<id>.json` and streamed to the dashboard.
   - At the end of the meeting the engine writes the summary and follow-ups.

Disputes work the same way:
1. A cheap, fast model (Gemini Flash-Lite) looks at every sentence with the last 8 lines and the top document passages.
2. Only sentences it flags go to the judge model.
3. `gate.py` then decides whether the alert reaches the meeting chat or stays on the dashboard only.

---

## 4. Where to look for what

| You want to understand… | Read |
|---|---|
| Why each decision was made | `DESIGN.md` (sections 3–4: architecture and data contract; section 8: decisions; section 10: the "when unsure" rule; the dated notes near the end record the changes after live testing) |
| Every data shape | `backend/app/contract/events.py`, `records.py` |
| How the parts are wired | `backend/app/main.py`, `core/runtime.py`, each `*/register.py` |
| Wake/stop logic and its edge cases | `backend/app/pipeline/phrases.py`, `backend/tests/engine/test_engine_live_*.py` (built from real captions) |
| The prompts sent to the models | `backend/app/providers/llm/real.py` |
| Talking to the meeting-bot vendors | `integrations/attendee_client.py`, `recall_client.py`, `bot.py` (the top of each file lists what was checked in the vendor's docs and what was assumed) |
| Security at the public edge | the tunnel guard in `backend/app/main.py`, token check in `integrations/receiver.py` |
| The dashboard | `frontend/src/app/` (pages), `frontend/src/lib/meetingState.ts` (folds the event stream into screen state) |
| The fake meeting | `fixtures/fake_meeting/script.json`, `scripts/replay.py` |
| What went wrong in real use and how it was fixed | `STATE.md`, `reviews/review-B.md`, `reviews/gate-v0.1.md` |

Every module starts with a plain-English comment saying **why it exists and what failure it prevents**. Reading only those comments is a fast first pass.

Tests are organized by area (`backend/tests/contract`, `engine`, `meeting`, `integrate`). Failure cases come first, and bug-fix tests are named after the symptom that was seen. For example: `test_bot_hears_its_own_voice_in_meet_captions_and_feeds_it_back_to_the_engine`.

---

## 5. Running it live: what you need

| Item | Used for | Without it |
|---|---|---|
| `GEMINI_API_KEY` (Google AI Studio) | every AI job by default | the canned stand-in is used |
| `ANTHROPIC_API_KEY` (optional) | Claude for any job, chosen in Settings | Gemini does everything |
| `INWORLD_API_KEY` + `INWORLD_VOICE_ID` | the bot's voice | answers are posted to chat only |
| `BOT_PROVIDER` + `ATTENDEE_API_KEY` or `RECALL_API_KEY` | a bot that joins Google Meet | only the fake meeting works |
| ngrok, with a static domain in `PUBLIC_BASE_URL` | lets the vendor reach your laptop | the bot joins but hears nothing |
| `RECALL_WEBHOOK_TOKEN` (any random 64 hex characters) | the secret in the webhook address | webhooks are rejected |

`python scripts/check_keys.py` tests each key against its vendor (Claude with one real 1-token message) and prints only OK or FAIL. It never prints a key.

---

## 6. How it was built (the method)

The code was written by AI coding agents (Claude). A human set the rules, made decisions and tested it in real meetings. The files that steered the agents are part of the repo:

1. **`CLAUDE.md`, the house rules.** Every piece of work ends with one command the human can run. Every module explains why it exists. Data shapes are defined once and generated everywhere else. Tests cover failure paths first. Secrets never leave `.env`.
2. **`DESIGN.md`, written and frozen before any code.** It covers the vendor capabilities checked against their docs, the data contract, a "lane" map saying which agent owns which folders, the milestones, the risks, and a "when unsure" rule so agents could decide without asking.
3. **Milestone 0.** The contract, the event bus, the store, placeholder parts that say CANNED, and the scripted fake meeting, so everything could be tested without a real call.
4. **Three parallel agents ("lanes"), each in its own git worktree.**
   - **engine:** detection, answers and alerts.
   - **meeting:** the vendors, the receiver and the voice.
   - **screens:** the dashboard.

   Their definitions are in `.claude/agents/`. Each lane proved its own part with tests, then an "integrate" step merged them and checked the whole system end to end.
5. **Hostile reviews.** A read-only reviewer agent tried to make it fail in front of an audience (`reviews/`). Every serious finding was fixed with a test.
6. **Live tests in Google Meet.** The real caption mistakes ("Hey GI", "Hey giant"), the bot hearing its own voice, and stop timing were all found this way and turned into tests.

`STATE.md` is the running log of where things stand. Each working session overwrote it before finishing.

---

## 7. Known limits

- **One meeting at a time.** It runs on one laptop; the dashboard has no login (it is local only, and the tunnel guard blocks everything except the webhook).
- **No speaker recognition.** Names come from the meeting platform.
- **Stopping with Attendee:** "stop talking" can't cut a sentence already playing, because Attendee has no stop-audio call. The current sentence finishes and the rest is dropped.
- **Meet's captions are the transcript.** Mistakes in them, especially with the wake word, are handled by the sounds-like layer, but not perfectly.
- **Documents:** search is keyword-based (BM25), not semantic. It suits short documents.
- **Sample data:** `knowledge/SAMPLE_board_deck_q3.md` is invented sample data for the fake meeting.
