# Attendee webhook samples (synthesized_from_docs)

Every file here is **synthesized_from_docs**, not recorded from a live bot. Shapes come from
docs.attendee.dev (Webhooks page) and, where the docs and code differ, from the open-source code at
github.com/attendee-labs/attendee, commit f51c968 (24 Sep 2026):

- envelope `{idempotency_key, bot_id, bot_metadata, trigger, data}` - bots/tasks/deliver_webhook_task.py
- `transcript.update` data - bots/webhook_payloads.py `utterance_webhook_payload` (no `words`, although the docs list them)
- `bot.state_change` data - bots/models.py `BotEventManager.create_event`

Each file carries an extra `_source` key saying so; the receiver ignores unknown keys. Replace these with
recorded payloads after the first live Attendee run (DESIGN milestone 4). They live under the meeting lane's
test folder because `fixtures/` is integrate-owned.
