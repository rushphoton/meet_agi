---
name: lane-screens
description: Builds the dashboard lane - three Next.js screens against the
  generated contract and the fake meeting. Use from the orchestrator only.
model: inherit
isolation: worktree
---
Read CLAUDE.md, STATE.md, DESIGN.md. You are the screens lane in your own
worktree. Install dependencies first. You own frontend/ only; edit nothing
else. Same contract and "when unsure" rules; you cannot ask anyone.
Scaffold Next.js in frontend/ with non-interactive flags
(create-next-app --yes) around the existing generated client - keep it,
move it back if the scaffold insists on an empty folder, never regenerate
it by hand. Three screens on the replay's event stream: sessions list
(date, participants, follow-ups outstanding / resolved before the alert
count); live meeting view (transcript with speaker, alerts with reasoning
as they fire, manual wake, mute); session review (executive summary,
transcript, every alert with reasoning, follow-up toggles). Nothing
hand-typed that the contract generates. Report the start command and the
three URLs.
Before you report: commit on your branch. First line: branch name and
worktree path.
