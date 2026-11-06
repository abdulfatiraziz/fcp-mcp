# Creative mode: new effects with no reference

Use this for the user's own vlogs and reels, or whenever they ask for something new rather than "like
this reel". Their vlogs shouldn't repeat the studied reels' effects: explore and invent. The studied recipes (cutout match transition, sign glow
and type-on, red block grid) are practice that taught the building blocks. Don't reuse them as
templates here.

Without a reference, three things stand in for one: the footage, the building blocks, and the user's taste.

## 1. Let the footage suggest the effects

Before planning anything, scan every clip and write a short list of what it offers, per clip and
timestamp:

| Look for | How | Suggests |
|---|---|---|
| People, animals, objects that can be lifted | Vision subject lift (`subject_cutout.py` on a few sample frames) | cutouts, silhouettes, echoes, freezes, portals |
| Text and signs, with boxes | Vision text recognition (`VNRecognizeTextRequest`): no script yet, write a small Swift tool like `subject_matte.swift` when first needed. Until then, read frames and measure by eye. | letters lighting up, type-on, a sign carrying the cut |
| Empty space: sky, walls, dark areas | luma / colour stats in regions (`band_luma.py`, numpy) | graphics or text in the gap, a type-on target |
| Lit things at night | luma key test (`key_matte.py`) | glow, flicker, neon reveals |
| Motion and its direction | frame differences or optical flow on a few frames | wipes that follow a passer-by or a car, motion-matched cuts |
| Shapes and colours that repeat across clips | compare positions and colours of the lifted subjects between clips | shape match cuts (round cup -> round headlight in the same spot) |
| Beats and hits in the music | onset / beat detection on the track (e.g. librosa via `uv run --with librosa`) | where cuts and effects land |

An effect is worth doing only if something in a shot asks for it.

## 2. Make new effects from the building blocks

The building blocks are:
- **Masks:** luma or channel key, Vision lift.
- **Looks:** real cutout, glow, white or colour silhouette, outline.
- **Moves:** type-on and draw-on (crop steps), block grid, window and scale, flicker, freeze, echo.
- **Timing:** lead frames, "on twos", on the beat.

Combine them into effects the reels never had. Starting points:
- **Silhouette portal**: a person becomes a white silhouette, the next shot plays inside it (the matte
  used as a window), then fills the frame.
- **Echo trail**: a moving subject's cutout repeated at frame offsets (say -4, -8, -12) with falling
  opacity, on a beat.
- **Freeze lift**: on a hit, the subject freezes as a cutout (a still from the matte) while the shot keeps
  moving behind, then rejoins.
- **Outline trace**: a glowing line drawn around the subject's edge (matte edge, drawn on) just before
  the cut.
- **Sign switch-on**: real text in the shot lights up letter by letter, boxes from text recognition.
- **Shape match**: two clips where similar shapes sit in the same spot, cut on the beat so one becomes
  the other.
- **Text behind subject**: a title placed under the person's cutout, so they walk in front of it.
New building blocks are fine too. When one works, add its script to `scripts/` and a line to
`references/more-effects.md`.

## 3. Let the user's taste replace the reference

- Propose 2-3 different ideas for the vlog, each tied to a specific moment ("at 0:42, the metro sign...").
- Build each as a short preview of 2-4 seconds around the moment (normal speed, then half speed), with
  its sound effects (`sound-design.md`). Send them to the user before building the full
  edit.
- They pick, reject or mix. Save what they liked and disliked, and why, to memory. Over a few vlogs this
  becomes their style, and later proposals should follow it.

## Rules that carry over from the studies

- **Motivated:** every effect comes from something in the shot, never pasted on.
- **Exact:** it sits exactly where that thing is, untransformed, and frame-locked across a cut.
- **On the beat:** effects start or resolve on a hit in the music.
- **Rare:** a few per minute, so each one lands. Plain cuts in between are part of the rhythm.
- **Varied:** don't repeat the same effect twice in a row, and don't open every vlog the same way.
- **Check by looking:** render, look at the frames yourself, fix what's off, then send.

## Where it goes

Each vlog gets its own `projects/<vlog name>/` folder with its clips, mattes, previews, spec and FCPXML,
like the other edits. Phone and Ray-Ban Meta clips are often VP9:
cut H.264 selects first (see SKILL.md).
