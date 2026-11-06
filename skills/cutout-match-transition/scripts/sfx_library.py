"""Recorded sound effects from free libraries: fetch once into a shared library, pick per effect, prep.

sfx_library.py fetch <library dir> [--mixkit=whoosh,swoosh,...] [--freesound=whoosh,riser,...] [--pages=1]
    Downloads Mixkit sound effects (full WAVs, Mixkit free licence) and Freesound CC0 sounds (HQ mp3
    previews) into <library dir>/mixkit and /freesound. Every file is split into events, each with its
    onset and loudest point measured, and listed in index.json (plus SOURCES.txt). Needs numpy:
    uv run --no-project --with numpy python -I sfx_library.py fetch ...
sfx_library.py reindex <library dir>
    Re-measure every file already downloaded (needs numpy), e.g. after the analysis changes.
sfx_library.py list <library dir> [role]
    Show the candidates for a role, best first.

From a project's make_spec.py (plain python3; prep only needs ffmpeg):
    from sfx_library import Library
    lib = Library(<library dir>)
    name = lib.use("whoosh", <project>/sfx, seed=0)   # pick, trim, level, copy; returns the file stem
Then cue `name` like any kit sound. `use` records each file's alignment lead in <project>/sfx/library.json
(whooshes peak on the cue, hits attack on it, risers end on it) and its source in sfx/SOURCES.txt.
Different seeds give different sounds for the same role.
"""
import json, re, subprocess, sys, time
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
MIXKIT_LICENSE = "Mixkit Sound Effects Free License (https://mixkit.co/license/#sfxFree)"
CC0 = "CC0 1.0 (https://creativecommons.org/publicdomain/zero/1.0/)"

# Role -> library tags, title words that fit (a title must contain one), words that mean another kind of
# sound, alignment, and duration range (seconds) of the event.
ROLES = {
    "whoosh": dict(tags=["whoosh", "swoosh", "swish", "transition"], words=["whoosh", "woosh", "swoosh", "swish", "swipe"],
                   avoid=["impact", "punch", "glitch", "click", "explosion", "boom", "riser", "hit"], align="peak",
                   dur=(0.25, 1.6)),
    "hit": dict(tags=["impact", "hit", "cinematic", "boom"], words=["impact", "hit", "punch", "slam", "thud"],
                avoid=["whoosh", "woosh", "swoosh", "swish", "glitch", "click", "ball", "whistle"], align="onset",
                dur=(0.3, 2.5)),
    "thump": dict(tags=["impact", "hit"], words=["thud", "thump", "punch", "knock", "hit"],
                  avoid=["whoosh", "woosh", "swoosh", "swish", "glitch", "click", "ball", "whistle"], align="onset",
                  dur=(0.1, 0.9)),
    "boom": dict(tags=["boom", "cinematic", "impact"], words=["boom", "explosion", "impact", "thunder", "deep"],
                 avoid=["whoosh", "woosh", "swoosh", "laser", "tape", "rewind"], align="onset", dur=(1.2, 5.0)),
    "riser": dict(tags=["riser"], words=["riser", "rise", "rising", "build", "tension", "uplifter"],
                  avoid=["impact", "hit"], align="end", dur=(0.8, 4.0)),
    "glitch": dict(tags=["glitch"], words=["glitch", "digital", "error", "static"], avoid=[], align="onset",
                   dur=(0.1, 1.2)),
    "sparkle": dict(tags=["magic"], words=["sparkle", "shimmer", "twinkle", "glitter", "chime"],
                    avoid=["notification", "ring", "slide", "stone", "arcade"], align="onset", dur=(0.3, 2.5)),
    "click": dict(tags=["click"], words=["click", "tick", "tap", "button"], avoid=[], align="onset", dur=(0.02, 0.4)),
    "shutter": dict(tags=["camera"], words=["shutter", "camera", "photo"], avoid=[], align="onset", dur=(0.05, 1.2)),
}
# Character words: fine for a game, wrong for a city vlog unless asked for.
CHARACTER = ["fire", "laser", "spell", "fairy", "cartoon", "comic", "game", "arcade", "funny", "ghost", "horror",
             "monster", "sword", "soccer", "creature", "pixel", "chiptune", "8-bit", "8 bit", "retro"]
PAD_BEFORE, PAD_AFTER = 0.015, 0.08  # seconds kept around each event


def get(url, out=None):
    cmd = ["curl", "-sfL", "--retry", "3", "--retry-all-errors", "--retry-delay", "2", "-A", UA, "--max-time", "60", url]
    if out:
        return subprocess.run(cmd + ["-o", str(out)], check=False).returncode == 0 and Path(out).stat().st_size > 0
    return subprocess.run(cmd, capture_output=True, check=True).stdout.decode("utf-8", "replace")


# ------------------------------------------------------------------ fetch ---

def mixkit_cards(html):
    for card in html.split('class="item-grid-card ')[1:]:
        m = re.search(r'data-audio-player-item-id-value="(\d+)"', card)
        t = re.search(r'item-grid-card__title">\s*([^<]+?)\s*</h2>', card)
        if m and t:
            yield {"source_id": m.group(1), "title": t.group(1),
                   "tags": re.findall(r'href="/free-sound-effects/([a-z0-9-]+)/">', card)}


def freesound_results(html):
    for block in html.split('class="bw-player"')[1:]:
        attr = dict(re.findall(r'data-([a-z0-9-]+)="([^"]*)"', block[:3000]))
        if "sound-id" in attr and "mp3" in attr:
            rating = re.search(r'Average rating of ([\d.]+)', block[:6000])
            yield {"source_id": attr["sound-id"], "title": attr.get("title", ""), "user": attr.get("username", ""),
                   "preview": attr["mp3"].replace("-lq.mp3", "-hq.mp3"), "downloads": int(attr.get("num-downloads") or 0),
                   "rating": float(rating.group(1)) if rating else 0.0}


def events(path):
    """Split a file into sound events (silence gaps), with onset and loudest point in each."""
    import numpy as np

    sr = 48000
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, "<f4")
    if not len(x):
        return []
    hop = 480  # 10 ms
    env = np.sqrt(np.add.reduceat(x.astype(np.float64) ** 2, np.arange(0, len(x), hop)) / hop)
    db = 20 * np.log10(np.maximum(env, 1e-9))
    top = db.max()
    active = db > top - 35
    out, i, n = [], 0, len(active)
    while i < n:
        if not active[i]:
            i += 1
            continue
        j = i
        while j < n and (active[j] or active[j:j + 12].any()):  # bridge gaps under 120 ms
            j += 1
        seg = db[i:j]
        energy = env[i:j] ** 2  # loudness: RMS of the loudest 300 ms (30 blocks), or of the whole event
        w = min(30, len(energy))
        loud = 10 * np.log10(max(np.convolve(energy, np.ones(w) / w, mode="valid").max(), 1e-18))
        start, end = max(0.0, i * hop / sr - PAD_BEFORE), min(len(x) / sr, j * hop / sr + PAD_AFTER)
        pk = i + int(seg.argmax())
        peak_t = pk * hop / sr - start
        # Attack: the steepest rise before the peak, i.e. the hit itself, not any air or noise before it.
        rise = np.diff(env[i:pk + 1])
        attack_t = (i + int(rise.argmax()) + 1) * hop / sr - start if len(rise) else i * hop / sr - start
        if end - start >= 0.05:
            out.append({"start": round(start, 4), "end": round(end, 4), "dur": round(end - start, 4),
                        "onset": round(i * hop / sr - start, 4), "attack": round(attack_t, 4), "peak_t": round(peak_t, 4),
                        "peak_db": round(float(seg.max()), 2), "loud_db": round(float(loud), 2)})
        i = j
    return out


def reindex(lib):
    """Re-measure every downloaded file (after a change to events()), keeping its metadata."""
    lib = Path(lib)
    old = json.loads((lib / "index.json").read_text())
    keep = ("source", "source_id", "title", "tags", "query", "rating", "downloads", "page", "license", "file")
    files = {}
    for e in old:
        files.setdefault(e["file"], {k: e[k] for k in keep})
    index = [{"id": f"{m['source']}-{m['source_id']}-{k}", **m, **ev}
             for f, m in files.items() for k, ev in enumerate(events(lib / f))]
    (lib / "index.json").write_text(json.dumps(index, indent=1))
    print(f"{lib}: {len(files)} files, {len(index)} events re-measured")


def fetch(lib, mixkit_tags, freesound_tags, pages):
    lib = Path(lib)
    (lib / "mixkit").mkdir(parents=True, exist_ok=True)
    (lib / "freesound").mkdir(parents=True, exist_ok=True)
    index_path = lib / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else []
    have = {(e["source"], e["source_id"]) for e in index}
    found = {}
    for tag in mixkit_tags:
        for page in range(1, pages + 1):
            url = f"https://mixkit.co/free-sound-effects/{tag}/" + (f"?page={page}" if page > 1 else "")
            for card in mixkit_cards(get(url)):
                found.setdefault(("mixkit", card["source_id"]), {**card, "query": tag})
            time.sleep(1.0)  # the website rate-limits; the asset CDN doesn't
    for tag in freesound_tags:
        for page in range(1, pages + 1):
            url = (f"https://freesound.org/search/?q={tag}&f=license%3A%22Creative+Commons+0%22"
                   + (f"&page={page}" if page > 1 else ""))
            for r in freesound_results(get(url)):
                found.setdefault(("freesound", r["source_id"]), {**r, "query": tag, "tags": [tag]})
            time.sleep(1.0)
    added = 0
    for (source, sid), meta in found.items():
        if (source, sid) in have:
            continue
        if source == "mixkit":
            f = lib / "mixkit" / f"{sid}.wav"
            ok = f.exists() or get(f"https://assets.mixkit.co/active_storage/sfx/{sid}/{sid}.wav", f)
            page, lic = f"https://mixkit.co/free-sound-effects/{meta['tags'][0] if meta['tags'] else meta['query']}/", MIXKIT_LICENSE
        else:
            f = lib / "freesound" / f"{sid}.mp3"
            ok = f.exists() or get(meta["preview"], f)
            page, lic = f"https://freesound.org/people/{meta['user']}/sounds/{sid}/", CC0
        if not ok:
            continue
        for k, ev in enumerate(events(f)):
            index.append({"id": f"{source}-{sid}-{k}", "source": source, "source_id": sid, "title": meta["title"],
                          "tags": meta["tags"], "query": meta["query"], "rating": meta.get("rating"),
                          "downloads": meta.get("downloads"), "page": page, "license": lic,
                          "file": str(f.relative_to(lib)), **ev})
        added += 1
    index_path.write_text(json.dumps(index, indent=1))
    sources = sorted({(e["source"], e["source_id"], e["title"], e["page"], e["license"]) for e in index})
    (lib / "SOURCES.txt").write_text("".join(f"{src}-{sid}  {title}  {page}  {lic}\n" for src, sid, title, page, lic in sources))
    print(f"{lib}: {added} new files, {len(index)} events in index.json")


# ------------------------------------------------------------- pick / prep ---

class Library:
    def __init__(self, path):
        self.path = Path(path)
        self.index = json.loads((self.path / "index.json").read_text())

    def candidates(self, role):
        r = ROLES[role]
        lo, hi = r["dur"]

        def score(e):
            title = e["title"].lower()
            s = 3.0 * any(w in title for w in r["words"]) + 1.0 * any(t in e["tags"] or t == e["query"] for t in r["tags"])
            s += 1.0 if e["source"] == "mixkit" else 0.6 * min(e.get("rating") or 0, 5) / 5  # full-quality WAVs first
            s -= 4.0 * any(w in title for w in r["avoid"]) + 1.5 * any(w in title for w in CHARACTER)
            s -= 1.0 * any(w in title for w in ("loop", "ambience", "music"))
            if r["align"] == "end":  # a riser should build to its end, not peak early
                s += 1.0 if e["peak_t"] > 0.7 * e["dur"] else -1.0
            return s

        fits = [e for e in self.index if lo <= e["dur"] <= hi and e["peak_db"] > -40]
        ranked = sorted(fits, key=lambda e: (-score(e), e["id"]))
        return [e for e in ranked if score(e) >= 3.0]

    def use(self, role, out_dir, seed=0, pitch=1.0, loud_db=-15.0, max_dur=None):
        """Pick the seed-th candidate for `role`, trim it to its event, level it, copy it into out_dir."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        meta_path = out_dir / "library.json"
        meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
        # A recording already used by another role in this project isn't reused (no punch as both hit and thump).
        taken = {m["id"].rsplit("-", 1)[0] for n, m in meta.items() if not n.startswith(f"{role}-")}
        cands = [e for e in self.candidates(role) if e["id"].rsplit("-", 1)[0] not in taken]
        if not cands:
            raise ValueError(f"No library sound fits role {role!r}; fetch more tags.")
        e = cands[seed % len(cands)]
        name = f"{role}-{e['source']}-{e['source_id']}" + (f"-p{pitch:g}" if pitch != 1.0 else "")
        out = out_dir / f"{name}.wav"
        dur = min(e["dur"], max_dur) if max_dur else e["dur"]
        if not out.exists():
            # Same levelling as sfx.py: the event's loudest 300 ms (RMS, measured at fetch) to -15 dBFS,
            # peaks limited under -1 dBFS (latency compensated, so the onset doesn't move).
            gain = loud_db - e["loud_db"]
            af = [f"atrim=start={e['start']}:duration={dur}", "asetpts=PTS-STARTPTS"]
            if pitch != 1.0:  # tape-style: pitch and speed change together, like a varispeed
                af += [f"asetrate={round(48000 * pitch)}", "aresample=48000"]
            af += [f"volume={gain:.2f}dB", "alimiter=limit=0.891:attack=1:release=30:level=false:latency=1", "afade=t=out:st=" + f"{max(0.0, dur / pitch - 0.01):.4f}:d=0.01"]
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(self.path / e["file"]), "-ar", "48000", "-af", ",".join(af),
                            "-ac", "2", "-c:a", "pcm_s24le", str(out)], check=True)
        align = ROLES[role]["align"]
        lead = {"peak": e["peak_t"], "onset": e.get("attack", e["onset"]), "end": dur}[align] / pitch
        meta[name] = {"lead": round(lead, 4), "align": align, "id": e["id"], "title": e["title"], "page": e["page"],
                      "license": e["license"]}
        meta_path.write_text(json.dumps(meta, indent=1))
        (out_dir / "SOURCES.txt").write_text("Recorded sound effects in this folder (the rest are synthesised by sfx.py):\n" + "".join(
            f"{n}.wav  {m['title']}  {m['page']}  {m['license']}\n" for n, m in sorted(meta.items())))
        return name


if __name__ == "__main__":
    flags = dict(a[2:].split("=", 1) for a in sys.argv[1:] if a.startswith("--"))
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args[0] == "fetch":
        fetch(args[1], [t for t in flags.get("mixkit", "").split(",") if t],
              [t for t in flags.get("freesound", "").split(",") if t], int(flags.get("pages", 1)))
    elif args[0] == "reindex":
        reindex(args[1])
    elif args[0] == "list":
        lib = Library(args[1])
        for role in ([args[2]] if len(args) > 2 else ROLES):
            c = lib.candidates(role)
            print(f"{role}: {len(c)} candidates")
            for e in c[:6]:
                print(f"   {e['id']:24s} {e['dur']:5.2f}s  peak@{e['peak_t']:.2f}s  {e['title']}")
