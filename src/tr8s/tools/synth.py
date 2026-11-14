"""synth tools: parts for the FM-1 (Mini-Tone). See docs/RIG-FM1.md and docs/FM1-PROTOCOL.md.

The studio never sends MIDI clock: the FM-1 follows the TR-8S. Anything that
writes to the FM-1 is `mutates=True`; the project argument is called `project`,
never `slot`, so the undo capture (which reads a TR-8S slot) leaves it alone.
"""

from __future__ import annotations

import copy
import re

from ..synth import minitone as mt
from ..synth.port import Fm1Error
from ._core import ToolError, _library_dir, close_fm1, fm1, opt, tool

_OVERRIDE = {"type": "object",
             "description": "per FM-1 track (0-5), replace the voicing: "
                            "{\"1\": {\"shape\": \"acid\", \"engine\": \"TB303\"}} "
                            "or null to drop that track. Optional per track: root "
                            "(register, e.g. \"A1\") and rhythm ([pulses, rotate], "
                            "Euclidean; not for pad or drone). Shapes: "
                            "bass acid stab arp pad drone lead.",
             "additionalProperties": {"anyOf": [
                 {"type": "null"},
                 {"type": "object",
                  "properties": {"shape": {"type": "string"}, "engine": {"type": "string"},
                                 "root": {"type": "string"},
                                 "rhythm": {"type": "array", "items": {"type": "integer"},
                                            "minItems": 2, "maxItems": 2}},
                  "required": ["shape", "engine"]}]}}


def _overrides(raw) -> dict | None:
    """{"1": {"shape", "engine", "root"?, "rhythm"?}, "2": null} -> {1: (shape, engine, extra), 2: None}"""
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise ToolError("override: an object of track -> {shape, engine} or null")
    out = {}
    for t, v in raw.items():
        try:
            track = int(t)
        except (TypeError, ValueError):
            raise ToolError(f"override track {t!r} is not a number 0-{mt.TRACKS - 1}") from None
        if v is None:
            out[track] = None
            continue
        if not (isinstance(v, dict) and isinstance(v.get("shape"), str)
                and isinstance(v.get("engine"), str)):
            raise ToolError(f"override for track {t}: null, or an object with a shape and an engine")
        extra = {}
        if v.get("root") is not None:
            if not isinstance(v["root"], str):
                raise ToolError(f"override for track {t}: root is a note name such as 'A1'")
            extra["root"] = v["root"]
        if v.get("rhythm") is not None:
            r = v["rhythm"]
            if not (isinstance(r, (list, tuple)) and len(r) == 2 and all(isinstance(x, int) for x in r)):
                raise ToolError(f"override for track {t}: rhythm is [pulses, rotate]")
            extra["rhythm"] = tuple(r)
        out[track] = (v["shape"], v["engine"], extra)
    return out


def build(style, key="C minor", energy=None, seed=0, length=16, override=None) -> dict:
    """{track: Part}; ValueError (-> ToolError) for an unknown style, shape or engine."""
    return mt.build_parts(style, key=key, energy=energy, seed=seed, length=length,
                          overrides=_overrides(override))


def summary(parts: dict, sections: list | None = None) -> dict:
    """What a caller needs to tell the musician: each track's shape, engine, channel and notes."""
    out = {"tracks": {str(t): {"shape": p.shape, "engine": p.engine, "channel": mt.CHANNELS[t],
                               "steps": p.length, "notes": p.names()}
                      for t, p in parts.items()},
           "warnings": mt.voice_budget(parts) + [w for p in parts.values() for w in p.warnings]}
    if sections is not None:
        out["sections"] = sections
    return out


def sections(parts: dict, energy, bars: int = 16) -> list:
    """The song sections for the parts (pure); ValueError (-> ToolError) if they cannot be a song."""
    return mt.song_sections(parts, 0.6 if energy is None else energy, bars)


def push(parts: dict, project: int | None = None, song: list | None = None) -> dict:
    """
    Write the parts to the FM-1: engine and length, then every step (RAM only). With `song`
    (sections from `sections`) the chain follows, in RAM. With `project` (0..3) it then saves,
    which STOPS the FM-1's transport and writes flash; the save comes last so that it captures
    the chain. A song needs a project: the FM-1 plays saved slots. A save without a song empties
    the chain first, so the project never keeps a song left in RAM by an earlier load or track.
    """
    if project is not None and not 0 <= project <= 3:
        raise ToolError(f"project {project} is outside 0..3")     # before anything is sent
    if song is not None and project is None:
        raise ToolError("a song needs a project slot: the FM-1 plays saved slots")
    rows = mt.song_rows(song, project) if song is not None else None
    try:
        port = fm1()
        sent = port.write_frames(mt.setup_frames(parts) + mt.to_syx(parts))
        out = {"sent": sent, "saved": None}
        if rows is not None:
            out["song"] = port.set_song(rows)
        elif project is not None:
            out["song"] = port.clear_song()
        if project is not None:
            out["saved"] = port.save(project)
        return out
    except Fm1Error:
        close_fm1()         # an unplugged FM-1 leaves a dead handle: the next call opens a fresh one
        raise


def _common() -> dict:
    """Fresh schema dicts for every tool: `tool()` pops the `_optional` marker, so they cannot be shared."""
    return {
        "key": opt({"type": "string", "description": "e.g. 'A minor', 'F# phrygian'"}),
        "energy": opt({"type": "number", "minimum": 0, "maximum": 1}),
        "seed": opt({"type": "integer", "description": "the same seed gives the same parts"}),
        "length": opt({"type": "integer", "enum": [16, 32, 64]}),
        "override": opt(copy.deepcopy(_OVERRIDE)),
    }
_STYLE = {"type": "string", "description": f"a voicing: {', '.join(mt.VOICING)}. Same names as the drum "
                                           "styles; not every voicing has a 303."}


@tool("synth.parts",
      "Generate the FM-1 parts for a style, without touching any device: which "
      "shape (bass, acid, stab, arp, pad, drone, lead) on which track and "
      "engine, and the notes. The FM-1 has six tracks on MIDI channels 1-6 and "
      "eight voices in all; a warning says when the parts together need more. "
      "Not every song needs a 303: a dub voicing is pad and stab. Seeded.",
      {"style": dict(_STYLE), **_common()})
def synth_parts(style, key="C minor", energy=None, seed=0, length=16, override=None):
    parts = build(style, key, energy, seed, length, override)
    return {"style": style, "key": key, "seed": seed, **summary(parts)}


@tool("synth.export",
      "Write the FM-1 parts for a style into library/synth/ as three files: "
      "NAME.json (the inputs, to rebuild it), NAME.mid (one channel per track, "
      "to audition in a DAW or play into the FM-1) and NAME.syx (the editor "
      "frames that write the parts). Writes files only; no device is touched.",
      {"name": {"type": "string", "description": "file name, letters, digits, - and _"},
       "style": dict(_STYLE), **_common(),
       "bpm": opt({"type": "integer", "minimum": 40, "maximum": 240,
                   "description": "tempo of the .mid (default 130)"})})
def synth_export(name, style, key="C minor", energy=None, seed=0, length=16, override=None,
                 bpm=130):
    import json
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name or ""):
        raise ToolError("name: letters, digits, - and _ only (up to 40)")
    parts = build(style, key, energy, seed, length, override)
    d = _library_dir() / "synth"
    d.mkdir(parents=True, exist_ok=True)
    doc = {"target": "minitone", "style": style, "key": key, "energy": energy, "seed": seed,
           "length": length, "override": override or {}, "bpm": bpm, **summary(parts)}
    files = {"json": d / f"{name}.json", "mid": d / f"{name}.mid", "syx": d / f"{name}.syx"}
    files["json"].write_text(json.dumps(doc, indent=2) + "\n")
    files["mid"].write_bytes(mt.to_mid(parts, bpm=bpm))
    files["syx"].write_bytes(b"".join(mt.setup_frames(parts) + mt.to_syx(parts)))
    return {"files": {k: str(v) for k, v in files.items()}, **summary(parts)}


@tool("synth.send",
      "Write the FM-1 parts for a style to the connected FM-1 (Mini-Tone). "
      "Without `project` the parts go into the FM-1's working memory only: "
      "they play now and are gone at power-off, which is how to audition. "
      "With `project` (0-3) they are then SAVED to that project slot, which "
      "STOPS the FM-1's transport and writes flash, so only do that on request. "
      "Every frame is checked first and nothing is sent if one is wrong.",
      {"style": dict(_STYLE), **_common(),
       "project": opt({"type": "integer", "minimum": 0, "maximum": 3,
                       "description": "FM-1 project slot to save to (flash write; stops its transport)"})},
      mutates=True)
def synth_send(style, key="C minor", energy=None, seed=0, length=16, override=None,
               project=None):
    parts = build(style, key, energy, seed, length, override)
    return {"style": style, "key": key, "seed": seed, **summary(parts), **push(parts, project)}
