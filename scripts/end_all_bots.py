"""
WHY THIS EXISTS
Panic button: makes every "Meet AGI" bot leave its call (every live meeting in our records, plus any
stray "Meet AGI" bot Recall still lists) and marks those meetings ended. Run it if the bot is stuck in
a call, or before walking away after a demo, so nothing keeps listening or billing.
  python scripts/end_all_bots.py

FAILURE IT PREVENTS
A bot left in a meeting (still hearing everything, still billing) with no way to remove it except
Recall's own website (review B, reversibility).

Works whether or not the backend is running. If it is running, restart it afterwards so its screens
pick up the ended meetings.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ROOT, ensure_venv  # noqa: E402

ensure_venv()
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from backend.app.integrations.register import lane_for  # noqa: E402
from backend.app.main import create_app  # noqa: E402


async def main() -> None:
    rt = create_app().state.runtime
    try:
        told = await lane_for(rt).bot.end_all()
    except Exception as exc:  # never print a key; the message names the setting at most
        sys.exit(f"Could not end the bots: {exc}")
    print(f"Told {len(told)} bot(s) to leave." if told else "No Meet AGI bots were in a call.")


asyncio.run(main())
