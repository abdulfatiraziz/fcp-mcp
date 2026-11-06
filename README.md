# fcp-mcp

**Let Claude (or any MCP client) edit in Final Cut Pro.**

`fcp-mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server for **Final Cut Pro on macOS**. It can read your libraries, build timelines from your media, import them, read timelines back clip by clip, and drive the app (menus, shortcuts, dialogs, exports, screenshots). All of it runs locally on your Mac.

> Tested with Final Cut Pro 12.3 ("Final Cut Pro Creator Studio") on macOS. Python 3.11+.

```text
You:     "Cut the voiceover at these points, put a chapter marker on each section,
          and open it in Final Cut."
Claude:  builds the FCPXML → validates it against FCP's own DTD → imports it →
          checks the project arrived → "Done: 'VO clean', 3:09, 20 clips, 12 markers."
```

---

## Why

Final Cut Pro has no editing API, and its AppleScript dictionary is read-only. `fcp-mcp` combines three channels into one set of tools:

| Channel | What it does | Needs |
|---|---|---|
| **AppleScript dictionary** | Lists libraries, events, projects, durations and frame rates | Automation permission |
| **FCPXML** | Builds new timelines (clips, gaps, titles, connected B-roll and music, markers, compositing) and imports them; reads exported timelines back clip by clip | Nothing extra |
| **UI scripting** (System Events) | Clicks menus, presses shortcuts, reads and clicks dialogs, runs Export XML and Share, takes screenshots | Accessibility (and Screen Recording for screenshots) |

**Safe by design:** an import always adds a **new** project and never changes an existing one. Every generated file is checked against the FCPXML DTD that ships inside Final Cut Pro before it is opened.

---

## Use cases

| Use case | How it works |
|---|---|
| 🎙️ **Voiceover cleanup** | Pair it with a transcriber (WhisperX, whisper.cpp). Claude finds the stumbles, repeats and long pauses, then builds a timeline of the kept ranges, as separate clips of your original file so every cut stays adjustable in FCP. |
| ✂️ **Rough cut from notes** | "Intro clip from 0:02 for 5 s, title card, then the demo." You get an ordered primary storyline with titles and gaps in one call. |
| 🎞️ **B-roll and music layout** | Connect B-roll above the storyline and music or VO below it, at exact times, with audio roles (`dialogue`, `music`, `effects`). |
| 📍 **Markers and chapters** | Add chapter markers for YouTube, to-dos ("check colour"), or notes from a review, onto a new timeline or an exported one. |
| 📱 **Reformatting** | Fit or fill 3:4 or 9:16 footage into 16:9 (or the reverse), with a background colour behind fitted clips. |
| 🧩 **Overlays and compositing** | Crop, place, scale, rotate, opacity, blend modes and keyframes (with stepped "hold" keys). ProRes 4444 clips with alpha work as overlays. |
| 🔍 **Timeline review** | Export a project's XML and read it back clip by clip (names, offsets, durations, lanes, markers), so the assistant can check an edit or report on it. |
| 📤 **Hands-off export** | Trigger Share with the destination's current settings, and screenshot the viewer or timeline to see what's on screen. |

---

## Tools

| Group | Tools |
|---|---|
| Status | `fcp_status`, `fcp_launch` |
| Library (read-only) | `fcp_list_libraries`, `fcp_get_project` |
| Media | `media_probe` (ffprobe: duration, fps, size, timecode, audio) |
| FCPXML | `fcpxml_build_timeline`, `fcpxml_import`, `fcpxml_read`, `fcpxml_add_markers`, `fcpxml_validate` |
| App control | `fcp_menu_list`, `fcp_menu_click`, `fcp_press_keys`, `fcp_ui_snapshot`, `fcp_click_button`, `fcp_save_dialog`, `fcp_export_xml`, `fcp_share_export`, `fcp_screenshot` |

Times can be given as seconds (`12.5`), timecode (`"00:00:12:15"`) or clock time (`"0:12.5"`).

### Example: `fcpxml_build_timeline`

```json
{
  "project_name": "Promo v1",
  "primary": [
    {"path": "~/Footage/intro.mov", "source_in": 2, "duration": 5},
    {"text": "Chapter 2", "duration": 3, "style": {"color": "#FFCC00"}},
    {"path": "~/Footage/demo.mov"}
  ],
  "connected": [
    {"path": "~/Audio/music.wav", "at": 0, "lane": -1, "audio_role": "music"},
    {"path": "~/Footage/broll.mov", "at": "0:06", "lane": 1, "duration": 2, "tracks": "video"}
  ],
  "markers": [{"at": 8, "name": "Check colour", "kind": "todo"}]
}
```

Clips and titles also take `conform` (`fit`, `fill` or `none`) and `adjust` (crop, place, scale, rotation, opacity, blend mode, keyframes, and a per-property `hold`). Generated files go to `~/Movies/fcp-mcp/` unless you pass `output_path`.

---

## Install

You need macOS, Final Cut Pro, [uv](https://docs.astral.sh/uv/) and `ffmpeg` (`brew install ffmpeg`).

```bash
git clone https://github.com/abdulfatiraziz/fcp-mcp.git
cd fcp-mcp
uv sync
uv run pytest -q        # optional: 8 tests, no Final Cut needed
```

### Claude Code

```bash
claude mcp add --scope user final-cut-pro -- uv run --directory /ABSOLUTE/PATH/TO/fcp-mcp fcp-mcp
```

### Claude Desktop

Add this to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "final-cut-pro": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/fcp-mcp", "fcp-mcp"]
    }
  }
}
```

Any other MCP client that speaks stdio works the same way.

### macOS permissions

Grant these to the app that runs the server (Claude, or your terminal) in **System Settings › Privacy & Security**:

- **Automation** › Final Cut Pro and System Events: allow when macOS asks.
- **Accessibility**: for the menu, keyboard, dialog, Export XML and Share tools. It also lets imports finish on their own (see below).
- **Screen & System Audio Recording**: for `fcp_screenshot`.

Run `fcp_status` to see which of these are working.

---

## How imports work

Final Cut Pro asks *"Which library do you want to import … into?"* for every FCPXML opened this way, even when the file names a library. With Accessibility on, the server answers the dialog itself:

- **The library is open:** it picks that library.
- **It's a closed `.fcpbundle`:** it opens the library first.
- **The path doesn't exist yet:** it clicks **New…** and creates the library.

It then waits until the project shows up. Without Accessibility, you pick the library in the dialog yourself.

---

## Known limits

- **When several libraries are open,** pass `library` so the server knows which one to choose. Otherwise the dialog is left for you.
- **Reading a timeline needs an export first.** Use `fcp_export_xml` (needs Accessibility) or File › Export XML, then `fcpxml_read`.
- **Export XML exports whatever has focus.** By default `fcp_export_xml` presses ⌘2 so the project in the timeline is exported. Pass `focus_timeline: false` to export the browser selection instead.
- **`fcp_share_export` is not yet tested live.** It renders with the destination's current settings.
- **UI tools depend on FCP's menu names and dialog layout.** Names are matched loosely ("Export XML" also matches "Export XML…"). Use `fcp_ui_snapshot` to see what is on screen.
- **Titles use the built-in Basic Title.** You can set the text, font, size, colour and alignment.

---

## Vlog effects skill (Claude Code)

`skills/cutout-match-transition/` is a Claude Code skill that uses fcp-mcp to build editorial effects as editable Final Cut timelines:

- **Cutout match transition:** before a cut, the next shot's hero element (a neon sign, a tower, a person) appears in the current shot, truly masked, at its exact next-shot position. It flickers on and stays locked through the cut.
- **More effects:** signs glowing on letter by letter, type-on onto the sky, a red block-grid transition, white silhouettes, glints and a letterbox.
- **Masks:** brightness keys for lit elements, and macOS Vision subject lifting for people and objects.
- **Sound:** sound effects made in code to match each effect, plus a free recorded library (Mixkit, Freesound CC0).
- **Creative mode:** invent new effects from your own footage, with no reference reel.
- **Previews:** quick MP4 previews and a frame-lock check without opening Final Cut.

Install it by linking the folder into your skills:

```bash
ln -s "$PWD/skills/cutout-match-transition" ~/.claude/skills/cutout-match-transition
swiftc -O -swift-version 5 -o skills/cutout-match-transition/scripts/bin/subject_matte \
  skills/cutout-match-transition/scripts/subject_matte.swift
```

Then ask Claude for "a cutout match transition between these clips" or "be creative with my vlog". The specs in `examples/` use `/path/to/...` placeholders: point them at your own media.

## Development

```bash
uv sync
uv run pytest -q
```

The tests generate their own media with ffmpeg (into `tests/media/`, which git ignores) and validate every generated timeline against Final Cut's DTD.

## License

[MIT](LICENSE). Not affiliated with or endorsed by Apple. Final Cut Pro is a trademark of Apple Inc.
