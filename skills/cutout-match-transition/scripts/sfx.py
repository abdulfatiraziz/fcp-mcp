"""Sound effects made in code (numpy), as 48 kHz 24-bit stereo WAVs for FCP connected audio clips.
Every file is levelled the same way (loudest 300 ms at -15 dBFS RMS, peaks under -1 dBFS), like the
recorded ones from sfx_library.py, so volume_db in a spec means the same for both.

sfx.py kit <dir>                         a standard kit: whooshes, riser, hit, thump, boom, zap, ticks
                                         and blips at 8 pitches, glitch, sparkle, shing, swell, zip
sfx.py <sound> <out.wav> [--dur=s] [--pitch=1.0] [--seed=n] [--loud=-9]
sfx.py neon <out.wav> --fps=23.976 --frames=10 --pattern="0:1,2:0,3:1"

Sounds (what they're for is in references/sound-design.md):
  whoosh   filtered noise sweeping up and panning across; loudest at --at (fraction, default 0.75)
  riser    noise + tone climbing in pitch and level to the end (put its end on the hit)
  hit      sub drop + noise crack: a cut landing, solid red slamming in
  thump    a softer, shorter hit: a window filling the frame, letterbox locking
  boom     long low tail for the last frame or the black end card
  zap      electric switch-on crack (neon relay)
  neon     mains hum with harmonics + crackle, gated by a frame pattern "frame:level,..."
  tick     tiny click, pitched by --pitch (type-on letters, quick cuts)
  blip     short digital square blip, pitched by --pitch (block-grid steps)
  glitch   chopped, bit-crushed stutter
  sparkle  high bell partials with staggered decays (glint)
  shing    bright airy burst (white silhouette flash)
  swell    reversed bloom that ends abruptly (put its end where the thing appears)
  zip      fast rising filtered noise (a line drawing on)
Every sound is seeded, so the same command always gives the same file; change --seed for a variant.
Run with: uv run --no-project --with numpy --with scipy python -I sfx.py ...
"""
import subprocess, sys
from pathlib import Path

import numpy as np
from scipy.signal import butter, istft, sosfilt, stft

SR = 48000
flags = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--"))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
rng = np.random.default_rng(int(flags.get("seed", 1)))


def t_axis(dur):
    return np.arange(int(dur * SR)) / SR


def bandpass_sweep(x, f0, f1, q=2.0):
    """Band-pass filter whose centre glides smoothly from f0 to f1 (Gaussian band per STFT frame)."""
    f, _, Z = stft(x, SR, nperseg=1024, noverlap=768)
    k = np.linspace(0, 1, Z.shape[1])
    fc = f0 * (f1 / f0) ** k  # one centre per frame, log glide
    bw = fc / q
    Z *= np.exp(-0.5 * ((f[:, None] - fc[None, :]) / bw[None, :]) ** 2)
    return istft(Z, SR, nperseg=1024, noverlap=768)[1][: len(x)]


def lowpass(x, fc, order=2):
    return sosfilt(butter(order, fc, "low", fs=SR, output="sos"), x)


def highpass(x, fc, order=2):
    return sosfilt(butter(order, fc, "high", fs=SR, output="sos"), x)


def env_ad(n, attack, decay_power=2.0):
    """Rise over `attack` (fraction) then fall with a curved decay."""
    t = np.linspace(0, 1, n)
    rise = np.clip(t / max(attack, 1e-4), 0, 1)
    fall = np.clip((1 - t) / max(1 - attack, 1e-4), 0, 1) ** decay_power
    return np.where(t < attack, rise, fall)


def stereo(mono, pan_from=0.0, pan_to=0.0, width=0.0):
    """Constant-power pan gliding from pan_from to pan_to (-1 left .. 1 right); width adds decorrelation."""
    p = np.linspace(pan_from, pan_to, len(mono))
    ang = (p + 1) * np.pi / 4
    left, right = mono * np.cos(ang), mono * np.sin(ang)
    if width:
        d = int(0.004 * SR * width)
        right = np.concatenate([np.zeros(d), right[:-d]]) if d else right
    return np.stack([left, right], 1) * np.sqrt(2)


def sine(f, t, phase=0.0):
    return np.sin(2 * np.pi * np.cumsum(np.broadcast_to(f, t.shape)) / SR + phase)


# ------------------------------------------------------------------ sounds ---

def whoosh(dur=0.7, pitch=1.0, at=0.75):
    n = int(dur * SR)
    x = bandpass_sweep(rng.standard_normal(n), 250 * pitch, 3500 * pitch, q=1.6)
    x += 0.35 * bandpass_sweep(rng.standard_normal(n), 120 * pitch, 700 * pitch, q=1.2)
    return stereo(x * env_ad(n, at, 3.0), -0.6, 0.6, width=1)


def riser(dur=1.2, pitch=1.0):
    t = t_axis(dur)
    k = (t / dur) ** 2
    tone = sine(180 * pitch * 2 ** (3 * k), t) * 0.5 + sine(270 * pitch * 2 ** (3 * k), t) * 0.25
    x = bandpass_sweep(rng.standard_normal(len(t)), 300 * pitch, 6000 * pitch, q=2.5) + tone * 0.6
    fade = np.clip((dur - t) / 0.01, 0, 1)
    return stereo(x * k ** 1.5 * fade, 0, 0, width=1)


def hit(dur=0.7, pitch=1.0):
    t = t_axis(dur)
    sub = sine(70 * pitch * np.exp(-t * 6) + 38 * pitch, t) * np.exp(-t * 5)
    crack = highpass(rng.standard_normal(len(t)), 1500) * np.exp(-t * 60) * 0.6
    body = lowpass(rng.standard_normal(len(t)), 500) * np.exp(-t * 18) * 0.8
    x = np.tanh(1.8 * (sub + body + crack))
    return stereo(x, 0, 0, width=0.5)


def thump(dur=0.35, pitch=1.0):
    t = t_axis(dur)
    x = sine(110 * pitch * np.exp(-t * 14) + 55 * pitch, t) * np.exp(-t * 14)
    x += lowpass(rng.standard_normal(len(t)), 900) * np.exp(-t * 40) * 0.5
    return stereo(np.tanh(1.5 * x))


def boom(dur=2.5, pitch=1.0):
    t = t_axis(dur)
    x = sine(48 * pitch * (1 + 0.4 * np.exp(-t * 3)), t) * np.exp(-t * 1.6)
    x += lowpass(rng.standard_normal(len(t)), 220) * np.exp(-t * 2.5) * 0.9
    x += highpass(rng.standard_normal(len(t)), 2500) * np.exp(-t * 40) * 0.2
    return stereo(np.tanh(1.4 * x), 0, 0, width=2)


def zap(dur=0.18, pitch=1.0):
    t = t_axis(dur)
    crackle = highpass(rng.standard_normal(len(t)), 2000) * (rng.random(len(t)) < 0.015)
    buzz = np.sign(sine(120 * pitch, t)) * 0.35 + sine(240 * pitch, t) * 0.35
    x = (crackle * 2.0 * np.exp(-t * 40) + buzz * np.exp(-t * 14))
    x[: int(0.002 * SR)] *= np.linspace(0, 1, int(0.002 * SR))
    return stereo(np.tanh(2 * x), 0, 0, width=0.5)


def neon(frames, fps, pattern, pitch=1.0):
    """Hum gated by a frame pattern: "0:1,2:0,3:1" = on at 0, off at 2, on at 3 (levels 0..1)."""
    dur = frames / fps
    t = t_axis(dur)
    hum = sum(np.sin(2 * np.pi * 60 * pitch * h * t) / h for h in (2, 3, 4, 6, 8)) * 0.4
    hum = np.tanh(2.5 * hum) + highpass(rng.standard_normal(len(t)), 3000) * 0.04
    gate = np.zeros(len(t))
    keys = sorted((int(k), float(v)) for k, v in (p.split(":") for p in pattern.split(",")))
    for (f0, v), (f1, _) in zip(keys, keys[1:] + [(frames, 0)]):
        gate[int(f0 / fps * SR):int(f1 / fps * SR)] = v
    smooth = np.convolve(gate, np.ones(96) / 96, mode="same")  # 2 ms edges, no clicks
    x = hum * smooth
    for f0, v in keys:  # a crack on every switch-on
        if v > 0:
            z = zap(0.12, pitch)[:, 0]
            i = int(f0 / fps * SR)
            x[i:i + len(z)] += z[: len(x) - i] * v
    return stereo(x, 0, 0, width=0.5)


def tick(dur=0.05, pitch=1.0):
    t = t_axis(dur)
    x = sine(2400 * pitch, t) * np.exp(-t * 160) + highpass(rng.standard_normal(len(t)), 4000) * np.exp(-t * 400) * 0.6
    return stereo(x)


def blip(dur=0.07, pitch=1.0):
    t = t_axis(dur)
    x = np.sign(sine(660 * pitch, t)) * 0.5 * np.exp(-t * 55)
    return stereo(lowpass(x, 5000))


def glitch(dur=0.35, pitch=1.0):
    n = int(dur * SR)
    x = np.zeros(n)
    i = 0
    while i < n:
        seg = int(rng.uniform(0.008, 0.035) * SR)
        kind = rng.integers(3)
        t = np.arange(seg) / SR
        if kind == 0:
            s = np.sign(np.sin(2 * np.pi * rng.uniform(200, 1800) * pitch * t))
        elif kind == 1:
            s = rng.standard_normal(seg)
        else:
            s = np.zeros(seg)
        x[i:i + seg] = s[: n - i] * rng.uniform(0.3, 1.0)
        i += seg
    x = np.round(x * 6) / 6  # bit-crush
    return stereo(lowpass(x, 9000), -0.3, 0.3)


def sparkle(dur=0.7, pitch=1.0):
    t = t_axis(dur)
    x = np.zeros(len(t))
    for _ in range(9):
        f = rng.uniform(2500, 9000) * pitch
        start = rng.uniform(0, 0.25)
        env = np.where(t >= start, np.exp(-(t - start) * rng.uniform(8, 20)), 0)
        x += np.sin(2 * np.pi * f * t) * env * rng.uniform(0.3, 1)
    x += highpass(rng.standard_normal(len(t)), 7000) * np.exp(-t * 12) * 0.15
    return stereo(x, -0.2, 0.3, width=1)


def shing(dur=0.3, pitch=1.0):
    t = t_axis(dur)
    x = bandpass_sweep(rng.standard_normal(len(t)), 5000 * pitch, 2500 * pitch, q=3) * np.exp(-t * 14)
    x += sine(3100 * pitch, t) * np.exp(-t * 18) * 0.25
    return stereo(x * np.clip(t / 0.003, 0, 1), 0, 0, width=1)


def swell(dur=0.5, pitch=1.0):
    t = t_axis(dur)
    k = (t / dur) ** 3
    x = bandpass_sweep(rng.standard_normal(len(t)), 400 * pitch, 2500 * pitch, q=1.5) + sine(330 * pitch, t) * 0.3
    return stereo(x * k * np.clip((dur - t) / 0.004, 0, 1), 0, 0, width=1)


def zip_(dur=0.35, pitch=1.0):
    n = int(dur * SR)
    x = bandpass_sweep(rng.standard_normal(n), 800 * pitch, 9000 * pitch, q=4)
    return stereo(x * env_ad(n, 0.85, 2.0), -0.7, 0.7)


SOUNDS = {"whoosh": whoosh, "riser": riser, "hit": hit, "thump": thump, "boom": boom, "zap": zap, "tick": tick,
          "blip": blip, "glitch": glitch, "sparkle": sparkle, "shing": shing, "swell": swell, "zip": zip_}


LOUD_DB, CEILING = -15.0, 10 ** (-1 / 20)  # shared with sfx_library.py: loudest 300 ms at -15 dBFS RMS, peaks <= -1 dBFS


def write(x, out, loud_db=LOUD_DB):
    """Level by loudness (RMS of the loudest 300 ms, or of the whole sound if shorter) rather than the
    sample peak, so a click and a dense hit at the same volume_db sound about equally loud; then
    soft-limit peaks under -1 dBFS."""
    mono = x.sum(1) * np.sqrt(0.5)  # the same downmix ffmpeg uses, so this matches sfx_library.py's measure
    win = min(len(mono), int(0.3 * SR))
    rms = np.sqrt(np.convolve(mono ** 2, np.ones(win) / win, mode="valid")).max()
    x = x * 10 ** (loud_db / 20) / max(rms, 1e-9)
    x = np.where(np.abs(x) > CEILING * 0.7, np.sign(x) * (CEILING * 0.7 + CEILING * 0.3 * np.tanh(
        (np.abs(x) - CEILING * 0.7) / (CEILING * 0.3))), x)
    fade = min(len(x) // 4, int(0.003 * SR))  # no click at the very end
    x[-fade:] *= np.linspace(1, 0, fade)[:, None]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                    "-c:a", "pcm_s24le", str(out)], input=x.astype("<f4").tobytes(), check=True)
    return out


def kv(name, default):
    return float(flags[name]) if name in flags else default


if args[0] == "kit":
    d = Path(args[1])
    d.mkdir(parents=True, exist_ok=True)
    kit = {"whoosh-short": (whoosh, dict(dur=0.45)), "whoosh-long": (whoosh, dict(dur=0.9, pitch=0.8)),
           "riser-1s": (riser, dict(dur=1.0)), "riser-2s": (riser, dict(dur=2.0)), "hit": (hit, {}),
           "thump": (thump, {}), "boom": (boom, {}), "zap": (zap, {}), "glitch": (glitch, {}),
           "sparkle": (sparkle, {}), "shing": (shing, {}), "swell": (swell, {}), "zip": (zip_, {})}
    for i in range(8):  # rising pitches: one per letter / step
        kit[f"tick-{i + 1}"] = (tick, dict(pitch=2 ** (i / 12 * 2)))
        kit[f"blip-{i + 1}"] = (blip, dict(pitch=2 ** (i / 12 * 2)))
    for name, (fn, kw) in kit.items():
        write(fn(**kw), d / f"{name}.wav")
    print(f"{d}: {len(kit)} sounds")
elif args[0] == "neon":
    fps = 24000 / 1001 if flags.get("fps", "23.976") in ("23.976", "23.98") else float(flags["fps"])
    print(write(neon(int(flags["frames"]), fps, flags.get("pattern", "0:1"), kv("pitch", 1.0)), args[1], kv("loud", LOUD_DB)))
else:
    fn = SOUNDS[args[0]]
    kw = {"pitch": kv("pitch", 1.0)}
    if "dur" in flags:
        kw["dur"] = float(flags["dur"])
    if args[0] == "whoosh" and "at" in flags:
        kw["at"] = float(flags["at"])
    print(write(fn(**kw), args[1], kv("loud", LOUD_DB)))
