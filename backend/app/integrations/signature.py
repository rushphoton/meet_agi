"""
WHY THIS EXISTS
Two checks that a webhook really comes from Recall.ai (DESIGN.md §8 decision 1):

1. token_matches(): the secret token in the webhook address must match
   RECALL_WEBHOOK_TOKEN. Compared in constant time, so an attacker cannot
   guess it one character at a time by measuring how fast we say "no".
2. recall_signature_valid(): Recall's own signature (headers webhook-id,
   webhook-timestamp, webhook-signature; HMAC-SHA256 with the workspace
   secret "whsec_..."), as documented at
   docs.recall.ai/docs/authenticating-requests-from-recallai.

LIMIT (recorded as an assumption): the signature is computed over the RAW
request bytes, but the receiver slot in api.py (integrate-owned) hands the
meeting lane already-parsed JSON. The receiver therefore can only check the
signature against a re-serialised body, which may not match byte for byte.
It logs a mismatch but does not reject on it; the path token remains the
enforced check. Enforcing the signature needs api.py to pass raw bytes.

FAILURE IT PREVENTS
Forged transcript lines being fed to the bot by anyone who finds the URL.

DEPENDENCIES (CLAUDE.md rule 4): standard library (hmac, hashlib, base64).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

TOLERANCE_SECONDS = 5 * 60


def token_matches(given: str, expected: str) -> bool:
    """False whenever the expected token is unset: an empty token must lock the door, not open it."""
    if not expected:
        return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def _header(headers: dict, name: str) -> str:
    lowered = {k.lower(): v for k, v in headers.items()}
    return lowered.get(name) or lowered.get(name.replace("webhook-", "svix-")) or ""


def recall_signature_valid(raw_body: bytes, headers: dict, secret: str, now: float | None = None) -> bool:
    msg_id, stamp, sigs = (_header(headers, "webhook-id"), _header(headers, "webhook-timestamp"),
                           _header(headers, "webhook-signature"))
    if not (secret.startswith("whsec_") and msg_id and stamp and sigs):
        return False
    try:
        if abs((now if now is not None else time.time()) - int(stamp)) > TOLERANCE_SECONDS:
            return False
        key = base64.b64decode(secret[len("whsec_"):])
    except ValueError:
        return False
    signed = f"{msg_id}.{stamp}.".encode() + raw_body
    expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
    for part in sigs.split():
        version, _, value = part.partition(",")
        if version == "v1" and hmac.compare_digest(value.encode(), expected.encode()):
            return True
    return False
