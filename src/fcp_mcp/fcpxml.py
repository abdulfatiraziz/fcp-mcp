"""Build, read, edit and validate FCPXML, Final Cut Pro's interchange format.

Final Cut Pro's AppleScript dictionary is read-only, so FCPXML is the way to
create timelines: write a document here, then open it in FCP to import it.
"""

from __future__ import annotations

import re
import struct
import subprocess
import tempfile
import zlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, Field

from .media import MediaInfo, probe
from .timecode import fmt_time, fps_of, frame_duration_for_rate, parse_time, parse_user_time, snap, to_timecode

FCP_APP_CANDIDATES = (
    Path("/Applications/Final Cut Pro Creator Studio.app"),
    Path("/Applications/Final Cut Pro.app"),
)
DTD_SUBPATH = "Contents/Frameworks/Interchange.framework/Versions/A/Resources"
DEFAULT_OUTPUT_DIR = Path.home() / "Movies" / "fcp-mcp"
BASIC_TITLE_UID = ".../Titles.localized/Bumper:Opener.localized/Basic Title.localized/Basic Title.moti"
REC709 = "1-1-1 (Rec. 709)"

TimeValue = float | str
TIME_HELP = 'Seconds (12.5), timecode ("00:00:12:15") or clock time ("0:12.5").'


# ---------------------------------------------------------------- versions ---


def fcp_app_path() -> Path | None:
    return next((p for p in FCP_APP_CANDIDATES if p.exists()), None)


def supported_versions() -> list[str]:
    app = fcp_app_path()
    if not app:
        return []
    found = []
    for dtd in (app / DTD_SUBPATH).glob("FCPXMLv*.dtd"):
        m = re.match(r"FCPXMLv(\d+)_(\d+)\.dtd", dtd.name)
        if m:
            found.append((int(m.group(1)), int(m.group(2))))
    return [f"{a}.{b}" for a, b in sorted(found)]


def default_version() -> str:
    versions = supported_versions()
    return versions[-1] if versions else "1.11"


def dtd_path(version: str) -> Path | None:
    app = fcp_app_path()
    if not app:
        return None
    p = app / DTD_SUBPATH / f"FCPXMLv{version.replace('.', '_')}.dtd"
    return p if p.exists() else None


def validate_file(path: Path) -> tuple[bool | None, list[str]]:
    """Validate against the DTD shipped inside Final Cut Pro.

    Returns (None, [reason]) when validation could not run.
    """
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        return False, [f"XML parse error: {e}"]
    version = root.get("version", "")
    dtd = dtd_path(version)
    if not dtd:
        return None, [f"No DTD for FCPXML {version} in the installed Final Cut Pro."]
    # A URI, not a path: xmllint cannot load a DTD from a path with spaces in it.
    proc = subprocess.run(
        ["xmllint", "--noout", "--dtdvalid", dtd.as_uri(), str(path)], capture_output=True, text=True, timeout=60
    )
    if proc.returncode == 0:
        return True, []
    errors = [line for line in proc.stderr.splitlines() if line.strip() and not line.startswith("Document ")]
    return False, errors[:30]


def validate_xml(xml: str) -> tuple[bool | None, list[str]]:
    with tempfile.NamedTemporaryFile("w", suffix=".fcpxml", delete=False, encoding="utf-8") as f:
        f.write(xml)
        tmp = Path(f.name)
    try:
        return validate_file(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def serialize(root: ET.Element) -> str:
    ET.indent(root, "    ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE fcpxml>\n\n' + ET.tostring(root, encoding="unicode") + "\n"


def resolve_document(path: str | Path) -> Path:
    """Accept a .fcpxml file or a .fcpxmld bundle (which holds Info.fcpxml)."""
    p = Path(path).expanduser().resolve()
    if p.is_dir():
        inner = p / "Info.fcpxml"
        if not inner.exists():
            raise FileNotFoundError(f"{p} is a folder without Info.fcpxml inside")
        return inner
    if not p.exists():
        raise FileNotFoundError(f"File not found: {p}")
    return p


# ------------------------------------------------------------------ inputs ---


class TitleStyle(BaseModel):
    font: str = "Helvetica"
    font_face: str | None = Field(None, description='Weight/style within the font, e.g. "Bold", "Medium", "Italic".')
    font_size: float = Field(63, description="Size in points; roughly pixels on a 1080p frame.")
    color: str = Field("#FFFFFF", description="Hex colour like #FFFFFF.")
    bold: bool = False
    alignment: Literal["left", "center", "right"] = "center"


# FCP's blend mode numbers (FCPXML adjust-blend "mode").
BLEND_MODES = {
    "normal": 0, "subtract": 2, "darken": 3, "multiply": 4, "color burn": 5, "linear burn": 6, "add": 8,
    "lighten": 9, "screen": 10, "color dodge": 11, "linear dodge": 12, "overlay": 14, "soft light": 15,
    "hard light": 16, "vivid light": 17, "linear light": 18, "pin light": 19, "hard mix": 20, "difference": 22,
    "exclusion": 23, "stencil alpha": 25, "stencil luma": 26, "silhouette alpha": 27, "silhouette luma": 28,
    "behind": 29,
}  # fmt: skip
BlendMode = Literal[
    "normal", "subtract", "darken", "multiply", "color burn", "linear burn", "add", "lighten", "screen",
    "color dodge", "linear dodge", "overlay", "soft light", "hard light", "vivid light", "linear light",
    "pin light", "hard mix", "difference", "exclusion", "stencil alpha", "stencil luma", "silhouette alpha",
    "silhouette luma", "behind",
]  # fmt: skip


class Box(BaseModel):
    """A region of the clip's own frame, as fractions measured from its top-left corner."""

    x0: float = Field(0, ge=0, le=1)
    y0: float = Field(0, ge=0, le=1)
    x1: float = Field(1, ge=0, le=1)
    y1: float = Field(1, ge=0, le=1)


class AdjustKey(BaseModel):
    """Values at one moment. Properties left out keep animating between their other keyframes."""

    time: TimeValue = Field(..., description="From the start of this item on the timeline. " + TIME_HELP)
    opacity: float | None = Field(None, ge=0, le=1)
    crop: Box | None = Field(None, description="Visible part of the frame at this moment (reveals, wipes).")
    place: tuple[float, float] | None = Field(None, description="Where the crop's centre sits, as in Adjust.place.")
    scale: float | None = None


class Adjust(BaseModel):
    """Compositing for a video clip or title: crop, move, scale, opacity and blend mode, optionally keyframed."""

    crop: Box | None = Field(None, description="Show only this part of the clip's frame.")
    place: tuple[float, float] | None = Field(
        None, description="Move the clip so the centre of `crop` lands at this point of the frame (fractions, 0–1)."
    )
    scale: float | None = Field(None, description="1 = original size.")
    rotation: float | None = Field(None, description="Degrees, counter-clockwise.")
    opacity: float | None = Field(None, ge=0, le=1)
    blend_mode: BlendMode | None = None
    keyframes: list[AdjustKey] | None = None
    hold: bool | list[Literal["opacity", "crop", "place", "scale"]] = Field(
        False,
        description="Jump between keyframe values instead of easing (flicker, step reveals): true for all "
        'properties, or a list such as ["opacity"].',
    )


class StoryItem(BaseModel):
    """One item in the primary storyline, in playback order."""

    path: str | None = Field(None, description="Media file (video, audio or still image). Omit for a gap or title.")
    text: str | None = Field(None, description="Make this item a Basic Title with this text (no path).")
    duration: TimeValue | None = Field(
        None, description="Length on the timeline. Defaults to the rest of the media, 5s for stills and titles. " + TIME_HELP
    )
    source_in: TimeValue = Field(0, description="Where to start inside the source media. " + TIME_HELP)
    source_out: TimeValue | None = Field(None, description="Alternative to duration: where to stop in the source.")
    name: str | None = None
    tracks: Literal["all", "video", "audio"] = Field("all", description="Use only the video or audio of the media.")
    audio_role: str | None = Field(
        None, description='Audio role, e.g. "dialogue", "music", "effects" (FCP defaults to dialogue).'
    )
    volume_db: float | None = Field(None, description="Clip volume in dB (0 = unchanged, -6 = half as loud).")
    style: TitleStyle | None = None
    adjust: Adjust | None = Field(None, description="Crop, position, scale, opacity, blend mode and keyframes.")
    conform: Literal["fit", "fill", "none"] | None = Field(
        None,
        description="How a clip of another shape sits in the frame: fit (FCP default, bars), fill (scale up and "
        "crop, e.g. 3:4 phone/glasses footage in a 9:16 timeline), none (native size).",
    )


class ConnectedItem(StoryItem):
    """A clip or title connected above (lane > 0) or below (lane < 0) the primary storyline."""

    at: TimeValue = Field(..., description="Timeline position where it starts. " + TIME_HELP)
    lane: int = Field(1, description="1, 2… above the storyline (B-roll, titles); -1, -2… below (music, VO).")


class MarkerSpec(BaseModel):
    at: TimeValue = Field(..., description="Timeline position. " + TIME_HELP)
    name: str
    kind: Literal["standard", "todo", "chapter"] = "standard"
    note: str | None = None
    done: bool = Field(False, description="For to-do markers: mark as completed.")


# ----------------------------------------------------------------- builder ---


@dataclass
class _Placed:
    el: ET.Element
    offset: Fraction  # position relative to timeline start
    duration: Fraction
    start: Fraction  # the element's local-time origin
    name: str
    anchors: list[ET.Element] = field(default_factory=list)
    markers: list[ET.Element] = field(default_factory=list)

    @property
    def end(self) -> Fraction:
        return self.offset + self.duration


class TimelineBuilder:
    def __init__(
        self,
        project_name: str,
        event_name: str,
        width: int | None,
        height: int | None,
        fps: float | str | None,
        start_timecode: str | None,
        version: str | None,
    ):
        self.project_name = project_name
        self.event_name = event_name
        self.version = version or default_version()
        self.warnings: list[str] = []
        self._ids = 0
        self._ts = 0
        self.resources = ET.Element("resources")
        self._formats: dict[tuple, str] = {}
        self._assets: dict[Path, tuple[str, MediaInfo]] = {}
        self._title_effect: str | None = None
        self._req = (width, height, fps)
        self._start_tc_text = start_timecode
        self.frame: Fraction = Fraction(1, 30)
        self.tc_start = Fraction(0)

    # resources

    def _next_id(self) -> str:
        self._ids += 1
        return f"r{self._ids}"

    def _format(self, width: int, height: int, frame: Fraction | None, still: bool = False) -> str:
        key = (width, height, frame, still)
        if key in self._formats:
            return self._formats[key]
        fid = self._next_id()
        attrs = {"id": fid}
        if still:
            attrs["name"] = "FFVideoFormatRateUndefined"
        else:
            name = _standard_format_name(width, height, frame)
            if name:
                attrs["name"] = name
            attrs["frameDuration"] = fmt_time(frame)
        attrs.update({"width": str(width), "height": str(height), "colorSpace": REC709})
        # Formats must precede the assets that reference them.
        self.resources.insert(len(self._formats), ET.Element("format", attrs))
        self._formats[key] = fid
        return fid

    def _asset(self, path: str) -> tuple[str, MediaInfo]:
        p = Path(path).expanduser().resolve()
        if p in self._assets:
            return self._assets[p]
        info = probe(p)
        aid = self._next_id()
        attrs = {"id": aid, "name": p.stem, "start": fmt_time(info.timecode_start)}
        if info.is_still:
            attrs["duration"] = "0s"
        elif info.has_video and info.frame_duration:
            attrs["duration"] = fmt_time(info.duration)
        else:
            rate = info.audio_rate or 48000
            attrs["duration"] = fmt_time(Fraction(round(info.duration * rate), rate))
        if info.has_video:
            attrs["hasVideo"] = "1"
            attrs["format"] = self._format(info.width or 1920, info.height or 1080, info.frame_duration, info.is_still)
            attrs["videoSources"] = "1"
        if info.has_audio:
            attrs["hasAudio"] = "1"
            attrs["audioSources"] = "1"
            attrs["audioChannels"] = str(info.audio_channels or 2)
            attrs["audioRate"] = str(info.audio_rate or 48000)
        asset = ET.SubElement(self.resources, "asset", attrs)
        ET.SubElement(asset, "media-rep", {"kind": "original-media", "src": p.as_uri()})
        self._assets[p] = (aid, info)
        return aid, info

    def _title_ref(self) -> str:
        if not self._title_effect:
            self._title_effect = self._next_id()
            ET.SubElement(self.resources, "effect", {"id": self._title_effect, "name": "Basic Title", "uid": BASIC_TITLE_UID})
        return self._title_effect

    # timing

    def setup_format(self, first_paths: list[str]) -> str:
        width, height, fps = self._req
        frame = frame_duration_for_rate(fps) if fps else None
        if not (width and height and frame):
            for path in first_paths:
                _, info = self._asset(path)
                if info.has_video and not info.is_still and info.frame_duration:
                    width = width or info.width
                    height = height or info.height
                    frame = frame or info.frame_duration
                    break
        width, height, frame = width or 1920, height or 1080, frame or Fraction(1, 30)
        self.frame = frame
        self.size = (width, height)
        if self._start_tc_text:
            self.tc_start = snap(parse_user_time(self._start_tc_text, frame), frame)
        return self._format(width, height, frame)

    def t(self, value: TimeValue) -> Fraction:
        return snap(parse_user_time(value, self.frame), self.frame)

    # story elements

    def make_item(self, item: StoryItem, offset_rel: Fraction, lane: int | None) -> _Placed:
        """Create the element for a story item; offset is set by the caller."""
        if item.path and item.text:
            raise ValueError("An item can have a path or text, not both.")
        common = {} if lane is None else {"lane": str(lane)}
        if item.text is not None:
            dur = self.t(item.duration) if item.duration is not None else self.t(5)
            name = item.name or item.text[:40]
            el = ET.Element("title", {**common, "ref": self._title_ref(), "name": name, "start": "0s", "duration": fmt_time(dur)})
            self._fill_title(el, item.text, item.style or TitleStyle())
            self._apply_adjust(el, item.adjust, Fraction(0), self.size, item.conform)
            return _Placed(el, offset_rel, dur, Fraction(0), name)
        if not item.path:
            if item.duration is None:
                raise ValueError("A gap (item with no path or text) needs a duration.")
            dur = self.t(item.duration)
            el = ET.Element("gap", {"name": item.name or "Gap", "start": "0s", "duration": fmt_time(dur)})
            if lane is not None:
                raise ValueError("Gaps can only go in the primary storyline.")
            if item.adjust:
                raise ValueError("Gaps cannot have an adjust.")
            return _Placed(el, offset_rel, dur, Fraction(0), "Gap")

        aid, info = self._asset(item.path)
        name = item.name or info.path.stem
        grid = info.frame_duration or self.frame
        src_in = snap(parse_user_time(item.source_in, grid), grid)
        if info.is_still:
            dur = self.t(item.duration) if item.duration is not None else self.t(5)
            el = ET.Element("video", {**common, "ref": aid, "name": name, "start": "0s", "duration": fmt_time(dur)})
            self._apply_adjust(el, item.adjust, Fraction(0), (info.width or 1920, info.height or 1080), item.conform)
            return _Placed(el, offset_rel, dur, Fraction(0), name)

        available = info.duration - src_in
        if available <= 0:
            raise ValueError(f"source_in is past the end of {info.path.name} ({float(info.duration):.2f}s).")
        if item.duration is not None:
            dur = self.t(item.duration)
        elif item.source_out is not None:
            dur = snap(parse_user_time(item.source_out, grid) - src_in, self.frame)
        else:
            dur = int(available / self.frame) * self.frame
        if dur > available:
            clamped = int(available / self.frame) * self.frame
            self.warnings.append(
                f"{info.path.name}: asked for {float(dur):.2f}s from {float(src_in):.2f}s but only "
                f"{float(available):.2f}s remain; trimmed to {float(clamped):.2f}s."
            )
            dur = clamped
        if dur <= 0:
            raise ValueError(f"{info.path.name}: duration must be at least one frame.")
        start = info.timecode_start + src_in
        attrs = {**common, "ref": aid, "name": name, "start": fmt_time(start), "duration": fmt_time(dur)}
        if info.has_video:
            attrs["format"] = self._format(info.width or 1920, info.height or 1080, info.frame_duration, False)
            attrs["tcFormat"] = "NDF"
        if item.tracks != "all":
            if item.tracks == "audio" and not info.has_audio or item.tracks == "video" and not info.has_video:
                raise ValueError(f"{info.path.name} has no {item.tracks}.")
            attrs["srcEnable"] = item.tracks
        if item.audio_role and info.has_audio:
            attrs["audioRole"] = item.audio_role
        el = ET.Element("asset-clip", attrs)
        if item.adjust or item.conform:
            if not info.has_video:
                raise ValueError(f"{info.path.name} has no video to adjust.")
            self._apply_adjust(el, item.adjust, start, (info.width or 1920, info.height or 1080), item.conform)
        if item.volume_db is not None:
            if not info.has_audio:
                raise ValueError(f"{info.path.name} has no audio to set a volume on.")
            # DTD: audio intrinsics follow the video ones and come before anchored clips.
            ET.SubElement(el, "adjust-volume", {"amount": f"{item.volume_db:g}dB"})
        return _Placed(el, offset_rel, dur, start, name)

    def _apply_adjust(
        self,
        el: ET.Element,
        adj: Adjust | None,
        local_start: Fraction,
        clip_size: tuple[int, int],
        conform: str | None = None,
    ) -> None:
        """Append adjust-crop / adjust-conform / adjust-transform / adjust-blend (DTD order) for an item.

        FCPXML units: crop edges and position are percentages of the frame height; keyframe
        times are in the item's local time (its `start` plus the offset into the item).
        """
        if adj is None and not conform:
            return
        adj = adj or Adjust()
        cw, ch = clip_size
        sw, sh = self.size
        # How FCP scales the clip into the frame before any transform: "fit" is its default.
        if conform == "fill":
            fit = max(Fraction(sw, cw), Fraction(sh, ch))
        elif conform == "none":
            fit = Fraction(1)
        else:
            fit = min(Fraction(sw, cw), Fraction(sh, ch))
        keys = sorted(adj.keyframes or [], key=lambda k: self.t(k.time))
        base_crop = adj.crop or Box()

        def crop_pct(box: Box) -> dict[str, float]:
            if not (box.x0 < box.x1 and box.y0 < box.y1):
                raise ValueError("A crop box needs x0 < x1 and y0 < y1.")
            aspect = cw / ch
            return {"left": box.x0 * aspect * 100, "top": box.y0 * 100,
                    "right": (1 - box.x1) * aspect * 100, "bottom": (1 - box.y1) * 100}

        def position(place: tuple[float, float] | None, scale: float) -> tuple[float, float]:
            if place is None:
                return (0.0, 0.0)
            # Offset (in timeline pixels) that moves the crop's centre to `place`; scaling is about the frame centre.
            rx = ((base_crop.x0 + base_crop.x1) / 2 - 0.5) * cw * float(fit)
            ry = (0.5 - (base_crop.y0 + base_crop.y1) / 2) * ch * float(fit)
            tx, ty = (place[0] - 0.5) * sw, (0.5 - place[1]) * sh
            return ((tx - scale * rx) / sh * 100, (ty - scale * ry) / sh * 100)

        def track(name: str) -> list[tuple[Fraction, object]]:
            points = [(local_start + self.t(k.time), getattr(k, name)) for k in keys if getattr(k, name) is not None]
            held_prop = adj.hold is True or (isinstance(adj.hold, list) and name in adj.hold)
            if held_prop and len(points) > 1:
                held = []
                for (t0, v0), (t1, _) in zip(points, points[1:], strict=False):
                    held.append((t0, v0))
                    if t1 - self.frame > t0:
                        held.append((t1 - self.frame, v0))
                points = held + [points[-1]]
            return points

        def animate(parent: ET.Element, name: str, points, fmt) -> None:
            if len(points) < 2 or len({fmt(v) for _, v in points}) == 1:
                return
            anim = ET.SubElement(ET.SubElement(parent, "param", {"name": name}), "keyframeAnimation")
            for t, v in points:
                # Linear curves: FCP's default smooth curve would round off flicker and step reveals.
                ET.SubElement(anim, "keyframe", {"time": fmt_time(t), "value": fmt(v), "curve": "linear"})

        num = lambda v: f"{v:.4f}".rstrip("0").rstrip(".")  # noqa: E731
        pair = lambda v: f"{num(v[0])} {num(v[1])}"  # noqa: E731

        crop_keys = track("crop")
        if adj.crop or crop_keys:
            first = crop_pct(crop_keys[0][1] if crop_keys else base_crop)
            rect = ET.SubElement(ET.SubElement(el, "adjust-crop", {"mode": "crop"}), "crop-rect", {k: num(v) for k, v in first.items()})
            for edge in ("left", "top", "right", "bottom"):
                animate(rect, edge, [(t, crop_pct(b)[edge]) for t, b in crop_keys], num)

        if conform:
            ET.SubElement(el, "adjust-conform", {"type": conform})

        scale0 = adj.scale if adj.scale is not None else 1.0
        scale_keys = track("scale")
        place_keys = track("place")
        if adj.place or adj.scale is not None or adj.rotation is not None or scale_keys or place_keys:
            times = sorted({t for t, _ in scale_keys} | {t for t, _ in place_keys})

            def value_at(points, t, default):
                v = default
                for pt, pv in points:
                    if pt <= t:
                        v = pv
                return v

            pos_keys = [(t, position(value_at(place_keys, t, adj.place), value_at(scale_keys, t, scale0))) for t in times]
            attrs = {"position": pair(pos_keys[0][1] if pos_keys else position(adj.place, scale0)),
                     "scale": pair((scale_keys[0][1],) * 2 if scale_keys else (scale0, scale0))}
            if adj.rotation is not None:
                attrs["rotation"] = num(adj.rotation)
            transform = ET.SubElement(el, "adjust-transform", attrs)
            animate(transform, "position", pos_keys, pair)
            animate(transform, "scale", [(t, (v, v)) for t, v in scale_keys], pair)

        opacity_keys = track("opacity")
        if adj.opacity is not None or adj.blend_mode or opacity_keys:
            amount = opacity_keys[0][1] if opacity_keys else (adj.opacity if adj.opacity is not None else 1.0)
            attrs = {"amount": num(amount)}
            if adj.blend_mode:
                attrs["mode"] = f"{BLEND_MODES[adj.blend_mode]} ({adj.blend_mode.title()})"
            blend = ET.SubElement(el, "adjust-blend", attrs)
            animate(blend, "amount", opacity_keys, num)

    def _fill_title(self, el: ET.Element, text: str, style: TitleStyle) -> None:
        self._ts += 1
        ts_id = f"ts{self._ts}"
        text_el = ET.SubElement(el, "text")
        ET.SubElement(text_el, "text-style", {"ref": ts_id}).text = text
        style_def = ET.SubElement(el, "text-style-def", {"id": ts_id})
        attrs = {
            "font": style.font,
            **({"fontFace": style.font_face} if style.font_face else {}),
            "fontSize": f"{style.font_size:g}",
            "fontColor": _hex_to_rgba(style.color),
            "alignment": style.alignment,
        }
        if style.bold:
            attrs["bold"] = "1"
        ET.SubElement(style_def, "text-style", attrs)


def solid_png(path: Path, width: int, height: int, color: str) -> Path:
    """Write a one-colour PNG (used as a background layer under clips that don't fill the frame)."""
    rgb = bytes(round(float(v) * 255) for v in _hex_to_rgba(color).split()[:3])
    row = b"\x00" + rgb * width
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(row * height, 9)) + chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return path


def build_timeline(
    project_name: str,
    primary: list[StoryItem],
    connected: list[ConnectedItem] | None = None,
    markers: list[MarkerSpec] | None = None,
    event_name: str = "fcp-mcp",
    width: int | None = None,
    height: int | None = None,
    fps: float | str | None = None,
    start_timecode: str | None = None,
    version: str | None = None,
    background: str | None = None,
    asset_dir: str | Path | None = None,
) -> tuple[str, dict]:
    """Return (fcpxml_text, summary) for a new project.

    background: hex colour laid under the clips (below every lane) from the start to the end of the
    last non-gap storyline item, e.g. white around 3:4 footage fitted into a 16:9 frame. Trailing gaps
    (an end card) stay black. The colour is written as a PNG in asset_dir.
    """
    connected = list(connected or [])
    markers = markers or []
    b = TimelineBuilder(project_name, event_name, width, height, fps, start_timecode, version)
    seq_format = b.setup_format([i.path for i in primary + connected if i.path])

    placed: list[_Placed] = []
    cursor = Fraction(0)
    for item in primary:
        p = b.make_item(item, cursor, None)
        placed.append(p)
        cursor += p.duration

    pending: list[tuple[Fraction, _Placed]] = []
    needed_end = cursor
    for c in connected:
        if c.lane == 0:
            raise ValueError("Connected items need a non-zero lane (use primary for the main storyline).")
        at = b.t(c.at)
        if at < 0:
            raise ValueError("Connected item position cannot be negative.")
        p = b.make_item(c, at, c.lane)
        if c.duration is None and c.source_out is None and c.text is None and at < cursor < p.end:
            # Without an explicit length, connected media (e.g. music) stops where the storyline ends.
            p.duration = cursor - at
            p.el.set("duration", fmt_time(p.duration))
        pending.append((at, p))
        needed_end = max(needed_end, at + b.frame)
    marker_times = []
    for m in markers:
        at = b.t(m.at)
        if at < 0:
            raise ValueError("Marker position cannot be negative.")
        marker_times.append((at, m))
        needed_end = max(needed_end, at + b.frame)

    if background:
        content_end = max((p.end for p in placed if p.el.tag != "gap"), default=cursor)
        w, h = b.size
        png = solid_png(Path(asset_dir or DEFAULT_OUTPUT_DIR) / f"background {background.lstrip('#')} {w}x{h}.png", w, h, background)
        lowest = min([c.lane for c in connected if c.lane < 0], default=0)
        bg = ConnectedItem(path=str(png), at=0, duration=fmt_time(content_end), lane=lowest - 1, name="Background")
        p = b.make_item(bg, Fraction(0), bg.lane)
        pending.insert(0, (Fraction(0), p))

    if needed_end > cursor:
        # Connected clips and markers must hang off a storyline element, so pad with a gap.
        gap = b.make_item(StoryItem(duration=fmt_time(needed_end - cursor), name="Gap"), cursor, None)
        placed.append(gap)
        cursor = needed_end
    if not placed:
        raise ValueError("The timeline is empty: give at least one primary item.")

    def host(at: Fraction) -> _Placed:
        for p in placed:
            if p.offset <= at < p.end:
                return p
        return placed[-1]

    for at, p in pending:
        parent = host(at)
        p.el.set("offset", fmt_time(parent.start + (at - parent.offset)))
        parent.anchors.append(p.el)
    for at, m in marker_times:
        parent = host(at)
        local = parent.start + (at - parent.offset)
        tag = "chapter-marker" if m.kind == "chapter" else "marker"
        attrs = {"start": fmt_time(local), "duration": fmt_time(b.frame), "value": m.name}
        if m.kind == "todo":
            attrs["completed"] = "1" if m.done else "0"
        if m.note:
            attrs["note"] = m.note
        if m.kind == "chapter":
            attrs["posterOffset"] = "0s"
        parent.markers.append(ET.Element(tag, attrs))

    root = ET.Element("fcpxml", {"version": b.version})
    ET.SubElement(
        ET.SubElement(root, "import-options"), "option", {"key": "suppress warnings", "value": "1"}
    )
    root.append(b.resources)
    event = ET.SubElement(root, "event", {"name": event_name})
    project = ET.SubElement(event, "project", {"name": project_name})
    seq = ET.SubElement(
        project,
        "sequence",
        {
            "format": seq_format,
            "duration": fmt_time(cursor),
            "tcStart": fmt_time(b.tc_start),
            "tcFormat": "NDF",
            "audioLayout": "stereo",
            "audioRate": "48k",
        },
    )
    spine = ET.SubElement(seq, "spine")
    for p in placed:
        p.el.set("offset", fmt_time(b.tc_start + p.offset))
        # Keep attribute order readable: ref/offset/name first.
        attrs = dict(p.el.attrib)
        p.el.attrib.clear()
        for k in ("ref", "lane", "offset", "name"):
            if k in attrs:
                p.el.set(k, attrs.pop(k))
        p.el.attrib.update(attrs)
        p.el.extend(p.anchors)  # DTD order: anchored items before markers
        p.el.extend(p.markers)
        spine.append(p.el)

    summary = {
        "project": project_name,
        "event": event_name,
        "fcpxml_version": b.version,
        "format": _describe_format(b.resources, seq_format),
        "duration_seconds": round(float(cursor), 3),
        "duration_timecode": to_timecode(cursor, b.frame),
        "primary_items": len(primary),
        "connected_items": len(connected),
        "markers": len(markers),
        "warnings": b.warnings,
    }
    return serialize(root), summary


def _standard_format_name(width: int, height: int, frame: Fraction | None) -> str | None:
    if not frame:
        return None
    rates = {
        Fraction(1001, 24000): "2398", Fraction(1, 24): "24", Fraction(1, 25): "25",
        Fraction(1001, 30000): "2997", Fraction(1, 30): "30", Fraction(1, 50): "50",
        Fraction(1001, 60000): "5994", Fraction(1, 60): "60",
    }  # fmt: skip
    rate = rates.get(frame)
    if not rate:
        return None
    if (width, height) == (1920, 1080):
        return f"FFVideoFormat1080p{rate}"
    if (width, height) == (1280, 720):
        return f"FFVideoFormat720p{rate}"
    if (width, height) in ((3840, 2160), (4096, 2160)):
        return f"FFVideoFormat{width}x{height}p{rate}"
    return None


def _describe_format(resources: ET.Element, fid: str) -> dict:
    f = resources.find(f"format[@id='{fid}']")
    if f is None:
        return {}
    frame = parse_time(f.get("frameDuration"), Fraction(0))
    return {
        "width": int(f.get("width", 0)),
        "height": int(f.get("height", 0)),
        "fps": fps_of(frame) if frame else None,
        "name": f.get("name"),
    }


def _hex_to_rgba(color: str) -> str:
    c = color.strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if not re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", c):
        raise ValueError(f"Colour must be hex like #FFFFFF, got {color!r}")
    parts = [int(c[i : i + 2], 16) / 255 for i in range(0, len(c), 2)]
    if len(parts) == 3:
        parts.append(1.0)
    return " ".join(f"{v:.4g}" for v in parts)


# ------------------------------------------------------------------ reader ---

STORY_TAGS = {
    "asset-clip", "clip", "video", "audio", "title", "gap", "ref-clip", "sync-clip",
    "mc-clip", "audition", "live-drawing", "caption", "transition",
}  # fmt: skip
MARKER_TAGS = {"marker", "chapter-marker"}
# Children that must come after markers inside a story element (DTD order).
AFTER_MARKERS = {
    "audio-channel-source", "audio-role-source", "filter-video", "filter-video-mask",
    "filter-audio", "metadata", "reserved", "sync-source", "mc-source",
}  # fmt: skip


@dataclass
class _Doc:
    root: ET.Element
    assets: dict[str, ET.Element]
    formats: dict[str, ET.Element]
    effects: dict[str, ET.Element]
    media: dict[str, ET.Element]


def _load(path: Path) -> _Doc:
    root = ET.parse(path).getroot()
    if root.tag != "fcpxml":
        raise ValueError(f"{path.name} is not an FCPXML document")
    res = root.find("resources")
    by_tag: dict[str, dict[str, ET.Element]] = {"asset": {}, "format": {}, "effect": {}, "media": {}}
    if res is not None:
        for el in res:
            if el.tag in by_tag and el.get("id"):
                by_tag[el.tag][el.get("id")] = el
    return _Doc(root, by_tag["asset"], by_tag["format"], by_tag["effect"], by_tag["media"])


def _src_path(asset: ET.Element) -> str | None:
    rep = asset.find("media-rep")
    if rep is None or not rep.get("src"):
        return None
    url = urlparse(rep.get("src"))
    return unquote(url.path) if url.scheme == "file" else rep.get("src")


def _parents(root: ET.Element) -> dict[ET.Element, ET.Element]:
    return {child: parent for parent in root.iter() for child in parent}


class _Reader:
    def __init__(self, doc: _Doc, frame: Fraction, tc_start: Fraction, drop: bool):
        self.doc = doc
        self.frame = frame
        self.tc_start = tc_start
        self.drop = drop
        self.items: list[dict] = []
        self.markers: list[dict] = []

    def tc(self, rel: Fraction) -> str:
        return to_timecode(self.tc_start + rel, self.frame, self.drop)

    def walk_spine(self, spine: ET.Element, to_rel, lane: int) -> None:
        for child in spine:
            if child.tag in STORY_TAGS:
                self.visit(child, to_rel, lane)

    def visit(self, el: ET.Element, to_rel, parent_lane: int) -> None:
        if el.tag == "audition":
            active = next((c for c in el if c.tag in STORY_TAGS), None)
            if active is not None:
                if el.get("offset") and not active.get("offset"):
                    active.set("offset", el.get("offset"))
                self.visit(active, to_rel, parent_lane + int(el.get("lane", 0)))
            return
        lane = parent_lane + int(el.get("lane", 0))
        offset = parse_time(el.get("offset"))
        start = parse_time(el.get("start"))
        duration = parse_time(el.get("duration"))
        asset = None
        ref = el.get("ref")
        if ref and ref in self.doc.assets:
            asset = self.doc.assets[ref]
            if not el.get("duration"):
                duration = parse_time(asset.get("duration"))
        rel_in = to_rel(offset)
        item: dict = {
            "type": el.tag,
            "name": el.get("name") or (asset.get("name") if asset is not None else None),
            "lane": lane,
            "start": round(float(rel_in), 3),
            "end": round(float(rel_in + duration), 3),
            "duration": round(float(duration), 3),
            "timecode": self.tc(rel_in),
        }
        if el.get("enabled") == "0":
            item["enabled"] = False
        if asset is not None:
            item["media"] = _src_path(asset)
            item["source_in"] = round(float(start - parse_time(asset.get("start"))), 3)
            if el.get("srcEnable", "all") != "all":
                item["tracks"] = el.get("srcEnable")
            vol = el.find("adjust-volume")
            if vol is not None and vol.get("amount"):
                item["volume_db"] = float(vol.get("amount").removesuffix("dB"))
        elif ref and ref in self.doc.media:
            item["compound"] = self.doc.media[ref].get("name")
        elif ref and ref in self.doc.effects:
            item["template"] = self.doc.effects[ref].get("name")
        if el.tag in ("title", "caption"):
            text = " / ".join("".join(t.itertext()).strip() for t in el.findall("text"))
            item["text"] = text
        effects = []
        for f in el:
            if f.tag in ("filter-video", "filter-audio"):
                effect = self.doc.effects.get(f.get("ref", ""))
                effects.append(f.get("name") or (effect.get("name") if effect is not None else f.get("ref")))
        if effects:
            item["effects"] = effects
        keywords = [k.get("value") for k in el.findall("keyword") if k.get("value")]
        if keywords:
            item["keywords"] = keywords
        adjust = {}
        for a in el:
            if a.tag == "adjust-blend":
                if a.get("mode"):
                    adjust["blend_mode"] = a.get("mode")
                if a.get("amount", "1") not in ("1", "1.0"):
                    adjust["opacity"] = float(a.get("amount"))
            elif a.tag == "adjust-crop":
                adjust["crop"] = True
            elif a.tag == "adjust-transform":
                adjust["transform"] = {k: v for k, v in a.attrib.items() if k in ("position", "scale", "rotation")}
            if a.tag.startswith("adjust-"):
                animated = sorted({p.get("name") for p in a.iter("param") if p.find("keyframeAnimation") is not None})
                if animated:
                    adjust.setdefault("keyframed", []).extend(f"{a.tag[7:]}.{n}" for n in animated)
        if adjust:
            item["adjust"] = adjust
        self.items.append(item)
        if el.tag == "transition":
            return

        def local_to_rel(t: Fraction, rel_in=rel_in, start=start) -> Fraction:
            return rel_in + (t - start)

        for child in el:
            if child.tag in MARKER_TAGS:
                at = local_to_rel(parse_time(child.get("start")))
                kind = "chapter" if child.tag == "chapter-marker" else ("todo" if child.get("completed") is not None else "standard")
                m = {"kind": kind, "name": child.get("value"), "at": round(float(at), 3), "timecode": self.tc(at), "on": item["name"]}
                if kind == "todo":
                    m["done"] = child.get("completed") == "1"
                if child.get("note"):
                    m["note"] = child.get("note")
                self.markers.append(m)
            elif child.tag == "spine" and child.get("lane"):
                spine_rel = local_to_rel(parse_time(child.get("offset")))
                self.walk_spine(child, lambda t, base=spine_rel: base + t, lane + int(child.get("lane")))
            elif child.tag in STORY_TAGS and child.get("lane"):
                self.visit(child, local_to_rel, lane)


def read_document(path: str | Path, max_items: int = 400) -> dict:
    p = resolve_document(path)
    doc = _load(p)
    parents = _parents(doc.root)
    out: dict = {"file": str(p), "fcpxml_version": doc.root.get("version"), "projects": [], "event_clips": []}

    for project in doc.root.iter("project"):
        seq = project.find("sequence")
        if seq is None:
            continue
        fmt = doc.formats.get(seq.get("format", ""))
        frame = parse_time(fmt.get("frameDuration") if fmt is not None else None, Fraction(1, 30)) or Fraction(1, 30)
        tc_start = parse_time(seq.get("tcStart"))
        reader = _Reader(doc, frame, tc_start, seq.get("tcFormat") == "DF")
        spine = seq.find("spine")
        if spine is not None:
            reader.walk_spine(spine, lambda t, base=tc_start: t - base, 0)
        reader.items.sort(key=lambda i: (i["start"], i["lane"]))
        reader.markers.sort(key=lambda m: m["at"])
        event = parents.get(project)
        duration = parse_time(seq.get("duration"))
        out["projects"].append(
            {
                "name": project.get("name"),
                "event": event.get("name") if event is not None and event.tag == "event" else None,
                "format": {
                    "width": int(fmt.get("width", 0)) if fmt is not None else None,
                    "height": int(fmt.get("height", 0)) if fmt is not None else None,
                    "fps": fps_of(frame),
                    "name": fmt.get("name") if fmt is not None else None,
                },
                "duration_seconds": round(float(duration), 3),
                "duration_timecode": to_timecode(duration, frame),
                "start_timecode": to_timecode(tc_start, frame),
                "item_count": len(reader.items),
                "items": reader.items[:max_items],
                "items_truncated": len(reader.items) > max_items,
                "markers": reader.markers,
            }
        )

    for event in doc.root.iter("event"):
        clips = [c.get("name") for c in event if c.tag in STORY_TAGS and c.tag != "transition"]
        if clips:
            out["event_clips"].append({"event": event.get("name"), "count": len(clips), "clips": clips[:max_items]})
    out["assets"] = [
        {"id": aid, "name": a.get("name"), "path": _src_path(a), "duration": round(float(parse_time(a.get("duration"))), 3)}
        for aid, a in doc.assets.items()
    ][:max_items]
    return out


# ------------------------------------------------------------------ editor ---


def add_markers(
    path: str | Path, markers: list[MarkerSpec], project_name: str | None = None
) -> tuple[str, dict]:
    """Add markers to a project's primary storyline. Returns (fcpxml_text, summary)."""
    p = resolve_document(path)
    doc = _load(p)
    projects = [pr for pr in doc.root.iter("project") if pr.find("sequence") is not None]
    if project_name:
        projects = [pr for pr in projects if pr.get("name") == project_name]
    if not projects:
        raise ValueError(f"No project{' named ' + repr(project_name) if project_name else ''} in {p.name}")
    if len(projects) > 1:
        names = ", ".join(repr(pr.get("name")) for pr in projects)
        raise ValueError(f"Several projects in the file ({names}); pass project_name.")
    project = projects[0]
    seq = project.find("sequence")
    fmt = doc.formats.get(seq.get("format", ""))
    frame = parse_time(fmt.get("frameDuration") if fmt is not None else None, Fraction(1, 30)) or Fraction(1, 30)
    tc_start = parse_time(seq.get("tcStart"))
    spine = seq.find("spine")
    elements = []
    for el in spine if spine is not None else []:
        if el.tag in STORY_TAGS and el.tag != "transition":
            off = parse_time(el.get("offset")) - tc_start
            dur = parse_time(el.get("duration"))
            if not el.get("duration") and el.get("ref") in doc.assets:
                dur = parse_time(doc.assets[el.get("ref")].get("duration"))
            elements.append((off, dur, el))
    if not elements:
        raise ValueError("The project's storyline is empty.")

    added = []
    for m in markers:
        at = snap(parse_user_time(m.at, frame), frame)
        host = next((e for e in elements if e[0] <= at < e[0] + e[1]), None)
        if host is None:
            raise ValueError(f"Marker {m.name!r} at {float(at):.2f}s is outside the storyline.")
        off, _, el = host
        if el.tag == "audition":
            el = next(c for c in el if c.tag in STORY_TAGS)
        local = parse_time(el.get("start")) + (at - off)
        tag = "chapter-marker" if m.kind == "chapter" else "marker"
        attrs = {"start": fmt_time(local), "duration": fmt_time(frame), "value": m.name}
        if m.kind == "todo":
            attrs["completed"] = "1" if m.done else "0"
        if m.note:
            attrs["note"] = m.note
        if m.kind == "chapter":
            attrs["posterOffset"] = "0s"
        marker = ET.Element(tag, attrs)
        idx = next((i for i, c in enumerate(el) if c.tag in AFTER_MARKERS), len(el))
        el.insert(idx, marker)
        added.append({"name": m.name, "kind": m.kind, "at": round(float(at), 3), "on": el.get("name")})
    return serialize(doc.root), {"project": project.get("name"), "added": added}


def write_output(xml: str, output_path: str | None, default_name: str) -> Path:
    if output_path:
        out = Path(output_path).expanduser().resolve()
        if out.suffix != ".fcpxml":
            out = out.with_suffix(".fcpxml")
    else:
        safe = re.sub(r"[^\w\- ]+", "_", default_name).strip() or "timeline"
        out = DEFAULT_OUTPUT_DIR / f"{safe}.fcpxml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(xml, encoding="utf-8")
    return out
