"""
WHY THIS EXISTS
Works out how long an MP3 clip lasts by reading its frame headers. Recall
plays a clip we hand it and answers at once, so the only way to play "one
clip at a time" is to wait for the clip's own length before sending the next.

FAILURE IT PREVENTS
Two clips talking over each other in the meeting (the filler line and the
answer, or two answers), because the second was sent while the first was
still playing.

DEPENDENCIES (CLAUDE.md rule 4): none - about forty lines of standard
library instead of an audio library (mutagen, pydub). An audio library would
be justified if we ever need formats other than MP3.
"""
from __future__ import annotations

_BITRATES_V1_L3 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
_BITRATES_V2_L3 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
_SAMPLE_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}
FALLBACK_BITRATE = 128_000  # used only when no frame can be read at all


def _skip_id3(data: bytes) -> int:
    if len(data) >= 10 and data[:3] == b"ID3":
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        return 10 + size
    return 0


def mp3_duration(data: bytes) -> float:
    """Seconds of audio in an MPEG Layer III byte string (CBR or VBR)."""
    pos, total, frames = _skip_id3(data), 0.0, 0
    n = len(data)
    while pos + 4 <= n:
        b1, b2 = data[pos + 1], data[pos + 2]
        if data[pos] != 0xFF or (b1 & 0xE0) != 0xE0:
            pos += 1
            continue
        version = (b1 >> 3) & 0x3          # 3 = MPEG1, 2 = MPEG2, 0 = MPEG2.5, 1 = reserved
        layer = (b1 >> 1) & 0x3            # 1 = Layer III
        br_index, sr_index = (b2 >> 4) & 0xF, (b2 >> 2) & 0x3
        if version == 1 or layer != 1 or br_index in (0, 15) or sr_index == 3:
            pos += 1
            continue
        padding = (b2 >> 1) & 0x1
        rate = _SAMPLE_RATES[version][sr_index]
        if version == 3:
            bitrate = _BITRATES_V1_L3[br_index] * 1000
            length, samples = 144 * bitrate // rate + padding, 1152
        else:
            bitrate = _BITRATES_V2_L3[br_index] * 1000
            length, samples = 72 * bitrate // rate + padding, 576
        total += samples / rate
        frames += 1
        pos += length
    if frames == 0:
        return len(data) * 8 / FALLBACK_BITRATE
    return total
