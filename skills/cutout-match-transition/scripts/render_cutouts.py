"""Preview the edit's overlays: base cut + every connected video/still overlay + captions, with numpy.

Mirrors the MCP's Adjust semantics (crop / place / scale / opacity keyframes, per-property hold,
Screen or straight-alpha normal blend) and FCP's conform (fit, fill, none). Overlays stack by lane.
render_cutouts.py <spec.json> <base.mp4> <captions dir> <out.mp4>
"""
import json, subprocess, sys
from fractions import Fraction

import numpy as np
from PIL import Image

spec_path, base_path, caps_dir, out_path = sys.argv[1:5]
spec = json.load(open(spec_path))
# Same shape rule as render_preview.py: long side 1280, timeline aspect kept.
_tw, _th = spec.get("width", 1920), spec.get("height", 1080)
W, H = (round(_tw * 1280 / max(_tw, _th) / 2) * 2, round(_th * 1280 / max(_tw, _th) / 2) * 2)
_fps = str(spec.get("fps", 23.976))
FPS = Fraction(24000, 1001) if _fps in ("23.976", "23.98", "2398") else Fraction(_fps).limit_denominator(1001)
RATE = f"{FPS.numerator}/{FPS.denominator}"
STILL = (".png", ".jpg", ".jpeg", ".tif", ".tiff")


def sec(v):
    if isinstance(v, str) and v.endswith("s"):
        n, d = v[:-1].split("/") if "/" in v else (v[:-1], 1)
        return Fraction(int(n), int(d))
    return Fraction(v).limit_denominator(100000)


def frames_of(v):
    v = v[:-1]
    n, d = v.split("/") if "/" in v else (v, 1)
    return round(Fraction(int(n), int(d)) * FPS)


def decode(path, start, count, conform=None):
    # Decode with alpha (opaque clips get alpha 255) so keyed ProRes 4444 overlays composite correctly.
    still = path.lower().endswith(STILL)
    if conform == "fill":
        geo = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},format=rgba"
    elif conform == "none":  # native pixels, scaled only by preview size / timeline size
        geo = (f"scale=iw*{W}/{_tw}:ih*{H}/{_th},format=rgba,pad=max(iw\\,{W}):max(ih\\,{H}):(ow-iw)/2:(oh-ih)/2:color=black@0,"
               f"crop={W}:{H}")
    else:  # fit (FCP default): transparent padding
        geo = f"scale={W}:{H}:force_original_aspect_ratio=decrease,format=rgba,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black@0"
    cmd = ["ffmpeg", "-v", "error", *(["-loop", "1", "-framerate", RATE] if still else ["-ss", f"{start:.4f}"]), "-i", path,
           "-vf", f"fps={RATE}," + geo, "-frames:v", str(count), "-f", "rawvideo", "-pix_fmt", "rgba", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 4)


def value_at(keys, k, hold, default):
    if not keys:
        return default
    if k <= keys[0][0]:
        return keys[0][1]
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t0 <= k < t1:
            if hold:
                return v0
            f = (k - t0) / (t1 - t0)
            return [a + (b - a) * f for a, b in zip(v0, v1)] if isinstance(v0, list) else v0 + (v1 - v0) * f
    return keys[-1][1]


class Cutout:
    def __init__(self, item):
        a = item.get("adjust") or {}
        self.at, self.n = frames_of(item["at"]), frames_of(item["duration"])
        self.src = decode(item["path"], float(sec(item.get("source_in", 0))), self.n, item.get("conform"))
        self.mode = a.get("blend_mode") or "normal"
        # Where the clip's own frame sits in the preview frame after FCP's conform (crop boxes are
        # fractions of the clip's frame, as in FCPXML).
        cw, ch = (int(v) for v in subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=width,height", "-of", "csv=p=0",
             item["path"]], capture_output=True, text=True, check=True).stdout.strip().split(",")[:2])
        conform = item.get("conform")
        sc = max(W / cw, H / ch) if conform == "fill" else W / _tw if conform == "none" else min(W / cw, H / ch)
        self.clip = (cw * sc, ch * sc, (W - cw * sc) / 2, (H - ch * sc) / 2)  # width, height, x offset, y offset
        b = a.get("crop") or {"x0": 0, "y0": 0, "x1": 1, "y1": 1}
        self.base = [b["x0"], b["y0"], b["x1"], b["y1"]]
        self.place, self.scale = a.get("place"), a.get("scale") or 1.0
        self.opacity = a.get("opacity", 1.0) if a.get("opacity") is not None else 1.0
        hold = a.get("hold") or []
        self.hold = {p: hold is True or p in hold for p in ("opacity", "crop", "place", "scale")}
        keys = [(frames_of(k["time"]), k) for k in a.get("keyframes") or []]
        self.keys = {
            "crop": [(t, [k["crop"][c] for c in ("x0", "y0", "x1", "y1")]) for t, k in keys if k.get("crop")],
            "place": [(t, list(k["place"])) for t, k in keys if k.get("place")],
            "opacity": [(t, k["opacity"]) for t, k in keys if k.get("opacity") is not None],
            "scale": [(t, k["scale"]) for t, k in keys if k.get("scale") is not None],
        }

    def composite(self, frame, i):
        k = i - self.at
        if not 0 <= k < self.n or k >= len(self.src):
            return
        box = value_at(self.keys["crop"], k, self.hold["crop"], self.base)
        op = value_at(self.keys["opacity"], k, self.hold["opacity"], self.opacity)
        cwp, chp, ox, oy = self.clip
        px = lambda fx: ox + fx * cwp  # noqa: E731  clip-frame fraction -> preview pixel
        py = lambda fy: oy + fy * chp  # noqa: E731
        cxp, cyp = px((self.base[0] + self.base[2]) / 2), py((self.base[1] + self.base[3]) / 2)
        place = value_at(self.keys["place"], k, self.hold["place"], self.place)
        tx, ty = (place[0] * W, place[1] * H) if place else (cxp, cyp)  # no place: the element stays put
        x0, y0 = max(0, int(round(px(box[0])))), max(0, int(round(py(box[1]))))
        x1, y1 = min(W, int(round(px(box[2])))), min(H, int(round(py(box[3]))))
        if x1 - x0 < 1 or y1 - y0 < 1 or op <= 0:
            return
        s = value_at(self.keys["scale"], k, self.hold["scale"], self.scale)
        if s <= 0:
            return
        dx = int(round(tx + (x0 - cxp) * s))
        dy = int(round(ty + (y0 - cyp) * s))
        w, h = max(1, int(round((x1 - x0) * s))), max(1, int(round((y1 - y0) * s)))
        piece = np.asarray(Image.fromarray(self.src[k, y0:y1, x0:x1], "RGBA").resize((w, h), Image.BILINEAR), np.float32) / 255
        fx0, fy0, fx1, fy1 = max(dx, 0), max(dy, 0), min(dx + w, W), min(dy + h, H)
        if fx1 <= fx0 or fy1 <= fy0:
            return
        part = piece[fy0 - dy:fy1 - dy, fx0 - dx:fx1 - dx]
        rgb, a = part[..., :3], part[..., 3:] * op
        region = frame[fy0:fy1, fx0:fx1]
        if self.mode == "screen":
            frame[fy0:fy1, fx0:fx1] = region + rgb * a * (1 - region)
        else:  # normal: straight-alpha "over"
            frame[fy0:fy1, fx0:fx1] = region * (1 - a) + rgb * a


overlays = [c for c in spec["connected"] if c.get("path") and not c.get("audio_role") and c.get("lane", 1) > 0]
cutouts = [Cutout(c) for c in sorted(overlays, key=lambda c: c.get("lane", 1))]
titles = [c for c in spec["connected"] if c.get("text")]
caps = []
for n, t in enumerate(titles, 1):
    img = np.asarray(Image.open(f"{caps_dir}/cap{n}.png").convert("RGBA"), np.float32) / 255
    caps.append((frames_of(t["at"]), frames_of(t["at"]) + frames_of(t["duration"]), img[..., :3], img[..., 3:]))

total = sum(frames_of(p["duration"]) for p in spec["primary"])
base = subprocess.Popen(["ffmpeg", "-v", "error", "-i", base_path, "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
enc = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", RATE,
                        "-i", "-", "-i", base_path, "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-crf", "19", "-preset", "medium",
                        "-pix_fmt", "yuv420p", "-c:a", "copy", "-shortest", "-movflags", "+faststart", out_path], stdin=subprocess.PIPE)
for i in range(total):
    raw = base.stdout.read(W * H * 3)
    if len(raw) < W * H * 3:
        break
    frame = np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(np.float32) / 255
    for c in cutouts:
        c.composite(frame, i)
    for a, b, rgb, alpha in caps:
        if a <= i < b:
            frame = frame * (1 - alpha) + rgb * alpha
    enc.stdin.write((np.clip(frame, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())
enc.stdin.close()
enc.wait()
base.wait()
print(out_path)
