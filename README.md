# Meet AGI

Meet AGI will be a Google Meet copilot: a bot that joins your call, listens to the live transcript, answers out loud when someone says "Hey AGI" (searching your own documents first), quietly flags disagreements or shaky facts in the meeting chat with its reasoning, and hands you a summary with follow-ups when the call ends - all visible on a small web dashboard.

Start here: DESIGN.md, STATE.md

## Run it

Open PowerShell in this folder first:

```
cd "C:\Users\YBBJ100572\Desktop\AI\Meet AGI"
```

The first command you run creates a private Python environment (`.venv`) and installs what it needs. This takes about a minute once, then is instant.

1. **Start the backend.** It serves http://localhost:8000 (health check: http://localhost:8000/api/health). Stop it with Ctrl+C.
   ```
   python scripts/serve.py
   ```
2. **Run the tests.** This runs every backend test, the contract drift check, a start-backend smoke test and the fake meeting end to end. It ends with `ALL CHECKS PASSED`.
   ```
   python scripts/verify.py
   ```
3. **Replay the fake meeting end to end.** It plays the scripted 3-minute Q3 revenue review through the real backend at 10x speed, printing every event live. If no backend is running it starts its own and stops it after. It ends with `REPLAY OK`. Options: `--speed 1` for real time, `--loop` to repeat until Ctrl+C. To use canned providers only, run `$env:OFFLINE=1` in PowerShell first (set it in the window that runs the backend too).
   ```
   python scripts/replay.py
   ```
4. **Open the dashboard.** In a second PowerShell window in this folder (the backend from step 1 must be running). The first time, install it: `npm --prefix frontend install`. Then build and start it, and open http://localhost:3000 (sessions), http://localhost:3000/live (live meeting) and the Review link on each session. Its own check (type check, tests, build): `npm --prefix frontend run verify`.
   ```
   npm --prefix frontend run build; npm --prefix frontend run start
   ```

Panic button: `python scripts/end_all_bots.py` makes every Meet AGI bot leave its call.

What is real and what is placeholder right now: `GET /api/health` lists every placeholder (canned by design) and every warning (a vendor failing right now). The dashboard shows both at the top of every page.
