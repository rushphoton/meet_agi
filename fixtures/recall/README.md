# Recall.ai payload samples - SYNTHESIZED FROM DOCS, NOT RECORDED

These files follow the payload schemas published by Recall.ai, filled in with
fictional values. They were **not** captured from a live Recall bot: we have
no Recall account yet (DESIGN.md risk R1/R2). Milestone 4 replaces them with
real recorded payloads.

| File | Schema source (read 24 Sep 2026) |
|---|---|
| transcript_data.json | https://docs.recall.ai/docs/real-time-event-payloads (`transcript.data`) |
| transcript_partial_data.json | same page (`transcript.partial_data`) - we must ignore these |
| participant_events_join.json | same page (`participant_events.join`) |
| bot_status_*.json | https://docs.recall.ai/docs/bot-status-change-events |

Unverified details: the example IDs, `platform` values and `sub_code` values
are made up; field names and nesting are from the docs.

Real-time webhooks are signed by Recall with `webhook-id`, `webhook-timestamp`,
`webhook-signature` headers (https://docs.recall.ai/docs/authenticating-requests-from-recallai);
these samples carry no headers.
