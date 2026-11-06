"""Small transparent PNG graphics for FCP overlays.

graphics.py letterbox <out.png> <W> <H> [bar fraction, default 0.11]
    Black bars top and bottom, clear middle. Slide them in by keyframing scale 1.3 -> 1 (about the
    frame centre the bars start just outside the frame and close in together).
graphics.py glint <out.png> [size px, default 360]
    A four-point star sparkle with a soft core. Use conform "none", `place` it on the highlight and
    keyframe scale 0 -> 1 -> 0 over a few frames.
Run with: uv run --no-project --with numpy --with pillow python -I graphics.py ...
"""
import sys

import numpy as np
from PIL import Image

kind, out = sys.argv[1], sys.argv[2]
if kind == "letterbox":
    W, H = int(sys.argv[3]), int(sys.argv[4])
    bar = round(H * (float(sys.argv[5]) if len(sys.argv) > 5 else 0.11))
    a = np.zeros((H, W, 4), np.uint8)
    a[:bar, :, 3] = 255
    a[H - bar:, :, 3] = 255
    Image.fromarray(a, "RGBA").save(out)
elif kind == "glint":
    S = int(sys.argv[3]) if len(sys.argv) > 3 else 360
    yy, xx = (np.mgrid[0:S, 0:S].astype(np.float32) - (S - 1) / 2) / (S / 2)
    r = np.hypot(xx, yy)
    # Two thin, tapering rays (horizontal and vertical) plus a smaller diagonal pair and a soft core.
    ray = lambda u, v, w: np.exp(-(v / (w * (1 - np.clip(abs(u), 0, 1)) + 1e-3)) ** 2) * (1 - np.clip(abs(u), 0, 1)) ** 1.5  # noqa: E731
    d1, d2 = (xx + yy) / np.sqrt(2), (xx - yy) / np.sqrt(2)
    a = np.maximum.reduce([ray(xx, yy, 0.06), ray(yy, xx, 0.06), 0.55 * ray(d1 * 1.6, d2, 0.05), 0.55 * ray(d2 * 1.6, d1, 0.05),
                           np.exp(-(r / 0.09) ** 2), 0.35 * np.exp(-(r / 0.28) ** 2)])
    img = np.zeros((S, S, 4), np.uint8)
    img[..., :3] = 255
    img[..., 3] = (np.clip(a, 0, 1) * 255).astype(np.uint8)
    Image.fromarray(img, "RGBA").save(out)
else:
    sys.exit(f"unknown graphic {kind!r}")
print(out)
