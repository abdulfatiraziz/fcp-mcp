"""Turn a matte overlay (ProRes 4444 with alpha) into a glowing sign or a white silhouette.

stylize_matte.py <in.mov> <out.mov> glow [--lift=0.85] [--radius=18] [--strength=1.3]
stylize_matte.py <in.mov> <out.mov> silhouette [--color=FFFFFF] [--radius=10] [--strength=0.9]

glow:       the keyed element switches "on" like neon: a near-white core (its colour lifted toward
            white) inside a halo of its own colour at full brightness. Use on key_matte.py output for
            signs lighting up. A white element on a bright background barely changes: pick signs whose
            letters have a colour, or raise --radius/--strength for a visible bloom.
silhouette: the element becomes a flat glowing shape (white by default) with a soft edge. Use on
            subject_cutout.py output for the white person / object silhouettes.
Frames, size and rate are kept, so the result lines up with its source exactly like the matte did.
Run with: uv run --no-project --with numpy --with scipy python -I stylize_matte.py ...
"""
import subprocess, sys

import numpy as np
from scipy.ndimage import gaussian_filter

flags = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--"))
src, out, mode = [a for a in sys.argv[1:] if not a.startswith("--")]
radius = float(flags.get("radius", 18 if mode == "glow" else 10))
strength = float(flags.get("strength", 1.3 if mode == "glow" else 0.9))
lift = float(flags.get("lift", 0.85))
color = np.array([int(flags.get("color", "FFFFFF")[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255

probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height,r_frame_rate",
                        "-of", "csv=p=0", src], capture_output=True, text=True, check=True).stdout.strip().split(",")
W, H, rate = int(probe[0]), int(probe[1]), probe[2]
r = radius * H / 1080  # radius is given for a 1080-line frame


def blur(a, radius=None):
    return gaussian_filter(a.astype(np.float32), radius or r, mode="constant")


dec = subprocess.Popen(["ffmpeg", "-v", "error", "-i", src, "-f", "rawvideo", "-pix_fmt", "rgba", "-"], stdout=subprocess.PIPE)
enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", rate,
                        "-i", "-", "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                        "-alpha_bits", "16", "-vendor", "apl0", out], stdin=subprocess.PIPE)
n = 0
while True:
    raw = dec.stdout.read(W * H * 4)
    if len(raw) < W * H * 4:
        break
    f = np.frombuffer(raw, np.uint8).reshape(H, W, 4).astype(np.float32) / 255
    a = f[..., 3]
    halo = np.clip(blur(a) * strength, 0, 1)
    alpha = np.clip(np.maximum(a, halo), 0, 1)
    if mode == "silhouette":
        rgb = np.broadcast_to(color, f[..., :3].shape)
    else:
        # Halo: the element's colour spread out (premultiplied blur) and pushed to full brightness.
        # Core: the element, very slightly fattened, lifted toward white. Neon reads this way.
        spread = np.dstack([blur(f[..., c] * a) for c in range(3)]) / np.maximum(blur(a), 1e-4)[..., None]
        vivid = np.clip(spread / np.maximum(spread.max(-1, keepdims=True), 1e-4), 0, 1)
        core = np.clip(blur(a, 2.5 * H / 1080) * 1.4, 0, 1)[..., None]
        rgb = (f[..., :3] + (1 - f[..., :3]) * lift) * core + vivid * (1 - core)
        alpha = np.maximum(core[..., 0], halo)
    # Black where fully clear: ProRes then stores almost nothing there (files shrink several times).
    rgb = np.where(alpha[..., None] > 0.5 / 255, rgb, 0)
    enc.stdin.write((np.dstack([rgb, alpha]) * 255 + 0.5).astype(np.uint8).tobytes())
    n += 1
enc.stdin.close(); enc.wait(); dec.wait()
print(f"{out}: {n} frames, {mode}, {W}x{H} @ {rate}")
