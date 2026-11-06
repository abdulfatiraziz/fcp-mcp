# More effects: sign glow + type-on, red block grid, white silhouettes

Two more reference reels were studied frame by frame and rebuilt with New York stock footage. Their
specs, with frame-exact times, are `examples/nyc-vlog-intro-sign-glow-type-on.spec.json` and
`examples/nyc-red-block-grid-silhouettes.spec.json`; read them next to this file.

These are studies, not templates. The same building blocks combine into other effects (see the end).

## Building blocks (all in `scripts/`)

| Block | Script | Notes |
|---|---|---|
| Key a lit or coloured element | `key_matte.py --key=luma\|red\|yellow\|white\|dark` | Pick the channel that separates the element from its own background: `red` = red letters on yellow, `yellow` = yellow on red, `white` = white letters on colour, `dark` = buildings against sky. Measure the channel's percentiles inside the window first and put lo/hi between them. |
| Lift a person or object | `subject_cutout.py` (Vision) | Lifts the most salient thing. On a busy street from above it lifted the yellow cabs, not the people. |
| Neon glow / white silhouette | `stylize_matte.py glow\|silhouette` | Glow is a near-white core inside a full-brightness halo of the element's own colour. A white sign on a bright billboard can't visibly brighten: use coloured letters. Output is black where clear, so files stay small (4-15 MB). |
| Red block grid | `block_grid.py` | Generated alpha layer: close / break / solid / open phases on a cols x rows grid, everything "on twos". |
| Letterbox, glint | `graphics.py letterbox\|glint` | PNGs. Letterbox slides in with scale 1.3 -> 1. Glint uses conform `none` plus `place`, scale 0 -> 0.4 -> 0 over 4 frames. |
| Still of the next shot's element | `key_matte.py` with a one-frame duration, then `ffmpeg -frames:v 1 -pix_fmt rgba x.png` | Start the key a little before the wanted frame (e.g. 1.49 s for frame 36 at 23.976), then confirm the frame by diffing it against the neighbouring source frames. |

The MCP takes stills (PNG) as connected clips with crop / scale / opacity keyframes. The preview renderer
handles stills, conform `none`, animated scale, 24 fps timelines and stacks overlays by lane.

## Vlog intro: signs switch on, then the next sign types onto the sky

Reference: "Intro to the new YouTube vlog" (23.976, 13.3 s). Cut timings (frames): 37, 117, 189, 201,
206, 210, 215, 244, 278, 282, 286, 291, 294, 298, black from 302.

1. **Signs switch on in place** (no cut). Key each sign separately (`kodak.mov`, `samsung.mov`...)
   from the shot's own source in-point and `stylize_matte.py glow` it. Overlay source_in = timeline
   frames into the shot (`(T - shot_start)/24s` for a 24 fps clip). Sequence, as in the reference:
   - one sign group flickers on for 10 frames (on, off for 1 frame, on);
   - the hero sign flashes whole for 2 frames;
   - single letters light one at a time (each a cropped copy of the glow clip, 3 frames, about 5 apart);
   - another sign blooms for 2 frames;
   - everything on together for 8 frames with a dip or two, then all off.
   Find letter boxes from the key's row or column profile (gaps between letters).
2. **Type-on match**. The next shot's sign is keyed from the exact cut frame as a still PNG (glowed so it
   reads on a daylight sky). It sits untransformed, so it is exactly where the sign is after the cut.
   - Choose the plate (a building on one side, open sky where the sign will land) *after* you know where
     the sign is.
   - Crop `x1` steps one letter every 6 frames (hold), starting about 15 frames into the plate.
   - The sign's other part (the In-N-Out arrow; VACANCY's neon tubes) then draws on: crop `x1` sweeps
     linearly over about 9 frames, then flickers.
   - Cut. The next shot starts on the keyed frame.
   - A still is right when the next shot pans: a moving matte would slide across the sky while it types.
3. **Montage + letterbox + glint**. A glint on a spire for 5 frames at the start of the arcade-style
   shot. Letterbox bars slide in over 6 frames about halfway through it and stay through the 3-5 frame
   closing burst. Then black.

## Sydney: red block grid with white silhouettes

Reference: "SYDNEY" (24 fps, 17.0 s). Cuts: 95 (hidden in red), 143, 154 then every 5 frames to 189,
201 (window), 284, about 300, 330, 354, 378, black from 390. Red is #B7090F.

1. **Block grid transition** (timeline 16-138), with layers from the bottom up:
   - shot A, then shot B (cut at 95, inside the solid red);
   - lane 1: `block_grid.py --fps=24 --color=B7090F --close=40 --break=38 --solid=18 --open=26 --out-subject=<A's person> --in-subject=<B's person>`;
   - lane 2: A's person as a real Vision cutout from 18 to 93, so they stay on top of the red as the
     frame closes and the picture breaks up;
   - lane 3: B's person as a white silhouette from 96, at their exact position. Opacity on 96-99, off
     100-107, on 108-137. The grid opens around them, then the silhouette goes and the real person stays.
2. **Window drop-in** (189-201). The next shot as a connected copy over the end of the current one:
   scale 0.83 -> 0.86, source_in = next_in − 12 frames. Then it cuts to full frame.
3. **White silhouette flashes** on the long hold (2-3 frames each, roughly every 24 frames): the
   foreground object (Vision), then the skyline (`key_matte.py --key=dark` against the sky). The last
   flash is the *next* shot's subject: white silhouettes 2 frames before the cut, staying 2 frames after.
   This is the cutout match transition, done as light.
4. The tennis-court shot's players flash white too. Use any small subjects Vision lifts (cabs on a crosswalk).
5. The reference also has a passerby wipe: a person crossing close to camera reveals the next shot
   behind them. It needs a clip with a large passerby crossing the whole frame. Our NYC version used a
   hard cut instead.

## Recombining

The blocks are general. For example:
- a white silhouette of the next subject can replace the real cutout in the cutout match transition;
- a block grid can open around a keyed sign instead of a person;
- type-on works for any sign whose next shot holds still at the cut;
- glow-on letters can be the "reveal" half of a cutout transition.
Start from what the footage offers (signs, people, skylines, strong shapes), not from a recipe.
