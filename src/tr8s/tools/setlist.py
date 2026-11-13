"""set tools: a whole set for Live mode. See docs/RIG-FM1.md and issue 05.

An ordered list of tracks becomes TR-8S pattern slots, saved FM-1 projects (one per synth track,
with its song when `arrange` is on) and a markdown cue sheet. `set.plan` is pure: it resolves
every default, rolls the seeds and renders the sheet without opening a port or writing a file.
`set.build` runs the same planner, then writes. Its `tracks` argument takes `set.plan`'s resolved
`tracks` back unchanged, so what is built is exactly what was previewed.

One song per FM-1 project slot, so a set holds at most four synth tracks.
"""

from __future__ import annotations

import json
import random
import re
from datetime import date

from .. import cuesheet
from ..device import DeviceError, slot_to_panel
from ..history import HISTORY
from ..midi_backends import TransportError
from ..synth import minitone as mt
from ..synth.port import Fm1Error
from ._core import DEFAULT_KEYS, DEFAULT_LINE, ToolError, _library_dir, _slot, fm1, opt, tool
from ._core import device as _device_helper
from .kit import kit_auto_build
from .pattern import pattern_set_header
from .synth import build as synth_build
from .synth import sections as synth_sections
from .synth import summary as synth_summary
from .track import track_create

MAX_SYNTH = 4                 # one song per FM-1 project slot, and the FM-1 has four
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
BARS = (4, 8, 16)
LENGTHS = (16, 32, 64)
KEYS = {"style", "name", "key", "energy", "seed", "slot", "kit_slot", "synth", "voicing",
        "project", "arrange", "bars", "length", "override"}
SYNTH_ONLY = ("voicing", "project", "arrange", "length", "override")

_TRACK = {
    "type": "object",
    "properties": {
        "style": {"type": "string", "description": "drum style (styles.list)"},
        "name": {"type": "string", "description": "up to 8 characters; default the style"},
        "key": {"type": "string", "description": "default: one that suits the style"},
        "energy": {"type": "number", "minimum": 0, "maximum": 1, "description": "default 0.7"},
        "seed": {"type": "integer", "description": "omit to roll one; it is returned"},
        "slot": {"type": ["integer", "string"],
                 "description": "TR-8S pattern slot; default the previous track's + 1"},
        "kit_slot": {"type": ["integer", "string"],
                     "description": "build a kit here from the catalogue. Omit to leave the kit alone"},
        "synth": {"type": "boolean", "description": "FM-1 parts (default true); false: drums only"},
        "voicing": {"type": "string", "description": "synth.parts voicing; default the style"},
        "project": {"type": "integer", "minimum": 0, "maximum": 3,
                    "description": "FM-1 project slot; default the next one free, in order"},
        "arrange": {"type": "boolean",
                    "description": "a song: parts come in and out with the drums' arc (default false)"},
        "bars": {"type": "integer", "enum": list(BARS), "description": "bars per variation (default 16)"},
        "length": {"type": "integer", "enum": list(LENGTHS), "description": "FM-1 part length (default 16)"},
        "override": {"type": "object", "description": "as in synth.parts"},
    },
    "required": ["style"],
    "additionalProperties": False,
}


def _params() -> dict:
    """Fresh schema dicts for each tool: `tool()` pops the `_optional` marker."""
    return {
        "name": {"type": "string",
                 "description": "the set's name: lowercase letters, digits and -. Files go to "
                                "library/sets/NAME.json and NAME.md"},
        "tracks": {"type": "array", "items": json.loads(json.dumps(_TRACK)), "minItems": 1,
                   "description": "the playing order"},
        "start_slot": opt({"type": ["integer", "string"],
                           "description": "TR-8S slot of the first track that names none, e.g. '2-01'"}),
    }


# ------------------------------------------------------------------ plan (pure)

def _resolve(name, tracks, start_slot) -> list:
    """Every default filled in, every seed rolled; refusals here, before anything is opened."""
    from ..style import STYLES
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise ToolError(f"name {name!r}: lowercase letters, digits and -, up to 32")
    if not isinstance(tracks, list) or not tracks or not all(isinstance(t, dict) for t in tracks):
        raise ToolError("tracks: a list of one or more objects, each with at least a style")
    synths = sum(1 for t in tracks if t.get("synth", True))
    if synths > MAX_SYNTH:
        raise ToolError(f"a set holds at most {MAX_SYNTH} synth tracks (one FM-1 project each), "
                        f"got {synths}; mark the others synth: false or split the set")

    out, nxt = [], None
    if start_slot is not None:
        nxt = _slot(start_slot)
    for i, t in enumerate(tracks, 1):
        where = f"track {i}"
        extra = set(t) - KEYS
        if extra:
            raise ToolError(f"{where}: unknown fields {sorted(extra)}; accepts {sorted(KEYS)}")
        style = t.get("style")
        if style not in STYLES:
            raise ToolError(f"{where}: unknown style {style!r}; have {', '.join(STYLES)}")
        if "slot" in t:
            slot = _slot(t["slot"])
        elif nxt is None:
            raise ToolError(f"{where}: no slot, and no start_slot to count from")
        else:
            slot = nxt
        if slot > 127:
            raise ToolError(f"{where}: the slots run past 8-16")
        nxt = slot + 1
        energy = t.get("energy", 0.7)
        if not isinstance(energy, (int, float)) or not 0 <= energy <= 1:
            raise ToolError(f"{where}: energy is 0..1, got {energy!r}")
        seed = t.get("seed")
        if seed is None:
            seed = random.randrange(1 << 30)
        elif not isinstance(seed, int):
            raise ToolError(f"{where}: seed is a whole number, got {seed!r}")
        bars = t.get("bars", 16)
        if bars not in BARS:
            raise ToolError(f"{where}: bars is one of {BARS}, got {bars!r}")
        e = {"style": style, "name": (t.get("name") or style.upper())[:8],
             "key": t.get("key") or DEFAULT_KEYS.get(style, "C minor"), "energy": energy,
             "seed": seed, "slot": slot,
             "kit_slot": _slot(t["kit_slot"], "kit") if t.get("kit_slot") is not None else None,
             "synth": bool(t.get("synth", True)), "bars": bars}
        if e["synth"]:
            length = t.get("length", 16)
            if length not in LENGTHS:
                raise ToolError(f"{where}: length is one of {LENGTHS}, got {length!r}")
            project = t.get("project")
            if project is not None and (not isinstance(project, int) or not 0 <= project <= 3):
                raise ToolError(f"{where}: project is an FM-1 slot 0..3, got {project!r}")
            e.update(voicing=t.get("voicing") or style, project=project,
                     arrange=bool(t.get("arrange", False)), length=length,
                     override=t.get("override") or {})
        else:
            given = [k for k in SYNTH_ONLY if k in t]
            if given:
                raise ToolError(f"{where}: {given} need synth: true")
        out.append(e)

    for field, what in (("slot", "TR-8S slot"), ("kit_slot", "kit slot"), ("project", "FM-1 project")):
        seen = {}
        for i, e in enumerate(out, 1):
            v = e.get(field)
            if v is None:
                continue
            if v in seen:
                raise ToolError(f"tracks {seen[v]} and {i} both use {what} {v}")
            seen[v] = i
    free = [p for p in range(4) if p not in {e.get("project") for e in out}]
    for e in out:
        if e["synth"] and e["project"] is None:
            e["project"] = free.pop(0)
    return out


def _timeline(arc, bars: int) -> list:
    rows, prev = [], None
    for n, (variation, role, _e, plays) in enumerate(arc):
        rows.append({"bar": 1 + n * bars, "variation": variation, "role": role, "plays": plays,
                     "in": [] if prev is None else [t for t in plays if t not in prev],
                     "out": [] if prev is None else [t for t in prev if t not in plays]})
        prev = plays
    return rows


def _plan_track(i: int, e: dict) -> dict:
    from ..style import ARC, arrangement
    where = f"track {i}"
    t = {"index": i, "name": e["name"], "style": e["style"], "key": e["key"], "energy": e["energy"],
         "seed": e["seed"], "slot": e["slot"], "panel": slot_to_panel(e["slot"]),
         "kit": None if e["kit_slot"] is None else e["kit_slot"] + 1, "bars": e["bars"],
         "end_bar": 1 + len(ARC) * e["bars"], "synth": None, "start_muted": [], "warnings": []}
    try:
        t["tempo"] = arrangement(e["style"], seed=e["seed"], energy=e["energy"])["tempo"]
    except ValueError as ex:
        raise ToolError(f"{where}: {ex}") from None
    if not e["synth"]:
        t["line"] = DEFAULT_LINE.get(e["style"], "bass")
        t["timeline"] = _timeline([(v, r, lv, []) for v, r, lv in ARC], e["bars"])
        return t
    try:
        parts = synth_build(e["voicing"], e["key"], e["energy"], e["seed"], e["length"], e["override"])
        sections = synth_sections(parts, e["energy"], e["bars"]) if e["arrange"] else None
    except (ValueError, ToolError) as ex:
        raise ToolError(f"{where} ({e['voicing']}): {ex}") from None
    s = synth_summary(parts, sections)
    t["line"] = "none"
    t["synth"] = {"voicing": e["voicing"], "project": e["project"],
                  "project_letter": cuesheet.project_letter(e["project"]), "arrange": e["arrange"],
                  "length": e["length"], "parts": s["tracks"], "sections": sections,
                  "song_rows": mt.song_rows(sections, e["project"]) if sections else None}
    t["warnings"] = s["warnings"]
    t["timeline"] = _timeline(mt.arc_plays(parts, e["energy"]), e["bars"])
    if not e["arrange"]:
        t["start_muted"] = [int(k) for k in sorted(s["tracks"], key=int)
                            if int(k) not in t["timeline"][0]["plays"]]
    return t


def _writes(plan: list) -> list:
    out = [f"TR-8S kit {t['kit']}: built from the catalogue (history.undo restores it)"
           for t in plan if t["kit"] is not None]
    for t in plan:
        out.append(f"TR-8S pattern {t['panel']}: {t['name']}, A-H arranged (history.undo restores it)")
        s = t["synth"]
        if s:
            song = f"a song of {len(s['sections'])} sections" if s["arrange"] else "no song"
            out.append(f"FM-1 project {s['project_letter']}: {len(s['parts'])} parts and {song}, saved "
                       "(stops the FM-1's transport, overwrites the slot, no undo)")
    return out


def _files(name: str) -> dict:
    d = _library_dir() / "sets"
    return {"inputs": str(d / f"{name}.json"), "cue_sheet": str(d / f"{name}.md")}


def plan(name, tracks, start_slot=None, built_on=None) -> dict:
    """The whole set, resolved and rendered. Touches no device and writes no file."""
    resolved = _resolve(name, tracks, start_slot)
    per = [_plan_track(i, e) for i, e in enumerate(resolved, 1)]
    out = {"name": name, "tracks": resolved, "plan": per, "writes": _writes(per), "files": _files(name)}
    out["cue_sheet"] = cuesheet.render(out, built_on or date.today())
    return out


@tool("set.plan",
      "Plan a whole set for Live mode without touching any device or writing any file: each "
      "track's TR-8S slot, FM-1 project, key, tempo, seed, parts and (with `arrange`) song "
      "sections, what set.build would overwrite, and the markdown cue sheet to play it from. "
      "Show this before set.build, then pass its `tracks` to set.build to build exactly this.",
      _params())
def set_plan(name, tracks, start_slot=None):
    return plan(name, tracks, start_slot)


# ------------------------------------------------------------------ build

def _state(stage: str, t: dict) -> str:
    if stage == "kit":
        return "no pattern was written and the FM-1 was not touched"
    if stage == "TR-8S":
        return (f"the tracks before {t['index']} are complete; pattern {t['panel']} may be partly "
                "written (history.undo restores it); the FM-1 was not touched for this track")
    letter = t["synth"]["project_letter"]
    return (f"pattern {t['panel']} is written; the FM-1 may hold part of this track's parts in its "
            f"working memory, and project {letter} was not saved, unless the save itself is what "
            "failed (then it may hold the new parts)")


@tool("set.build",
      "Build a set: write each track's TR-8S pattern (and kit, if it names one), write and SAVE "
      "each synth track's FM-1 project (its song too, with `arrange`), and save the cue sheet to "
      "library/sets/NAME.md with the inputs in NAME.json. A save stops the FM-1's transport and "
      "overwrites the project; the TR-8S side can be undone with history.undo (the result says "
      "how many steps). Stops at the first failure and says what was done. At most four synth "
      "tracks. Only on the user's request, after showing set.plan.",
      _params(), mutates=True)
def set_build(name, tracks, start_slot=None):
    p = plan(name, tracks, start_slot)
    per = p["plan"]

    try:
        dev = _device_helper()          # an unplugged or powered-off box refuses here, before anything is written
    except (DeviceError, TransportError) as e:
        raise ToolError(f"the TR-8S is not reachable, nothing was written: {e}") from None
    if any(t["synth"] for t in per):
        try:
            fm1()
        except Fm1Error as e:
            raise ToolError(f"the FM-1 is not reachable, nothing was written: {e}") from None

    d = _library_dir() / "sets"
    d.mkdir(parents=True, exist_ok=True)
    files = _files(name)
    with open(files["inputs"], "w") as f:
        f.write(json.dumps({"name": name, "tracks": p["tracks"]}, indent=2) + "\n")
    written = [files["inputs"]]

    captured, not_captured = [], []

    def capture(kind, slot, label):
        (captured if HISTORY.capture(dev, kind, slot, label) else not_captured).append(label)

    built, failed = [], None
    for t in per:
        if t["kit"] is None:
            continue
        label = f"set.build {name}: kit {t['kit']} (track {t['index']})"
        capture("kit", t["kit"] - 1, label)
        try:
            kit_auto_build(t["kit"] - 1, style=t["style"], key=t["key"], name=t["name"], seed=t["seed"])
        except (ToolError, DeviceError, ValueError) as e:
            failed = {"index": t["index"], "panel": t["panel"], "stage": "kit", "error": str(e),
                      "state": _state("kit", t)}
            break

    if failed is None:
        for t, e in zip(per, p["tracks"]):
            capture("pattern", t["slot"], f"set.build {name}: track {t['index']} on {t['panel']}")
            synth = None
            if e["synth"]:
                synth = {"voicing": e["voicing"], "project": e["project"], "arrange": e["arrange"],
                         "bars": e["bars"], "length": e["length"], "override": e["override"]}
            try:
                r = track_create(t["slot"], t["style"], name=t["name"], key=t["key"], energy=t["energy"],
                                 kit_slot=None, seed=t["seed"], synth=synth)
                if t["kit"] is not None:
                    pattern_set_header(t["slot"], kit=t["kit"] - 1)
            except (ToolError, DeviceError, Fm1Error, ValueError) as ex:
                failed = {"index": t["index"], "panel": t["panel"], "stage": "TR-8S", "error": str(ex),
                          "state": _state("TR-8S", t)}
                break
            done = {"index": t["index"], "panel": t["panel"], "tempo": r["tempo"], "steps": r["steps"],
                    "audit": (r.get("audit") or {}).get("verdict")}
            if r["tempo"] != t["tempo"]:
                done["warning"] = f"tempo {r['tempo']} differs from the plan's {t['tempo']}"
            if "synth" in r:
                done["saved"] = r["synth"].get("saved")
                done["song"] = r["synth"].get("song")
            built.append(done)
            if "synth_error" in r:
                failed = {"index": t["index"], "panel": t["panel"], "stage": "FM-1",
                          "error": r["synth_error"], "state": _state("FM-1", t)}
                break

    if failed is None:
        with open(files["cue_sheet"], "w") as f:
            f.write(p["cue_sheet"])
        written.append(files["cue_sheet"])

    saved = [t["synth"]["project_letter"] for t, b in zip(per, built)
             if t["synth"] and b.get("saved")]
    if failed is None:
        untouched = []
    elif failed["stage"] == "kit":
        untouched = [t["index"] for t in per]
    else:
        untouched = [t["index"] for t in per if t["index"] > failed["index"]]
    n = len(captured)
    return {**p, "built": built, "failed": failed, "untouched": untouched,
            "undo": {"history_steps": n,
                     "call": {"tool": "history.undo", "args": {"steps": n}} if n else None,
                     "covers": captured, "not_captured": not_captured,
                     "fm1": ("no undo: FM-1 project " + ", ".join(saved) + " overwritten") if saved
                            else "no FM-1 project was saved"},
            "written": written}
