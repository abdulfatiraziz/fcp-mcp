"""Moving subject cutout: Vision subject masks -> smoothed, choked alpha -> ProRes 4444 with alpha.

subject_cutout.py <subject_matte binary> <video> <out.mov> <start frame> <frame count>
Frames are counted on the clip's own frame grid, so frame n of the output is frame (start+n) of the
source: overlay and original line up exactly at a cut.
"""
import subprocess, sys
from collections import deque
from fractions import Fraction

import numpy as np
from PIL import Image, ImageFilter

tool, src, out = sys.argv[1:4]
first, count = int(sys.argv[4]), int(sys.argv[5])
rate = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=r_frame_rate",
                       "-of", "csv=p=0", src], capture_output=True, text=True, check=True).stdout.strip()
num, den = (int(v) for v in rate.split("/"))
start = Fraction(first * den, num)
# AVAssetReader returns every frame that overlaps the start time, so start a quarter frame into
# frame `first`: the previous frame has ended by then.
lift = subprocess.Popen([tool, src, f"{float(start + Fraction(den, 4 * num)):.6f}", str(count)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
W, H = (int(v) for v in lift.stderr.readline().split())
enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", rate,
                        "-i", "-", "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                        "-alpha_bits", "16", "-vendor", "apl0", out], stdin=subprocess.PIPE)

def finish(rgb, alpha):
    # Choke 1px and feather the edge so no background halo survives.
    a = Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(3))
    a = a.filter(ImageFilter.GaussianBlur(1.2))
    enc.stdin.write(np.dstack([rgb, np.asarray(a)]).tobytes())

# Temporal smoothing: per-pixel median of the neighbouring masks. It removes one-frame mask flicker
# without the half-transparent trails an average leaves behind fast-moving subjects.
window = deque(maxlen=3)
n = 0
while True:
    raw = lift.stdout.read(W * H * 4)
    if len(raw) < W * H * 4:
        break
    f = np.frombuffer(raw, np.uint8).reshape(H, W, 4)
    window.append((f[..., :3].copy(), f[..., 3].astype(np.float32) / 255))
    if len(window) == 2 and n == 0:
        finish(window[0][0], window[0][1]); n += 1
    elif len(window) == 3:
        finish(window[1][0], np.median(np.stack([w[1] for w in window]), axis=0)); n += 1
if len(window) >= 2:
    finish(window[-1][0], window[-1][1]); n += 1
enc.stdin.close(); enc.wait(); lift.wait()
print(f"{out}: {n} frames from source frame {first}, {W}x{H} @ {rate}")
