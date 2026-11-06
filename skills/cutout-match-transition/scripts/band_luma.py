"""Which outgoing shot lets the element read? Mean brightness where the element will land.

    band_luma.py <x0,y0,x1,y1> <clip@seconds> [<clip@seconds> ...]

Lower is better: the cutout is lit, so it pops over a dark region (the bridge lights read at 0.03,
not at 0.24 over a busy taxi street). Run with: uv run --no-project --with numpy python -I band_luma.py ...
"""

import subprocess
import sys

import numpy as np

x0, y0, x1, y1 = (float(v) for v in sys.argv[1].split(","))
for arg in sys.argv[2:]:
    clip, t = arg.rsplit("@", 1)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", t, "-i", clip, "-frames:v", "1", "-vf", "scale=640:360",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    f = np.frombuffer(raw, np.uint8).reshape(360, 640, 3).astype(np.float32) / 255
    y = 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]
    band = y[int(y0 * 360):int(y1 * 360), int(x0 * 640):int(x1 * 640)]
    print(f"{band.mean():.3f}  {clip.rsplit('/', 1)[-1]} @ {t}s")
