"""Render a preview MP4 of the build spec (same cuts as the FCPXML) with ffmpeg.

render_preview.py <spec.json> <captions dir> <out.mp4>
Audio: the music bed (audio_role "music", optional) plus every other connected audio clip (sound
effects), each at its `at`, trimmed to its `duration` and set to its `volume_db`.
"""
import json, subprocess, sys
from fractions import Fraction

spec = json.load(open(sys.argv[1]))
caps, out = sys.argv[2], sys.argv[3]
_fps = str(spec.get("fps", 23.976))
FR = Fraction(24000, 1001) if _fps in ("23.976", "23.98", "2398") else Fraction(_fps).limit_denominator(1001)
RATE = f"{FR.numerator}/{FR.denominator}"
# Preview keeps the timeline's shape, long side 1280 (16:9 -> 1280x720, 4:3 -> 1280x960, 9:16 -> 720x1280).
_tw, _th = spec.get("width", 1920), spec.get("height", 1080)
W, H = (round(_tw * 1280 / max(_tw, _th) / 2) * 2, round(_th * 1280 / max(_tw, _th) / 2) * 2)
BG = spec.get("background") or "black"


def conform_vf(conform, pad_color):
    """Scale like FCP's spatial conform: fill crops to the frame; fit (FCP's default) pads it."""
    if conform == "fill":
        return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    return f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={pad_color}"

def secs(v):
    if isinstance(v, str) and v.endswith("s"):
        n, d = v[:-1].split("/") if "/" in v else (v[:-1], 1)
        return Fraction(int(n), int(d))
    return Fraction(v).limit_denominator(1000)

inputs, chains, labels = [], [], []
for i, item in enumerate(spec["primary"]):
    dur = secs(item["duration"])
    frames = round(dur * FR)
    if "path" in item:
        inputs += ["-ss", f"{float(secs(item.get('source_in', 0))):.4f}", "-t", f"{float(dur) + 1:.3f}", "-i", item["path"]]
        src = f"[{len(inputs) // 6 - 1}:v]"
        chains.append(f"{src}fps={RATE},{conform_vf(item.get('conform'), BG.replace('#', '0x'))},setsar=1,"
                      f"trim=end_frame={frames},setpts=PTS-STARTPTS[v{i}]")
    else:
        chains.append(f"color=c=black:s={W}x{H}:r={RATE},trim=end_frame={frames},setpts=PTS-STARTPTS[v{i}]")
    labels.append(f"[v{i}]")
n_clips = len(inputs) // 6
chains.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0[cut]")

titles = [c for c in spec["connected"] if c.get("text")]
total = sum(secs(i["duration"]) for i in spec["primary"])
# Audio: the music bed plus every sound-effect clip (any other connected audio), each at its timeline
# position and volume_db, mixed without auto-gain. No music -> the effects play over silence.
AUDIO_EXT = (".wav", ".aif", ".aiff", ".mp3", ".m4a", ".caf")
music = next((c for c in spec["connected"] if c.get("audio_role") == "music"), None)
sounds = [c for c in spec["connected"] if c is not music and str(c.get("path", "")).lower().endswith(AUDIO_EXT)]
audio_labels = []
for k, c in enumerate(([music] if music else []) + sounds):
    idx = n_clips + k
    inputs += ["-ss", f"{float(secs(c.get('source_in', 0))):.4f}", "-i", c["path"]]
    chain = f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={c.get('volume_db', 0)}dB"
    if c.get("duration") is not None:
        chain += f",atrim=end={float(secs(c['duration'])):.4f}"
    delay = round(float(secs(c.get("at", 0))) * 1000)
    chains.append(chain + f",adelay={delay}:all=1[a{k}]")
    audio_labels.append(f"[a{k}]")
chains.append(f"anullsrc=r=48000:cl=stereo,atrim=end={float(total):.4f}[silence]")
chains.append("[silence]" + "".join(audio_labels) + f"amix=inputs={len(audio_labels) + 1}:normalize=0:duration=first[aout]")
prev = "[cut]"
for k, t in enumerate(titles):
    inputs += ["-loop", "1", "-i", f"{caps}/cap{k + 1}.png"]
    start = secs(t["at"])
    end = start + secs(t["duration"])
    chains.append(f"{prev}[{n_clips + len(audio_labels) + k}:v]overlay=shortest=1:enable='between(t,{float(start):.4f},{float(end):.4f})'[t{k}]")
    prev = f"[t{k}]"
cmd = ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", ";".join(chains), "-map", prev, "-map", "[aout]",
       "-t", f"{float(total):.4f}", "-r", RATE, "-c:v", "libx264", "-preset", "medium", "-crf", "20",
       "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]
subprocess.run(cmd, check=True)
print(out)
