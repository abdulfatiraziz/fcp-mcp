"""MCP server for Final Cut Pro."""

from __future__ import annotations

import functools
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Annotated

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import fcpxml, osa
from .fcpxml import ConnectedItem, MarkerSpec, StoryItem
from .media import MediaError, probe

INSTRUCTIONS = """\
Control Final Cut Pro (FCP) on this Mac.

How FCP can be automated:
- Reading: fcp_list_libraries / fcp_get_project use FCP's own (read-only) scripting dictionary.
- Creating and editing timelines: FCP cannot be edited directly by script. Build an FCPXML file
  (fcpxml_build_timeline, fcpxml_add_markers) and import it; FCP adds it as a new project.
- Seeing a timeline's contents: export it (fcp_export_xml), then the result is parsed for you
  (clips, positions, titles, markers). fcpxml_read parses any .fcpxml/.fcpxmld file.
- Driving the app: fcp_menu_list / fcp_menu_click, fcp_press_keys, fcp_ui_snapshot, fcp_click_button,
  fcp_save_dialog, fcp_share_export, fcp_screenshot. These need Accessibility permission for the app
  running this server (fcp_status tells you).

Tips: probe media first with media_probe. Times accept seconds, "HH:MM:SS:FF" timecode or "M:SS.s".
Imports create new projects; they never overwrite an existing one. Call fcp_status first if unsure.
"""

mcp = MCPServer("final-cut-pro", instructions=INSTRUCTIONS)

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)
UI = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=False)

_EXPECTED = (osa.OSAError, MediaError, ValueError, FileNotFoundError, ET.ParseError, subprocess.SubprocessError)


def _tool(annotations: ToolAnnotations):
    """Register a tool and turn expected failures into messages the model can read."""

    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except ToolError:
                raise
            except _EXPECTED as e:
                raise ToolError(str(e)) from e

        return mcp.tool(annotations=annotations)(inner)

    return wrap


def _require_running() -> None:
    if not osa.is_running():
        raise ToolError("Final Cut Pro is not running. Call fcp_launch first.")


def _require_running_or_launch() -> None:
    if not osa.is_running() and not osa.launch():
        raise ToolError("Final Cut Pro did not start.")


def _open_library(path: Path) -> str:
    """Open an existing .fcpbundle and return its name once FCP lists it."""
    osa.open_document(str(path))
    deadline = time.time() + 30
    while time.time() < deadline:
        for lib in osa.library_files():
            if lib["file"] and Path(lib["file"]).resolve() == path:
                return lib["name"]
        time.sleep(1)
    raise ToolError(f"Final Cut Pro did not open {path.name}.")


def _import(path: Path, library: str | None, expect_project: str | None = None) -> dict:
    """Open a document in FCP and answer its "Which library…?" dialog.

    FCP asks that question for every FCPXML opened this way, even when the file names a
    library, so the answer is given through the UI (needs Accessibility).
    """
    _require_running_or_launch()
    libs = osa.library_files()
    names = ", ".join(repr(lib["name"]) for lib in libs) or "none"
    target: str | None = None
    create: Path | None = None
    if library and (library.endswith(".fcpbundle") or "/" in library):
        p = Path(library).expanduser().resolve()
        p = p if p.suffix == ".fcpbundle" else p.with_name(p.name + ".fcpbundle")
        target = next((lib["name"] for lib in libs if lib["file"] and Path(lib["file"]).resolve() == p), None)
        if target is None:
            if p.exists():
                target = _open_library(p)
            else:
                create = p
    elif library:
        if library not in [lib["name"] for lib in libs]:
            raise ToolError(f"No open library named {library!r}. Open libraries: {names}.")
        target = library
    elif len(libs) == 1:
        target = libs[0]["name"]

    osa.open_document(str(path))
    wanted = target or (create.stem if create else None)
    if not osa.accessibility_enabled():
        pick = f"{wanted!r} (New… to create it)" if create else repr(wanted) if wanted else "a library"
        return {
            "library": wanted,
            "note": f"FCP is asking which library to import into. Ask the user to choose {pick}. "
            "Turning on Accessibility for the app running this server lets the import finish on its own.",
        }
    if wanted is None:
        why = f"several libraries are open ({names})" if libs else "no library is open"
        return {"library": None, "note": f"FCP is asking which library to import into because {why}. Pass library=."}

    answer = osa.answer_import_dialog(target, create=create is not None)
    if create and answer.get("dialog"):
        create.parent.mkdir(parents=True, exist_ok=True)
        osa.fill_save_dialog(str(create.parent), create.stem)
    result = {"library": wanted, "note": f"Imported into the library {wanted!r}."}
    if not answer.get("dialog") and not answer.get("windows_visible"):
        result["note"] = (
            "Could not reach FCP's windows: it is probably on another Space or in full screen while the user works "
            "elsewhere, and macOS will not let a background process bring it forward. Ask the user to switch to "
            "Final Cut Pro, then answer its 'Which library' dialog with fcp_click_button / fcp_save_dialog, "
            "or call fcpxml_import again."
        )
    elif not answer.get("dialog"):
        result["note"] = "FCP did not ask which library to use; check fcp_list_libraries to confirm the import."
    if expect_project:
        result["verified"] = _wait_for_project(expect_project, wanted)
    return result


def _wait_for_project(name: str, library: str, seconds: float = 30) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if osa.find_project(name, library=library):
                return True
        except osa.OSAError:
            pass
        time.sleep(1.5)
    return False


LIBRARY_HELP = (
    "Name of an open library, or path to a .fcpbundle (opened if closed, created if missing). "
    "Default: the open library when exactly one is open."
)


# ------------------------------------------------------------------ status ---


@_tool(READ)
def fcp_status() -> dict:
    """Check Final Cut Pro, permissions and helpers. Call this first when something fails."""
    app = fcpxml.fcp_app_path()
    version = None
    if app:
        out = subprocess.run(
            ["defaults", "read", str(app / "Contents/Info"), "CFBundleShortVersionString"], capture_output=True, text=True
        )
        version = out.stdout.strip() or None
    running = osa.is_running()
    automation: str = "not checked (Final Cut Pro is not running)"
    if running:
        try:
            libs = osa.run_applescript(
                f'tell application id "{osa.BUNDLE_ID}" to get name of every library', timeout=15
            )
            automation = f"ok (open libraries: {libs or 'none'})"
        except osa.OSAError as e:
            automation = f"failed: {e}"
    return {
        "app_path": str(app) if app else None,
        "version": version,
        "running": running,
        "automation": automation,
        "accessibility_for_ui_control": osa.accessibility_enabled(),
        "fcpxml_versions": fcpxml.supported_versions(),
        "ffprobe": shutil.which("ffprobe"),
        "default_output_dir": str(fcpxml.DEFAULT_OUTPUT_DIR),
    }


@_tool(WRITE)
def fcp_launch() -> str:
    """Open Final Cut Pro (or bring it forward) and wait until it responds."""
    if osa.launch():
        return "Final Cut Pro is running."
    raise ToolError(
        "Final Cut Pro opened but did not respond to Apple Events within 60s. It may be showing a dialog or "
        "waiting on a macOS permission prompt (Automation). Check the screen."
    )


# ---------------------------------------------------------------- library ---


@_tool(READ)
def fcp_list_libraries(
    library: Annotated[str | None, Field(description="Only this library (exact name).")] = None,
    event: Annotated[str | None, Field(description="Only this event (exact name).")] = None,
    details: Annotated[bool, Field(description="Include each project's duration and frame rate (slower).")] = True,
) -> list[dict]:
    """List open libraries with their events and projects."""
    _require_running()
    return osa.list_libraries(library, event, details)


@_tool(READ)
def fcp_get_project(
    name: Annotated[str, Field(description="Project name (exact).")],
    library: str | None = None,
    event: str | None = None,
) -> list[dict]:
    """Get a project's sequence details (duration, start, frame rate, timecode format).

    To see clips, titles and markers inside it, open it in the timeline and use fcp_export_xml.
    """
    _require_running()
    found = osa.find_project(name, library, event)
    if not found:
        raise ToolError(f"No project named {name!r} in the open libraries. Use fcp_list_libraries to see names.")
    return found


# ------------------------------------------------------------------ media ---


@_tool(READ)
def media_probe(
    paths: Annotated[list[str], Field(description="Media files to inspect.")],
) -> list[dict]:
    """Inspect media files: duration, resolution, frame rate, timecode, audio."""
    results = []
    for p in paths:
        try:
            results.append(probe(p).to_dict())
        except MediaError as e:
            results.append({"path": p, "error": str(e)})
    return results


# ----------------------------------------------------------------- fcpxml ---


@_tool(WRITE)
def fcpxml_build_timeline(
    project_name: Annotated[str, Field(description="Name of the new project.")],
    primary: Annotated[
        list[StoryItem],
        Field(description="Primary storyline in order: media clips, gaps (duration only) and titles (text)."),
    ],
    connected: Annotated[
        list[ConnectedItem] | None,
        Field(description="Clips/titles connected at a timeline position: B-roll and titles above, music/VO below."),
    ] = None,
    markers: Annotated[list[MarkerSpec] | None, Field(description="Markers, to-dos and chapter markers.")] = None,
    event_name: Annotated[str, Field(description="Event to put the project in (created if missing).")] = "fcp-mcp",
    width: Annotated[int | None, Field(description="Frame width. Defaults to the first video clip's.")] = None,
    height: Annotated[int | None, Field(description="Frame height. Defaults to the first video clip's.")] = None,
    fps: Annotated[
        float | str | None, Field(description="Frame rate, e.g. 25, 29.97, 30. Defaults to the first video clip's.")
    ] = None,
    start_timecode: Annotated[str | None, Field(description='Timeline start, e.g. "01:00:00:00". Default 0.')] = None,
    library: Annotated[str | None, Field(description=LIBRARY_HELP)] = None,
    output_path: Annotated[
        str | None, Field(description="Where to save the .fcpxml. Default: ~/Movies/fcp-mcp/<project>.fcpxml")
    ] = None,
    background: Annotated[
        str | None,
        Field(description='Hex colour behind clips that don\'t fill the frame (e.g. "#FFFFFF" around vertical '
              "footage fitted into 16:9). Ends where a trailing gap/end card starts."),
    ] = None,
    import_into_fcp: Annotated[bool, Field(description="Open the file in Final Cut Pro after writing it.")] = True,
) -> dict:
    """Create a new FCP project from media files, titles and markers, as FCPXML, and import it.

    Example primary: [{"path": "/clips/a.mov", "source_in": 2, "duration": 5}, {"text": "Chapter 2", "duration": 3},
    {"path": "/clips/b.mov"}]. Example connected: [{"path": "/music.mp3", "at": 0, "lane": -1}].
    """
    xml, summary = fcpxml.build_timeline(
        project_name=project_name,
        primary=primary,
        connected=connected,
        markers=markers,
        event_name=event_name,
        width=width,
        height=height,
        fps=fps,
        start_timecode=start_timecode,
        background=background,
        asset_dir=Path(output_path).expanduser().resolve().parent if output_path else None,
    )
    out = fcpxml.write_output(xml, output_path, project_name)
    valid, errors = fcpxml.validate_file(out)
    summary.update({"file": str(out), "dtd_valid": valid, "dtd_errors": errors})
    if import_into_fcp:
        if valid is False:
            summary["import"] = "Skipped: the file failed DTD validation."
        else:
            summary["import"] = _import(out, library, expect_project=project_name)
    return summary


@_tool(WRITE)
def fcpxml_import(
    path: Annotated[str, Field(description=".fcpxml file or .fcpxmld bundle.")],
    library: Annotated[str | None, Field(description=LIBRARY_HELP)] = None,
) -> dict:
    """Import an FCPXML file or bundle into Final Cut Pro. Projects are added; nothing is overwritten."""
    p = Path(path).expanduser().resolve()
    valid, errors = fcpxml.validate_file(fcpxml.resolve_document(p))
    if valid is False:
        raise ToolError("The file fails DTD validation, so FCP would reject it:\n" + "\n".join(errors))
    return {"file": str(p), "dtd_valid": valid, "import": _import(p, library)}


@_tool(READ)
def fcpxml_read(
    path: Annotated[str, Field(description=".fcpxml file or .fcpxmld bundle.")],
    max_items: Annotated[int, Field(description="Cap on timeline items listed per project.")] = 400,
) -> dict:
    """Summarise an FCPXML: projects, every clip/title/gap with timeline position and lane, markers, media."""
    return fcpxml.read_document(path, max_items)


@_tool(WRITE)
def fcpxml_add_markers(
    path: Annotated[str, Field(description="Source .fcpxml/.fcpxmld (e.g. from fcp_export_xml).")],
    markers: list[MarkerSpec],
    project_name: Annotated[str | None, Field(description="Needed if the file has several projects.")] = None,
    output_path: Annotated[str | None, Field(description="Default: next to the source, '<name> markers.fcpxml'.")] = None,
    import_into_fcp: Annotated[
        bool, Field(description="Import the result. FCP adds it as a new copy of the project.")
    ] = False,
    library: Annotated[str | None, Field(description=LIBRARY_HELP)] = None,
) -> dict:
    """Add markers, to-dos or chapter markers to a project in an FCPXML file and save a new file."""
    xml, summary = fcpxml.add_markers(path, markers, project_name)
    src = Path(path).expanduser().resolve()
    default = src.with_name(f"{src.stem} markers.fcpxml")
    out = fcpxml.write_output(xml, output_path or str(default), src.stem)
    valid, errors = fcpxml.validate_file(out)
    summary.update({"file": str(out), "dtd_valid": valid, "dtd_errors": errors})
    if import_into_fcp and valid is not False:
        summary["import"] = _import(out, library)
    return summary


@_tool(READ)
def fcpxml_validate(path: Annotated[str, Field(description=".fcpxml file or .fcpxmld bundle.")]) -> dict:
    """Check an FCPXML file against the DTD bundled with the installed Final Cut Pro."""
    p = fcpxml.resolve_document(path)
    valid, errors = fcpxml.validate_file(p)
    return {"file": str(p), "valid": valid, "errors": errors}


# ------------------------------------------------------------- UI control ---


@_tool(READ)
def fcp_menu_list(
    path: Annotated[
        list[str] | None, Field(description='Omit for the menu bar, ["File"] for a menu, ["File", "Share"] for a submenu.')
    ] = None,
) -> dict:
    """List Final Cut Pro menu items with enabled state and keyboard shortcuts."""
    _require_running()
    return osa.menu(path or [], click=False)


@_tool(UI)
def fcp_menu_click(
    path: Annotated[
        list[str],
        Field(description='Menu path, e.g. ["File", "New", "Project"], ["Mark", "Markers", "Add Marker"]. "..." is optional.'),
    ],
) -> dict:
    """Click a Final Cut Pro menu item. Acts on the current selection, like a person clicking it."""
    _require_running()
    return osa.menu(path, click=True)


@_tool(UI)
def fcp_press_keys(
    combo: Annotated[
        str, Field(description='Shortcut like "space", "cmd+b", "shift+z", "left", "m", "cmd+2" (focus timeline).')
    ],
    repeat: Annotated[int, Field(ge=1, le=100)] = 1,
) -> str:
    """Press a keyboard shortcut in Final Cut Pro (it is brought to the front first)."""
    _require_running()
    return "Pressed " + osa.press_keys(combo, repeat) + (f" x{repeat}" if repeat > 1 else "")


@_tool(READ)
def fcp_ui_snapshot(
    window: Annotated[str | None, Field(description="Window name. Default: the open dialog, else the front window.")] = None,
    depth: Annotated[int, Field(ge=1, le=10)] = 4,
    max_nodes: Annotated[int, Field(ge=10, le=1500)] = 250,
) -> dict:
    """Describe the UI of the open dialog or front window (buttons, fields, values) as a tree."""
    _require_running()
    return osa.ui_tree(window, depth, max_nodes)


@_tool(UI)
def fcp_click_button(
    label: Annotated[str, Field(description='Button title, e.g. "Next", "Cancel", "Import", "OK".')],
) -> dict:
    """Click a button in Final Cut Pro's open dialog (or front window)."""
    _require_running()
    return osa.click_button(label)


@_tool(UI)
def fcp_save_dialog(
    output_path: Annotated[str, Field(description="Full path to save to (folder must exist).")],
    button: Annotated[str, Field(description="Confirm button.")] = "Save",
    overwrite: bool = False,
) -> dict:
    """Fill in Final Cut Pro's open save dialog with a folder and file name, then confirm."""
    _require_running()
    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    return osa.fill_save_dialog(str(out.parent), out.name, button, overwrite)


@_tool(UI)
def fcp_export_xml(
    output_path: Annotated[
        str | None, Field(description="Where to save. Default: ~/Movies/fcp-mcp/export-<time>.fcpxml")
    ] = None,
    focus_timeline: Annotated[
        bool,
        Field(description="Press cmd+2 first so the project open in the timeline is exported (not the browser selection)."),
    ] = True,
    overwrite: bool = False,
    read_after: Annotated[bool, Field(description="Parse the export and return its contents.")] = True,
) -> dict:
    """Export the open timeline (or browser selection) as FCPXML via File > Export XML, then read it."""
    _require_running()
    out = Path(output_path or fcpxml.DEFAULT_OUTPUT_DIR / f"export-{time.strftime('%Y%m%d-%H%M%S')}.fcpxml")
    out = out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if focus_timeline:
        osa.press_keys("cmd+2")
    osa.menu(["File", "Export XML"], click=True)
    time.sleep(1.0)
    # Type the bare name: FCP appends .fcpxml or .fcpxmld itself, depending on the format chosen in the dialog.
    stem = out.name.removesuffix(".fcpxmld").removesuffix(".fcpxml")
    osa.fill_save_dialog(str(out.parent), stem, "Save", overwrite)
    found = _wait_for([out.parent / f"{stem}.fcpxmld", out.parent / f"{stem}.fcpxml"], 60)
    if not found:
        raise ToolError(
            f"Export started but no file appeared at {out} within 60s. Use fcp_ui_snapshot to check for a dialog."
        )
    result: dict = {"file": str(found)}
    if read_after:
        result["contents"] = fcpxml.read_document(found)
    return result


@_tool(UI)
def fcp_share_export(
    output_path: Annotated[str, Field(description="Where to save the render, e.g. ~/Movies/out.mov (extension optional).")],
    destination: Annotated[
        str, Field(description='Share destination as named under File > Share, e.g. "Export File", "Apple Devices 1080p".')
    ] = "Export File",
    focus_timeline: bool = True,
    overwrite: bool = False,
) -> dict:
    """Render the open timeline via File > Share using that destination's current settings.

    Rendering runs in the background in FCP; this returns once the export has started.
    """
    _require_running()
    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if focus_timeline:
        osa.press_keys("cmd+2")
    osa.menu(["File", "Share", destination], click=True)
    time.sleep(1.5)
    osa.click_button("Next")
    time.sleep(1.5)
    osa.fill_save_dialog(str(out.parent), out.stem, "Save", overwrite)
    time.sleep(2)
    started = sorted(str(p) for p in out.parent.glob(f"{out.stem}.*"))
    return {
        "destination": destination,
        "folder": str(out.parent),
        "name": out.stem,
        "files_seen": started,
        "note": "FCP renders in the background (Window > Background Tasks). The file is complete when that finishes.",
    }


@_tool(READ)
def fcp_screenshot(
    window_only: Annotated[bool, Field(description="Capture just FCP's front window (else the main display).")] = True,
) -> Image:
    """Take a screenshot of Final Cut Pro so you can see the viewer, timeline or a dialog."""
    tmp = Path(tempfile.mkdtemp(prefix="fcp-mcp-"))
    png, jpg = tmp / "shot.png", tmp / "shot.jpg"
    cmd = ["screencapture", "-x", "-m", str(png)]
    if window_only:
        try:
            b = osa.front_window_bounds()
            cmd = ["screencapture", "-x", "-R", f"{b['x']},{b['y']},{b['w']},{b['h']}", str(png)]
        except osa.OSAError:
            pass
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not png.exists():
        raise ToolError(
            "Screenshot failed. Give the app running this server Screen Recording permission in System Settings > "
            f"Privacy & Security > Screen & System Audio Recording. Details: {proc.stderr.strip()}"
        )
    subprocess.run(
        ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "70", "-Z", "1600", str(png), "--out", str(jpg)],
        capture_output=True,
    )
    return Image(path=jpg if jpg.exists() else png)


def _wait_for(candidates: list[Path], seconds: float) -> Path | None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        for c in candidates:
            if c.exists():
                time.sleep(0.5)  # let FCP finish writing
                return c
        time.sleep(0.5)
    return None


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
