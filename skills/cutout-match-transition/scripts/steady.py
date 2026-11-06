"""Find the steadiest stretches of handheld or head-mounted footage (Ray-Ban glasses, phone).

steady.py <out.json> <clip> [<clip> ...] [--min=1.0] [--fps=15]
For every clip: the camera's frame-to-frame movement (global shift by phase correlation on small grey
frames, as a fraction of the frame width per second), then the steadiest windows of at least --min
seconds, best first. Printed as a table and saved as JSON:
  {clip: {"duration": s, "motion": [per-sample shift/s], "windows": [{"start", "end", "motion"}]}}
Rough scale: under 0.05 frame widths per second looks locked off; 0.05-0.12 is a calm handheld drift;
above 0.25 the picture is visibly swinging.
Run with: uv run --no-project --with numpy python -I steady.py ...
"""
import json, subprocess, sys
from pathlib import Path

import numpy as np

flags = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--"))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
out, clips = Path(args[0]), args[1:]
MIN, FPS = float(flags.get("min", 1.0)), float(flags.get("fps", 15))
SW = 320  # analysis width


def frames(path):
    w, h = (int(v) for v in subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                            "stream=width,height", "-of", "csv=p=0", path], capture_output=True, text=True,
                                           check=True).stdout.strip().split(",")[:2])
    sh = round(h * SW / w / 2) * 2
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps={FPS},scale={SW}:{sh},format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, sh, SW).astype(np.float32)


def shift(a, b, win):
    """Global translation from a to b (pixels), by phase correlation."""
    fa, fb = np.fft.rfft2((a - a.mean()) * win), np.fft.rfft2((b - b.mean()) * win)
    r = fa * np.conj(fb)
    c = np.fft.irfft2(r / np.maximum(np.abs(r), 1e-6), s=a.shape)
    y, x = np.unravel_index(np.argmax(c), c.shape)
    h, w = a.shape

    def sub(m, p, l, r):  # parabola through the peak and its neighbours: sub-pixel offset
        d = l - 2 * m + r
        return p + (0.5 * (l - r) / d if d < 0 else 0.0)

    fx = sub(c[y, x], x, c[y, (x - 1) % w], c[y, (x + 1) % w])
    fy = sub(c[y, x], y, c[(y - 1) % h, x], c[(y + 1) % h, x])
    return (fx if fx <= w / 2 else fx - w), (fy if fy <= h / 2 else fy - h)


result = {}
for clip in clips:
    f = frames(clip)
    win = np.outer(np.hanning(f.shape[1]), np.hanning(f.shape[2])).astype(np.float32)
    motion = np.array([np.hypot(*shift(f[i], f[i + 1], win)) for i in range(len(f) - 1)]) / SW * FPS  # widths per second
    motion = np.convolve(motion, np.ones(3) / 3, mode="same")  # ignore single-frame blips
    n = max(2, int(MIN * FPS))
    score = np.convolve(motion, np.ones(n) / n, mode="valid")  # mean motion of every window of n samples
    windows, used = [], np.zeros(len(motion), bool)
    for i in np.argsort(score):  # steadiest first, non-overlapping
        if used[i:i + n].any():
            continue
        j = i + n  # grow while it stays as calm, without running into a window already taken
        while j < len(motion) and not used[j] and motion[j] < max(1.5 * score[i], 0.03):
            j += 1
        used[i:j] = True
        windows.append({"start": round(i / FPS, 2), "end": round(j / FPS, 2), "motion": round(float(motion[i:j].mean()), 3)})
        if len(windows) == 6:
            break
    result[str(Path(clip).resolve())] = {"duration": round(len(f) / FPS, 2), "motion": [round(float(m), 3) for m in motion],
                    "windows": sorted(windows, key=lambda w: w["motion"])}
    best = ", ".join(f"{w['start']:.1f}-{w['end']:.1f}s ({w['motion']:.3f})" for w in result[str(Path(clip).resolve())]["windows"][:4])
    print(f"{Path(clip).name:32s} {len(f) / FPS:5.1f}s  median {np.median(motion):.3f}  steadiest: {best}", flush=True)
out.write_text(json.dumps(result))
