---
name: cutout-match-transition
description: >
  A Final Cut Pro effects toolkit, built as editable FCPXML through the final-cut-pro MCP. Core
  effect: the in-place cutout match transition. Before a cut, the next shot's key element (neon sign,
  tower, person) appears in the current shot truly masked (luma key or macOS Vision subject lift) at its
  exact next-shot position, flickers on, and stays locked through the cut. Also studied effects (signs
  glowing on, type-on onto the sky, red block grid, white silhouettes, window drop-in, letterbox,
  glint), sound effects synthesised to match each effect, and creative mode: inventing new effects from
  your own footage with no reference. Use whenever the user wants a mask or cutout transition, a person or
  object cutout with the background removed, a neon or letter reveal, silhouettes, a block-grid
  transition, sound effects made in code for an edit, a reel's effects recreated, or new creative
  effects for their vlogs or reels ("be creative", "surprise me", no reference), even if they only say
  "do that cutout thing".
---

# Cutout match transition

## What the effect is, and why exactness matters

A hard cut where the next shot's hero element arrives early. For about 0.5–0.8s before the cut, the
element (a sign, a landmark, a person) is composited into the current shot. It is isolated by a true
mask, sits where it will be in the next shot, and is revealed (letter by letter, bottom-up, rising from
a skyline) and flickered like neon. Then the cut swaps the whole background while the element doesn't
move. The eye stays on the element, so the cut feels seamless.

Two rules define the method (a first attempt that broke them was rejected):

1. **No rectangles.** A crop plus Screen blend leaves a visible box wherever the background isn't pure
   black. Use a real matte (alpha), not a cropped layer.
2. **Same position.** The overlay must be *untransformed* (no place, no scale), so the element is
   pixel-for-pixel where it sits in the next shot. Moving it somewhere "nicer" breaks the illusion.

A third rule makes it hold: the overlay must play the **same source frames** that the next shot
continues from, so nothing jumps at the cut.

## Workflow

Work at the project frame rate (all examples: 23.976, `F(n) = n*1001/24000 s`). Count frames on each
clip's own frame grid.

1. **Choose the cut and the element.** Pick a cut on a beat where the next shot has one readable hero
   element. Pick the lead `L` (12–20 frames) and the next shot's in-point `N` (its own frame number).
   Prefer a current shot whose area under the element is darker or calmer, so the element reads.

2. **Make the matte.** It's a ProRes 4444 clip whose frame `n` is source frame `first + n`:
   - **Lit or bright elements** (neon, signs, lit towers at night): `scripts/key_matte.py`, run with
     `uv run --no-project --with numpy --with pillow --with scipy python -I`. It applies a brightness
     key inside one or more windows (fractions of the frame, `"x0,y0,x1,y1;x0,y0,x1,y1"` for several
     elements in one matte); RGB stays the untouched source. Measure the element's and its
     surroundings' brightness first and set `lo`/`hi` between them. Set `lo` low enough to keep the
     coloured glow, or neon reads as pale cores (VACANCY needed 0.28, not 0.40).
   - **Coloured light next to grey light** (LED crowns against lit clouds): add `sat_lo sat_hi`
     (e.g. `0.30 0.45`). Only saturated light passes; enclosed blown-out white centres are filled back
     in, so the crowns stay solid while the clouds drop out.
   - **People, animals, objects**: macOS subject lifting. Compile the helper once:
     `swiftc -O -swift-version 5 -o scripts/bin/subject_matte scripts/subject_matte.swift`, then
     `scripts/subject_cutout.py <subject_matte> <clip> <out.mov> <first> <count>`. It uses a 3-frame
     *median* (anti-flicker without trails behind fast motion) plus a 1px choke and feather. It does
     about 13 frames/s at 1080p. Vision handles busy backgrounds (Times Square) as long as there is one
     clear subject.
   - Check alignment with `scripts/lock_check.py align …`. The expected frame must score clearly lowest.
     If a neighbour wins, the matte is off by one.

3. **Build it in FCP through the MCP** (`fcpxml_build_timeline`). The server is this repo
   (fcp-mcp); the skill lives in its `skills/` folder and is linked into `~/.claude/skills`. If its
   tools aren't loaded in the session, call it directly:
   `M=<path to fcp-mcp>; UV_PROJECT_ENVIRONMENT=~/.cache/fcp-mcp-venv uv run --directory $M python <skill>/scripts/call_mcp.py $M fcpxml_build_timeline @spec.json`
   (the venv lives in ~/.cache so the project folder stays clean).
   Templates are in `examples/`:
   - Next shot: `source_in = F(N)`.
   - Overlay: a connected clip, `lane 1`, `at = cut − L`, `duration = L`, `source_in = F(N − L − first)`
     (in the matte's own frames). Use `adjust.crop` = the element's window (only for the reveal),
     `hold: ["crop", "opacity"]`, keyframes for the reveal and flicker, **no** `place`/`scale`/`blend_mode`.
   - Captions go on a higher lane (4) so they stay above the cutouts.
   - Import with `import_into_fcp` / `fcpxml_import`. FCP must be on the current Space, and the MCP
     answers the "Which library?" dialog itself.

4. **Preview** (works even when FCP is unreachable):
   - Render the base cut: `scripts/render_preview.py <spec without overlays/titles> <captions dir> base.mp4`.
   - Composite the overlays: `uv run --no-project --with numpy --with pillow python -I scripts/render_cutouts.py <spec> base.mp4 <captions dir> out.mp4`.
     It follows the same rules as the FCPXML (crop/place/scale/opacity keys, per-property hold, Screen
     or straight-alpha normal).
   - Captions come from `scripts/caption.js` (`osascript -l JavaScript caption.js "TEXT" out.png 1280 720 29.3`).
   - Show the user a close-up: the transition at normal speed, then half speed.

5. **Verify the lock**: `scripts/lock_check.py lock <render> <cut frame> <matte> <matte frame before cut>`.
   Compare the element's change **across** the cut with its change **before** the cut, not with zero.
   Real footage gives *element 0.005 vs background 0.29*. Time-lapses twinkle every frame, so a skyline
   reading *before 0.26, across 0.23* is also locked. If the across-cut number jumps well above the
   before-cut number, the source frames or the position don't match.

## Planning a whole edit

- Keep the reference's cut lengths and the full music. Only cuts whose **outgoing** shot is at least
  about 12 frames long can carry a cutout; leave fast montage cuts (7 frames) as hard cuts.
- Reorder shots so that each long cut lands on a shot with one clean hero element: a lit landmark,
  a neon sign or lettering, one clear person roughly centred, or a lit skyline. Mixed element types
  (keyed lights plus one Vision person) keep it varied. 4–5 cutouts in 16s is plenty.
- The outgoing shot must be dark where the element lands. Check it with
  `scripts/band_luma.py <element window> clip@t clip@t …` and pick the lowest. Bridge lights read at
  0.03 over a night sky but vanish at 0.24 over a busy taxi street. Repeating an establishing shot
  for this is fine.
- Good elements: lit landmarks, neon and lettering, lit skylines and bridges against a night sky, and
  one clear person. Poor ones: signs inside defocused or bright clutter (the "NEW YORKER" sign in a
  bokeh pan), which no key separates. Use those as plain montage flashes.
- Avoid vertical (9:16) clips in a 16:9 edit. FCP pillarboxes them, while the preview renderer fills
  the frame, so the two disagree.
- Start luma mattes at source time 0, so matte time equals source time. Then
  `overlay source_in = next_in − lead` in seconds; Vision cutouts are offset by their first frame.
- Frame rates: with 29.97 sources on a 23.976 timeline, use leads that are multiples of 4 frames.
  20 timeline frames is exactly 25 source frames, so `F30(N − 1.25·L)` is exact and the handover is
  frame-perfect. 25/30.000 fps clips snap to their own grid and can be off by a fraction of a frame,
  which is invisible on static elements; for moving subjects prefer 23.976/29.97 clips.
- Examples to adapt: `examples/full-edit-5-cutouts.spec.json` (Tokyo/Sydney-style),
  `examples/nyc-5-cutouts.spec.json` (New York: Empire State crowns, moon and skyline, Vision woman,
  VACANCY, bridge lights) and `examples/rayban-vertical-5-cutouts.spec.json` (vertical 9:16 reel
  from Ray-Ban Meta glasses footage: menu, barista, suit selfie, plane, friend).

## Your own footage (phone, Ray-Ban glasses) and vertical reels

- **Steady shots first.** Keep every shot clean and stable, never constantly moving. Glasses footage
  moves with the wearer's head, so:
  - run `scripts/steady.py <out.json> <clips...>` (camera shift per second, steadiest windows per clip)
    and take shots from the calm stretches;
  - then lock each shot off by moving the crop window against the measured shift inside the crop
    margin;
  - no continuous pushes, bumps, springs or tilts. Effects land on beats as short hits.
- **16:9 from Ray-Ban 3:4** (a clean look for YouTube): the clip in its own 3:4 shape at full height
  (810x1080), cropped in about 1.35x, white sides. Not a small card, not cropped to 16:9. The crop-in also
  gives the stabiliser its margin. Newer Ray-Ban clips are HEVC HLG HDR: tone-map to SDR with VideoToolbox
  (`scale_vt=...:color_transfer=bt709`).
- **Codec:** phone and glasses clips are often VP9, which FCP can't import. Cut short H.264 *selects*
  (3–7s around each in-point) with the hardware encoder:
  `nice -n 15 ffmpeg -ss S -t D -i raw.mp4 -an -c:v h264_videotoolbox -b:v 24M -pix_fmt yuv420p -fps_mode passthrough select.mp4`.
  Build mattes and the spec from the selects. Keep `broll/SOURCES.txt` mapping each select to its
  original and start time.
- **Vertical:** set `width: 1080, height: 1920` and `conform: "fill"` on every clip *and* overlay
  (3:4 footage would otherwise get bars). The preview renderers switch to 720×1280 automatically, so
  render caption PNGs at 720×1280 too. `lock_check` fill-crops mattes the same way.
- **Vision on everyday footage:** it lifts people and handheld objects well (a menu with both hands, a
  mirror selfie). It may also lift a second subject (a laptop in the foreground): crop the overlay
  window to the one you want. It finds nothing in lit structures at night (a plane's jet bridge):
  brightness-key those instead.
- **Brightness:** night footage from glasses is exposed brighter than it looks. Measure with
  `band_luma.py`; opaque Vision cutouts still read over mid-dark backgrounds.
- **30 fps sources on a 23.976 timeline:** one timeline frame is 1.25 source frames, so consecutive
  frames step 1 or 2 source frames. On handheld footage, an across-cut number up to about 1.5× the
  before-cut number is still a lock. In-points: `F30(n) = n/30 s`, and the overlay starts at
  `next_in − round(lead × 1.25125)` source frames.
- **Heat:** run heavy steps with `nice -n 10` and ffmpeg `-threads 2`, so the fan stays down.
- **Other aspect ratios of a finished vertical reel** (16:9, 4:3): copy the spec, set
  `width`/`height`, switch every `conform` to `"fit"` (whole clip, no stretch or crop) and set
  `background: "#FFFFFF"` (or any hex). The MCP then lays a solid colour layer under everything up to
  the end card, so the end card stays black and white captions still read. The cutouts fit the same way,
  so they stay in place. Example: `examples/rayban-16x9-fit-white.spec.json`.
- The preview renderers mirror FCP's spatial conform (fill crops; fit pads with the background colour)
  at any aspect (long side 1280). They map crop boxes from the clip's own frame, like FCPXML.

## Reveal and flicker recipes (frames from the overlay's start)

- **Neon letters** (TELEPHONE): crop `x1` steps across the word, one letter per frame (hold), then
  opacity `10: 0.15, 11: 1, 13: 0.5, 14: 1`. Lead 16.
- **Person materialising**: crop `y0` 0.75 → 0.5 → 0.25 → 0 over frames 0–3 (hold), opacity
  `0: 1, 1: 0, 2: 0.8, 3: 0, 4: 1, 9: 0.4, 10: 1`. Lead 20.
- **Sign switching on top-down** (GOWINGS-style): crop `y1` in 5–6 steps (hold) plus a late flicker. Two
  copies left and right echo a building with two signs.
- **Landmark rising from a skyline**: animate `place` up *and* crop `y1` down linearly with the same
  timing, so the visible bottom edge stays on the horizon line (bottom = place_y + (y1 − cy)·scale).
  This is the one recipe that moves the element; use it only when the element isn't in the next shot's
  position anyway.

## Other effects in this toolkit

`references/more-effects.md` has two more reels rebuilt in New York, with timings and settings:
- **Vlog intro** (`examples/nyc-vlog-intro-sign-glow-type-on.spec.json`): signs in the shot switch on one letter at a time with a
  neon glow (`key_matte.py --key=…` plus `stylize_matte.py glow`). Then the next shot's sign types onto
  the sky at its exact position as a still of the cut frame, its tubes draw on, and the cut lands on
  the real sign. Also a glint and a letterbox slide-in (`graphics.py`).
- **Sydney** (`examples/nyc-red-block-grid-silhouettes.spec.json`): a red block grid (`block_grid.py`) closes over shot A. A's
  person stays on top as a Vision cutout. B's person appears as a flickering white silhouette
  (`stylize_matte.py silhouette`) and the grid opens around them. Also a window drop-in and white
  silhouette flashes.
Treat them as studies, not templates.

## Sound effects: every effect gets its sound

The reference reels had their effects' sounds baked into the music. Your own vlogs won't, so add them.
`scripts/sfx.py` synthesises them in code (no licences): whooshes, risers, hits, thumps, boom, neon zaps
and gated hum, ticks and blips at rising pitches, glitch, sparkle, shing, swell, zip. Place them as
connected audio clips (`audio_role: "effects"`, lanes -2 and below, `volume_db`) on the same frame
as the visual event:
- whooshes peak on the cut;
- swells and risers end on it;
- a neon hum flickers with the glow's opacity pattern.
Recorded sounds come from a shared library of free Mixkit WAVs and Freesound CC0 sounds
(`sfx_library.py`, kept in one shared folder such as `~/sfx-library/`):
- `lib.use(role, sfx_dir, seed)` picks a recorded whoosh, hit, boom, riser, glitch or sparkle, trims and
  levels it, and records where it should align;
- `sfx_place.place()` lines every sound up on its frame.
Use recorded sounds for air and impact; keep code for neon, ticks, blips and swells.
`render_preview.py` mixes them all, so previews play with sound. Read `references/sound-design.md` for
the effect-to-sound table, levels (effects about 7-8 dB under the music) and the onset check.

## Creative mode: new effects for your vlogs, no reference needed

For the user's own vlogs and reels, don't repeat the studied effects. Explore and invent. Read `references/creative-mode.md` first. In short:
1. **Footage first.** Scan every clip and note what it offers: people and objects Vision can lift, text
   and signs, empty sky or walls, lit things, motion direction, shapes that repeat across clips, and
   beats in the music.
2. **Recombine the building blocks.** Masks, cutouts, silhouettes, glow, type-on, grid, window, echo,
   freeze and flicker combine into effects the reels never had: a silhouette portal, an echo trail, a
   freeze lift, an outline trace, a shape match cut, text behind the subject.
3. **The user's taste replaces the reference.** Propose 2-3 ideas tied to specific moments and send
   short previews. Build the full edit from their picks, and save what they liked and disliked to
   memory.
The rules from the studies still hold:
- every effect comes from something in the shot;
- it sits exactly where that thing is;
- it lands on a beat;
- only a few per minute.

## Choosing sources

Free stock footage: Mixkit (no key, free licence, no attribution needed).
- Find ids with a few tag searches (`https://mixkit.co/free-stock-video/<tag>/`, then grep
  `href="/free-stock-video/<slug>-<id>/"`). The site returns HTTP 429 after a few dozen page requests,
  so search sparingly.
- Preview and choose from the CDN, which isn't rate-limited: `scripts/mixkit_sheet.py ids.txt <dir>`
  builds a numbered contact sheet from 360p previews.
- Download `https://assets.mixkit.co/videos/<id>/<id>-1080.mp4`, falling back to `-720` and `-360`.
  Many clips are free only up to 720p, and their pages list no 1080 file. That's fine for these reels:
  make each clip's mattes at its own size and FCP scales both the same way. Newer clips (id 100000+)
  live under the page's `active_storage` URL.
- Keep a `SOURCES.txt` with the page links and licence.

Night shots with dark backgrounds make the cleanest luma mattes. For people, any clear single subject
works with Vision.

## Details worth knowing

Read `references/fcpxml-notes.md` before hand-editing FCPXML or debugging an import. It covers units
(crop and position are percent of frame height), blend mode numbers, keyframe time base, DTD element
order, and macOS / FCP gotchas (Spaces, Accessibility, AVAssetReader off-by-one, xmllint paths).

## Where things live

Keep each edit in its own folder, `projects/<name>/`, with `broll/`, `music/`, `preview/`, a
`SOURCES.txt`, the spec JSON and the FCPXML. The specs in `examples/` use `/path/to/projects/...` for
media: point them at your own clips and mattes before building.
