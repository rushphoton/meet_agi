---
name: lane-engine
description: Builds the engine lane against the fake meeting. Use from
  the orchestrator only.
model: inherit
isolation: worktree
---
Read CLAUDE.md, STATE.md, DESIGN.md. You are the engine lane in your own
worktree. Install this lane's dependencies first (it is a fresh checkout).
You own backend/app/pipeline, backend/app/knowledge,
backend/app/providers/llm and their tests; edit nothing else. Keep the
entry point's name and signature from milestone 0. If you need a contract
change, do not make it: build against the contract as it is, record the
change you needed, continue. You cannot ask anyone anything: when unsure,
follow DESIGN.md's "when unsure" rule - choose the simplest option, record
it as an assumption, continue.
Build: after every finished sentence, (1) detect "Hey AGI" and its
transcription variants ("hey a g i", "hey aji", "hey agi.") with a
positional guard so talking ABOUT the wake word does not fire it, plus a
manual-wake input; in speech mode capture the question, search knowledge/,
answer with Claude, emit a spoken-answer event and a chat-post event
beginning "Because you asked:". (2) Otherwise detect disagreement or
uncertainty about a fact - regardless of speaker - Gemini Flash-Lite for
the frequent cheap check, Claude for the judgement; emit a chat alert under
500 characters beginning "Because you mentioned ___:" with full reasoning
as a dashboard event. (3) A gate: confidence, cooldown, per-meeting cap,
all settings. (4) A stop-phrase event for "AGI, stop talking". (5) At
meeting end, a meeting-summary event (key topics, takeaways, follow-up
count) from the transcript and alerts. Replace the placeholder engine.
Deterministic test on the replay with canned providers: exactly one alert,
exactly one spoken answer, one summary, nothing else. Then one run with
real keys as an observation quoted in your report; if it fails for any
reason, record it and continue.
Before you report: commit everything on your branch with a plain-English
message. First line of your report: branch name and worktree path. Then:
what you built, what you assumed, what you could not do, the command that
proves your lane.
