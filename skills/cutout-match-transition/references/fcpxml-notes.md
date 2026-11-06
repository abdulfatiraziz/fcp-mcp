# FCPXML and Final Cut Pro facts for cutout transitions

Verified against FCP 12.3 ("Final Cut Pro Creator Studio", FCPXML 1.14) and Apple's FCPXML reference.

## Units

- **Crop** (`adjust-crop mode="crop"` → `crop-rect left/top/right/bottom`): percent of the clip's
  *own* frame **height**. A 16:9 frame is 177.78 units wide. Edges are insets, not coordinates.
- **Position** (`adjust-transform position="x y"`): percent of frame height, measured from the frame
  centre, y up. 10 on a 1080p frame = 108 px.
- **Scale**: factor (`"1 1"` = 100%), about the frame centre (anchor `0 0`).
- **Blend mode** (`adjust-blend mode`): an *integer*, optionally followed by the name in parentheses:
  `0 (Normal)`, `8 (Add)`, `9 (Lighten)`, `10 (Screen)`, `14 (Overlay)`. Lowercase names
  (`mode="screen"`), which some community docs show, are wrong.
- **Opacity**: `adjust-blend amount`, 0–1.

## Keyframes

- `<param name="amount|position|scale|left|top|right|bottom"><keyframeAnimation><keyframe time value/>`
  inside the adjust element (crop edges go inside `crop-rect`).
- `time` is in the clip's **local time**: its `start` plus the offset into the clip. This was verified
  by a round trip (2026-10-31): FCP imported 5 cutouts and exported identical keyframes.
- Use `curve="linear"`. The default is `smooth`, which rounds off flicker and step reveals.
- There is no "hold" interpolation. To jump, repeat the old value one frame before the next key. The
  MCP's `hold` option does this for you.

## Element order (the DTD rejects anything else)

In an `asset-clip`: intrinsic adjustments (`adjust-crop`, `adjust-transform`, `adjust-blend`), then
anchored (connected) clips, then markers, then filters. Connected clips that overlap must be on
different lanes; otherwise FCP re-lanes them on import.

## Alpha overlays

ProRes 4444 with alpha (`prores_ks -profile:v 4444 -pix_fmt yuva444p10le`) is read by FCP as a
normal clip with transparency. Use normal blend, no transform.

## Importing

- FCP asks "Which library do you want to import … into?" for **every** FCPXML opened from outside,
  even when the file names a library location. The MCP answers it through UI scripting (needs
  Accessibility for the app running the server).
- If FCP is full screen or on another Space while the user works elsewhere, macOS hides its windows
  from UI scripting and blocks bringing it forward. The user must switch to FCP first.

## Tooling gotchas

- `xmllint --dtdvalid` cannot load a DTD from a path with spaces: pass a `file://` URI.
- AVAssetReader returns every frame that *overlaps* the start time. Start a quarter frame into the
  first wanted frame, or the matte is one frame early (`subject_cutout.py` already does this).
- Homebrew's ffmpeg here has no `drawtext`: render text with `caption.js` (AppKit) instead.
- Homebrew Python lacks SSL certificates: fetch web pages with `curl`.

## Verified by round trip (import → FCP → Export XML, FCP 12.3)

- Positions, lengths and source in-points of all 24 items came back identical, including 30 fps
  sources on a 23.976 timeline.
- `adjust-conform type="fill"` survives on every clip.
- Crop and opacity keyframes (`hold`-stepped, linear) come back identical.
- `fontFace="Bold"` is normalised to `bold="1"`; the text stays bold.
- The imported project opens straight into the timeline, so `fcp_export_xml` with `focus_timeline: true`
  exports it.
- Only the first FCPXML imported opens in the timeline. Later imports land in the browser, and
  Export XML stays disabled until a project is opened or selected, so round-trip one import at a time.
- `adjust-conform type="fit"` is FCP's default, so writing it out is harmless and documents the intent.
  A solid colour PNG as a still (`video`) on the lowest lane works as a background layer.

