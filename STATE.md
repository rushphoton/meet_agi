# STATE
## Where we are
Branch `main`, tag `milestone-0` (commit "A3: milestone 0"). Milestone 0 is built and verified.

What works:
- The data contract (Pydantic models, with the API description and TypeScript types generated from them).
- The in-process event bus and the live event stream.
- The in-memory store, with one JSON file per meeting.
- A placeholder Recall webhook receiver and a placeholder engine. Both are CANNED.
- The scripted fake meeting, replayed end to end through the real backend.

What does not exist yet:
- Real detection (the engine lane).
- Real Recall bot, voice and chat delivery (the meeting lane).
- The dashboard (the screens lane).
- A Recall account (RECALL_API_KEY is empty).
## Done (with how it was verified)
- Setup and keys: `python scripts/check_keys.py` shows Anthropic, Gemini, Inworld, voice, webhook token and ngrok domain all OK.
- A1 rules, agents, hooks: pushed to https://github.com/rushphoton/meet_agi.
- DESIGN.md: written, then updated at milestone 0 to match the contract as built (register(rt) slots, /api/dev/meetings, canned flags).
- Milestone 0, verified with `python scripts/verify.py` → ALL CHECKS PASSED (run on the Linux side of this laptop, 24 Sep 2026):
  - 26 backend tests, failure paths first: wrong or unset token rejected; partial transcripts ignored; duplicate webhooks counted once; chat over 500 characters rejected; unknown event types refused; a broken subscriber doesn't stop others; JSON file per meeting survives a restart; entry-point signature frozen; wake-word mention does not fire.
  - Contract drift check: openapi.json and schema.d.ts match the models.
  - scripts/serve.py starts and answers /api/health.
  - Fake meeting end to end, through webhook → bus → live stream: 25 lines, 1 alert, 1 spoken answer, 1 wake, 1 summary → REPLAY OK.
  - The tests were checked to bite: breaking the token check, or making "hey agi" match anywhere in a sentence, turns 2 tests red each.
  - Replay against an already-running backend: works. Live-stream resume with ?since=33 returns only events 34-35. --loop repeats cleanly.
## Not done
- Not yet run on Windows itself. The first `python scripts/verify.py` there builds .venv, and the drift check needs Node's npx (Node 22 is installed).
- Hooks (.claude/settings.json) still not observed inside a Windows Claude Code session.
- fixtures/recall/ is synthesized from Recall's docs, not recorded (risk R2).
- No Recall account (risk R1); blocks milestone 4 only.
- The tests show one harmless deprecation warning: Starlette recommends `httpx2` for its test client.
## Next three steps
1. Ray: push (`git -C "C:\Users\YBBJ100572\Desktop\AI\Meet AGI" push --follow-tags`) and run `python scripts/verify.py` once on Windows.
2. Milestone 1: launch lane-engine, lane-meeting and lane-screens in parallel worktrees against the fake meeting.
3. Milestone 2: integrate the lanes on main and re-run the end-to-end replay with real (non-canned) outputs.
## Verify the current state
`python scripts/verify.py`, run from the Meet AGI folder. It should end with `ALL CHECKS PASSED`, after a replay line reading `25 transcript lines, 1 alert, 1 spoken answer, 1 wake, 1 summary -> REPLAY OK`.
## Needs me
- Push the commit and tag (one line above).
- Before milestone 4 only: a Recall.ai account (non-Gmail sign-up). Put RECALL_API_KEY and RECALL_WORKSPACE_SECRET (whsec_...) into .env yourself.
## Assumptions recorded by sub-agents
- None yet (no lanes run).
## Conditions carried from the last gate
- None yet (no gate run).
