"""
A set reworked from the machine's own patterns and kits. See docs/PERFORMANCE.md.

A spec (library/sets/NAME.json) lists 16 slots, each naming an onboard SOURCE pattern, the
set's tempo and a role. Building one slot:

  * the source pattern's blob is copied whole -- every byte this project has not decoded yet
    (sub-steps, flams, probability, per-track and per-variation last step, the fill-ins,
    scatter) comes along unchanged -- then renamed, given the set's tempo, and pointed at the
    slot's own kit;
  * the source pattern's kit is copied whole into the slot's kit, renamed, and its fader
    colours set by role (one scheme across the whole set: ROLE_COLOURS);
  * the kit index equals the pattern slot (8-01 is slot 112 and uses kit 113, shown 1-based).

`plan()` is pure: it works from blobs already read (the survey directory) and says exactly
what would be written. `build()` writes: first one pattern this process owns (the last
slot), so that every kit commit re-points a slot of the set and `Device` repairs it; then
every kit; then every pattern -- kits before patterns, docs/PROTOCOL.md.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .device import DeviceError, check_blob, panel_to_slot, slot_to_panel
from .kit import TRACKS, Kit
from .pattern import Pattern
from .survey import analyse_pattern

# The fader palette, confirmed on the panel 2026-11-13 (docs/PROTOCOL.md).
ROLE_COLOURS = {
    "kick": 0,          # red
    "snare": 1,         # orange      snare, clap
    "acid": 2,          # yellow      the pitched line
    "odd1": 3,          # light green the first voice on an odd cycle
    "odd2": 4,          # green       the second
    "light": 5,         # light blue  a tom or perc voice with no special job
    "hat": 6,           # blue        closed hat
    "open": 7,          # dark blue   open hat
    "cymbal": 8,        # purple      crash, ride
    "rim": 9,           # pink        rim, small percussion
    "fx": 10,           # light pink
}
DEFAULT_ROLE = {"BD": "kick", "SD": "snare", "HC": "snare", "CH": "hat", "OH": "open",
                "CC": "cymbal", "RC": "cymbal", "RS": "rim",
                "LT": "light", "MT": "light", "HT": "light"}


class ReworkError(ValueError):
    pass


def sha(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def roles(analysis: dict) -> dict:
    """Each instrument's role in this pattern: a pitched line is the acid voice (the first
    one; any other pitched voice counts as an odd-cycle voice), odd cycles are green."""
    out = dict(DEFAULT_ROLE)
    pitched = [i for i in analysis.get("pitched_motion", []) if i not in ("BD", "SD", "CH", "OH")]
    odd = []
    for item in analysis.get("poly_tracks", []):
        inst = item.split("/")[0]
        if inst not in odd and inst not in ("BD",):
            odd.append(inst)
    if pitched:
        out[pitched[0]] = "acid"
    greens = [i for i in pitched[1:] + odd if out.get(i) != "acid"]
    seen = []
    for i in greens:
        if i not in seen:
            seen.append(i)
    for n, inst in enumerate(seen[:2]):
        out[inst] = "odd1" if n == 0 else "odd2"
    return out


def route(analysis: dict) -> list:
    """A playing route through A-H from what each variation measures: lightest with a kick
    first, the kickless (or lightest) one as the break, the densest last. Advice for the cue
    sheet; the pattern itself is not reordered."""
    d = {v: b for v, b in analysis["detail"].items() if v in "ABCDEFGH"}
    if not d:
        return []
    kick = {v: "BD" in b["tracks"] for v, b in d.items()}
    hits = {v: b["hits"] for v, b in d.items()}
    breaks = sorted([v for v in d if not kick[v]], key=lambda v: (hits[v], v))
    rest = sorted([v for v in d if kick[v]], key=lambda v: (hits[v], v))
    out = []

    def add(v, role):
        out.append({"variation": v, "role": role, "hits": hits[v]})

    intro = rest.pop(0) if rest else None
    brk, brk_role = None, None
    if breaks:
        brk, brk_role = breaks.pop(0), "break (no kick)"
    elif len(rest) >= 3:
        brk, brk_role = rest.pop(0), "break (mute the kick)"
    mains, tops = rest[:len(rest) // 2], rest[len(rest) // 2:]
    if intro:
        add(intro, "intro")
    for v in mains:
        add(v, "main")
    if brk:
        add(brk, brk_role)
    for n, v in enumerate(tops):
        add(v, "drop" if n == 0 else "peak")
    for v in breaks:
        add(v, "breather (no kick)")
    return out


def tune_lines(blob: bytes, minimum: int = 4) -> list:
    """Instruments carrying a Fine Tune line (tune motion on at least `minimum` steps, any block)."""
    from .pattern import NBLOCKS, STEPS
    p = Pattern.from_bytes(blob)
    return [inst for inst in TRACKS
            if sum(p.get_motion(b, inst, st)["tune"] is not None
                   for b in range(NBLOCKS) for st in range(STEPS)) >= minimum]


def tune_offsets(blob: bytes, inst: str | None) -> list:
    """The distinct Fine Tune offsets `inst` carries in the source, in semitones, lowest first."""
    if not inst:
        return []
    from .pattern import NBLOCKS, STEPS
    p = Pattern.from_bytes(blob)
    vals = {p.get_motion(b, inst, st)["tune"] for b in range(NBLOCKS) for st in range(STEPS)}
    return sorted(round(v / FINE_UNITS, 2) for v in vals if v is not None)


def is_sweep(offsets: list, tolerance: float = 0.15) -> bool:
    """Offsets off the semitone grid: a recorded knob sweep, not a melody in any key."""
    return any(abs(o - round(o)) > tolerance for o in offsets)


def load_spec(path) -> dict:
    spec = json.loads(Path(path).read_text())
    slots = spec.get("slots") or []
    if not slots:
        raise ReworkError("the spec has no slots")
    seen = set()
    for i, s in enumerate(slots, 1):
        for k in ("slot", "source", "bpm", "label"):
            if k not in s:
                raise ReworkError(f"slot {i}: missing {k!r}")
        if s["slot"] in seen:
            raise ReworkError(f"slot {s['slot']} appears twice")
        seen.add(s["slot"])
    return spec


def _slot(panel: str) -> int:
    b, n = panel.split("-")
    return panel_to_slot(int(b), int(n))


def plan(spec: dict, survey_dir) -> list:
    """Every slot resolved from blobs already on disk; nothing is opened or written."""
    d = Path(survey_dir)
    out = []
    for i, s in enumerate(spec["slots"], 1):
        dst, src = _slot(s["slot"]), _slot(s["source"])
        pf = d / f"pattern_{src:03d}.bin"
        if not pf.exists():
            raise ReworkError(f"{s['slot']}: no survey read of source {s['source']} at {pf}")
        pblob = pf.read_bytes()
        why = check_blob("pattern", pblob)
        if why:
            raise ReworkError(f"{s['slot']}: source {s['source']}: {why}")
        a = analyse_pattern(pblob, src)
        if a.get("empty"):
            raise ReworkError(f"{s['slot']}: source {s['source']} is empty")
        src_kit = pblob[18] - 1
        name = f"{i:02d} {s['label']}"[:16]
        r = roles(a)
        if s.get("acid"):
            for inst, role in list(r.items()):
                if role == "acid" and inst != s["acid"]:
                    r[inst] = "odd"
            r[s["acid"]] = "acid"
            odd = [i for i in TRACKS if r[i] in ("odd", "odd1", "odd2")]
            for n, inst in enumerate(odd):
                r[inst] = "odd1" if n == 0 else "odd2" if n == 1 else DEFAULT_ROLE[inst]
        out.append({
            "index": i, "slot": dst, "panel": slot_to_panel(dst), "source": src,
            "source_panel": s["source"], "source_name": a["name"], "source_tempo": a["tempo"],
            "source_sha256": sha(pblob), "name": name, "bpm": s["bpm"], "role": s.get("role", ""),
            "energy": s.get("energy"), "notes": s.get("notes", ""),
            "kit": dst, "kit_panel": dst + 1, "source_kit": src_kit, "source_kit_panel": src_kit + 1,
            "roles": r, "colours": {inst: ROLE_COLOURS[role] for inst, role in r.items()},
            "route": route(a), "blocks": a["blocks"], "poly_tracks": a["poly_tracks"],
            "pitched_motion": a["pitched_motion"],
            "kit_design": s.get("kit"), "coarse_to_fine": s.get("coarse_to_fine", []),
            "clear_ctrl": s.get("clear_ctrl", []), "acid": s.get("acid"),
            "tune_lines": tune_lines(pblob),
            "tune_offsets": tune_offsets(pblob, s.get("acid")),
            "shuffle": a.get("shuffle", 0),
            "soften": s.get("soften", spec.get("soften")),
            "keep_lines": s.get("keep_lines", [s["acid"]] if s.get("acid") else []),
        })
    kits = [e["kit"] for e in out]
    if len(set(kits)) != len(kits):
        raise ReworkError("two slots would share a kit")
    return out


def writes(entries: list) -> list:
    last = max(entries, key=lambda e: e["slot"])
    w = [f"pattern {last['panel']}: rewritten with its own current bytes first (the slot every "
         "kit commit re-points, repaired at once)"]
    for e in entries:
        if e.get("kit_design"):
            what = "new: " + ", ".join(f"{i} {t['tone']}" for i, t in e["kit_design"].items())
        else:
            what = f"copy of kit {e['source_kit_panel']} (from {e['source_panel']})"
        w.append(f"kit {e['kit_panel']}: {what}; named {e['name']!r}, colours by role")
    w += [f"pattern {e['panel']}: copy of {e['source_panel']} {e['source_name']!r}, named "
          f"{e['name']!r}, {e['bpm']:g} BPM, kit {e['kit_panel']}" for e in entries]
    return w


def build_pattern(src_blob: bytes, e: dict) -> Pattern:
    p = Pattern.from_bytes(src_blob)
    if e.get("coarse_to_fine"):
        e["converted"] = coarse_to_fine(p, e["coarse_to_fine"])
    if e.get("clear_ctrl"):
        e["ctrl_cleared"] = clear_ctrl(p, e["clear_ctrl"])
    if e.get("soften") is not None:
        e["softened"] = soften(p, e.get("keep_lines") or [], e["soften"])
    p.name = e["name"]
    p.tempo = e["bpm"]
    p.kit = e["kit"]
    return p


def build_kit(src_blob: bytes, e: dict) -> Kit:
    k = Kit.from_bytes(src_blob)
    k.name = e["name"]
    for inst in TRACKS:
        k.set_color(inst, e["colours"][inst])
    return k


def build(dev, entries: list, survey_dir, pause: float = 1.5, sleep=time.sleep, log=print,
          designed: dict | None = None, only=None) -> dict:
    """
    Write the set. Stops at the first failure and says what was done.

    Each source pattern is read again (twice) and must equal its survey read, so a slot that
    changed since the survey is never copied blind. Each source kit is read twice and kept in
    the survey directory.
    """
    d = Path(survey_dir)
    if only:
        # Touch these slots (their kit and pattern) and nothing else: no reads, no compares,
        # no restores elsewhere -- the player may be working on the other patterns.
        entries = [e for e in entries if e["panel"] in set(only)]
        if not entries:
            raise ReworkError(f"none of {sorted(only)} is a slot of this set")
    done = {"owned": None, "kits": [], "patterns": [], "failed": None}

    def fail(stage, e, err):
        done["failed"] = {"stage": stage, "slot": e and e.get("panel"), "error": str(err)}
        log(f"FAILED at {stage} {e and e.get('panel')}: {err}")
        return done

    src_patterns, src_kits = {}, {}
    for e in entries:
        try:
            blob = dev.read_checked("pattern", e["source"], twice=True, sleep=sleep)
        except DeviceError as err:
            return fail("read source pattern", e, err)
        if sha(blob) != e["source_sha256"]:
            return fail("read source pattern", e, f"{e['source_panel']} changed since the survey")
        src_patterns[e["index"]] = blob
        sleep(pause)
        if designed and e["index"] in designed:
            log(f"read {e['source_panel']}")
            continue
        if e["source_kit"] not in src_kits:
            try:
                kb = dev.read_checked("kit", e["source_kit"], twice=True, sleep=sleep)
            except DeviceError as err:
                return fail("read source kit", e, err)
            (d / f"kit_{e['source_kit']:03d}.bin").write_bytes(kb)
            src_kits[e["source_kit"]] = kb
            sleep(pause)
        log(f"read {e['source_panel']} and kit {e['source_kit_panel']}")

    last = max(entries, key=lambda e: e["slot"])
    try:
        own = dev.read_checked("pattern", last["slot"], twice=True, sleep=sleep)
        r = dev.write_pattern(last["slot"], Pattern.from_bytes(own), commit=True, verify=True)
    except DeviceError as err:
        return fail("own pattern", last, err)
    if not r.get("verified"):
        return fail("own pattern", last, r)
    done["owned"] = last["panel"]
    sleep(pause)

    def same_kit(a: bytes, b: bytes) -> bool:
        a, b = bytearray(a), bytearray(b)
        for inst in TRACKS:                          # level is the fader's: ignore it
            o = Kit.record_offset(inst) + 4
            a[o] = b[o] = 0
        return a == b

    done["skipped"] = []
    for e in entries:
        kit = designed[e["index"]][0] if designed and e["index"] in designed else \
            build_kit(src_kits[e["source_kit"]], e)
        try:
            current = dev.read_checked("kit", e["kit"], sleep=sleep)
        except DeviceError:
            current = None
        sleep(pause)
        if current is not None and same_kit(current, kit.to_bytes()):
            done["skipped"].append(f"kit {e['kit_panel']}")
            log(f"kit {e['kit_panel']} already holds {e['name']}: skipped")
            continue
        try:
            r = dev.write_kit(e["kit"], kit, verify=True)
        except DeviceError as err:
            return fail("kit", e, err)
        if not r.get("verified"):
            return fail("kit", e, r)
        done["kits"].append(e["kit_panel"])
        log(f"kit {e['kit_panel']} written ({e['name']})")
        sleep(pause)

    for e in entries:
        want = build_pattern(src_patterns[e["index"]], e)
        try:
            current = dev.read_checked("pattern", e["slot"], sleep=sleep)
        except DeviceError:
            current = None
        sleep(pause)
        if current == want.to_bytes():
            done["skipped"].append(f"pattern {e['panel']}")
            log(f"pattern {e['panel']} already holds {e['name']}: skipped")
            continue
        try:
            r = dev.write_pattern(e["slot"], want, commit=True, verify=True)
        except DeviceError as err:
            return fail("pattern", e, err)
        if not r.get("verified"):
            return fail("pattern", e, r)
        done["patterns"].append(e["panel"])
        log(f"pattern {e['panel']} written ({e['name']}, {e['bpm']:g} BPM, kit {e['kit_panel']})")
        sleep(pause)
    return done


# ------------------------------------------------------------------ pattern operations

FINE_UNITS = 24.3            # Fine Tune motion units per semitone (docs/PROTOCOL.md §4, measured)
COARSE_OFFSET = 24           # Coarse Tune is stored as semitones + 24


def coarse_to_fine(p: Pattern, insts) -> dict:
    """
    Turn Coarse Tune lines (CTRL motion holding semitones + 24) into Fine Tune motion, so the
    line plays whatever the instrument's CTRL knob is assigned to. Fine Tune spans about ±5
    semitones: a note outside that is folded by an octave, then clamped. Every block,
    A-H and both fill-ins. Returns how many steps moved and how many were folded or clamped.
    """
    from .pattern import NBLOCKS, STEPS
    moved = folded = clamped = 0
    for blk in range(NBLOCKS):
        for inst in insts:
            for s in range(STEPS):
                m = p.get_motion(blk, inst, s)
                if m["ctrl"] is None or not 0 <= m["ctrl"] <= 48:
                    continue
                semis = m["ctrl"] - COARSE_OFFSET
                if semis > 5:
                    semis -= 12
                    folded += 1
                elif semis < -5:
                    semis += 12
                    folded += 1
                units = int(round(semis * FINE_UNITS))
                if not -128 <= units <= 127:
                    units = max(-128, min(127, units))
                    clamped += 1
                o = p._motion_offset(blk, inst, s)
                p.raw[o + 2] = 0
                p.raw[o + 3] &= ~0x09 & 0xFF
                p.set_motion(blk, inst, s, tune=units)
                moved += 1
    return {"moved": moved, "folded": folded, "clamped": clamped}


def soften(p: Pattern, keep, scale: float = 0.25) -> dict:
    """
    Make the motion the sources carry subtle on every instrument not in `keep` (the melodic
    voices, whose lines would go out of key if scaled): Fine Tune motion is scaled by `scale`
    (a step that rounds to 0 loses its tune motion), and the 0x40 lane (byte +1, presumably
    decay; values are absolute 0-255, so it cannot be scaled safely) is cleared. Other lanes
    are left alone. Every block, A-H and both fill-ins.
    """
    from .pattern import NBLOCKS, STEPS
    scaled = cleared = decay = 0
    for blk in range(NBLOCKS):
        for inst in TRACKS:
            if inst in keep:
                continue
            for st in range(STEPS):
                o = p._motion_offset(blk, inst, st)
                m = p.raw[o + 3]
                if m & 0x80:
                    t = int(round((p.raw[o] - 128) * scale))
                    if t:
                        p.raw[o] = (t + 128) & 0xFF
                        scaled += 1
                    else:
                        p.raw[o] = 0
                        p.raw[o + 3] &= ~0x80 & 0xFF
                        cleared += 1
                if p.raw[o + 3] & 0x40:
                    p.raw[o + 1] = 0
                    p.raw[o + 3] &= ~0x40 & 0xFF
                    decay += 1
    return {"scaled": scaled, "cleared": cleared, "decay_cleared": decay}


def clear_ctrl(p: Pattern, insts) -> int:
    """Remove CTRL motion (keep any Fine Tune motion) on these instruments, every block."""
    from .pattern import NBLOCKS, STEPS
    n = 0
    for blk in range(NBLOCKS):
        for inst in insts:
            for s in range(STEPS):
                o = p._motion_offset(blk, inst, s)
                if p.raw[o + 3] & 0x09:
                    p.raw[o + 2] = 0
                    p.raw[o + 3] &= ~0x09 & 0xFF
                    n += 1
    return n


# ------------------------------------------------------------------ kits from scratch

def tone_index(tones: dict) -> dict:
    """name -> (id, type) from a survey tones.json; the first id wins on a repeated name."""
    out = {}
    for k, t in sorted(tones.items(), key=lambda kv: int(kv[0])):
        if t["name"] and t["name"] not in out:
            out[t["name"]] = (int(k), t["type"])
    return out


def design_kit(base: Kit, donors: dict, design: dict, tones: dict, name: str, colours: dict) -> tuple:
    """
    A new kit: the blank DEFAULT kit's records for modelled (ACB) tones, a working sample
    record (from `donors`, instrument -> Kit) for every SAMPLE tone, then the designed tone,
    tune, decay, pan and sends per instrument. Returns (kit, notes).
    """
    k = base.copy()
    k.name = name
    idx = tone_index(tones)
    notes = []
    for inst in TRACKS:
        spec = design.get(inst)
        if spec:
            if spec["tone"] not in idx:
                raise ReworkError(f"{name}/{inst}: no tone named {spec['tone']!r} on the machine")
            tid, ttype = idx[spec["tone"]]
            if ttype == "SAMPLE":
                donor = donors.get(inst) or donors.get("*")      # same position first
                if donor is None:
                    raise ReworkError(f"{name}/{inst}: {spec['tone']!r} is a sample and there is no donor")
                k.inherit_record(inst, donor[0], donor[1])
                notes.append(f"{inst}: sample record from {donor[0].name.strip()}/{donor[1]}")
            k.set(inst, "tone", tid)
            k.set(inst, "tune", spec.get("tune", 0))
            k.set(inst, "decay", spec.get("decay", 128))
            k.set(inst, "pan", spec.get("pan", 0))
            k.set(inst, "reverb", spec.get("reverb", 0))
            k.set(inst, "delay", spec.get("delay", 0))
            k.set(inst, "lfo", 0)
        k.set_color(inst, colours[inst])
    return k, notes


def sample_donors(survey_dir, tones: dict, avoid=()) -> dict:
    """
    Working sample records from kits already read in the survey directory: instrument ->
    (Kit, instrument), preferring the same instrument position, plus "*" for any.

    A record qualifies when it holds a SAMPLE tone and its sample-parameter bytes are set.
    Only records whose bytes +28..+41 match the most common such block are used: those bytes
    are copied whole into the new instrument, and a few records carry unusual values there
    (+37, which reads 255 on nearly every sample instrument, is 0 or 35 on some), which would
    make every tone given that record dark or quiet. Kits listed in `avoid` (1-based, e.g.
    third-party ones) are never used.
    """
    from collections import Counter
    from .kit import Kit
    found = []
    for f in sorted(Path(survey_dir).glob("kit_*.bin")):
        n = int(f.stem.split("_")[1]) + 1
        if n in avoid:
            continue
        blob = f.read_bytes()
        if check_blob("kit", blob):
            continue
        k = Kit.from_bytes(blob)
        for inst in TRACKS:
            t = tones.get(str(k.get(inst, "tone")))
            if t and t["type"] == "SAMPLE" and k.has_sample_params(inst):
                o = k.record_offset(inst)
                found.append((bytes(k.raw[o + 28:o + 42]), k, inst))
    if not found:
        return {}
    usual = Counter(sig for sig, _, _ in found).most_common(1)[0][0]
    out = {}
    for sig, k, inst in found:
        if sig == usual:
            out.setdefault(inst, (k, inst))
            out.setdefault("*", (k, inst))
    return out


def design_kits(spec: dict, entries: list, survey_dir) -> dict:
    """Every slot's new kit, built offline: index -> (Kit, notes)."""
    from .kit import Kit
    d = Path(survey_dir)
    tones = json.loads((d / "tones.json").read_text())
    base_file = d / f"kit_{spec['base_kit'] - 1:03d}.bin"
    base = Kit.from_bytes(base_file.read_bytes())
    if base.name.strip() != "DEFAULT":
        raise ReworkError(f"base kit {base_file} is {base.name!r}, expected the blank DEFAULT kit")
    donors = sample_donors(d, tones, avoid=set(range(1, 17)))     # never the Golab kits 1-16
    defaults = spec.get("kit_defaults", {})
    out = {}
    for e in entries:
        if not e.get("kit_design"):
            continue
        design = {i: {**defaults, **v} for i, v in e["kit_design"].items()}
        out[e["index"]] = design_kit(base, donors, design, tones, e["name"], e["colours"])
    return out


# ------------------------------------------------------------------ the FM-1 alongside the set
#
# The spec's "fm1" block: {"measure": [{slot, inst}], "roots": {tone: measured}, "projects":
# {"A": {name, key_from, mode, seed, energy, length, parts: {"track": {shape, engine, register,
# rhythm?}}}}}, and per slot "fm1": {"project": letter, "plays": {variation: [tracks]},
# "load": letter?}. A slot names the project the FM-1 holds while it plays; "load" says the
# next one goes in during this slot, with every track muted. Parts come in and out by hand
# (mutes): `plays` is what the cue sheet tells the player, per route row.
#
# The rule the checks follow (docs/PERFORMANCE.md): one bass and one line at a time. A key comes
# only from a measured root (`tr8s roots`); nothing here guesses one.

PROJECT_LETTERS = "ABCD"
REGISTERS = {"low": "E1", "mid": "E2", "high": "E3"}   # the octave a part's root falls in, from here up
SWING_SHUFFLE = 15            # above this a straight 16th on an even step flams against the TR-8S
LINE_SHAPES = ("acid", "lead", "arp")
BASS_SHAPES = ("bass", "acid")


def register_root(pc: str, register: str) -> str:
    """The note of pitch class `pc` in a register: the first one at or above its start (E1, E2, E3)."""
    from .audio import NOTE_NAMES
    from .melody import midi_to_note, note_to_midi
    if register not in REGISTERS:
        raise ReworkError(f"register {register!r}: one of {', '.join(REGISTERS)}")
    if pc not in NOTE_NAMES:
        raise ReworkError(f"pitch class {pc!r}: one of {' '.join(NOTE_NAMES)}")
    start = note_to_midi(REGISTERS[register])
    return midi_to_note(start + (NOTE_NAMES.index(pc) - start) % 12)


def _measured(roots: dict, tone: str):
    r = roots.get(tone)
    return r if r and r.get("clear") and r.get("pc") else None


def _fm1_projects(f: dict) -> dict:
    from .lines import scale_pitches
    from .synth import minitone as mt
    from .tools._core import ToolError
    from .tools.synth import build as synth_build
    roots = f.get("roots") or {}
    out = {}
    for letter, pj in sorted((f.get("projects") or {}).items()):
        if letter not in PROJECT_LETTERS:
            raise ReworkError(f"FM-1 project {letter!r}: the FM-1 has projects A-D")
        tone = pj.get("key_from")
        r = _measured(roots, tone)
        if r is None:
            raise ReworkError(f"FM-1 project {letter}: the root of {tone!r} is not measured (or not "
                              "clearly); run `tr8s roots SPEC`. No key is guessed")
        key = f"{r['pc']} {pj.get('mode', 'minor')}"
        override = {}
        for t, part in (pj.get("parts") or {}).items():
            o = {"shape": part["shape"], "engine": part["engine"],
                 "root": register_root(r["pc"], part.get("register", "low"))}
            if part.get("rhythm") is not None:
                o["rhythm"] = part["rhythm"]
            override[str(t)] = o
        if not override:
            raise ReworkError(f"FM-1 project {letter}: no parts")
        try:
            parts = synth_build(None, key, pj.get("energy"), pj.get("seed", 0), pj.get("length", 16),
                                override)
        except (ValueError, ToolError) as e:
            raise ReworkError(f"FM-1 project {letter}: {e}") from None
        out[letter] = {"letter": letter, "index": PROJECT_LETTERS.index(letter),
                       "name": pj.get("name", letter), "key": key, "key_from": tone, "root": r,
                       "scale": scale_pitches(key)[1], "parts": parts,
                       "tracks": {t: {"shape": p.shape, "engine": p.engine, "channel": mt.CHANNELS[t],
                                      "root": p.root, "steps": p.length, "notes": p.names()}
                                  for t, p in parts.items()},
                       "warnings": [w for p in parts.values() for w in p.warnings]
                                   + _too_low(parts)}
    return out


LOWEST = "E1"                 # about 41 Hz: below this a part only muddies the kick


def _too_low(parts: dict) -> list:
    from .melody import midi_to_note, note_to_midi
    floor = note_to_midi(LOWEST)
    out = []
    for t, p in sorted(parts.items()):
        low = min((n for s in p.steps for n in s.notes), default=None)
        if low is not None and low < floor:
            out.append(f"track {t + 1} ({p.shape}) reaches {midi_to_note(low)}, below {LOWEST}: "
                       "raise its register")
    return out


def _slot_warnings(e: dict, project: dict, plays_sets: list, roots: dict) -> list:
    from .audio import NOTE_NAMES
    from .synth import minitone as mt
    out = []
    parts = project["parts"]
    playing = sorted({t for p in plays_sets for t in p})
    if not playing:
        return out
    yellow = [i for i, role in e["roles"].items() if role == "acid"]
    for t in playing:
        p = parts[t]
        label = f"FM-1 track {t + 1} ({p.shape})"
        if abs(e.get("shuffle", 0)) > SWING_SHUFFLE:       # signed: either way swings
            odd = [i + 1 for i, s in enumerate(p.steps) if s.sounding and not s.tie and i % 2]
            if odd:
                out.append(f"shuffle {e['shuffle']}: {label} has notes on swung 16ths "
                           f"(steps {', '.join(map(str, odd[:6]))}): they flam against the TR-8S")
        for inst in yellow:
            line = inst in (e.get("tune_lines") or [])
            if p.shape in BASS_SHAPES:
                out.append(f"{label} plays under the TR-8S's {inst} (yellow): two basses at once")
            elif line and p.shape in LINE_SHAPES:
                out.append(f"{label} plays over the TR-8S's {inst} line: two lines at once")
    for plays in {tuple(p) for p in plays_sets if p}:
        out += [f"tracks {', '.join(str(t + 1) for t in plays)}: {w}"
                for w in mt.voice_budget({t: parts[t] for t in plays})]
    for inst, d in (e.get("kit_design") or {}).items():
        r = _measured(roots, d.get("tone"))
        if r and NOTE_NAMES.index(r["pc"]) not in project["scale"]:
            out.append(f"{inst} ({d['tone']}) sounds {r['pc']}, outside {project['key']}: pull its "
                       "fader while the FM-1 plays")
    return out


def fm1_plan(spec: dict, entries: list) -> dict | None:
    """
    The FM-1 side of the set, resolved: each project's key (from a measured root) and parts,
    and per slot the route rows with what the FM-1 plays and what goes in and out by hand.
    Pure. None when the spec has no "fm1" block. Refuses an unmeasured root, a project nobody
    loads, a route variation without a `plays`, and a track the project does not have.
    """
    from .cuesheet import _route_rows
    f = spec.get("fm1")
    if not f:
        return None
    projects = _fm1_projects(f)
    roots = f.get("roots") or {}
    given = {s["slot"]: s.get("fm1") for s in spec["slots"]}
    slots, prev, loaded, first = [], [], None, None
    for e in entries:
        where = e["panel"]
        sf = given.get(where) or {}
        project = sf.get("project")
        if project is not None:
            if project not in projects:
                raise ReworkError(f"{where}: FM-1 project {project!r} is not in fm1.projects")
            if loaded is None:
                first = loaded = project
            elif project != loaded:
                raise ReworkError(f"{where}: plays FM-1 project {project}, but {loaded} is loaded and no "
                                  f"slot before loads {project}")
        plays_map = sf.get("plays") or {}
        variations = [r["variation"] for r in e["route"]]
        if project is not None:
            missing = [v for v in variations if v not in plays_map]
            extra = [v for v in plays_map if v not in variations]
            if missing or extra:
                raise ReworkError(f"{where}: fm1.plays needs exactly the route's variations "
                                  f"{''.join(variations)}; missing {missing}, not in the route {extra}")
        elif any(plays_map.values()):
            raise ReworkError(f"{where}: fm1.plays without a project")
        rows = []
        for bar, variation, role, n, _hits in _route_rows(e)[0]:     # the very rows the sheet prints
            plays = sorted(plays_map.get(variation) or [])
            bad = [t for t in plays if not isinstance(t, int) or t not in projects[project]["parts"]]
            if bad:
                raise ReworkError(f"{where} {variation}: FM-1 project {project} has no track {bad}")
            rows.append({"bar": bar, "variation": variation, "role": role, "bars": n,
                         "plays": plays, "in": [t for t in plays if t not in prev],
                         "out": [t for t in prev if t not in plays]})
            prev = plays
        load = sf.get("load")
        if load is not None:
            if load not in projects or load == loaded:
                raise ReworkError(f"{where}: fm1.load {load!r} is not another project of fm1.projects")
            if any(r["plays"] for r in rows):
                raise ReworkError(f"{where}: loads project {load}, so every FM-1 track is muted in it")
            if first is None:
                first = load                  # the set's first project arrives by a load mid-set
        warnings = _slot_warnings(e, projects[project], [r["plays"] for r in rows], roots) \
            if project is not None else []
        slots.append({"panel": where, "project": loaded, "rows": rows, "load": load,
                      "first_of_project": project is not None and (not slots or slots[-1]["project"] != project),
                      "warnings": warnings})
        if load is not None:
            loaded = load
    # loaded before the set only when the first slot already plays it; otherwise a slot loads it
    before = first if slots and slots[0]["project"] == first and first is not None else None
    return {"projects": projects, "slots": slots, "first": first, "before_set": before,
            "roots": roots, "measure": f.get("measure") or []}


ROOTS_AGREE = 15              # cents: two measurements of one tone further apart than this disagree


EACH_KEYS = ("pc", "note", "cents", "hz", "hits", "spread_cents", "clear", "measured")


def merge_roots(measured: list, on: str, previous: dict | None = None) -> dict:
    """
    {tone: root} from [(slot, inst, tone, measure_root result)], merged with the roots stored
    before (`previous`, as this returns them): a stored measurement of a slot and instruction
    not measured again is kept, and a new one replaces it unless the new one is not clear and
    the stored one is (a noisy re-run never loses a good root). A tone measured more than once
    must agree (one pitch class, within ROOTS_AGREE cents) or it is not clear. Pure.
    """
    by_tone: dict = {}
    for tone, root in (previous or {}).items():
        for x in root.get("each") or []:
            r = dict(x)
            r.setdefault("pc", (r.get("note") or "").rstrip("-0123456789") or None)
            by_tone.setdefault(tone, {})[(x["slot"], x["inst"])] = r
    for slot, inst, tone, r in measured:
        old = by_tone.setdefault(tone, {}).get((slot, inst))
        if old and old.get("clear") and not r.get("clear"):
            continue
        by_tone[tone][(slot, inst)] = {**r, "measured": on}
    out = {}
    for tone, each in by_tone.items():
        rows = [(s, i, r) for (s, i), r in each.items()]
        clear = [(s, i, r) for s, i, r in rows if r.get("clear")]
        how = ", ".join(f"{s} {i}" for s, i, _ in rows)
        base = {"measured": max(r.get("measured") or on for _, _, r in rows),
                "how": f"TR-8S USB audio, note-ons on {how}",
                "each": [{"slot": s, "inst": i, **{k: r.get(k) for k in EACH_KEYS}} for s, i, r in rows]}
        if not clear:
            out[tone] = {**base, "clear": False, "why": "no clear measurement"}
            continue
        pcs = {r["pc"] for _, _, r in clear}
        cents = [r["cents"] for _, _, r in clear]
        if len(pcs) > 1 or max(cents) - min(cents) > ROOTS_AGREE:
            out[tone] = {**base, "clear": False, "why": f"the measurements disagree: "
                         + ", ".join(f"{r['note']} {r['cents']:+d}" for _, _, r in clear)}
            continue
        r0 = clear[0][2]
        out[tone] = {**base, "clear": True, "pc": r0["pc"], "note": r0["note"],
                     "cents": round(sum(cents) / len(cents)), "hz": r0["hz"]}
    return out


SILENT_STEPS = 16


def all_tracks(parts: dict) -> dict:
    """`parts` plus a silent part (rests, no engine change) on every other FM-1 track. Pure."""
    from .synth import minitone as mt
    from .synth.line import Part, SynthStep
    out = dict(parts)
    key = next(iter(parts.values())).key if parts else "C minor"
    for t in range(mt.TRACKS):
        if t not in out:
            out[t] = Part("rest", key, "C2", 0, 0.0, [SynthStep() for _ in range(SILENT_STEPS)])
    return out


def fm1_push(fplan: dict, letters, save: bool = False, ask=None, log=print) -> list:
    """
    Write projects' parts to the FM-1: RAM only (an audition: they play now, gone at power-off),
    or with `save` then saved to their slot, which stops the FM-1's transport and overwrites the
    slot (no undo). `ask(question) -> bool` is asked before each save; a no skips that project
    and nothing of it is sent. Never opens the TR-8S.

    All six tracks are written: a track the project does not use gets a silent part (rests, 16
    steps), so nothing left in the FM-1's working memory by an earlier project is saved with
    this one. An audition (RAM only) takes one project: a second would replace the first.
    """
    from .tools._core import fm1
    from .tools.synth import push
    letters = list(letters)
    if not save and len(letters) != 1:
        raise ReworkError(f"an audition writes one project into the FM-1's working memory, got "
                          f"{', '.join(letters) or 'none'}: name one (--project A)")
    done = []
    for letter in letters:
        pj = fplan["projects"].get(letter)
        if pj is None:
            raise ReworkError(f"FM-1 project {letter!r} is not in the plan")
        if save:
            used = fm1().project_used(pj["index"])
            q = (f"FM-1 project {letter} {'holds a saved project' if used else 'is empty'}. Overwrite "
                 f"it with {pj['name']} ({pj['key']}, {len(pj['parts'])} parts)? This stops the "
                 "FM-1's transport and cannot be undone.")
            if ask is None or not ask(q):
                log(f"project {letter}: not saved, nothing sent")
                done.append({"project": letter, "sent": None, "saved": None, "skipped": True})
                continue
        r = push(all_tracks(pj["parts"]), project=pj["index"] if save else None)
        log(f"project {letter}: {r['sent']['frames']} frames sent"
            + (f", saved to slot {letter}" if r.get("saved") else " (RAM only: an audition)"))
        done.append({"project": letter, **r})
    return done
