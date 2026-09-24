# STATE
## Where we are
Branch `main`, commit "A1: rules, agents, hooks". The repo holds the working rules (CLAUDE.md), four sub-agent briefs (.claude/agents/), two hooks (.claude/settings.json + .claude/hooks/test_guard.py) and the setup scripts from session S. DESIGN.md is written (design frozen for milestone 0) and follows Ray's decision to use Recall.ai; the lane-meeting brief matches it. No product code exists yet. Keys present for Anthropic, Gemini, Inworld (+ Attendee); RECALL_API_KEY is still empty because Recall rejected Gmail sign-up - this blocks only milestone 4 (live call), not milestones 0-3, which run on the fake meeting.
## Done (with how it was verified)
- Session S setup: tools installed and all 7 key checks OK (verified: `python scripts/check_keys.py`, every line `OK`).
- A1 files written: CLAUDE.md, STATE.md, .gitignore, .worktreeinclude, README.md, 4 agent briefs, hooks (verified: test_guard.py exercised against a throwaway pytest suite - passes with no suite, passes while green, blocks 8 times when a green test turns red then gives up, resets after a fix).
- Public repo created and pushed (verified: https://github.com/rushphoton/meet_agi loads, 1 commit, no .env).
- DESIGN.md written with Recall capability matrix verified against docs.recall.ai and model IDs verified against the live Anthropic/Gemini model lists (verified: file present on main).
## Not done
- Milestone 0 (contract, skeleton, fake meeting) - not started, by instruction.
- Recall.ai account/key (RECALL_API_KEY) and RECALL_WORKSPACE_SECRET - missing.
- .env.example lacks RECALL_WORKSPACE_SECRET and DEV_MODE (added in milestone 0).
- Hooks not yet observed firing inside a real Claude Code session on Windows (tested on Linux only).
- Spend caps: none set - no card on file at any vendor.
## Next three steps
1. Ray reviews DESIGN.md (sections 7 and 8 especially) and says go.
2. Milestone 0: contract, skeleton, fake meeting, generated client, scripts/verify.py.
3. Milestone 1: launch the three lanes in parallel worktrees.
## Verify the current state
`python scripts/check_keys.py` - every line should end in `OK`. Open https://github.com/rushphoton/meet_agi - it should show CLAUDE.md and the commit "A1: rules, agents, hooks".
## Needs me
- Go / changes on DESIGN.md.
- Before milestone 4 only: a Recall.ai account (non-Gmail sign-up) -> paste RECALL_API_KEY and the workspace verification secret (whsec_...) into .env yourself.
- Optional: reset the ngrok authtoken if not done (it was pasted in chat once).
## Assumptions recorded by sub-agents
- None yet.
## Conditions carried from the last gate
- None yet (no gate run).
