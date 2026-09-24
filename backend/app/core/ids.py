"""
WHY THIS EXISTS
One way to make IDs ("mtg_3f9a...") so every lane produces the same format.

FAILURE IT PREVENTS
Two lanes generating clashing or differently shaped IDs for the same thing.
"""
import uuid


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
