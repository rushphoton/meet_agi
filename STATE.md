# STATE

## Where we are

- **Branch `integrate`** holds milestones 1–3. It has the three lanes merged and integrated, review B done, and every P0/P1 fixed.
- **Branch `main` = `integrate`** (fast-forwarded 27 Sep after the live Meet test passed).
- **The fake meeting works end to end, with real code in every part:**
  - it hears the transcript;
  - it flags the one planted dispute in chat with its reasoning;
  - it answers "Hey AGI" out loud and in chat;
  - it ignores the other mention of the wake word;
  - it writes a summary and follow-ups;
  - the dashboard shows all of it live.
- **Proof:**
  - `python scripts/verify.py` → `ALL CHECKS PASSED`. That covers 227 backend tests, the contract drift check, the serve smoke test and `REPLAY OK`.
  - `npm --prefix frontend run verify` → 47 tests passed, plus the type check and the build.
  - A headless browser drove all three screens during a replay: the alert arrived live, the summary appeared, both follow-ups toggled and stayed toggled, the send-bot form returned the expected 503, and the health poll ran for 60 s with no errors.
- **With real models:** since 25 Sep, every AI job (cheap check, judge, answer, summary) runs on `gemini-3.5-flash-lite` by default. Ray decided to wait on Anthropic credit (the account has none). The replay with real keys and these defaults gives `REPLAY OK`, with real Gemini text in the alert, the answer and the summary.
- **Cost:** about US$0.19 per 30-minute meeting on Gemini's paid tier (estimate), or free on the free tier. See reviews/MeetAGI_cheap_model_alternatives.pdf.
- **27 Sep: first live Google Meet test passed** (see the section below).
- **Real meetings (25 Sep):** the bot can now join a real Google Meet through **Attendee**, using the key already in `.env` (`BOT_PROVIDER=attendee`). This is DESIGN risk R1's fallback, built by the meeting lane. It is tested against a fake Attendee; nothing has joined a real call yet.
  - Attendee can't cut a clip that is already playing: "AGI, stop talking" drops queued answers, but the one playing finishes (up to about 20 s).
  - Recall stays the design's vendor for later.
- **One command for a real meeting:** `python scripts/go.py`. It starts the backend with real AI, the ngrok tunnel and the dashboard, checks each one, opens http://localhost:3000, and prints a plain fix when a step fails. Ctrl+C stops all three.
- All of this ran in the cloud copy (Linux). Nothing has been run on Windows yet.

## First live test: real Google Meet via Attendee (27 Sep 2026, HKT afternoon)

Result: **it works end to end in a real call.** Ray hosted the Meet, admitted the bot, spoke test lines, and the bot heard, alerted and answered out loud. Measured from the saved meeting file (`data/meetings/mtg_e0cc6b011608.json`):

| Measure | Target (DESIGN §6) | Live result | Verdict |
|---|---|---|---|
| Send → bot in the call (includes the host admitting it) | n/a | 53 s | depends on the host clicking Admit |
| Disputed sentence → alert on the dashboard and in Meet chat | ≤ 8 s | 3 s (alert), 5 s (chat "sent") | pass |
| "Hey AGI, what was Q3 revenue…" caption → answer queued / chat sent / voice playing | ≤ 10 s | 3 s / 5 s / 7 s | pass |
| Answer content | from the documents | "Q3 revenue was 41.2 million dollars", citing the board deck | pass |
| Transcript lines with the right speaker name | every line | yes ("Ray Wan") | pass |

What went wrong, and what was done:

1. **Meet captioned "Hey AGI" as "Hey GI" twice, and it didn't fire.** Added `hey gi` and `hey g i` to the wake variants (contract default on main), plus Ray's saved `data/settings.json`. Test: `test_hey_agi_captioned_by_meet_as_hey_gi_did_not_wake_the_bot`.
2. **The bot heard its own voice.** Captions contained "Meet AGI: According to the board deck…", and those lines went to the engine, so it could have flagged or answered itself. The bot's own captions are now dropped (its speech is still shown under Spoken answers). Test: `test_bot_hears_its_own_voice_in_meet_captions_and_feeds_it_back_to_the_engine`.
3. **After the test, the bot stayed in the Meet**, still recording and billing, and the meeting had no summary: the window was closed with Ctrl+C without pressing End meeting. That bot was told to leave (Attendee now shows it as `ended`). `go.py` now ends any open meeting on Ctrl+C (the bot leaves and the summary is written) and on start-up for anything a previous run left open. Test: `test_ctrl_c_without_pressing_end_meeting_left_the_bot_in_the_call`.
4. **Not a bug, noted:** a bare "Hey AGI!" waits 8 s for the question. The question came 24 s later (after the filler line), so the bot said "Sorry, I didn't catch a question". Say it in one breath: "Hey AGI, what was…". A Wake pressed before the bot was admitted failed to post (the bot wasn't in the call yet).
5. **The meeting is recorded with source "recall"** although it came through Attendee (known label issue, see assumptions).

## Round 2: Ray's feedback after the live test (27 Sep 2026)

| # | Ray said | Root cause | Fix (lane) | Proof |
|---|---|---|---|---|
| 1 | "AGI" gets captioned as giant, GI, GI Joe, so the bot misses it | only exact spellings were matched | More exact spellings, plus a sounds-like layer: "hey/hi/ok" followed by an AGI-like word is confirmed by one quick Gemini check (engine) | tests with the real caption lines; live: "Hey Aggie, what's a typical SaaS gross margin?" woke and was answered |
| 2 | "Stop talking" should work without saying AGI, from anyone | stop phrases all contained "AGI" | "stop talking", "be quiet", "that's enough", "stop" and similar, from anyone, but only while the bot is answering or just after; bare "stop" must lead the sentence, so "Stop the recording please" doesn't count (contract defaults + engine) | tests; also fixed a real bug where "Fair enough, my mistake" had counted as a stop in the replay |
| 3 | The bot should chime in when ONE person contradicts or doubts themselves | prompts looked for disagreement between people | New cheap-check and judge prompts list self-correction, hedging and right/wrong flip-flops; when the documents don't settle it, the alert says so (engine) | test with Ray's sequence; live Gemini: one alert, confidence 0.95, 3 s |
| 4 | "Not very reactive" to Hey AGI questions | answers were limited to the documents; question wait was 8 s | Answers the question asked in 2–5 sentences; if the documents are silent it answers from general knowledge and says so first; question wait 15 s; a re-asked question within 35 s is answered (contract + engine) | tests; live answers in 0.7–1.4 s |
| 5 | Longer answers that "stop talking" can cut part-way | one clip per answer; Attendee can't cut a playing clip | Up to 120 words; spoken sentence by sentence, and stop drops the remaining sentences (with Attendee the current sentence finishes) (contract + meeting) | tests: stop after sentence 2 means 3–5 never sent; first clip starts ~1 s after the answer instead of ~5 s |

- Also: "hey aj"/"hey ajay" are not exact wake words, because a colleague named Ajay would wake the bot; the sounds-like check still catches them.
- DESIGN.md has a note listing these overrides (before §9).
- Proof: `python scripts/verify.py` → ALL CHECKS PASSED (312 tests, REPLAY OK). Dashboard 47 tests pass. Replay with real Gemini keys: REPLAY OK with real text.
- New assumptions (engine r2, meeting r2) are in the lane reports summarized above. The main ones:
  - a general-knowledge answer replaces DESIGN §8 decision 9;
  - the question wait counts from the wake, not from the end of the filler;
  - "words spoken before stop" is only in the log (a contract field `spoken_text` was requested but not added yet).
- Not tested live yet: the sounds-like wake, stop mid-answer on Attendee, Inworld's voice across many short clips (pauses or pitch changes between sentences).

## Needs me

1. **Before any real (non-fake) meeting: turn on Google billing** for the Gemini key at https://aistudio.google.com. On the free tier, Google says content is "used to improve our products".
   - This is optional for the fake meeting.
   - Anthropic credit is no longer needed. It is optional later: `python scripts/check_keys.py` shows `ANTHROPIC_API_KEY: FAIL - ... no credit` until you top up, and that line can be ignored while every job runs on Gemini.
2. **Recall.ai account: no longer needed to test.** Attendee works now. Recall stays optional, for later; it can cut playing audio. If you sign up (non-Gmail): When it exists:
   - put `RECALL_API_KEY` and `RECALL_WORKSPACE_SECRET` (whsec_…) into `.env` yourself;
   - change `BOT_PROVIDER=attendee` to `recall` in `.env` (the code is built for Recall, per DESIGN);
   - in Recall's dashboard, point the status webhook at `PUBLIC_BASE_URL/webhooks/recall/<your RECALL_WEBHOOK_TOKEN>`. This is optional, because the backend also checks the bot's status every 3 s.
3. **Push**, if the push from this session failed. Run in PowerShell:
   ```
   git -C "C:\Users\YBBJ100572\Desktop\AI\Meet AGI" push origin main integrate
   ```
4. **Done 27 Sep:** `main` now equals `integrate` (fast-forward after the live test passed). Nothing to do.

## Test in a real Google Meet (Attendee)

1. VPN on. In PowerShell: `cd "C:\Users\YBBJ100572\Desktop\AI\Meet AGI"`, then `python scripts/go.py`. Wait for `READY`; Chrome opens http://localhost:3000.
2. In another Chrome tab, while signed in to your **personal** Google account, open https://meet.google.com, choose **New meeting → Start an instant meeting**, and copy the link from the address bar.
3. On the dashboard, paste the link into **Send Meet AGI to a meeting** and click Send. The live view opens and shows "bot: joining".
4. In Meet, within about 30 s an **"Ask to join"** prompt for "Meet AGI" appears. Click **Admit**. Attendee removes the bot if it waits 15 minutes.
5. Turn on your mic and speak, for example "Our Q3 revenue was rising, up three percent." Then say "Hey AGI, what was Q3 revenue according to the board deck?"
6. Watch the dashboard (transcript, alert, answer) and the Meet chat, and listen for the bot's voice.
7. End with **End meeting** on the dashboard (the bot leaves and the summary is written), then press Ctrl+C in the PowerShell window.

## Run the product on the fake meeting (two commands, two PowerShell windows in the Meet AGI folder)

Window 1 starts the backend with the real models (Gemini). If you want canned AI instead, with no vendor calls, put `$env:OFFLINE=1; ` in front of the command.
```
python scripts/serve.py
```
Window 2 builds and starts the dashboard, then runs the meeting. The first time only, run `npm --prefix frontend install` first. Open http://localhost:3000/live before the replay starts.
```
npm --prefix frontend run build; Start-Process npm -ArgumentList '--prefix','frontend','run','start'; Start-Sleep 5; python scripts/replay.py --speed 2
```
Screens:
- sessions: http://localhost:3000
- live: http://localhost:3000/live
- review: the Review link on each session
- settings: http://localhost:3000/settings

The replay ends with `REPLAY OK`.

## What each lane built

| Lane | Built | Tests | Branch (merged) |
|---|---|---|---|
| engine | "Hey AGI" and stop-phrase detection with the positional guard. Manual wake. Question capture. BM25 search over `knowledge/`. Spoken answers of 60 words or fewer ("Because you asked:"). Gemini cheap check → Claude judge → gate (confidence, cooldown, cap). Chat alerts ("Because you mentioned ___:", 500 characters or fewer) with full reasoning. Summary and follow-ups at the end. Any job can run on Gemini from Settings. Health warnings for failing vendors. Near-miss wake phrases are logged. The dispute-check backlog is skipped when it grows. | 95 | `worktree-agent-ad67231307852fb42` |
| meeting | Recall bot lifecycle: create as "Meet AGI", status polling every 3 s, leave, end-all. Webhook receiver: finalized lines only, constant-time token, answers fast then works, deduplicates, builds sentences on pause, at 40 words or on a change of speaker. One-clip-at-a-time audio queue that respects mute and stop. Inworld voice with a canned clip that says it is canned, used in replay/OFFLINE only; in a real call a voice failure means the answer goes to chat only. Filler bank. Chat poster (500 characters or fewer, cut at a word). Subscribed on the bus to spoken.answer, stop, wake, mute, meeting.ended (audio) and chat.post (chat). | 66 | `worktree-agent-afccc9fb44059fb59` |
| screens | Next.js dashboard with four screens: sessions (with a "Send Meet AGI" form), live (transcript, alerts with reasoning, answers, chat, wake/stop/mute/end, red "no transcript for N s" notice), review (summary, follow-up toggles, alerts, transcript) and settings. Folds the live stream by seq and resumes with `?since`. Relays the stream so there is no 15 s stall. The health banner shows placeholders in yellow and warnings in red. | 47 | `worktree-agent-a4bb159774c8675df` |

The integrate step confirmed that the meeting lane's queue and chat poster are subscribed on `rt.bus` to the engine's `spoken.answer`, `chat.post` and `stop` events (`backend/app/integrations/register.py`, `subscriptions()`, covered by a test).

## Merges and conflicts

- **Text conflicts:** none. Each lane changed only files inside its own folders.
- **Generated contract:** no lane edited it by hand. It is byte-identical to `main` after every merge.
- **Behavior clash found after merging:** the engine's fake-meeting test and `scripts/replay.py` counted *events*. The meeting lane now publishes progress as new events with the same id (answer queued → playing → played; chat pending → sent), as DESIGN §4.3 says, so the counts went up.
  - Engine test fix: the engine lane (the folder owner) changed its test to count distinct ids and to check the answer ends "played" and both posts end "sent".
  - Replay fix: integrate changed `replay.py` to count distinct answers and alerts that passed the gate.
- **Old test retired:** the milestone-0 test "sending a real bot says not built yet" now expects the meeting lane's 503 naming `RECALL_API_KEY`.

## Contract changes

- **One change, made on `main` (commit d88d9a8), then regenerated:** `/api/health` now also returns `warnings` (vendors failing right now) and `last_webhook_at`, and `Runtime` gained `warnings` and `last_webhook_at`.
- **Why:** review B found health stayed green while every Claude call failed. No lane requested a contract change.
- **After the change:** all three lanes merged it into their worktrees and adapted.

## What the reviewer found and what was fixed (reviews/review-B.md)

Audience: "a live demo in front of an audience". Verdict before the fixes: not ready. Security and observability were rated Not ready.

| # | Finding | P | Owner | Fixed |
|---|---|---|---|---|
| 1 | Every Claude call fails (no credit); the billing message was shown on the review screen; the key check was a false green | P0 | engine + integrate | Code fixed: jobs can move to Gemini from Settings; vendor text is kept out of the summary; red health warnings; `check_keys.py` makes a real call. **Credit itself needs Ray.** |
| 2 | Bot status never updates; the meeting never ends by itself | P1 | meeting | Yes: status polled every 3 s, deduplicated against webhooks, meeting ends on call_ended/done/fatal |
| 3 | No way to send the bot from the dashboard | P1 | screens | Yes: send form on the sessions page |
| 4 | A stale "live" meeting blocks the bot with 409 | P1 | integrate | Yes: stale fake meetings are closed at startup, the 409 names the meeting, the dashboard links to End |
| 5 | Whole API public through ngrok | P0 | integrate | Yes: tunnel guard. Through the tunnel, only `POST /webhooks/recall/…` is allowed; everything else gets 404 (tested) |
| 6 | Voice outage plays the "this is canned" clip to the room | P1 | meeting | Yes: in a real call the answer goes to chat only, plus a warning |
| 7 | Wake phrase mis-transcribed | P1 | engine | Partly: near-misses are logged. The real spellings need a rehearsal (milestone 4), then get added in Settings |
| 8 | Nothing shows whether transcripts are still arriving | P1 | meeting + screens + integrate | Yes: `last_webhook_at`, red "no transcript for N s", vendor warnings |
| 9 | A Windows file lock during save silently drops chat/voice/summary | P1 | integrate | Yes: 5 retries, and a failed save no longer stops subscribers (tested). Untested on real Windows |
| 10 | Dispute-check backlog grows without limit | P2 | engine | Yes: skips to the newest sentence |
| P2 | Chat/audio after the meeting ended; replay ending a real meeting; no panic button | P2 | meeting + integrate | Yes: nothing is delivered after the end; replay refuses to end a real meeting (409); `scripts/end_all_bots.py` |

- **Also fixed at integrate:** an intermittent 500 on the live screen. uvicorn's 5 s keep-alive raced the dashboard's 5 s health poll. It is now 65 s; a 60 s soak test gave zero errors.
- **Not fixed (P2):**
  - The Recall signature is only logged, not enforced, because the receiver slot gets parsed JSON, not raw bytes. The path token is enforced.
  - The whole meeting file is rewritten on every event.
  - `knowledge/SAMPLE_board_deck_q3.md` sits beside the real documents. Remove it before a real demo unless it is the demo deck.
- **Re-run after all fixes:** `python scripts/verify.py` → ALL CHECKS PASSED (195 tests). `npm --prefix frontend run verify` → 47 passed and the build succeeded. The browser run on the three screens passed.

## Assumptions recorded by sub-agents

Format: `lane · assumption · why · how to undo`.

**Engine**

- engine · canned provider under pytest, OFFLINE=1 or a missing key · tests must never call a vendor · remove the PYTEST_CURRENT_TEST check in `providers/llm/__init__.py` now that conftest sets OFFLINE=1
- engine · the canned judge has one scripted rule (someone says Q3 revenue was rising) · milestone-0 tests run with empty knowledge but expect that alert · replace rules in `providers/llm/canned.py`
- engine · model work runs in background tasks, so the wake button's answer lands just after the request returns · the filler must not wait for Claude · await inside `on_wake_button`
- engine · a new "Hey AGI" cancels an earlier unanswered wake or an answer still being prepared · never two answers · remove `_cancel_speech` on wake
- engine · a bare "Hey AGI" with no question gives a spoken "didn't catch" line and no chat post · "Because you asked:" needs a question · set post_chat=True in `_start_fixed_answer`
- engine · the wake button without a question takes the next sentence from anyone within 8 s · no speaker is known for a button · restrict in `on_wake_button`
- engine · the stop phrase counts while waiting, generating, queued/playing (capped at 120 s) and for 5 s after · status updates may not arrive · constants in `engine.py`
- engine · cooldown is measured in meeting time, not wall-clock time · replay at any speed behaves like a live meeting · switch to `time.monotonic`
- engine · inside the cooldown, a flagged sentence sharing 2+ content words with the alerted topic is not judged or recorded · the live run showed 4 flags for one dispute; this departs slightly from DESIGN §3.3 step 5 · remove `_continues_recent_alert`
- engine · when muted, the chat post is still published with status suppressed_muted · matches the milestone-0 test · stop publishing when muted
- engine · answer failure → the bot says so out loud; summary failure → "SUMMARY UNAVAILABLE" (no vendor text) and every alert becomes a follow-up · never silent, never without a summary · change fallbacks in `engine.py`
- engine · follow_up events are published before meeting.summary · counts match when the summary arrives · reorder in `_summarize`
- engine · Gemini and Claude are called with httpx (no SDKs); Claude gives structured output via a forced tool call · no requirements change · add SDKs if streaming is needed
- engine · the fallback model is tried only on a 400/404 that mentions the model · billing/auth errors are not hidden · edit `_with_fallback`
- engine · the vendor follows the model name: `gemini-…` → Gemini, anything else → Claude · Settings can switch vendors without code · top of `RealProvider._structured`
- engine · Gemini gets the result shape as a JSON schema in its instructions, not `responseSchema` · Gemini's schema field is narrower · pass `responseSchema` in `VendorClient.gemini_json`
- engine · warning text "<vendor> <job> failing (<message>) - <consequence>", under 120 characters · fits the banner · `vendor_warning` in `engine.py`
- engine · backlog over 2 → check only the newest sentence with its 8-line window; more than 7 skipped → the oldest are not checked · one check per burst · `CONTEXT_LINES` / `MAX_WAITING` in `engine.py`
- engine · the canned provider checks every new line so the replay stays deterministic after a skip · revert canned `cheap_check`/`judge` to the last line only
- engine · PDFs are read if pypdf is installed · integrate added `pypdf==6.19.0` to requirements · none needed now

**Meeting**

- meeting · several sentences in one unbroken Recall utterance stay one transcript line; fragments are held and joined · keeps the fake meeting at 25 lines · split on . ? ! in `SentenceAssembler.add`
- meeting · the fake meeting uses a DRY RUN delivery target and the canned voice, publishing sent/played at once · the replay must never reach a vendor · `MeetingLane.target_for`
- meeting · RECALL_API_KEY, INWORLD_API_KEY, INWORLD_VOICE_ID are read from the environment, not Config · settings.py is integrate-owned · add them to Config
- meeting · the Recall signature is logged, not enforced · the receiver slot gets parsed JSON · pass raw bytes from `api.py` and reject on mismatch
- meeting · the canned clip and silent clip were made once with ffmpeg/flite and committed (46 KB) · the canned clip must say it is canned in its audio · delete `providers/voice/assets/`
- meeting · `register(rt)` keeps one argument; tests use `install(rt, MeetingLane(...))` · the signature is frozen by a test · none
- meeting · no new dependencies (httpx only) · none
- meeting · Recall status webhooks are set up once in Recall's dashboard; polling every 3 s covers it if not · status is per workspace · remove `BotLifecycle.watch`
- meeting · a real call with no INWORLD_API_KEY counts as a voice failure (chat only, warning) · the room must never hear a canned clip · missing-key branch in `VoiceService.synthesize`
- meeting · Recall `fatal` ends the meeting as `bot_left`; `call_ended`/`done` as `call_ended` · the contract has no "failed" reason · `ENDED_REASON` in `integrations/bot.py`
- meeting · a chat post arriving after the meeting ended is dropped with a log line and stays `pending` · "do nothing" per the brief · publish `failed` in `ChatPoster._post`
- meeting · bots in meetings reloaded from disk after a restart are not polled · polling starts at launch · start `watch()` for unended recall meetings in `register`

**Screens**

- screens · browser calls go to relative `/api/*`, forwarded by Next.js to `NEXT_PUBLIC_API_BASE`, with compression off · no CORS in the backend · add CORS in main.py and delete the rewrites in `frontend/next.config.ts`
- screens · `NEXT_PUBLIC_API_BASE` is read at dev/build start · Next fixes rewrites at build · read it per request in a route handler
- screens · the live stream is read with fetch plus a small parser resuming by `?since`, not EventSource · named events and `?since` resume · switch if the backend sends unnamed events and honors Last-Event-ID
- screens · `/live` opens the newest meeting · `MeetingListItem` has no ended flag · add `ended_at` to `MeetingListItem`
- screens · the sessions "Alerts" column uses `alert_count` (non-gated) · matches store.py · none
- screens · the silence clock uses the newest of the last line time, `health.last_webhook_at` and the meeting start · null before the first webhook · `frontend/src/lib/silence.ts`
- screens · the silence check compares server time with the browser clock · same laptop · compute the gap on the server
- screens · the live stream goes through a relay route; other /api calls use fallback forwarding · plain forwarding stalled quiet streams up to 15 s · delete `frontend/src/app/api/meetings/[id]/events/route.ts` once CORS exists
- screens · the Meet link is only checked to be an http(s) URL · the backend judges the rest · `checkSendForm` in `frontend/src/lib/sendBot.ts`
- screens · a 409 links to `/live` rather than the exact meeting · no ended flag in the list · add `ended_at` to `MeetingListItem`

**Integrate**

- integrate · tests force BOT_PROVIDER=recall and an empty ATTENDEE_API_KEY (conftest) · Ray's .env has a real Attendee key · none needed
- integrate · `scripts/go.py` assumes ngrok is logged in on the laptop (authtoken) and prints the exact fix if not · the authtoken is not in .env · none
- integrate · Attendee meetings are stored with source "recall" and the Attendee id in `recall_bot_id` (the meeting lane asked for a neutral field; deferred) · works as is; only the label is wrong · add `source: "attendee"` and `bot_id` to the contract

**Meeting (Attendee, 25 Sep)**

- meeting · BOT_PROVIDER unset or "recall" → Recall; "attendee" → Attendee; anything else → Recall with a note · Recall is the design's vendor · `choose_provider` in `register.py`
- meeting · OFFLINE=1 never selects Attendee · OFFLINE means no vendor calls · remove the offline branch in `choose_provider`
- meeting · captions come from Google Meet's own closed captions (en-US) · no third-party transcription key needed · `transcription_settings` in `AttendeeVendor.create_payload`
- meeting · webhook triggers are only `bot.state_change` and `transcript.update` · nothing else is used · `WEBHOOK_TRIGGERS`
- meeting · a repeated caption is detected by (speaker, caption start) plus Attendee's delivery key; the first final text wins · Attendee re-sends edited captions · `_accept_attendee`
- meeting · a caption's words are spread evenly over its length · Attendee sends no per-word timings · `_attendee_words` in `receiver.py`
- meeting · stop drops queued answers; a clip already playing finishes · Attendee has no stop-audio call · `AttendeeClient.stop_audio`
- meeting · end-all finds our bots by the tag `{"created_by": "meet-agi"}` · Attendee bots have no name field · `METADATA` / `is_ours`
- meeting · a refused audio or chat call → answer marked failed, red warning, cleared on next success · DESIGN's chat-only / dashboard-only fallbacks · `AttendeeClient.output_audio` / `send_chat`
- meeting · Attendee signature mismatch is logged only (needs ATTENDEE_WEBHOOK_SECRET in .env) · same as Recall · reject in `_accept_attendee`
- meeting · a non-https PUBLIC_BASE_URL stops sending the bot with a 503 · Attendee rejects http webhooks · `AttendeeVendor.check`
- meeting · state "leaving" = no change; "post_processing"/"ended"/"data_deleted" = left; "meeting_ended" ends as call_ended, other exits as bot_left · the contract has no "failed" reason · `STATE_MAP`

- integrate · judge, answer and summary default to `gemini-3.5-flash-lite` (contract default, commit on main 25 Sep) · Ray chose to wait on Anthropic credit · set them back to `claude-haiku-4-5-20251001` in Settings, or revert that default in `backend/app/contract/records.py`
- integrate · three engine tests that are about Claude failures now pick a Claude model explicitly · they relied on the old Claude default · none needed

- integrate · the tunnel guard treats any Host / X-Forwarded-Host other than localhost, 127.0.0.1, ::1 or testserver as public · ngrok sets Host to its domain · remove the middleware in `main.py`
- integrate · `serve.py` keeps DEV_MODE on by default · the fake meeting needs it, and the tunnel guard blocks dev routes from outside · set DEV_MODE=0 in `.env` for the live demo if wanted

## Conditions carried from the gate

1. Before any live demo: Google billing on (paid tier), and `python scripts/check_keys.py` shows `GEMINI_API_KEY: OK`.
2. Before a demo in front of people: one real rehearsal through Attendee (the section above). Measure wake-to-answer time; captions arrive only when a line is final, so answers may start later than with Recall.
3. At milestone 4, record real Recall payloads, check that stop cuts a playing clip (R3), add the real wake-phrase spellings seen, and confirm saves on Windows (review item 9).

## Not done

- Nothing has run on Windows. The first `python scripts/verify.py` there builds `.venv` and needs internet for pip.
- Nothing has joined a real Google Meet yet. The Attendee and Recall payloads are synthesized from docs and code. One live read-only call was made: Attendee "list bots" → HTTP 200.
- 27 Sep: Ray's first `go.py` run on Windows got the venv, backend and Attendee key check OK, then failed at the tunnel: ERR_NGROK_334 (an old ngrok still held the address). Fixed: go.py now reuses that tunnel if it already reaches this backend; otherwise it stops old ngrok processes on the laptop and retries once, and if the address is held elsewhere it points to https://dashboard.ngrok.com/endpoints (test named after the symptom).
- 27 Sep, second run: the tunnel passed, then the dashboard failed with `connect ECONNREFUSED ::1:8000`. Cause: Windows resolves "localhost" to IPv6 ::1, but the backend listens on 127.0.0.1 only. Fixed: go.py points the dashboard at 127.0.0.1 for build and start (rebuilding when that changes) and checks it at 127.0.0.1; the frontend default and .env.example use 127.0.0.1. Reproduced here by making localhost resolve to ::1 only: with the fix, the dashboard starts and the built forwarding rule targets 127.0.0.1. The frontend default is a one-line change in the screens lane's file, made at integrate.
- `scripts/go.py` was tested on Linux without the tunnel only. The ngrok step and the Windows process handling are untested.
- Claude has not produced any output yet (no credit). This is no longer blocking, since Gemini runs every job.
- Gemini free-tier rate limits are unknown; they are shown only in AI Studio. A lively meeting makes about 10 checks a minute. If the limit is lower, alerts get skipped and the dashboard shows a red warning.
- The Recall signature check is not enforced (P2, above).
- `integrate` is not merged into `main` (Needs me, item 4).

## Next three steps

1. Ray: run `python scripts/go.py` and follow "Test in a real Google Meet" above.
2. Paste what happened into the Claude chat: the go.py window's lines, plus what the dashboard and Meet showed. The next session replaces the synthesized Attendee fixtures with the real payloads it recorded.
3. Push (Needs me, item 3), then merge `integrate` into `main`.

## Verify the current state

`python scripts/verify.py` in the Meet AGI folder. It should end with `ALL CHECKS PASSED`, after `25 transcript lines, 1 alert, 1 spoken answer, 1 wake, 1 summary -> REPLAY OK`. The dashboard check is `npm --prefix frontend run verify`.
