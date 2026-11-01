"""FCPXML round trips: build -> DTD-validate -> read back -> add markers.

Media in tests/media is generated with ffmpeg (see make_media below).
"""

import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from fcp_mcp import fcpxml
from fcp_mcp.fcpxml import ConnectedItem, MarkerSpec, StoryItem
from fcp_mcp.timecode import frame_duration_for_rate, parse_user_time, to_timecode

MEDIA = Path(__file__).parent / "media"
NTSC = Fraction(1001, 30000)


def make_media() -> None:
    MEDIA.mkdir(exist_ok=True)
    ff = ["ffmpeg", "-v", "error", "-y"]
    if not (MEDIA / "clipA.mov").exists():
        subprocess.run(ff + ["-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30000/1001:duration=8",
                             "-f", "lavfi", "-i", "sine=frequency=440:duration=8", "-c:v", "libx264",
                             "-pix_fmt", "yuv420p", "-c:a", "aac", "-timecode", "01:00:00:00", "-shortest",
                             str(MEDIA / "clipA.mov")], check=True)  # fmt: skip
    if not (MEDIA / "clipB.mp4").exists():
        subprocess.run(ff + ["-f", "lavfi", "-i", "smptebars=size=1920x1080:rate=30000/1001:duration=6",
                             "-f", "lavfi", "-i", "sine=frequency=880:duration=6", "-c:v", "libx264",
                             "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(MEDIA / "clipB.mp4")],
                       check=True)  # fmt: skip
    if not (MEDIA / "music.wav").exists():
        subprocess.run(ff + ["-f", "lavfi", "-i", "sine=frequency=220:duration=20", "-ac", "2", "-ar", "48000",
                             str(MEDIA / "music.wav")], check=True)  # fmt: skip
    if not (MEDIA / "still.png").exists():
        subprocess.run(ff + ["-f", "lavfi", "-i", "color=c=navy:size=1920x1080", "-frames:v", "1",
                             str(MEDIA / "still.png")], check=True)  # fmt: skip


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
needs_fcp = pytest.mark.skipif(not fcpxml.supported_versions(), reason="Final Cut Pro not installed")


def test_timecode_helpers():
    assert frame_duration_for_rate(29.97) == NTSC
    assert frame_duration_for_rate("30000/1001") == NTSC
    assert frame_duration_for_rate(25) == Fraction(1, 25)
    assert parse_user_time("00:00:01:15", Fraction(1, 30)) == Fraction(3, 2)
    assert parse_user_time("1:30.5", Fraction(1, 30)) == Fraction(181, 2)
    assert parse_user_time("1001/30000s", NTSC) == NTSC
    assert to_timecode(Fraction(3, 2), Fraction(1, 30)) == "00:00:01:15"
    # 29.97 drop frame skips frame numbers 00 and 01 at each minute except every tenth.
    assert to_timecode(1800 * NTSC, NTSC, drop_frame=True) == "00:01:00;02"


@needs_ffmpeg
@needs_fcp
def test_build_validate_read_and_mark(tmp_path):
    make_media()
    xml, summary = fcpxml.build_timeline(
        project_name="Test Cut",
        primary=[
            StoryItem(path=str(MEDIA / "clipA.mov"), source_in=1, duration=4),
            StoryItem(text="Chapter Two", duration=2),
            StoryItem(path=str(MEDIA / "still.png"), duration=3),
            StoryItem(duration=1),
            StoryItem(path=str(MEDIA / "clipB.mp4"), duration=30),  # longer than the clip: trimmed
        ],
        connected=[
            ConnectedItem(path=str(MEDIA / "music.wav"), at=0, lane=-1, duration=12, audio_role="music"),
            ConnectedItem(text="Lower third", at="00:00:01:00", duration=2, lane=1),
            ConnectedItem(path=str(MEDIA / "clipB.mp4"), at=7, lane=1, duration=1.5, tracks="video"),
        ],
        markers=[
            MarkerSpec(at=0.5, name="Intro"),
            MarkerSpec(at=6.5, name="Fix colour", kind="todo"),
            MarkerSpec(at=25, name="Past the end"),
            MarkerSpec(at=4, name="Part 2", kind="chapter"),
        ],
    )
    out = tmp_path / "test.fcpxml"
    out.write_text(xml)
    valid, errors = fcpxml.validate_file(out)
    assert valid, errors
    assert summary["format"]["fps"] == 29.97
    assert summary["warnings"], "clipB should have been trimmed"

    doc = fcpxml.read_document(out)
    proj = doc["projects"][0]
    assert proj["name"] == "Test Cut"
    primary = [i for i in proj["items"] if i["lane"] == 0]
    assert [i["type"] for i in primary] == ["asset-clip", "title", "video", "gap", "asset-clip", "gap"]
    assert primary[0]["source_in"] == pytest.approx(1, abs=0.04)
    assert primary[1]["start"] == pytest.approx(4, abs=0.04)
    assert primary[1]["text"] == "Chapter Two"
    music = next(i for i in proj["items"] if i["lane"] == -1)
    assert music["start"] == 0 and music["duration"] == pytest.approx(12, abs=0.04)
    lower = next(i for i in proj["items"] if i.get("text") == "Lower third")
    assert lower["start"] == pytest.approx(1, abs=0.04)
    broll = next(i for i in proj["items"] if i["lane"] == 1 and i["type"] == "asset-clip")
    assert broll["start"] == pytest.approx(7, abs=0.04) and broll["tracks"] == "video"
    marks = {m["name"]: m for m in proj["markers"]}
    assert marks["Intro"]["at"] == pytest.approx(0.5, abs=0.04)
    assert marks["Fix colour"]["kind"] == "todo"
    assert marks["Part 2"]["kind"] == "chapter" and marks["Part 2"]["at"] == pytest.approx(4, abs=0.04)
    assert marks["Past the end"]["at"] == pytest.approx(25, abs=0.04)

    marked, info = fcpxml.add_markers(out, [MarkerSpec(at=2, name="Added"), MarkerSpec(at=9, name="Later", kind="todo")])
    out2 = tmp_path / "marked.fcpxml"
    out2.write_text(marked)
    valid, errors = fcpxml.validate_file(out2)
    assert valid, errors
    names = {m["name"]: m for m in fcpxml.read_document(out2)["projects"][0]["markers"]}
    assert names["Added"]["at"] == pytest.approx(2, abs=0.04)
    assert names["Later"]["at"] == pytest.approx(9, abs=0.04)


@needs_ffmpeg
@needs_fcp
def test_start_timecode(tmp_path):
    make_media()
    xml, _ = fcpxml.build_timeline(
        project_name="TC",
        primary=[StoryItem(path=str(MEDIA / "clipB.mp4"))],
        markers=[MarkerSpec(at=2, name="m")],
        start_timecode="01:00:00:00",
        fps=25,
    )
    out = tmp_path / "tc.fcpxml"
    out.write_text(xml)
    assert fcpxml.validate_file(out)[0]
    proj = fcpxml.read_document(out)["projects"][0]
    assert proj["start_timecode"] == "01:00:00:00"
    assert proj["items"][0]["start"] == 0
    assert proj["markers"][0]["at"] == pytest.approx(2, abs=0.04)
    assert proj["markers"][0]["timecode"] == "01:00:02:00"



@needs_ffmpeg
@needs_fcp
def test_composite_adjust(tmp_path):
    """A cropped, placed, blended overlay with held (stepped) keyframes."""
    import xml.etree.ElementTree as ET

    from fcp_mcp.fcpxml import Adjust, AdjustKey, Box

    make_media()
    cut = Adjust(
        crop=Box(x0=0.1, y0=0.1, x1=0.2, y1=0.3),
        place=(0.5, 0.5),
        blend_mode="screen",
        hold=["opacity", "crop"],
        keyframes=[
            AdjustKey(time=0, opacity=0, crop=Box(x0=0.1, y0=0.1, x1=0.2, y1=0.1001)),
            AdjustKey(time="00:00:00:02", opacity=1),
            AdjustKey(time="00:00:00:03", opacity=0),
            AdjustKey(time="00:00:00:04", opacity=1, crop=Box(x0=0.1, y0=0.1, x1=0.2, y1=0.3)),
        ],
    )
    xml, _ = fcpxml.build_timeline(
        project_name="Composite",
        primary=[StoryItem(path=str(MEDIA / "clipA.mov"), duration=3)],
        connected=[ConnectedItem(path=str(MEDIA / "clipB.mp4"), at=1, duration=1, lane=1, adjust=cut)],
    )
    out = tmp_path / "composite.fcpxml"
    out.write_text(xml)
    valid, errors = fcpxml.validate_file(out)
    assert valid, errors

    clip = next(e for e in ET.parse(out).getroot().iter("asset-clip") if e.get("lane") == "1")
    assert [c.tag for c in clip][:3] == ["adjust-crop", "adjust-transform", "adjust-blend"]
    rect = clip.find("adjust-crop/crop-rect")
    assert float(rect.get("left")) == pytest.approx(0.1 * 16 / 9 * 100, abs=0.01)
    assert float(rect.get("bottom")) == pytest.approx(89.99, abs=0.01)  # first keyframe: almost fully cropped
    x, y = (float(v) for v in clip.find("adjust-transform").get("position").split())
    assert (x, y) == (pytest.approx(62.2222, abs=0.001), pytest.approx(-30, abs=0.001))
    blend = clip.find("adjust-blend")
    assert blend.get("mode").startswith("10 ")
    keys = blend.findall("param[@name='amount']/keyframeAnimation/keyframe")
    # Held: a value repeats one frame before the next key (keys already 1 frame apart need no repeat).
    assert [k.get("value") for k in keys] == ["0", "0", "1", "0", "1"]
    assert {k.get("curve") for k in keys} == {"linear"}
    assert keys[0].get("time") == clip.get("start")  # keyframe times are in the clip's local time

    item = next(i for i in fcpxml.read_document(out)["projects"][0]["items"] if i["lane"] == 1)
    assert item["adjust"]["blend_mode"].startswith("10")
    assert "blend.amount" in item["adjust"]["keyframed"] and "crop.bottom" in item["adjust"]["keyframed"]


@needs_ffmpeg
@needs_fcp
def test_vertical_fill_conform(tmp_path):
    """3:4-ish footage filling a 9:16 timeline: adjust-conform sits between crop and transform."""
    import xml.etree.ElementTree as ET

    from fcp_mcp.fcpxml import Adjust, Box

    make_media()
    xml, summary = fcpxml.build_timeline(
        project_name="Vertical",
        width=1080, height=1920, fps=23.976,
        primary=[StoryItem(path=str(MEDIA / "clipA.mov"), duration=2, conform="fill")],
        connected=[ConnectedItem(path=str(MEDIA / "clipB.mp4"), at=0.5, duration=1, lane=1, conform="fill",
                                 adjust=Adjust(crop=Box(x0=0.2, y0=0.1, x1=0.8, y1=0.5)))],
    )
    out = tmp_path / "vertical.fcpxml"
    out.write_text(xml)
    valid, errors = fcpxml.validate_file(out)
    assert valid, errors
    assert summary["format"]["width"] == 1080 and summary["format"]["height"] == 1920
    clips = list(ET.parse(out).getroot().iter("asset-clip"))
    assert clips[0].find("adjust-conform").get("type") == "fill"
    assert [c.tag for c in clips[1]][:2] == ["adjust-crop", "adjust-conform"]


@needs_ffmpeg
@needs_fcp
def test_background_behind_fitted_clips(tmp_path):
    """White behind a fitted clip, under every lane, stopping where the trailing end card starts."""
    import xml.etree.ElementTree as ET

    from fcp_mcp.timecode import parse_time

    make_media()
    xml, _ = fcpxml.build_timeline(
        project_name="Wide",
        width=1440, height=1080, fps=23.976,
        primary=[StoryItem(path=str(MEDIA / "clipA.mov"), duration=2, conform="fit"), StoryItem(duration=0.5)],
        connected=[ConnectedItem(path=str(MEDIA / "music.wav"), at=0, lane=-1)],
        background="#FFFFFF", asset_dir=tmp_path,
    )
    out = tmp_path / "wide.fcpxml"
    out.write_text(xml)
    valid, errors = fcpxml.validate_file(out)
    assert valid, errors
    bg = next(e for e in ET.parse(out).getroot().iter("video") if e.get("name") == "Background")
    assert bg.get("lane") == "-2"
    assert float(parse_time(bg.get("duration"))) == pytest.approx(2.0, abs=0.05)  # not over the end card
    png = next(tmp_path.glob("background FFFFFF 1440x1080.png"))
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_whole_frames_tolerates_rounded_durations():
    from fractions import Fraction

    from fcp_mcp.media import whole_frames

    assert whole_frames(Fraction("5.083333"), Fraction(1, 24)) == 122  # 122 frames printed with 6 decimals
    assert whole_frames(Fraction("5.06"), Fraction(1, 24)) == 121  # a genuinely partial last frame
    assert whole_frames(Fraction(10010, 24000) * 12, Fraction(1001, 24000)) == 120


@needs_ffmpeg
@needs_fcp
def test_sfx_clip_volume(tmp_path):
    """Sound effects as connected audio clips with a role and a volume, valid and read back."""
    make_media()
    xml, _ = fcpxml.build_timeline(
        project_name="SFX",
        fps=24,
        primary=[StoryItem(path=str(MEDIA / "clipA.mov"), duration=3, tracks="video")],
        connected=[ConnectedItem(path=str(MEDIA / "music.wav"), at=0, lane=-1, audio_role="music", volume_db=-3),
                   ConnectedItem(path=str(MEDIA / "music.wav"), at=1, duration=0.5, lane=-2, audio_role="effects",
                                 volume_db=-9.5)],
    )
    out = tmp_path / "sfx.fcpxml"
    out.write_text(xml)
    valid, errors = fcpxml.validate_file(out)
    assert valid, errors
    assert 'amount="-9.5dB"' in xml
    items = fcpxml.read_document(out)["projects"][0]["items"]
    sfx = next(i for i in items if i["lane"] == -2)
    assert sfx["volume_db"] == -9.5
