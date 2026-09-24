# Review B: hostile review of branch `integrate`

- Audience: a live demo in front of an audience.
- Reviewer: the `reviewer` sub-agent, read-only. It judged from the code and the tests and ran nothing.
- Date: 24 Sep 2026.
- What was fixed afterwards is recorded in STATE.md ("What the reviewer found and what was fixed").

---

**Verdict: not ready for a live demo with a real bot.** The replay path is solid. The live path has one blocker we already know about, one security hole, and a gap in how the bot's status comes back that the replay hides. Claims about how Recall behaves are from memory of its docs and have not been tested against a real account.

## Top 10 failures (ranked by likelihood × severity)

**1. P0 · engine: every Claude call fails live.**
- **What the room sees:** every "Hey AGI" gets the filler, then "Sorry, I couldn't look that up just now." There are no chat alerts all meeting. The review screen shows "SUMMARY UNAVAILABLE … (HTTP 400: Your credit balance is too low …)", which puts Anthropic's billing message on the projector.
- **Root cause:**
  - The judge, answer and summary jobs are hard-wired to Claude (`backend/app/providers/llm/real.py:162,177,189`).
  - The model fallback only fires on errors that mention "model" (`vendors.py:85`), and its fallback is another Claude model anyway.
  - The vendor's error text goes straight into the summary (`engine.py:370`).
  - `scripts/check_keys.py:39` tests `GET /v1/models`, which succeeds without credit, so the key check shows a false green.
  - `/api/health` lists nothing wrong (`pipeline/register.py:25-27`).
- **Fix:** Buy credits, then prove it with one real `/v1/messages` call and make `check_keys.py` do that call. In code, send any model whose name starts with `gemini-` to `gemini_json`, so Settings can move the judge, answer and summary jobs to Gemini with no code change. Take `({exc})` out of the takeaways text.
- **Cost:** credits about 10 minutes; routing about 2 hours.

**2. P1 · meeting: the bot's status never updates and the meeting never ends by itself.**
- **What the room sees:**
  - The live view says "bot: joining" for the whole demo.
  - "waiting_room" never shows, so the host can miss the admit prompt.
  - When the call ends, the meeting stays "still live" and no summary is written.
- **Root cause:**
  - The bot is created with only `transcript.data` realtime events (`integrations/bot.py:70`).
  - Recall sends bot status changes (`bot.in_waiting_room`, `bot.call_ended`, `bot.done`) through workspace webhooks set up in Recall's dashboard. Nothing sets those up or documents them.
  - `BotLifecycle.status()` (`bot.py:126`) is never called.
  - The replay passes only because `scripts/replay.py` sends the status webhooks itself, so the test feeds the backend something real Recall won't.
- **Fix:** After `launch()`, ask Recall `get_bot` every 3 s until the bot has left or failed. Publish `bot.status` when it changes and `meeting.ended` on `call_ended`/`done`. Also document pointing the Recall dashboard webhook at the same token URL.
- **Cost:** about 2 hours plus a test.

**3. P1 · screens: the dashboard has no way to send the bot.**
- **What Ray sees:** the dashboard has nowhere to paste a Meet link. The sessions page only suggests `python scripts/replay.py`.
- **Root cause:** `frontend/src/lib/api.ts:74-94` has no `POST /api/meetings`, and `app/page.tsx:42-46` offers only the replay hint.
- **Fix:** Add a "Send Meet AGI" form (Meet URL and title) on the sessions page. It should call `POST /api/meetings`, show the 409, 502 and 503 messages as they come back, then jump to `/meetings/{id}/live`.
- **Cost:** about 1 hour.

**4. P1 · integrate: an old "live" meeting blocks sending the bot.**
- **What Ray sees:** "409: A meeting is already live; end it first", on stage.
- **Root cause:**
  - `store.live_meeting()` (`core/store.py:67-71`) returns any saved meeting with no end time. That includes a replay stopped with Ctrl+C and every real rehearsal (because of item 2). These reload from `data/meetings/` at startup.
  - The error doesn't name which meeting is live (`api.py:55-56`).
  - The fake-meeting endpoint closes old meetings automatically; the real send doesn't.
- **Fix:** At startup, close any unended replay meeting that was reloaded from disk. Put the live meeting's id and title in the 409 message. Add an "End" link to each unended row on the sessions list.
- **Cost:** about 45 minutes.

**5. P0 · integrate: the whole API is public through ngrok, with no login.**
- **The exposure:** anyone who knows the ngrok address can:
  - read every board-meeting transcript (`GET /api/meetings/{id}`);
  - change Settings (`PUT /api/settings`, `api.py:137`);
  - upload documents that poison the answers (`api.py:147-155`);
  - mute, wake or end the meeting;
  - use `/docs`.
- **Root cause:**
  - `ngrok http 8000` forwards every path, not just the webhook.
  - `scripts/serve.py:19` always turns on `DEV_MODE`, so `/api/dev/meetings` (`api.py:172-178`) can end the live meeting from outside.
  - The address is written in `DESIGN.md:150`, which has been pushed to GitHub.
  - Design decision 8 ("localhost only") stops being true once the tunnel is up. `main.py:38-41` has no guard.
- **Fix:** Add a check in `main.py`: when a request arrives through the tunnel (it carries `X-Forwarded-For`, or its Host is the ngrok domain), allow only `POST /webhooks/recall/*` and return 404 for everything else. Add an ngrok traffic-policy file that allows only that path. Default `DEV_MODE` to off for the live demo. Add a test named after the symptom: "public tunnel can read transcripts".
- **Cost:** about 45 minutes.

**6. P1 · meeting: a voice outage plays a clip that announces itself as canned.**
- **What the room hears:** "This is a canned sample clip, not the real voice…", twice per wake (once as the filler, once as the answer), each after up to an 8 s timeout.
- **Root cause:**
  - `VoiceService.synthesize` falls back to the canned clip on any Inworld error (`providers/voice/__init__.py:127-129`).
  - The filler bank retries that for every filler line (`speech/fillers.py:37-39`).
  - DESIGN risk R5 says to post the answer to chat only in this case. The code does something else.
- **Fix:** When a real bot is in the call and the voice fails, mark the answer `failed` and play nothing. The chat line is already posted. Skip the filler. Show "voice down" on the dashboard.
- **Cost:** about 1 hour.

**7. P1 · engine / integrate: the wake phrase gets transcribed as something the list doesn't cover.**
- **What the room sees:** Ray says "Hey AGI" and nothing happens.
- **Root cause:** Recall's transcription may write "Hey Aggie", "Hey AJ", "Hey Ajay" or "Hey AI". The default list (`contract/records.py:99-101`) matches only exact word runs (`pipeline/phrases.py:53-54`).
- **Fix:** Rehearse once with Recall and add the spellings it actually produces through Settings (no code). Log any sentence that starts with "hey" and doesn't match, so the misses are easy to find. The manual wake button is the fallback.
- **Cost:** 15 minutes plus a rehearsal.

**8. P1 · meeting + screens: nothing shows whether transcripts are still arriving.**
- **What Ray sees:** the dashboard just goes quiet. Nobody can tell a quiet room from a dead pipe.
- **Root cause:**
  - Risk R6's promised "dashboard shows endpoint health" was never built. The receiver doesn't record when the last webhook arrived (`integrations/receiver.py:71-99`).
  - If the laptop sleeps or the VPN drops for more than 60 s, Recall gives up on the webhook address for the rest of the meeting.
  - Failed Gemini checks only go to the log (`engine.py:292-294, 304-306`).
  - `serve.py:25` sets `log_level="warning"`, which hides the per-request log.
- **Fix:** Track the last webhook time and count vendor failures, and show both on `/api/health` (a contract change, so integrate does it). The live page turns red when no transcript has arrived for more than 30 s.
- **Cost:** about 2 hours.

**9. P1 · integrate: saving to disk on Windows can silently drop chat posts, answers or the summary.**
- **What the room sees:** an alert or answer on the dashboard that never reaches the Meet, or a meeting that never gets its summary.
- **Root cause:**
  - `core/store.py:127-135` rewrites the whole meeting file on every event using a temp file and `os.replace`.
  - On Windows, Defender or OneDrive briefly holding the file (the project sits on the Desktop) raises `PermissionError`.
  - That error is raised inside `bus.publish` after the in-memory record has changed but before listeners and subscribers run (`core/bus.py:64-71`). So the chat poster, the audio queue and the summary step never see that event.
  - Rewriting the whole file every time also grows with meeting length (quadratic cost).
- **Fix:** Retry `os.replace` 5 times with 50 ms gaps. Catch and log save failures in `apply()` so the live path keeps going. Save less often.
- **Cost:** about 45 minutes. This has only ever run on Linux.

**10. P2 · engine: the dispute-check queue has no limit.**
- **What the room sees:** in a lively meeting, alerts show up minutes late, about points the room has already moved past.
- **Root cause:** one worker checks sentences strictly in order (`engine.py:266-282`). Each check is a Gemini call and possibly a Claude call, each allowed up to 8 s, from Beijing over VPN.
- **Fix:** When more than 2 sentences are waiting, drop the older ones and check only the newest window of context.
- **Cost:** about 30 minutes.

**Also noted (P2):**
- `knowledge/SAMPLE_board_deck_q3.md` may sit next to the real deck and get cited. Remove it before the demo if it isn't the demo deck.
- The chat poster doesn't check whether the meeting has ended (`chat.py:90-103`). If `leave_call` fails, or someone runs the replay mid-demo (`api.py:176-178` ends the real meeting without making the bot leave), the bot keeps posting alerts in the Meet.
- `bot.end_all()` (the "make every bot leave" panic button) has no route and no script, so nothing can call it.

## Verdict on six axes

| Axis | Verdict | Reason |
|---|---|---|
| Correctness | Conditional | The replay path is proven end to end. The live path fails on Claude credit (item 1) and bot status (item 2). |
| Blast radius | Conditional | Lanes are isolated by the event bus and a crashing subscriber is logged and skipped. But a disk-save failure aborts the whole event for every subscriber (item 9). |
| Security | Not ready | The unauthenticated API and dev endpoints are public through ngrok, and the address is in the repo (item 5). Recall's signature is only logged, not enforced; the path token is the only real check. |
| Reversibility | Conditional | State is plain JSON files and `settings.json`, with no migrations. But the stray-bot panic button can't be reached, so a bot left running (and billing) needs Recall's own dashboard. |
| Observability | Not ready | Health shows green while Claude fails. There is no webhook freshness check, vendor failures go to the log only, and the request log is off. |
| Tests | Conditional | 161 tests cover failure paths first, which is strong. But all Recall inputs are synthesized. No test covers status arriving without the replay injecting it, the public tunnel exposure, or saving on Windows. |

## Fallbacks for the top three

1. **Claude credit:** top up the account and confirm with a real `/v1/messages` call before going on stage. If that isn't possible, the honest fallback is the replay with `OFFLINE=1`: every output is labelled CANNED and FAKE MEETING. It is not a live fallback, because `OFFLINE=1` also cans the voice.
2. **Bot status:** end every run with the dashboard's "End meeting" button, which makes the bot leave and writes the summary. The host watches the Meet for the admit prompt personally. Point the Recall dashboard webhook at `PUBLIC_BASE_URL/webhooks/recall/<token>`.
3. **No send-bot form:** use `http://127.0.0.1:8000/docs` → `POST /api/meetings` → Try it out, or run in PowerShell:
   ```
   Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/meetings -ContentType 'application/json' -Body '{"meeting_url":"<meet link>","title":"Demo"}'
   ```
   On a 409, end the old meeting first (`POST /api/meetings/{id}/end`). Rollback for the whole branch: `main` is unchanged, so demo from the replay.

## What was not examined, and why

- **Frontend files not read:** `AlertCard`, `Transcript`, `AnswerList`, `HealthBanner`, the settings page and `format.ts`. The orchestrator's browser run already exercised them.
- **Backend files not read:** `canned.py`, `fake_recall.py`, `mp3.py`, `fake_meeting.py`, `verify.py`, `setup_windows.ps1`, `publish_repo.ps1` and the hooks. Their failure paths are covered by the passing tests.
- **Test bodies:** only the test names were read.
- **Nothing was run.** The reviewer is read-only: it couldn't run commands, open a browser, or check whether the GitHub repo is public.
- **Recall's behavior** (status webhooks being dashboard-level) and the Windows file-locking behavior come from the reviewer's knowledge, not from anything it ran. Milestone 4's live dry run should confirm items 2 and 9 first.
