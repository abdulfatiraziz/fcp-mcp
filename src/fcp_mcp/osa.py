"""Talk to Final Cut Pro through Apple Events.

Two channels:
- FCP's own scripting dictionary (read-only: libraries, events, projects).
  Needs Automation permission for the app running this server.
- System Events UI scripting (menus, keys, dialogs).
  Needs Accessibility permission for the app running this server.
"""

from __future__ import annotations

import json
import re
import subprocess
import time

BUNDLE_ID = "com.apple.FinalCutApp"


class OSAError(RuntimeError):
    pass


def _explain(stderr: str) -> str:
    s = stderr.strip()
    if "-1743" in s or "Not authorized to send Apple events" in s:
        return (
            "macOS blocked Apple Events to Final Cut Pro. Allow it in System Settings > Privacy & Security > "
            "Automation: turn on Final Cut Pro (and System Events) under the app running this MCP server "
            f"(Claude or your terminal). Details: {s}"
        )
    if "-1719" in s or "-25211" in s or "assistive access" in s.lower():
        return (
            "UI control needs Accessibility permission. Turn on the app running this MCP server (Claude or your "
            f"terminal) in System Settings > Privacy & Security > Accessibility, then retry. Details: {s}"
        )
    if "-1712" in s:
        return (
            "Final Cut Pro did not answer in time. It may be busy, showing a dialog, or waiting on a macOS "
            f"permission prompt. Check the screen and retry. Details: {s}"
        )
    if "-600" in s or "isn’t running" in s or "isn't running" in s:
        return "Final Cut Pro is not running. Call fcp_launch first."
    # "123:456: execution error: Error: Error: <message> (-2700)" -> "<message>"
    s = re.sub(r"^.*?execution error: (Error: )*", "", s)
    return re.sub(r"\s*\(-?\d+\)$", "", s)


def run_applescript(script: str, *args: str, timeout: float = 60) -> str:
    return _run(["osascript", "-", *args], script, timeout)


def run_jxa(script: str, payload: dict | None = None, timeout: float = 60):
    """Run a JXA script whose run(argv) gets JSON in argv[0] and returns JSON."""
    out = _run(["osascript", "-l", "JavaScript", "-", json.dumps(payload or {})], script, timeout)
    return json.loads(out) if out else None


def _run(cmd: list[str], script: str, timeout: float) -> str:
    try:
        proc = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise OSAError(_explain(f"AppleEvent timed out (-1712) after {timeout:.0f}s")) from e
    if proc.returncode != 0:
        raise OSAError(_explain(proc.stderr))
    return proc.stdout.strip()


# ------------------------------------------------------------ app state ---


def is_running() -> bool:
    out = subprocess.run(
        ["osascript", "-e", f'application id "{BUNDLE_ID}" is running'], capture_output=True, text=True
    ).stdout.strip()
    return out == "true"


def launch(wait: float = 60) -> bool:
    """Open Final Cut Pro and wait until it answers Apple Events."""
    subprocess.run(["open", "-b", BUNDLE_ID], check=True)
    deadline = time.time() + wait
    while time.time() < deadline:
        if is_running():
            try:
                run_applescript(f'tell application id "{BUNDLE_ID}" to get name', timeout=10)
                return True
            except OSAError:
                pass
        time.sleep(1)
    return False


def accessibility_enabled() -> bool:
    try:
        return run_applescript('tell application "System Events" to UI elements enabled', timeout=10) == "true"
    except OSAError:
        return False


def open_document(path: str) -> None:
    """Open a file in Final Cut Pro (imports FCPXML into the library)."""
    proc = subprocess.run(["open", "-b", BUNDLE_ID, path], capture_output=True, text=True)
    if proc.returncode != 0:
        raise OSAError(proc.stderr.strip() or f"open failed for {path}")


# ---------------------------------------------------- library dictionary ---

_JXA_COMMON = r"""
const fcp = Application("com.apple.FinalCutApp");
function secs(mt) {
  try { if (mt && mt.timescale) return Math.round(mt.value / mt.timescale * 1000) / 1000; } catch (e) {}
  return null;
}
function fps(mt) {
  try { if (mt && mt.value) return Math.round(mt.timescale / mt.value * 1000) / 1000; } catch (e) {}
  return null;
}
function seqInfo(seq) {
  const r = {};
  try { r.duration_seconds = secs(seq.duration()); } catch (e) {}
  try { r.start_seconds = secs(seq.startTime()); } catch (e) {}
  try { r.fps = fps(seq.frameDuration()); } catch (e) {}
  try { r.timecode_format = String(seq.timecodeFormat()); } catch (e) {}
  return r;
}
"""

_JXA_LIBRARIES = _JXA_COMMON + r"""
function run(argv) {
  const a = JSON.parse(argv[0]);
  const out = [];
  for (const lib of fcp.libraries()) {
    const name = lib.name();
    if (a.library && name !== a.library) continue;
    let file = null;
    try { file = lib.file().toString(); } catch (e) {}
    const events = [];
    for (const ev of lib.events()) {
      const evName = ev.name();
      if (a.event && evName !== a.event) continue;
      const projects = [];
      for (const p of ev.projects()) {
        const r = { name: p.name() };
        if (a.details) {
          try { Object.assign(r, seqInfo(p.sequence())); } catch (e) { r.error = String(e); }
        }
        projects.push(r);
      }
      events.push({ name: evName, projects: projects });
    }
    out.push({ name: name, file: file, events: events });
  }
  return JSON.stringify(out);
}
"""

_JXA_PROJECT = _JXA_COMMON + r"""
function run(argv) {
  const a = JSON.parse(argv[0]);
  const found = [];
  for (const lib of fcp.libraries()) {
    const libName = lib.name();
    if (a.library && libName !== a.library) continue;
    for (const ev of lib.events()) {
      const evName = ev.name();
      if (a.event && evName !== a.event) continue;
      for (const p of ev.projects()) {
        if (p.name() !== a.name) continue;
        const r = { name: a.name, id: p.id(), event: evName, library: libName };
        try { Object.assign(r, seqInfo(p.sequence())); } catch (e) { r.error = String(e); }
        try { r.essential_properties = JSON.parse(JSON.stringify(p.essentialProperties())); } catch (e) {}
        found.push(r);
      }
    }
  }
  return JSON.stringify(found);
}
"""


_JXA_LIBRARY_FILES = r"""
function run(argv) {
  const fcp = Application("com.apple.FinalCutApp");
  return JSON.stringify(fcp.libraries().map(lib => {
    let file = null;
    try { file = lib.file().toString(); } catch (e) {}
    return { name: lib.name(), file: file };
  }));
}
"""


def library_files() -> list[dict]:
    """Open libraries as [{name, file}] (fast: no events or projects)."""
    return run_jxa(_JXA_LIBRARY_FILES, timeout=30)


def list_libraries(library: str | None = None, event: str | None = None, details: bool = True) -> list[dict]:
    return run_jxa(_JXA_LIBRARIES, {"library": library, "event": event, "details": details}, timeout=120)


def find_project(name: str, library: str | None = None, event: str | None = None) -> list[dict]:
    return run_jxa(_JXA_PROJECT, {"name": name, "library": library, "event": event}, timeout=120)


# ------------------------------------------------------------ UI control ---

_JXA_UI_COMMON = r"""
const se = Application("System Events");
function proc() {
  const ps = se.processes.whose({ bundleIdentifier: "com.apple.FinalCutApp" });
  if (ps.length === 0) throw new Error("Final Cut Pro is not running.");
  return ps[0];
}
function norm(s) {
  return String(s || "").toLowerCase().replace(/…/g, "...").replace(/\.\.\.$/, "").replace(/\s+/g, " ").trim();
}
function pick(names, label, where) {
  const want = norm(label);
  let i = names.findIndex(n => norm(n) === want);
  if (i < 0) i = names.findIndex(n => norm(n).startsWith(want));
  if (i < 0) {
    const shown = names.filter(n => n).join(", ");
    throw new Error("No '" + label + "' in " + where + ". Available: " + shown);
  }
  return i;
}
function activate() {
  Application("com.apple.FinalCutApp").activate();
  const p = proc();
  p.frontmost = true;
  delay(0.2);
  return p;
}
const CONTROLS = ["AXButton", "AXCheckBox", "AXRadioButton", "AXPopUpButton", "AXMenuButton", "AXTextField", "AXSlider", "AXMenuItem"];
function info(el, depth, maxDepth, budget) {
  budget.n -= 1;
  const d = {};
  try { d.role = el.role(); } catch (e) { d.role = "?"; }
  for (const k of ["title", "name", "description", "value"]) {
    try {
      const v = el[k]();
      if (v !== null && v !== undefined && v !== "" && !(k !== "title" && v === d.title)) d[k] = String(v).slice(0, 200);
    } catch (e) {}
  }
  if (CONTROLS.indexOf(d.role) >= 0) {
    try { if (!el.enabled()) d.enabled = false; } catch (e) {}
  }
  if (depth < maxDepth && budget.n > 0) {
    let kids = [];
    try { kids = el.uiElements(); } catch (e) {}
    if (kids.length) {
      d.children = [];
      for (let i = 0; i < kids.length && i < 80 && budget.n > 0; i++) d.children.push(info(kids[i], depth + 1, maxDepth, budget));
      if (kids.length > d.children.length) d.more_children = kids.length - d.children.length;
    }
  }
  return d;
}
// Front to back: sheets, then dialog windows, then normal windows. FCP keeps
// 1-pixel overlay windows around; those are skipped.
function targets(p) {
  const sheets = [], dialogs = [], windows = [];
  for (const w of p.windows()) {
    let size = [0, 0], sub = "";
    try { size = w.size(); } catch (e) {}
    if (size[0] < 40 || size[1] < 40) continue;
    try { sub = w.subrole(); } catch (e) {}
    try { const s = w.sheets(); if (s.length) sheets.push(s[0]); } catch (e) {}
    if (sub === "AXDialog" || sub === "AXSystemDialog" || sub === "AXFloatingWindow") dialogs.push(w);
    else windows.push(w);
  }
  return { sheets: sheets, dialogs: dialogs, windows: windows, all: sheets.concat(dialogs, windows) };
}
function frontTarget(p) {
  const all = targets(p).all;
  if (!all.length) throw new Error("Final Cut Pro has no open window.");
  return all[0];
}
function findButton(root, label, budget) {
  const want = norm(label);
  const queue = [root];
  while (queue.length && budget.n-- > 0) {
    const el = queue.shift();
    let role = "";
    try { role = el.role(); } catch (e) {}
    if (role === "AXButton" || role === "AXRadioButton" || role === "AXCheckBox" || role === "AXPopUpButton") {
      for (const k of ["title", "name", "description"]) {
        try { const v = el[k](); if (v && (norm(v) === want || norm(v).startsWith(want))) return el; } catch (e) {}
      }
    }
    try { for (const k of el.uiElements()) queue.push(k); } catch (e) {}
  }
  return null;
}
"""

_JXA_MENU = _JXA_UI_COMMON + r"""
function run(argv) {
  const a = JSON.parse(argv[0]);
  const p = a.click ? activate() : proc();
  const bar = p.menuBars[0];
  const topNames = bar.menuBarItems.name();
  if (!a.path.length) return JSON.stringify({ menus: topNames.filter(n => n && n !== "Apple") });
  let item = bar.menuBarItems[pick(topNames, a.path[0], "the menu bar")];
  let menu = item.menus[0];
  for (let i = 1; i < a.path.length; i++) {
    const names = menu.menuItems.name();
    item = menu.menuItems[pick(names, a.path[i], "menu '" + a.path.slice(0, i).join(" > ") + "'")];
    if (i < a.path.length - 1) {
      if (!item.menus.length) throw new Error("'" + a.path[i] + "' has no submenu.");
      menu = item.menus[0];
    }
  }
  if (a.click) {
    if (a.path.length < 2) throw new Error("Give a full path to a menu item, like ['File', 'Export XML…'].");
    if (!item.enabled()) throw new Error("Menu item '" + a.path.join(" > ") + "' is disabled right now.");
    const clicked = item.name();
    item.click();
    return JSON.stringify({ clicked: a.path.slice(0, -1).concat([clicked]) });
  }
  if (a.path.length > 1) {
    if (!item.menus.length) throw new Error("'" + a.path[a.path.length - 1] + "' has no submenu.");
    menu = item.menus[0];
  }
  const items = [];
  const mods = { 0: "cmd", 1: "shift+cmd", 2: "opt+cmd", 3: "opt+shift+cmd", 4: "ctrl+cmd", 5: "ctrl+shift+cmd", 6: "ctrl+opt+cmd", 8: "", 9: "shift", 10: "opt", 11: "opt+shift", 12: "ctrl", 13: "ctrl+shift", 14: "ctrl+opt" };
  for (const mi of menu.menuItems()) {
    const name = mi.name();
    if (!name) continue;
    const r = { name: name };
    try { if (!mi.enabled()) r.enabled = false; } catch (e) {}
    try { if (mi.menus.length) r.submenu = true; } catch (e) {}
    try {
      const ch = mi.attributes.byName("AXMenuItemCmdChar").value();
      if (ch) {
        const m = mi.attributes.byName("AXMenuItemCmdModifiers").value();
        const prefix = mods[m] !== undefined ? mods[m] : "mods" + m;
        r.shortcut = (prefix ? prefix + "+" : "") + ch;
      }
    } catch (e) {}
    items.push(r);
  }
  return JSON.stringify({ menu: a.path, items: items });
}
"""

_JXA_UI_TREE = _JXA_UI_COMMON + r"""
function run(argv) {
  const a = JSON.parse(argv[0]);
  const p = proc();
  const t = targets(p);
  const wins = t.dialogs.concat(t.windows);
  const windows = wins.map(w => {
    const r = { name: w.name() };
    try { if (w.subrole() === "AXDialog") r.dialog = true; } catch (e) {}
    try { if (w.sheets().length) r.has_sheet = true; } catch (e) {}
    return r;
  });
  let target;
  if (a.window) {
    const i = pick(wins.map(w => w.name()), a.window, "the window list");
    const s = wins[i].sheets();
    target = s.length ? s[0] : wins[i];
  } else {
    target = frontTarget(p);
  }
  return JSON.stringify({ windows: windows, tree: info(target, 0, a.depth, { n: a.max_nodes }) });
}
"""

_JXA_CLICK_BUTTON = _JXA_UI_COMMON + r"""
function run(argv) {
  const a = JSON.parse(argv[0]);
  const p = activate();
  const target = frontTarget(p);
  const btn = findButton(target, a.label, { n: 400 });
  if (!btn) throw new Error("No button '" + a.label + "' in the front window or dialog.");
  btn.click();
  return JSON.stringify({ clicked: a.label });
}
"""

_JXA_SAVE_DIALOG = _JXA_UI_COMMON + r"""
function run(argv) {
  const a = JSON.parse(argv[0]);
  const p = activate();
  let target = null;
  for (let i = 0; i < 40 && !target; i++) {
    const t = targets(p);
    target = t.sheets[0] || t.dialogs[0] || null;
    if (!target) delay(0.25);
  }
  if (!target) throw new Error("No save dialog is open in Final Cut Pro.");
  // File name: prefer the panel's own name field, fall back to the first text field.
  let field = null;
  for (const el of target.entireContents()) {
    let role = "";
    try { role = el.role(); } catch (e) {}
    if (role !== "AXTextField") continue;
    let ident = "";
    try { ident = el.attributes.byName("AXIdentifier").value(); } catch (e) {}
    if (ident === "saveAsNameTextField") { field = el; break; }
    if (!field) field = el;
  }
  if (!field) throw new Error("Could not find the file name field in the save dialog.");
  field.focused = true;
  field.value = a.name;
  delay(0.2);
  // Folder: Go to Folder (shift-cmd-G), type the path, Return.
  se.keystroke("g", { using: ["command down", "shift down"] });
  delay(0.8);
  se.keystroke(a.folder);
  delay(0.4);
  se.keyCode(36);
  delay(1.0);
  try { if (field.value() !== a.name) field.value = a.name; } catch (e) {}
  const btn = findButton(target, a.button, { n: 400 });
  if (!btn) throw new Error("No '" + a.button + "' button in the save dialog.");
  btn.click();
  delay(0.8);
  let replaced = false;
  for (const w of p.windows()) {
    for (const s of w.sheets().concat([w])) {
      if (s === w && w.subrole() !== "AXDialog") continue;
      const r = findButton(s, "Replace", { n: 200 });
      if (r) {
        if (!a.overwrite) {
          const cancel = findButton(s, "Cancel", { n: 200 });
          if (cancel) cancel.click();
          throw new Error("A file with that name already exists. Pass overwrite=true to replace it.");
        }
        r.click();
        replaced = true;
      }
    }
  }
  return JSON.stringify({ saved: a.folder + "/" + a.name, replaced: replaced });
}
"""

_JXA_IMPORT_DIALOG = _JXA_UI_COMMON + r"""
function run(argv) {
  const a = JSON.parse(argv[0]);
  // Windows on another Space (e.g. FCP in full screen) are invisible to UI scripting until FCP is in front.
  const p = activate();
  let d = null;
  for (let i = 0; i < a.wait * 4 && !d; i++) {
    for (const w of targets(p).dialogs) {
      try { if (w.scrollAreas.length && findButton(w, "Choose", { n: 60 })) { d = w; break; } } catch (e) {}
    }
    if (!d) delay(0.25);
  }
  if (!d) return JSON.stringify({ dialog: false, windows_visible: targets(p).all.length > 0, frontmost: p.frontmost() });
  if (a.create) {
    findButton(d, "New", { n: 60 }).click();
    return JSON.stringify({ dialog: true, clicked: "New" });
  }
  const rows = d.scrollAreas[0].tables[0].rows();
  const names = rows.map(r => { try { return r.textFields[0].value(); } catch (e) { return ""; } });
  const i = names.indexOf(a.name);
  if (i < 0) {
    findButton(d, "Cancel", { n: 60 }).click();
    throw new Error("FCP's import dialog does not list '" + a.name + "' (it lists: " + names.join(", ") + "). Import cancelled.");
  }
  rows[i].selected = true;
  delay(0.3);
  findButton(d, "Choose", { n: 60 }).click();
  return JSON.stringify({ dialog: true, chose: a.name });
}
"""

_JXA_FRONT_BOUNDS = _JXA_UI_COMMON + r"""
function run(argv) {
  const w = frontTarget(proc());
  const pos = w.position();
  const size = w.size();
  return JSON.stringify({ x: pos[0], y: pos[1], w: size[0], h: size[1] });
}
"""

KEY_CODES = {
    "space": 49, "return": 36, "enter": 76, "tab": 48, "escape": 53, "esc": 53, "delete": 51,
    "backspace": 51, "forwarddelete": 117, "left": 123, "right": 124, "down": 125, "up": 126,
    "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100,
    "f9": 101, "f10": 109, "f11": 103, "f12": 111,
}  # fmt: skip
MODIFIERS = {
    "cmd": "command down", "command": "command down", "shift": "shift down",
    "opt": "option down", "option": "option down", "alt": "option down",
    "ctrl": "control down", "control": "control down",
}  # fmt: skip


def menu(path: list[str], click: bool) -> dict:
    return run_jxa(_JXA_MENU, {"path": path, "click": click}, timeout=60)


def ui_tree(window: str | None, depth: int, max_nodes: int) -> dict:
    return run_jxa(_JXA_UI_TREE, {"window": window, "depth": depth, "max_nodes": max_nodes}, timeout=90)


def click_button(label: str) -> dict:
    return run_jxa(_JXA_CLICK_BUTTON, {"label": label}, timeout=60)


def fill_save_dialog(folder: str, name: str, button: str = "Save", overwrite: bool = False) -> dict:
    return run_jxa(
        _JXA_SAVE_DIALOG, {"folder": folder, "name": name, "button": button, "overwrite": overwrite}, timeout=90
    )


def answer_import_dialog(library_name: str | None, create: bool = False, wait: float = 20) -> dict:
    """Answer FCP's "Which library do you want to import … into?" dialog.

    Picks the named library, or clicks New… when create is set. Returns {"dialog": False}
    if no dialog appeared within `wait` seconds.
    """
    return run_jxa(_JXA_IMPORT_DIALOG, {"name": library_name, "create": create, "wait": wait}, timeout=wait + 30)


def front_window_bounds() -> dict:
    return run_jxa(_JXA_FRONT_BOUNDS, timeout=20)


def press_keys(combo: str, repeat: int = 1) -> str:
    """Press a shortcut like "cmd+b", "shift+z", "space", "left" in Final Cut Pro."""
    parts = [p.strip().lower() for p in combo.replace("-", "+").split("+") if p.strip()]
    if not parts:
        raise ValueError("Empty key combo.")
    *mods, key = parts
    using = []
    for m in mods:
        if m not in MODIFIERS:
            raise ValueError(f"Unknown modifier {m!r}. Use cmd, shift, opt, ctrl.")
        using.append(MODIFIERS[m])
    using_clause = f" using {{{', '.join(using)}}}" if using else ""
    if key in KEY_CODES:
        action = f"key code {KEY_CODES[key]}{using_clause}"
    elif len(key) == 1:
        action = f"keystroke {json.dumps(key)}{using_clause}"
    else:
        raise ValueError(f"Unknown key {key!r}. Use a single character or one of: {', '.join(sorted(KEY_CODES))}.")
    script = f"""
tell application id "{BUNDLE_ID}" to activate
delay 0.2
tell application "System Events"
    repeat {int(repeat)} times
        {action}
        delay 0.05
    end repeat
end tell
"""
    run_applescript(script, timeout=30)
    return combo
