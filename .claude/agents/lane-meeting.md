---
name: lane-meeting
description: Builds the meeting lane - Recall.ai bot, transcript receiver,
  Inworld voice - against recorded payloads. Use from the orchestrator
  only.
model: inherit
isolation: worktree
---
Read CLAUDE.md, STATE.md, DESIGN.md. You are the meeting lane in your own
worktree. Install this lane's dependencies first. You own
backend/app/integrations, backend/app/speech, backend/app/providers/voice
and their tests; edit nothing else. Same contract rule and "when unsure"
rule as CLAUDE.md and DESIGN.md state; you cannot ask anyone anything.
Build, from Recall.ai's documentation (output audio, chat, real-time
webhook endpoints) and Inworld's TTS docs: (1) bot lifecycle - create with
the name "Meet AGI", status, leave, end-all. (2) The real-time transcript
receiver at PUBLIC_BASE_URL: finalized transcript events only, never
partial; verify RECALL_WEBHOOK_TOKEN in constant time; answer fast, work
afterwards; assemble finished sentences with speaker names on pause OR
maximum length; feed the engine's single entry point. (3) Audio out: an
utterance queue, one clip at a time, mute-aware; Inworld voice with a
sample-clip fallback that says so; a cached bank of filler lines ("Sure,
let me look that up", "One moment, checking the documents"); the stop
phrase discards queued audio. (4) Chat posting under 500 characters.
Subscribe the queue and the chat poster to the engine's spoken-answer,
chat-post and stop events on the bus. Test against fixtures/recall/ and a
fake Recall; never call a live vendor from a test. Keys are in .env; never
print them.
Before you report: commit on your branch. First line: branch name and
worktree path. Then what you built, assumed, could not do, and the command
that proves your lane.
