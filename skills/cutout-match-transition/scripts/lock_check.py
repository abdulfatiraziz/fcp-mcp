"""Verify a cutout match transition.

Two checks:

  align  — is matte frame n really source frame (first + n)?  (catches off-by-one mattes)
      lock_check.py align <matte.mov> <source clip> <first source frame> [matte frame]

  lock   — at the cut, does the element hold still while the world swaps?
      lock_check.py lock <rendered.mp4> <cut frame> <matte.mov> <matte frame shown just before the cut>

A good transition: in `lock`, the element's change across the cut is no bigger than its change just
before the cut (time-lapses twinkle every frame), while the background change is many times larger.
In `align`, the best match should be the expected frame, clearly lower than its neighbours.
Run with:  uv run --no-project --with numpy python -I lock_check.py ...
"""

import subprocess
import sys

import numpy as np


def size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True).stdout
    w, h = out.strip().split(",")[:2]
    return int(w), int(h)


def grab(path, first, n, rgba=False, to=None):
    w, h = to or size(path)
    # Scale like FCP's "fill" conform (and the preview renderer), so mattes of another shape line up.
    vf = f"select='between(n,{first},{first + n - 1})'" + (
        f",scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}" if to else "")
    fmt, ch = ("rgba", 4) if rgba else ("rgb24", 3)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-vf", vf, "-vsync", "0", "-f", "rawvideo",
                          "-pix_fmt", fmt, "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, ch).astype(np.float32) / 255


def align(matte, source, first, n=0):
    cut = grab(matte, n, 1, rgba=True)[0]
    src = grab(source, first + n - 1, 3, to=(cut.shape[1], cut.shape[0]))
    inside = cut[..., 3] > 0.8
    for i, frame in enumerate(src):
        diff = np.abs(cut[..., :3] - frame)[inside].mean()
        mark = "  <- expected" if i == 1 else ""
        print(f"matte frame {n} vs source frame {first + n - 1 + i}: {diff:.4f}{mark}")


def lock(video, cut, matte, matte_frame):
    v = grab(video, cut - 2, 3)
    h, w = v.shape[1:3]
    element = grab(matte, matte_frame, 1, rgba=True, to=(w, h))[0][..., 3] > 0.8
    for a, b, label in [(0, 1, "before the cut"), (1, 2, "across the cut")]:
        d = np.abs(v[a] - v[b]).mean(-1)
        print(f"frames {cut - 2 + a}->{cut - 2 + b} ({label}): element {d[element].mean():.3f}, "
              f"background {d[~element].mean():.3f}")


if __name__ == "__main__":
    if len(sys.argv) >= 5 and sys.argv[1] == "align":
        align(sys.argv[2], sys.argv[3], int(sys.argv[4]), int(sys.argv[5]) if len(sys.argv) > 5 else 0)
    elif len(sys.argv) == 6 and sys.argv[1] == "lock":
        lock(sys.argv[2], int(sys.argv[3]), sys.argv[4], int(sys.argv[5]))
    else:
        sys.exit(__doc__)
