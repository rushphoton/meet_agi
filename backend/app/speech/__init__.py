"""
WHY THIS EXISTS
The meeting lane's speech package: the sentence assembler (assembler.py),
the one-clip-at-a-time audio queue (audio_out.py) and the cached filler
lines (fillers.py). Owner: lane-meeting.

FAILURE IT PREVENTS
Speech logic scattered across the receiver and the Recall client, where a
change to one silently breaks the other.
"""
