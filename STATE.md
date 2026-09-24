# STATE
## Where we are
Branch `main`, commit "A1: rules, agents, hooks". The repo holds the working rules (CLAUDE.md), four sub-agent briefs (.claude/agents/), two hooks (.claude/settings.json + .claude/hooks/test_guard.py) and the setup scripts from session S. No product code exists yet. All vendor keys pass `scripts/check_keys.py`. Meeting-bot vendor is Attendee, not Recall.ai (Recall rejected Gmail sign-up); the lane-meeting brief still says Recall and must be updated before milestone 0. DESIGN.md does not exist yet.
## Done (with how it was verified)
- Session S setup: tools installed and all 7 key checks OK (verified: `python scripts/check_keys.py`, every line `OK`).
- A1 files written: CLAUDE.md, STATE.md, .gitignore, .worktreeinclude, README.md, 4 agent briefs, hooks (verified: test_guard.py exercised against a throwaway pytest suite - passes with no suite, passes while green, blocks 8 times when a green test turns red then gives up, resets after a fix).
- Public repo created and pushed (verified: repo URL loads; see "Verify the current state").
## Not done
- DESIGN.md (referenced by README and every agent brief).
- lane-meeting brief still targets Recall.ai; the bot vendor is Attendee.
- Hooks not yet observed firing inside a real Claude Code session on Windows (tested on Linux only).
- Spend caps: none set - no card on file at any vendor.
## Next three steps
1. Write DESIGN.md (including the "when unsure" rule the briefs point to).
2. Update lane-meeting.md (and RECALL_* names) for Attendee, on main.
3. Milestone 0: skeleton, contract, fake meeting replay, "Run it" section.
## Verify the current state
`python scripts/check_keys.py` - every line should end in `OK`. Open https://github.com/rushphoton/meet_agi - it should show CLAUDE.md and the commit "A1: rules, agents, hooks".
## Needs me
- Nothing blocking. Optional: reset the ngrok authtoken if not done (it was pasted in chat once).
## Assumptions recorded by sub-agents
- None yet.
## Conditions carried from the last gate
- None yet (no gate run).
