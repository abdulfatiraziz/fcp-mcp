"""Red block-grid transition layer (Sydney style), as a ProRes 4444 overlay with alpha.

block_grid.py <out.mov> [--size=1920x1080] [--fps=24] [--cols=10] [--rows=6] [--color=AE0012]
              [--close=40] [--break=38] [--solid=18] [--open=26]
              [--out-subject=0.5,0.8] [--in-subject=0.5,0.6] [--break-curve=1.4] [--seed=7]

Four phases, in frames of the output (which starts where you place it on the timeline):
  close  a red frame creeps in from the edges, each edge in notched one-cell segments, until the
         picture is an inset one cell in from every side;
  break  the inset breaks into grid cells that drop out (cells far from the outgoing subject first),
         until the frame is solid red (--break-curve > 1 keeps more of the picture for longer);
  solid  solid red (the cut to the next shot goes here, hidden);
  open   holes open cell by cell, starting at the incoming subject and spreading outward in steps,
         until the next shot is fully revealed. The last frame is fully clear.
Everything moves "on twos" (changes every second frame), like the reference.
Put the outgoing subject's cutout above this layer so the person stays visible on the red, and the
incoming subject's white silhouette above that.
Run with: uv run --no-project --with numpy python -I block_grid.py ...
"""
import subprocess, sys

import numpy as np

flags = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--"))
out = next(a for a in sys.argv[1:] if not a.startswith("--"))
W, H = (int(v) for v in flags.get("size", "1920x1080").split("x"))
fps = flags.get("fps", "24")
cols, rows = int(flags.get("cols", 10)), int(flags.get("rows", 6))
color = [int(flags.get("color", "AE0012")[i:i + 2], 16) for i in (0, 2, 4)]
n_close, n_break, n_solid, n_open = (int(flags.get(k, d)) for k, d in
                                     (("close", 40), ("break", 38), ("solid", 18), ("open", 26)))
out_sub = [float(v) for v in flags.get("out-subject", "0.5,0.8").split(",")]
in_sub = [float(v) for v in flags.get("in-subject", "0.5,0.6").split(",")]
break_curve = float(flags.get("break-curve", 1.4))
rng = np.random.default_rng(int(flags.get("seed", 7)))
cw, ch = W / cols, H / rows
xs = [round(i * cw) for i in range(cols + 1)]
ys = [round(j * ch) for j in range(rows + 1)]


def twos(t):
    return t - t % 2


# close: every edge segment (one cell long) creeps in to one cell deep with its own delay, in steps.
segs = []
for side, count in (("top", cols), ("bottom", cols), ("left", rows), ("right", rows)):
    for i in range(count):
        delay = rng.uniform(0, 0.45) * n_close
        segs.append((side, i, delay, rng.uniform(0.35, 0.55) * n_close))


def close_mask(t):
    m = np.zeros((H, W), bool)
    for side, i, delay, dur in segs:
        p = np.clip((t - delay) / dur, 0, 1)
        p = np.floor(p * 6) / 6  # depth in sixths of a cell: hard steps, not a smooth slide
        if side in ("top", "bottom"):
            d = round(p * ch)
            x0, x1 = xs[i], xs[i + 1]
            if side == "top":
                m[:d, x0:x1] = True
            else:
                m[H - d:, x0:x1] = True
        else:
            d = round(p * cw)
            y0, y1 = ys[i], ys[i + 1]
            if side == "left":
                m[y0:y1, :d] = True
            else:
                m[y0:y1, W - d:] = True
    return m


def cell_dist(c, r, sub, vbias=1.0):
    cx, cy = sub[0] * cols - 0.5, sub[1] * rows - 0.5
    return max(abs(c - cx), abs(r - cy) * vbias) + 0.25 * abs(c - cx)


# break: interior cells drop out, farthest from the outgoing subject first, with jitter.
interior = [(c, r) for c in range(1, cols - 1) for r in range(1, rows - 1)]
far = np.array([cell_dist(c, r, out_sub) for c, r in interior])
order = np.argsort(-(far + rng.uniform(0, 2.0, len(far))))
drop_at = {interior[k]: twos(int(round((rank / len(order)) ** (1 / break_curve) * (n_break - 4))))
           for rank, k in enumerate(order)}

# open: every cell opens, nearest the incoming subject first (a stepped pyramid around it).
cells = [(c, r) for c in range(cols) for r in range(rows)]
near = np.array([cell_dist(c, r, in_sub, vbias=1.3) for c, r in cells]) + rng.uniform(0, 0.6, len(cells))
near = (near - near.min()) / (near.max() - near.min())
open_at = {cell: twos(int(round(v * (n_open - 3)))) for cell, v in zip(cells, near)}


def cells_mask(red_cells):
    m = np.zeros((H, W), bool)
    for c, r in red_cells:
        m[ys[r]:ys[r + 1], xs[c]:xs[c + 1]] = True
    return m


frame_mask = close_mask(n_close)  # the finished one-cell frame
enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", fps,
                        "-i", "-", "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                        "-alpha_bits", "16", "-vendor", "apl0", out], stdin=subprocess.PIPE)
rgba = np.zeros((H, W, 4), np.uint8)
rgba[..., :3] = color
total = n_close + n_break + n_solid + n_open
for t in range(total):
    if t < n_close:
        m = close_mask(twos(t))
    elif t < n_close + n_break:
        k = t - n_close
        m = frame_mask | cells_mask([cell for cell, at in drop_at.items() if at <= twos(k)])
    elif t < n_close + n_break + n_solid:
        m = np.ones((H, W), bool)
    else:
        k = t - n_close - n_break - n_solid
        m = cells_mask([cell for cell, at in open_at.items() if at > twos(k)]) if t < total - 1 else np.zeros((H, W), bool)
    rgba[..., 3] = m * 255
    enc.stdin.write(rgba.tobytes())
enc.stdin.close(); enc.wait()
print(f"{out}: {total} frames (close {n_close}, break {n_break}, solid {n_solid}, open {n_open}), {W}x{H} @ {fps}")
