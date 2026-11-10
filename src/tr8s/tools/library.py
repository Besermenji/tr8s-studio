"""library tools — see the package docstring for the conventions."""

from __future__ import annotations

import inspect
import re
import subprocess
from pathlib import Path

from .. import config
from ..device import Device, DeviceError, panel_to_slot, slot_to_panel
from ..history import HISTORY
from ..kit import FIELDS as KIT_FIELDS
from ..kit import TRACKS, Kit
from ..melody import MelodyError
from ..melody import read as melody_read
from ..melody import write as melody_write
from ..pattern import VARIATIONS, Pattern
from ..tones import Catalog
from ._core import (DEFAULT_KEYS, DEFAULT_LINE, REGISTRY, ToolError,
                    _library_dir, _slot, opt, tool)
from ._core import device as _device_helper
from .pattern import pattern_import

@tool("library.list",
      "The finished tracks kept as JSON in the repo, with the style, key and "
      "tempo of each. These are known-good starting points -- loading one is "
      "usually better than generating from scratch when the user asks for "
      "something that already exists here.",
      {})
def library_list():
    import json as _json
    out = []
    d = _library_dir()
    if not d.is_dir():
        return {"tracks": [], "note": f"no library directory at {d}"}
    for f in sorted(d.glob("*.json")):
        try:
            doc = _json.loads(f.read_text())
        except Exception as e:
            out.append({"name": f.stem, "error": str(e)})
            continue
        meta = doc.get("_meta") or {}
        entry = {"name": f.stem, "title": doc.get("name"),
                 "tempo": doc.get("tempo"), "style": meta.get("style"),
                 "key": meta.get("key"), "about": meta.get("about"),
                 "variations": sorted(doc.get("variations") or {})}
        if doc.get("synth"):
            entry["synth"] = doc["synth"].get("voicing")
        out.append(entry)
    return {"tracks": out, "directory": str(d)}


# `project` is not kept: a saved track must not carry a flash slot that a replay would overwrite
SYNTH_KEYS = ("target", "voicing", "key", "energy", "seed", "length", "override")


@tool("library.save",
      "Keep the track in a pattern slot in the library as JSON, with its style, "
      "key and seed so it can be rebuilt or varied, and optionally the FM-1 "
      "parts that go with it. Pass the `synth` block that track_create returned "
      "(its inputs are stored, not the notes). Writes a file in the repo; the "
      "device is only read. Refuses to replace a track unless overwrite=true.",
      {"slot": {"type": ["integer", "string"], "description": "the pattern to keep"},
       "name": {"type": "string", "description": "file name, letters, digits, - and _"},
       "style": {"type": "string"},
       "key": {"type": "string"},
       "seed": opt({"type": "integer"}),
       "about": opt({"type": "string"}),
       "synth": opt({"type": "object", "description": "the `synth` result of track_create"}),
       "overwrite": opt({"type": "boolean"})})
def library_save(slot, name, style, key, seed=None, about=None, synth=None, overwrite=False):
    import json as _json
    from .pattern import pattern_export
    name = str(name).strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,40}", name):
        raise ToolError("name: letters, digits, - and _ only (up to 40)")
    f = _library_dir() / f"{name}.json"
    if f.exists() and not overwrite:
        raise ToolError(f"library track {name!r} exists; pass overwrite=true to replace it")
    block = None
    if synth:
        block = {k: synth[k] for k in SYNTH_KEYS if k in synth}
        from .synth import build as synth_build
        synth_build(block.get("voicing") or style, block.get("key") or key, block.get("energy"),
                    block.get("seed") or 0, block.get("length", 16), block.get("override"))   # refuses a bad block
    s = _slot(slot)
    doc = pattern_export(s)
    meta = {"style": style, "key": key, "about": about or "", "built_by": "tr8s library.save",
            "origin_panel": slot_to_panel(s)}
    if seed is not None:
        meta["seed"] = seed
    doc["_meta"] = meta
    if block:
        doc["synth"] = block
    f.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(f, "w" if overwrite else "x") as fh:      # "x": never replace a track another call just made
            fh.write(_json.dumps(doc, indent=2) + "\n")
    except FileExistsError:
        raise ToolError(f"library track {name!r} exists; pass overwrite=true to replace it") from None
    return {"saved": name, "path": str(f), "synth": bool(synth)}




@tool("library.load",
      "Write one of the library tracks into a pattern slot. Reports what was "
      "in the slot beforehand, since this overwrites it -- history.undo puts "
      "it back.",
      {"name": {"type": "string", "description": "from library.list, e.g. 'acidtrax'"},
       "slot": {"type": ["integer", "string"]},
       "commit": opt({"type": "boolean"})}, mutates=True)
def library_load(name, slot, commit=True):
    import json as _json
    f = _library_dir() / f"{str(name).strip().lower()}.json"
    if not f.is_file():
        have = sorted(p.stem for p in _library_dir().glob("*.json"))
        raise ToolError(f"no library track {name!r}; have {', '.join(have)}")
    doc = _json.loads(f.read_text())

    s = _slot(slot)
    try:
        was = _device_helper().read_pattern(s).name
    except DeviceError:
        was = None

    r = pattern_import(s, doc, commit=commit)
    r.update(loaded=name, title=doc.get("name"), replaced=was,
             meta=doc.get("_meta") or {})
    if doc.get("synth"):
        # the inputs of the FM-1 parts that go with this track; synth.send rebuilds them
        r["synth"] = doc["synth"]
    if doc.get("variations"):
        r["panel_setup"] = [
            "MOTION [ON] must be lit for the melodies to sound",
            "Coarse Tune must be on the melodic instrument's CTRL knob",
            f"the track expects kit {doc.get('kit', 0) + 1}",
        ]
    return r


