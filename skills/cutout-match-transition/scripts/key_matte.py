"""Key a bright object (neon, lit sign) out of a clip into a ProRes 4444 overlay with alpha.

key_matte.py <src> <out.mov> <start s> <dur s> <x0> <y0> <x1> <y1> [lo hi]
key_matte.py <src> <out.mov> <start s> <dur s> "x0,y0,x1,y1;x0,y0,x1,y1;..." [lo hi [sat_lo sat_hi]]
Alpha = smoothstep(lo, hi, luma) inside the window(s) (fractions of the frame), feathered at the
window edges. Several windows key several separate elements into one matte (e.g. two lit towers).
RGB is the untouched source, so the overlay lines up pixel for pixel with the original clip.
Optional saturation gate (sat_lo sat_hi): keep only coloured light (LED crowns, neon) and drop grey/white
light such as lit clouds. Gaps in the gate are closed and enclosed holes filled, so blown-out white
centres *inside* a coloured light stay, while grey light outside it (clouds) is still dropped.
--key=<channel> keys something other than brightness (default luma): red (R minus G, for red letters on
yellow or white), yellow (min(R,G) minus B), white (min(R,G,B): bright and grey), dark (1 - luma, for dark
letters on a light sign). lo/hi then apply to that channel.
Run with: uv run --no-project --with numpy --with pillow --with scipy python -I key_matte.py ...
"""
import subprocess, sys
import numpy as np
from PIL import Image, ImageFilter


def _max1d(a, r, axis):
    out = a.copy()
    for d in range(1, r + 1):
        if axis == 0:
            out[d:] = np.maximum(out[d:], a[:-d]); out[:-d] = np.maximum(out[:-d], a[d:])
        else:
            out[:, d:] = np.maximum(out[:, d:], a[:, :-d]); out[:, :-d] = np.maximum(out[:, :-d], a[:, d:])
    return out


def close(a, r):
    """Morphological closing (square window 2r+1): fills holes and gaps smaller than the window."""
    dilated = _max1d(_max1d(a, r, 0), r, 1)
    return -_max1d(_max1d(-dilated, r, 0), r, 1)

flags = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--"))
sys.argv = [a for a in sys.argv if not a.startswith("--")]
KEY = flags.get("key", "luma")
src, out = sys.argv[1], sys.argv[2]
start, dur = float(sys.argv[3]), float(sys.argv[4])
if "," in sys.argv[5]:
    windows = [tuple(float(v) for v in w.split(",")) for w in sys.argv[5].split(";") if w.strip()]
    rest = sys.argv[6:]
else:
    windows = [tuple(float(v) for v in sys.argv[5:9])]
    rest = sys.argv[9:]
lo, hi = (float(v) for v in rest[:2]) if len(rest) >= 2 else (0.16, 0.45)
sat = tuple(float(v) for v in rest[2:4]) if len(rest) >= 4 else None

probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height,r_frame_rate",
                        "-of", "csv=p=0", src], capture_output=True, text=True, check=True).stdout.strip().split(",")
W, H, rate = int(probe[0]), int(probe[1]), probe[2]

yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
feather = 0.012
def ramp(v, a, b):
    return np.clip((v - a) / feather, 0, 1) * np.clip((b - v) / feather, 0, 1)
window = np.zeros((H, W), np.float32)
for x0, y0, x1, y1 in windows:
    window = np.maximum(window, ramp(xx / W, x0, x1) * ramp(yy / H, y0, y1))

dec = subprocess.Popen(["ffmpeg", "-v", "error", "-ss", f"{start}", "-t", f"{dur}", "-i", src,
                        "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", rate,
                        "-i", "-", "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                        "-alpha_bits", "16", "-vendor", "apl0", out], stdin=subprocess.PIPE)
n = 0
while True:
    raw = dec.stdout.read(W * H * 3)
    if len(raw) < W * H * 3:
        break
    rgb = np.frombuffer(raw, np.uint8).reshape(H, W, 3)
    f = rgb.astype(np.float32) / 255
    luma = 0.2126 * f[..., 0] + 0.7152 * f[..., 1] + 0.0722 * f[..., 2]
    chan = {"luma": luma, "red": f[..., 0] - f[..., 1], "yellow": np.minimum(f[..., 0], f[..., 1]) - f[..., 2],
            "white": f.min(-1), "dark": 1 - luma}[KEY]
    t = np.clip((chan - lo) / (hi - lo), 0, 1)
    alpha = (t * t * (3 - 2 * t)) * window
    if sat:
        mx, mn = f.max(-1), f.min(-1)
        s = np.clip(((mx - mn) / np.maximum(mx, 1e-4) - sat[0]) / (sat[1] - sat[0]), 0, 1)
        from scipy.ndimage import binary_fill_holes

        g = close(s.astype(np.float32), max(2, min(W, H) // 180))  # bridge thin gaps, ~6px at 1080p
        g = np.maximum(g, binary_fill_holes(g > 0.5).astype(np.float32))  # fill enclosed blown-out centres
        g = Image.fromarray((g * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(1.5))
        alpha = alpha * (np.asarray(g, np.float32) / 255)
    enc.stdin.write(np.dstack([rgb, (alpha * 255 + 0.5).astype(np.uint8)]).tobytes())
    n += 1
enc.stdin.close(); enc.wait(); dec.wait()
print(f"{out}: {n} frames, {W}x{H} @ {rate}")
