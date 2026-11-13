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
