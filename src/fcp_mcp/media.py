"""Media inspection with ffprobe."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from .timecode import fmt_time, fps_of, frame_duration_for_rate, parse_user_time

STILL_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".heic", ".bmp", ".gif", ".psd", ".webp"}


class MediaError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: Path
    duration: Fraction  # seconds; 0 for stills
    has_video: bool
    has_audio: bool
    is_still: bool = False
    width: int | None = None
    height: int | None = None
    frame_duration: Fraction | None = None
    timecode_start: Fraction = Fraction(0)
    timecode: str | None = None
    audio_channels: int | None = None
    audio_rate: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "duration_seconds": round(float(self.duration), 4),
            "duration_fcpxml": fmt_time(self.duration),
            "has_video": self.has_video,
            "has_audio": self.has_audio,
            "is_still": self.is_still,
            "width": self.width,
            "height": self.height,
            "fps": fps_of(self.frame_duration) if self.frame_duration else None,
            "frame_duration": fmt_time(self.frame_duration) if self.frame_duration else None,
            "timecode": self.timecode,
            "audio_channels": self.audio_channels,
            "audio_rate": self.audio_rate,
            "video_codec": self.video_codec,
            "audio_codec": self.audio_codec,
            **self.extra,
        }


def whole_frames(duration: Fraction, frame: Fraction) -> int:
    """Frames in `duration`, counting a final frame that is short only by rounding (< 1% of a frame)."""
    n = duration / frame
    return round(n) if abs(n - round(n)) < Fraction(1, 100) else int(n)


def _ffprobe() -> str:
    exe = shutil.which("ffprobe") or next(
        (p for p in ("/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe") if Path(p).exists()), None
    )
    if not exe:
        raise MediaError("ffprobe not found. Install it with `brew install ffmpeg`.")
    return exe


def probe(path: str | Path) -> MediaInfo:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise MediaError(f"File not found: {p}")
    proc = subprocess.run(
        [_ffprobe(), "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(p)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise MediaError(f"ffprobe could not read {p.name}: {proc.stderr.strip()}")
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video = next(
        (s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    is_still = p.suffix.lower() in STILL_EXTENSIONS or (
        video is not None and audio is None and fmt.get("format_name", "").endswith("_pipe")
    )

    info = MediaInfo(path=p, duration=Fraction(0), has_video=video is not None, has_audio=audio is not None)
    info.is_still = is_still

    if video:
        info.width = video.get("width")
        info.height = video.get("height")
        info.video_codec = video.get("codec_name")
        if not is_still:
            rate = video.get("r_frame_rate") or video.get("avg_frame_rate")
            if rate and rate not in ("0/0", "0/1"):
                info.frame_duration = frame_duration_for_rate(rate)
    if audio:
        info.audio_channels = audio.get("channels")
        info.audio_codec = audio.get("codec_name")
        if audio.get("sample_rate"):
            info.audio_rate = int(audio["sample_rate"])

    if not is_still:
        raw = fmt.get("duration") or (video or audio or {}).get("duration")
        if raw:
            info.duration = Fraction(raw).limit_denominator(1_000_000)
            if info.frame_duration:
                # Whole frames only; FCP rejects assets whose duration is off the frame grid. ffprobe
                # prints 6 decimals, so 122 frames at 24 fps reads 5.083333s: snap to a frame that
                # close instead of flooring it to 121.
                info.duration = whole_frames(info.duration, info.frame_duration) * info.frame_duration

    tc = (video or {}).get("tags", {}).get("timecode") or fmt.get("tags", {}).get("timecode")
    if tc and info.frame_duration:
        info.timecode = tc
        try:
            info.timecode_start = parse_user_time(tc.replace(";", ":"), info.frame_duration)
        except ValueError:
            pass
    return info
