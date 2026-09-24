"""
WHY THIS EXISTS
This package is the single place where every piece of data Meet AGI passes
around is defined: transcript lines, alerts, spoken answers, chat posts,
meeting records, settings. The dashboard's TypeScript types are generated
from these definitions (scripts/export_openapi.py), never typed by hand.

FAILURE IT PREVENTS
Two parts of the system disagreeing about what an "alert" looks like, which
shows up as blank screens or crashes only during the live demo.

Only the integrate step on main edits this package (CLAUDE.md rules 3, 10).
"""
from .events import *  # noqa: F401,F403
from .records import *  # noqa: F401,F403
from .context import MeetingContext  # noqa: F401
