# Sound design: an effect needs its sound

The reference reels had their sound effects baked into the music, so the recreations got them for free.
Your own vlogs won't. Every effect needs a sound that lands on the same frame, or the effect feels
weightless. All the sounds here are synthesised in code (`scripts/sfx.py`), so there are no licences
and any sound can be remade at any length or pitch.

## Toolkit

- `sfx.py kit <project>/sfx`: the standard kit, 29 sounds, 48 kHz 24-bit stereo:
  - whoosh-short, whoosh-long;
  - riser-1s, riser-2s;
  - hit, thump, boom;
  - zap;
  - glitch, sparkle, shing, swell, zip;
  - tick-1..8 and blip-1..8 (rising pitch).
- `sfx.py <sound> out.wav --dur= --pitch= --seed=` makes a single variant.
- `sfx.py neon out.wav --fps= --frames= --pattern="0:1,2:0,3:1"` makes a mains hum gated frame by frame,
  with a crack on every switch-on. Give it the same pattern as the glow overlay's opacity keys.
- Each project has a `make_sfx.sh` (the kit plus its variants). Its `make_spec.py` calls `place()`
  from `sfx_place.py`:
  - it takes (sound, frame, volume_db) cues;
  - it shifts whooshes so they peak on the frame, and swells and risers so they end on it;
  - it gives overlapping sounds separate lanes below the music (-2, -3, ...).
- **Every file, code or recorded, is levelled the same way**: the loudest 300 ms (or the whole sound
  if it's shorter) at -15 dBFS RMS, measured on ffmpeg's mono downmix, with peaks limited under
  -1 dBFS. So a `volume_db` in a cue means the same for a synthesised tick and a recorded hit.
  - Levelling by sample peak was wrong: a thin recorded punch sounded 4 dB quieter than a dense synth
    hit at the same peak.
  - Levelling by the loudest 10 ms over-weighted sharp transients.
  - Measuring mono as (L+R)/2 in one tool and ffmpeg's (L+R)x0.707 in the other put 3 dB between them.
- The MCP writes them as connected audio clips: `audio_role: "effects"`, with `volume_db` as FCP's
  `adjust-volume`. Each one stays a separate clip you can move or re-level in FCP.
- `render_preview.py` mixes music plus every effect clip at its position and volume, so previews play
  the sound design. With no music item, the effects play over silence: an "SFX demo".

## Recorded sounds: the library mode

Code is right for synthetic sounds that must follow the picture exactly: neon hum gated per frame,
ticks per letter, blips per grid step, zips, swells. Recorded sounds are better for anything with real
air, mass or texture: whooshes, impacts, booms, glints, glitches, camera shutters. So use both.

- **Shared library**: one folder of your choice, e.g. `~/sfx-library/`,, built by
  `sfx_library.py fetch <dir> --mixkit=<tags> --freesound=<tags>`.
  - **Mixkit:** full 24-bit WAVs under its free licence; tags like whoosh, swoosh, swish, transition,
    impact, hit, boom, cinematic, glitch, magic, click, camera.
  - **Freesound:** CC0 only, high-quality mp3 previews; use it for what Mixkit lacks, like risers.
  - Every file is split into events (silence gaps), and each event's onset, attack (steepest rise),
    loudest point and loudness are measured into `index.json`. `SOURCES.txt` lists the source and
    licence of every file.
  - After changing the analysis, run `sfx_library.py reindex <dir>`. It re-measures without downloading
    again (about 15 s for 340 files).
- **Roles**: `sfx_library.py list <dir> [role]` shows the ranked candidates. The roles are whoosh, hit,
  thump, boom, riser, glitch, sparkle, click and shutter. Ranking:
  - the title must name the role;
  - words that mean another kind of sound count against it (no "swoosh" as a hit);
  - "character" words (fire, laser, spell, cartoon, game...) are pushed down, so city vlogs get neutral
    sounds;
  - Mixkit WAVs beat Freesound previews.
- **In make_spec.py**: `lib.use(role, <project>/sfx, seed=n)` picks the n-th candidate, trims it to
  its event, levels it, copies it into the project and returns its name. Cue that name like any kit
  sound. `place()` (`sfx_place.py`) aligns it from its measured lead:
  - whooshes peak on the cue;
  - hits *attack* on it: the steepest rise, not the first sound, because many recorded hits have a
    little air before the punch;
  - risers end on it.
  Use a different seed for each repeat, so no two whooshes are the same file. A recording already used
  for one role in a project isn't picked for another.
- `<project>/sfx/SOURCES.txt` records which recorded sounds an edit uses, with licences.
- Other sources tested by script are in memory: Kenney and OpenGameArt CC0 zips work. Pixabay and
  Sonniss block scripts. BBC is non-commercial only.

## Which sound for which effect

In the Sound column, *(rec)* marks the sounds that are best taken from the recorded library; the rest
come from code.

| Visual event | Sound | Typical volume_db |
|---|---|---|
| Hard cut that should hit (cutout lands, the real sign appears) | hit *(rec)* on the cut frame (+ whoosh *(rec)* peaking there) | -6 / -10 |
| Soft scene change, window filling the frame, letterbox locking | thump *(rec)* | -8 to -14 |
| Sign or letters switching on | `zap` (pitch up per letter: `zap-2`, `zap-3`) | -8 to -15 |
| Neon staying on, flickering | `neon` gated with the overlay's opacity pattern | -8 to -16 |
| Type-on, one letter per step | `tick-1..8` rising | -10 |
| Line drawing on (tubes, arrow, outline trace) | `zip` | -10 |
| Block grid closing | riser *(rec)* ending as it locks + thump *(rec)* | -14 / -10 |
| Cells dropping out | `blip` falling in pitch, every 4 frames | -16 |
| Cells opening | `blip` rising in pitch | -16 |
| Solid colour slam | hit *(rec)* | -6 |
| White silhouette appearing or flashing | `shing` | -10 to -15 |
| Something about to appear (silhouette, person materialising) | `swell` ending on the appearance | -12 |
| Glint | sparkle *(rec)* starting a frame before | -8 |
| Glitch, stutter, digital break | glitch *(rec)* | -14 |
| Fast montage cuts (3-5 frames) | `tick` or `blip`, quiet, or nothing | -15 to -17 |
| Last frame, black end card | boom *(rec)* | -6 |

## Rules

- **On the frame.** Transients land on the visual event's frame. Whooshes *peak* on the cut. Swells and
  risers *end* on it. Check onsets: the steepest rise of the mix's 5 ms envelope should be within one
  frame of each cue. Both NYC demos measured within 0.25 frames, code and recorded, except where
  a louder sound sits right next to the one being measured.
- **Follow the picture's pattern.** A flicker's sound flickers with it: the same frames, the same dips.
- **Pitch tells direction.** Pitch rises across a sequence that builds (letters, cells opening) and falls
  for things disappearing. Repeats get a new `--pitch` or `--seed`, so they don't sound copy-pasted.
- **Under the music, not over it.** Mix so the effects-only mix sits about 6-10 dB under the music
  (around -20 to -24 LUFS against a -14 LUFS bed). Recorded sounds decay naturally, so a
  recorded + code mix reads a few LUFS lower overall than an all-code one even when every cue matches.
  Compare cue moments (the loudest 300 ms around each cue), not the integrated figure. Hits and booms
  may poke through, everything else sits below. Keep the final mix at -14 LUFS for social, peaking at or below -1 dBTP. Measure with
  `ffmpeg -af ebur128=peak=true:framelog=quiet`.
- **Restraint.** Not every cut needs a sound. Give the big moments full weight, the montage at most a
  tick, and leave silence where the music carries it.
- **Don't double up.** When the music already has effects in it (a recreation using a reel's own audio),
  don't add more over it. Make a separate "SFX demo" project instead, like the two NYC edits.
- **In creative mode** (`creative-mode.md`), design the sound with the idea, so every preview option
  already plays with its sound.
