"""Rational time helpers shared by the FCPXML writer and reader.

FCPXML stores every time as a rational number of seconds ("1001/30000s",
"10s"), so all math here is done with Fraction to stay frame-exact.
"""

from __future__ import annotations

import re
from fractions import Fraction

# Common NTSC rates are stored as exact frame durations, not floats.
_NTSC = {
    23.976: Fraction(1001, 24000),
    23.98: Fraction(1001, 24000),
    29.97: Fraction(1001, 30000),
    47.952: Fraction(1001, 48000),
    59.94: Fraction(1001, 60000),
    119.88: Fraction(1001, 120000),
}


def parse_time(value: str | None, default: Fraction = Fraction(0)) -> Fraction:
    """Parse an FCPXML time attribute such as "1001/30000s" or "10s"."""
    if value is None or value == "":
        return default
    v = value.strip()
    if v.endswith("s"):
        v = v[:-1]
    if "/" in v:
        num, den = v.split("/", 1)
        return Fraction(int(num), int(den))
    return Fraction(v)


def fmt_time(t: Fraction) -> str:
    """Format a Fraction as an FCPXML time attribute."""
    t = Fraction(t)
    if t.denominator == 1:
        return f"{t.numerator}s"
    return f"{t.numerator}/{t.denominator}s"


def frame_duration_for_rate(rate: float | str) -> Fraction:
    """Exact frame duration for a frame rate like 29.97, "30000/1001" or 25."""
    if isinstance(rate, str):
        r = rate.strip()
        if "/" in r:
            num, den = r.split("/", 1)
            fps = Fraction(int(num), int(den))
            if fps == 0:
                raise ValueError(f"invalid frame rate {rate!r}")
            return 1 / fps
        rate = float(r)
    for known, dur in _NTSC.items():
        if abs(rate - known) < 0.006:
            return dur
    if rate <= 0:
        raise ValueError(f"invalid frame rate {rate!r}")
    return 1 / Fraction(rate).limit_denominator(1001)


def fps_of(frame_duration: Fraction) -> float:
    return round(float(1 / frame_duration), 3)


def snap(t: Fraction, frame_duration: Fraction) -> Fraction:
    """Round a time to the nearest whole frame."""
    return round(Fraction(t) / frame_duration) * frame_duration


def to_timecode(t: Fraction, frame_duration: Fraction, drop_frame: bool = False) -> str:
    """Format a time as HH:MM:SS:FF (or HH:MM:SS;FF for drop frame)."""
    nominal = round(1 / frame_duration)
    frames = int(round(Fraction(t) / frame_duration))
    sign = "-" if frames < 0 else ""
    frames = abs(frames)
    if drop_frame and nominal in (30, 60):
        drop = 2 if nominal == 30 else 4
        per_10min = nominal * 600 - drop * 9
        per_min = nominal * 60 - drop
        tens, rem = divmod(frames, per_10min)
        if rem > drop:
            frames += drop * 9 * tens + drop * ((rem - drop) // per_min)
        else:
            frames += drop * 9 * tens
        sep = ";"
    else:
        sep = ":"
    ff = frames % nominal
    total_s = frames // nominal
    return f"{sign}{total_s // 3600:02d}:{total_s // 60 % 60:02d}:{total_s % 60:02d}{sep}{ff:02d}"


_TC = re.compile(r"^(\d+):(\d{1,2}):(\d{1,2})[:;.](\d{1,3})$")
_CLOCK = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)$")


def parse_user_time(value: float | int | str, frame_duration: Fraction) -> Fraction:
    """Parse a time given by a person or model.

    Accepts seconds (12.5 or "12.5"), timecode "HH:MM:SS:FF", clock time
    "MM:SS(.sss)" / "HH:MM:SS(.sss)", or an FCPXML time "1001/30000s".
    """
    if isinstance(value, (int, float)):
        return Fraction(value).limit_denominator(1_000_000)
    v = value.strip()
    if v.endswith("s") and re.match(r"^[\d/]+s$", v):
        return parse_time(v)
    m = _TC.match(v)
    if m and v.count(":") + v.count(";") >= 3:
        h, mi, s, f = (int(x) for x in m.groups())
        nominal = round(1 / frame_duration)
        return (h * 3600 + mi * 60 + s) * Fraction(nominal) * frame_duration + f * frame_duration
    m = _CLOCK.match(v)
    if m:
        h, mi, s = m.groups()
        return (int(h or 0) * 3600 + int(mi) * 60) + Fraction(s).limit_denominator(1_000_000)
    return Fraction(v).limit_denominator(1_000_000)
