"""Place sound-effect cues on the timeline as connected audio clips (for a project's make_spec.py).

    from sfx_place import place
    items = place([("whoosh-short", 189, -10), ("hit", 189, -6)], sfx_dir, sec)

A cue is (sound file stem in sfx_dir, timeline frame, volume_db). `sec(frame)` converts timeline frames
to seconds. Each sound is aligned to its frame by its kind:
  - whooshes peak on it;
  - risers and swells end on it;
  - everything else starts its transient on it.
Recorded sounds carry their lead in sfx_dir/library.json (written by sfx_library.Library.use).
Overlapping sounds get separate lanes below the music (-2, -3, ...) so FCP keeps them where placed.
Plain python3, no dependencies.
"""
import json, wave
from pathlib import Path

# Synthesised kit (sfx.py): seconds from the file's start to the moment that lands on the cue.
KIT_LEAD = {"whoosh-short": 0.45 * 0.75, "whoosh-long": 0.9 * 0.75, "swell": 0.5, "riser-1s": 1.0, "riser-2s": 2.0}


def length(path):
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def place(cues, sfx_dir, sec, first_lane=-2):
    sfx_dir = Path(sfx_dir)
    meta_path = sfx_dir / "library.json"
    lead = {**KIT_LEAD, **{k: v["lead"] for k, v in (json.loads(meta_path.read_text()) if meta_path.exists() else {}).items()}}
    lanes, items = [], []
    for name, frame, db in sorted(cues, key=lambda c: c[1]):
        path = sfx_dir / f"{name}.wav"
        start = max(0.0, sec(frame) - lead.get(name, 0.0))
        end = start + length(path)
        k = next((i for i, busy in enumerate(lanes) if busy <= start), len(lanes))
        if k == len(lanes):
            lanes.append(end)
        else:
            lanes[k] = end
        items.append({"path": str(path), "name": f"SFX: {name}", "at": round(start, 4), "lane": first_lane - k,
                      "audio_role": "effects", "volume_db": db})
    return items
