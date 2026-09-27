# Review C (gate v0.1): hostile review of branch `integrate` (commit 944c2bb)

- **Audience:** a live demo in front of an audience tonight.
- **Reviewer:** the `reviewer` sub-agent, read-only. I judged from the code and the tests and ran nothing.
- **Date:** 27 Sep 2026.
- **Commit:** 944c2bb (read-only checkout).
- **Focus:** the live path (go.py → Attendee → captions webhook → engine → Gemini → chat and voice → dashboard), especially what changed since review B.

> **Orchestrator's check, added after the review (27 Sep, not written by the reviewer):** item 1 does **not** apply to Ray's laptop today. The reviewer could not see `data/settings.json` (git-ignored). The orchestrator read it on the laptop: its stop phrases include `stop talking`, `stop`, `be quiet` and `that's enough`; `answer_max_words` is 120; `question_wait_seconds` is 15; and the wake spellings include `hey gi`, `hey giant` and `hey gi joe`. The code fault is real (`backend/app/settings.py:72-75` loads the saved file whole and never merges in new defaults), so the **next** change to the defaults would be hidden in the same way. For tonight, the effective top risk is item 2 (Gemini quota), then item 3 (go.py after a tunnel retry). The orchestrator also confirmed the item 3 lines in `scripts/go.py` (`children.append` at :100, the retry at :181-184, the watchdog at :281-283). Everything else below is the reviewer's text, unchanged.

---

**Verdict: Conditional. The core loop has already worked in a real Meet, but three things outside the AI can sink tonight's demo. First, the laptop's saved settings file may quietly switch off most of the Round 2 changes. Second, nobody has confirmed the Gemini quota. Third, one go.py restart path shuts everything down 10 seconds after it prints READY.**

## Top 10 failures (ranked by likelihood × severity)

**1. P0 · integrate: the laptop's saved settings file overrides the Round 2 defaults, so "stop talking" (without "AGI") does nothing.**
- **What the room sees:**
  - Someone says "Stop talking" and the bot keeps going.
  - Answers are still capped at the old length, if the saved file holds the old 60-word cap.
  - "Hey AGI." followed by the question after the filler line gets "Sorry, I didn't catch a question" again, which is exactly the 27 Sep live failure. That happens if the saved file holds the old 8-second wait.
  - "Hey giant" and "Hey GI Joe" are not treated as exact wake phrases.
- **Root cause:**
  - `backend/app/settings.py:72-75` loads `data/settings.json` whole whenever the file exists. Nothing merges in new defaults.
  - STATE.md:41 confirms that a saved `data/settings.json` exists on Ray's laptop and was edited by hand on 27 Sep.
  - Round 2 changed only the defaults in the code (the list of accepted phrasings): stop phrases (`contract/records.py:131-135`), 120 words (`:138`), 15 s wait (`:114`) and new wake spellings (`:105-108`). A saved file keeps the older values.
  - The old stop list (DESIGN.md §4.5) contains "agi" in every phrase. So `detect_stop` (`pipeline/phrases.py:169-193`) never matches a plain "stop talking".
  - Every test uses the defaults (`Settings()`, for example `tests/integrate/test_live_test_findings.py:11`). No test loads a saved file.
  - The settings page has no "reset to defaults" button.
- **Caveat:** `data/` is git-ignored and not in this checkout, so I could not see what the file actually contains. STATE.md does not say that Round 2 updated it.
- **Fix:** Before the show, rename the file so the defaults apply. In code, merge the saved file over the defaults field by field, or add a settings version number, plus a test named after the symptom ("saved settings hid the new stop phrases").
- **Cost:** 5 minutes (rename); 45 minutes (code and test).

**2. P1 · engine / Ray: a Gemini quota or rate-limit error makes the bot say "Sorry, I couldn't look that up just now" on stage.**
- **What the room sees:**
  - The filler line plays, then the apology.
  - Alerts silently stop, and a red warning appears in the go.py window.
- **Root cause:**
  - Turning on Google billing is still open in "Needs me" (STATE.md:68), and the free-tier limits are unknown (STATE.md:255).
  - All five jobs share one model and one quota: check, judge, answer, summary and wake check (`contract/records.py:79-85`, and `real.py:249-254` for the wake check).
  - Every sentence costs one call (`engine.py:276-277`).
  - A rate-limit error (HTTP 429) becomes an ordinary error with no retry (`providers/llm/vendors.py:73-74`). The fallback model is tried only when the error mentions the model (`:80-87`).
  - A failed answer is spoken as the apology (`engine.py:79`, `:440-446`).
  - Rehearsals earlier today count against the same daily free-tier quota.
- **Fix:** Turn on billing and confirm with `python scripts/check_keys.py`. In code, retry an HTTP 429 once after about 1 s, and put the answer job on a different model from the per-sentence check.
- **Cost:** 10 minutes (billing); 30 minutes (retry and test).

**3. P1 · integrate: after a successful ngrok retry, go.py shuts everything down about 10 s after READY, and the bot stays in the Meet.**
- **What the room sees:**
  - READY appears and the dashboard opens.
  - Within 10 s the window prints "[FAIL] A part stopped unexpectedly", and the backend, tunnel and dashboard all die.
  - If a meeting was already running, the bot stays in the call, still listening and billing, with no summary.
- **Root cause:**
  - Every process go.py starts is added to one list, `children` (`scripts/go.py:100`). That includes the first ngrok attempt, which exits with an error on ERR_NGROK_334.
  - The retry path (`:177-184`) starts a second ngrok, or reuses one that was already running, but never removes the dead first attempt from the list.
  - The watchdog treats any child with a non-zero exit code as a failure (`:281-283`) and calls `fail()`. `fail()` calls `stop()` → `sys.exit` (`:80-88`, `:62-77`).
  - `SystemExit` is not caught by `except Exception` (`:297`), so `end_live_meetings()` is skipped. That is the same "bot left in the call" bug that STATE.md:43 says was fixed, arriving by a different route.
  - This retry path has already been hit on Ray's laptop (STATE.md:251). The only test (`tests/integrate/test_go_script.py:31-40`) covers both attempts failing, not "retry, then success".
- **Fix:**
  - Remove a failed attempt's process from `children` (or ignore it in the watchdog).
  - Watch only the three long-running processes.
  - Call `end_live_meetings()` from `fail()` while the backend still answers.
  - Add a test: "tunnel retry succeeded, then go.py shut everything down".
- **Cost:** about 30 minutes.

**4. P1 · engine: the bot posts "Because you mentioned…" alerts about the presenter's own loose numbers.**
- **What the room sees:** while Ray narrates ("it costs about 19 cents, I think"), the Meet chat and dashboard get an uncertainty alert. This can happen up to 8 times, every 90 s.
- **Root cause:**
  - Round 2 made the quick check flag any hedge about a number ("I think it was around…") as "ALWAYS worth a look", even from one speaker and even when the documents are silent (`providers/llm/real.py:67-82`).
  - The judge is told that explicit doubt about a number means high confidence (`:98-106`).
  - The judge is now the same Gemini Flash-Lite model as the quick check (`contract/records.py:79-85`). The two-stage filter is therefore one model asked twice, so its mistakes line up.
  - The gate needs only 0.75 confidence (`:95-98`). An alert that passes goes straight to chat (`engine.py:553-555`).
- **Fix:** For the demo, set the minimum confidence to 0.9 and the cooldown to 180 in Settings. After the demo, run the judge on a different model and require a stated figure plus a hedge.
- **Cost:** 2 minutes (Settings); 1 hour (prompt and model change, retest).

**5. P1 · engine: ordinary phrases trigger the "sounds like Hey AGI" check, and the bot can butt in.**
- **What the room sees:**
  - "Okay, again, what was Q3 revenue?" (asked of a person), "OK, give me the churn number", "Hey Ali…" or "Hey, agreed" makes the bot answer uninvited.
  - Even when the check says no, that speaker's next sentences wait up to 8 s before they are checked for disputes.
- **Root cause:**
  - Greetings include "ok", "okay", "yo", "hay" and a leading "a" (`pipeline/phrases.py:90`).
  - Any word starting with "ag", "aj", "gi", "ji", "eg" or "edg" counts as AGI-like, and so does anything within one letter of "agi", such as Ali, Avi or Abi (`:91-93`, `:108-114`).
  - Each match costs one Gemini yes/no call. The prompt even lists "Okay AGI" as a known misspelling (`real.py:164-173`).
  - While the check runs, that speaker's sentences are held (`engine.py:210-213`, `:301-336`).
  - "giant", "aj" and "ajay" are "strong" spellings, so if the check fails (including on a rate limit, item 2) the bot wakes anyway (`phrases.py:95`, `engine.py:320-324`).
  - Tests cover only "Hey Jim" and "Hi Gina" as negatives (`tests/engine/test_engine_live_feedback.py:43`).
- **Fix:** Drop "ok", "okay" and "a" as greetings for the sounds-like check (keep them for exact spellings). Require the AGI-like word to be followed by a pause or a question word. Add tests for these sentences.
- **Cost:** about 1 hour.

**6. P1 · engine + meeting: a new "Hey AGI" during a long answer doesn't cut the old answer.**
- **What the room sees:** Ray asks a follow-up while the bot is mid-answer. The bot finishes the whole old answer (up to 120 words, about 40 s), then plays the filler line, then answers.
- **Root cause:**
  - `_wake` calls `_cancel_speech`, which only cancels an answer still being written (`engine.py:281`, `:391-395`). It publishes no `stop`.
  - The audio side puts the new filler at the back of the line (`speech/audio_out.py:100-110`) and plays items strictly one after another (`:179-194`, `:205-237`).
  - The re-ask path does the same (`engine.py:271`).
- **Fix:** When `_speaking(st)` is true, publish `stop` before `wake`.
- **Cost:** about 30 minutes plus a test.

**7. P2 · meeting: "stop talking" can still let one or two sentences finish, and sentence clips may sound choppy.**
- **What the room hears:** after "stop", the current sentence and sometimes the next one; possibly audible gaps or pitch changes between sentences.
- **Root cause:**
  - The next clip is sent after the clip's calculated length plus 0.25 s (`speech/audio_out.py:54`, `:266-270`).
  - Attendee queues audio on its own servers and has no way to cut a clip (`integrations/attendee_client.py:90-95`, `:256-263`). If Attendee takes more than 0.25 s to start a clip, the next sentence is already queued there when "stop" arrives.
  - Each sentence is a separate voice call (`audio_out.py:226-227`).
  - STATE.md:64 says none of this has been tested live.
- **Fix:** Rehearse once. If two sentences leak through, raise the margin to about 0.8 s, or leave a gap before the last sentence.
- **Cost:** about 15 minutes after a rehearsal.

**8. P2 · screens: a red "No transcript for N s – check the laptop, VPN and ngrok" banner appears whenever the Meet is quiet.**
- **What the room sees:** a red alarm on the projector while Ray talks to the audience with the Meet quiet.
- **Root cause:**
  - The banner fires after 30 s with no sign of life (`frontend/src/lib/silence.ts:15`, `:39-47`).
  - Attendee sends webhooks only for captions and status changes (`attendee_client.py:141`). There is no heartbeat, so a quiet room looks like a dead connection.
- **Fix:** Raise the threshold to 120 s, or word it "quiet for N s" in grey unless the backend's last webhook is also stale.
- **Cost:** about 15 minutes.

**9. P2 · integrate (carried from review B, not fixed): the whole meeting file is rewritten on every event, blocking the backend.**
- **What the room sees:** later in a long session, captions, alerts and answer status land progressively later.
- **Root cause:**
  - Every event re-serializes the whole record plus all events, with indentation (`core/store.py:137-144`).
  - This runs synchronously inside `bus.publish` (`core/bus.py:64`) on the backend's single request loop.
  - Retry sleeps block that loop too (`store.py:149-152`), and on Windows, Defender scans every write.
- **Fix:** Save at most once a second in a background task, or append events to a log.
- **Cost:** about 1 hour.

**10. P2 · integrate: a dashboard opened by any address other than localhost returns 404 for every call.**
- **What the room sees:** someone opens the dashboard from a second machine or by the laptop's name or LAN IP, for example to mirror it. The page loads but shows errors.
- **Root cause:**
  - The tunnel guard treats any non-local `Host` or `X-Forwarded-Host` as public (`backend/app/main.py:36`, `:46-51`, `:67-73`).
  - The dashboard's pass-through rule (`frontend/next.config.ts:43`) goes through Next.js's built-in proxy, which I believe adds `X-Forwarded-Host` with the original host.
  - **Moderate confidence:** this is from knowledge of Next.js, not something I ran.
- **Fix:** Present from `http://localhost:3000` on the laptop itself. In code, remove `x-forwarded-host` in the pass-through rule, or trust it only from 127.0.0.1.
- **Cost:** 0 minutes (operational); 30 minutes (code).

**Also noted (P2):**
- Webhook signatures (Attendee and Recall) are still only logged, not enforced (`receiver.py:121-125`, `:181-189`). The address token is enforced (`:88-89`).
- `knowledge/SAMPLE_board_deck_q3.md` is committed next to the real documents. That is fine if it is the demo deck.

**Review B items re-checked and fixed in code:**
- #1: any `gemini-` model is routed to Gemini (`real.py:230-240`), and the vendor's error text is kept out of the summary (`engine.py:585-593`).
- #4: the 409 names the meeting that is still live (`api.py:57-60`).
- #5: the tunnel guard (`main.py:67-73`).
- #6: a voice outage in a real call never plays the canned clip (`providers/voice/__init__.py:132-148`, `audio_out.py:104-105`).
- #9: saves retry, and a failed save no longer stops the live path (`store.py:125-131`).
- #10: the dispute-check backlog skips to the newest sentence (`engine.py:480-485`).
- P2: nothing is posted to chat after the meeting ends (`chat.py:94-96`).

## Where STATE.md and the code disagree (the code is right)

1. **Is main merged?** STATE.md:5-6 and :79 say `main` equals `integrate`. STATE.md:257 says it is not merged, and :263 says to merge it.
2. **Has it joined a real call?** STATE.md:21 and :250 say nothing has joined a real Meet. STATE.md:27-45 records the live test that passed.
3. **Has it run on Windows?** STATE.md:25 and :249 say nothing has run on Windows. STATE.md:251-252 and the live test say it has.
4. **Answer length:** STATE.md:113 says 60 words. The code caps answers at 120 (`records.py:138`).
5. **What "stop" leaves playing:** STATE.md:22 says the current answer finishes (up to about 20 s). The code now speaks sentence by sentence, so only the current sentence finishes (`audio_out.py:205-237`).
6. **Wake-button wait:** STATE.md:170 says 8 s. The code waits 15 s (`records.py:114`).
7. **Next three steps:** STATE.md:259-263 describes the first live test, which is already done.
8. **Engine description:** the `engine.py:24-25` docstring says Claude judges disputes. The judge defaults to Gemini (`records.py:83`).

## Verdict on six axes

| Axis | Verdict | Reason |
|---|---|---|
| Correctness | Conditional | The live loop worked on 27 Sep (STATE.md:31-37). Round 2 behavior is proven only against the built-in defaults, not the laptop's saved file (item 1). A new wake doesn't cut an old answer (item 6). |
| Blast radius | Conditional | A failing listener is logged and skipped (`bus.py:67-71`), and dispute checks catch everything (`engine.py:486-489`). But one go.py watchdog takes down all three processes and skips ending the meeting (item 3), and one Gemini quota serves every job (item 2). |
| Security | Conditional | Review B's P0 is fixed: the tunnel only lets `POST /webhooks/recall/*` through (`main.py:67-73`). Signatures are logged, not enforced. Until billing is on, meeting content goes to Gemini's free tier, which Google may use to improve its products (STATE.md:68). |
| Reversibility | Conditional | State is plain JSON with no migrations. But `main` equals `integrate`, so no branch holds a pre-Round-2 version to roll back to, and the settings file has no reset. |
| Observability | Conditional | Vendor warnings reach `/api/health` and the go.py window (`go.py:284-290`). But the silence banner cries wolf (item 8), the words spoken before a stop are only in the log (`audio_out.py:272-278`), go.py's failure path never says the bot is still in the call, and STATE.md contradicts itself. |
| Tests | Conditional | 312 tests, with failure paths first. Gaps: no test loads a saved settings file. No test covers "tunnel retry, then success" (`test_go_script.py:31-40`). Only two negative sounds-like sentences are tested (`test_engine_live_feedback.py:43`). The Ctrl+C test runs the venv's Python directly, bypassing the relaunch step Ray actually goes through (`test_go_script.py:71`). Sentence-by-sentence audio through Attendee is untested live. |

## Demo fallback for the top three

**1. Stale settings file**
- **Before the show (5 minutes):**
  1. Open http://localhost:3000/settings.
  2. Check that the stop phrases include `stop talking`, the answer limit is 120 and the question wait is 15.
  3. If any of them differ, press **End meeting** on the dashboard if a meeting is open, then Ctrl+C in the go.py window.
  4. In PowerShell, run:
     ```
     Rename-Item "C:\Users\YBBJ100572\Desktop\AI\Meet AGI\data\settings.json" settings.old.json
     ```
  5. Run `python scripts/go.py` again and re-enter any speaker names in Settings.
- **On stage:**
  - If "stop talking" does nothing, say **"AGI, stop talking"** (the old phrase still works) or click **Stop talking** on the dashboard.
  - If the bot says "Sorry, I didn't catch a question", ask in one breath: "Hey AGI, what was …?"
- **Rollback:** rename `settings.old.json` back.

**2. Gemini quota**
- **Before the show:** turn on billing at https://aistudio.google.com, then run `python scripts/check_keys.py` and expect `GEMINI_API_KEY: OK`.
- **On stage:** if the bot apologizes, or the go.py window prints "Gemini answers failing (HTTP 429…)":
  1. Open Settings.
  2. Change the **Answer** model to `gemini-2.5-flash-lite` (it has its own quota) and click Save.
  3. Re-ask the question. The change applies from the next sentence, with no restart.
- **Last resort:** the fake-meeting replay with `OFFLINE=1`. Say that it is canned; the screens label it as such.

**3. go.py shuts down after a tunnel retry**
- **Warning sign:** go.py printed "An old ngrok is holding the tunnel address – stopping it and retrying" or "…already running from an earlier ngrok – using it". Expect "[FAIL] A part stopped unexpectedly" within 10 s of READY.
- **On stage:**
  1. Remove the bot from the Meet: open the People panel, find **Meet AGI**, click **⋮**, then **Remove from call**. Alternatively, run `python scripts/end_all_bots.py`, which STATE.md lists but I did not read.
  2. Run `python scripts/go.py` again. The address is now free, so the first attempt succeeds and the bug doesn't trigger.
- **Before the show:** start go.py at least 10 minutes early, wait a full minute after READY to confirm it stays up, and don't restart during the demo.
- **Rollback:** none needed; the code fix is about 30 minutes.

## What I did NOT examine, and why

- **Ray's `data/settings.json`:** git-ignored and not in the checkout, so item 1 depends on a file I could not see.
- **Frontend files not read:** `AlertCard`, `Transcript`, `AnswerList`, `HealthBanner`, `SendBotForm`, the sessions and review pages, and `meetingState.ts`. For the settings page I only searched for a reset button and found none. The 27 Sep live run exercised the screens.
- **Backend files not read:** `gate.py`, `entry.py`, `speech/assembler.py`, `recall_client.py`, `fake_attendee.py`, `fake_recall.py`, `providers/llm/canned.py`, `base.py`, `contract/events.py`, `check_keys.py`, `end_all_bots.py`, `verify.py`, `replay.py`, `setup_windows.ps1` and `publish_repo.ps1`.
- **Test bodies:** I read names for most tests and full bodies only for `test_go_script.py`, `test_live_test_findings.py` and part of `test_engine_live_feedback.py`.
- **Ctrl+C when go.py relaunches itself inside `.venv`** (`scripts/_bootstrap.py:49-51`):
  - On Linux or macOS, Python's `subprocess.run` would kill the inner go.py about 0.25 s after Ctrl+C, before it can end the meeting.
  - On Windows I believe the outer wait can't be interrupted, so the inner go.py finishes its cleanup. That is from my knowledge of Python, not a test, and the existing test bypasses this relaunch.
  - To remove the doubt, start the demo with `.venv\Scripts\python.exe scripts\go.py`.
- **Attendee's real playback timing and queueing, Gemini's free-tier limits, and Next.js adding `X-Forwarded-Host`:** from code comments and my own knowledge. Nothing was run.
- **Nothing was run at all:** the reviewer is read-only. It could not run tests, open a browser or place a call.
