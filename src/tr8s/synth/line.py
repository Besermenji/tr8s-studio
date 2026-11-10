"""
Synth lines for the FM-1.

`lines.py` makes bass, acid, stab and arp lines for the TR-8S and then clamps
them to Coarse Tune's reach. The FM-1 has no such limit, so this module reuses
the same cores (same notes for the same seed) and skips the clamp. It adds what
a 303 needs and the TR-8S path could not carry (slides), and the shapes that
make a song not-all-acid: pad, drone and lead.

Everything is seeded. A rest is a step with no notes.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from .. import lines as _lines
from ..melody import midi_to_note, note_to_midi
from ..style import euclid

STEPS_PER_BAR = 16
LENGTHS = (16, 32, 64)
SYNTH_SHAPES = ("bass", "acid", "stab", "arp", "pad", "drone", "lead")

# Mini-Tone's force names (GHOST SOFT NORM HARD ACC) and the velocity each is
# written with.
FORCE_VEL = {"GHOST": 40, "SOFT": 70, "NORM": 100, "HARD": 115, "ACC": 127}

DEFAULT_ROOT = {"bass": "C2", "acid": "C2", "stab": "C3", "arp": "C3",
                "pad": "C3", "drone": "C2", "lead": "C3"}
DEFAULT_ENERGY = {"bass": 0.6, "acid": 0.7, "stab": 0.4, "arp": 0.6,
                  "pad": 0.4, "drone": 0.3, "lead": 0.6}


@dataclass(frozen=True)
class SynthStep:
    notes: tuple = ()           # MIDI numbers; a chord has several, a rest none
    force: str = "NORM"
    slide: bool = False         # glide into the next step's note
    tie: bool = False           # hold: the previous step's notes carry on
    ratchet: int = 1

    @property
    def sounding(self) -> bool:
        return bool(self.notes)


@dataclass
class Part:
    shape: str
    key: str
    root: str
    seed: int
    energy: float
    steps: list = field(default_factory=list)
    engine: str | None = None
    warnings: list = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.steps)

    def names(self) -> str:
        out = []
        for s in self.steps:
            if not s.notes:
                out.append(".")
            else:
                out.append("+".join(midi_to_note(m) for m in s.notes) + ("~" if s.tie else ""))
        return " ".join(out)


REST = SynthStep()


def _stream(seed: int, name: str) -> random.Random:
    """
    A generator for a named part of a seed. Not `Random(seed + n)`: neighbouring
    seeds would share streams, so bar 2 of seed 5 would be bar 1 of seed 6, and a
    re-roll with seed + 1 would hand back material already heard. Bar 1 itself
    stays `Random(seed)` so it matches the TR-8S line for the same seed.
    """
    return random.Random(f"{seed}/{name}")


def _midi_row(names: list) -> list:
    return [None if n == _lines.REST else note_to_midi(n) for n in names]


# ------------------------------------------------------------ core shapes

def _core_bar(shape, rng, tonic, scale, energy, root, kw):
    """One bar of a TR-8S-derived shape, as (midi-or-None list, accent indices)."""
    if shape == "bass":
        notes, ex = _lines._core_bass(rng, tonic, scale, energy, root, kw.get("octave_offset", 0))
    elif shape == "acid":
        notes, ex = _lines._core_acid(rng, tonic, scale, energy, root)
    elif shape == "stab":
        notes, ex = _lines._core_stab(rng, tonic, scale, energy, root)
    else:
        notes, ex = _lines._core_arp(rng, tonic, scale, energy, root, kw.get("direction", "up"))
    return _midi_row(notes), set(ex.get("accents", ()))


def _force_for(shape, i, accents):
    if shape == "acid":
        return "ACC" if i in accents else "NORM"
    if shape == "arp":
        return "HARD" if i % 4 == 0 else "NORM"
    return "NORM"


def _bar_steps(shape, rng, tonic, scale, energy, root, kw):
    row, accents = _core_bar(shape, rng, tonic, scale, energy, root, kw)
    return [SynthStep((m,), _force_for(shape, i, accents)) if m is not None else REST
            for i, m in enumerate(row)]


def _apply_rhythm(shape, bar, rhythm):
    """
    Re-place a bar's notes, in order, on a Euclidean rhythm. A force that belongs
    to the note (an acid accent) moves with it; one that belongs to the beat (the
    arp's downbeats) is worked out again for the new position.
    """
    pulses, rotate = rhythm
    hits = euclid(pulses, STEPS_PER_BAR, rotate)
    seq = [s for s in bar if s.sounding]
    out = [REST] * STEPS_PER_BAR
    for k, i in enumerate(hits):
        s = seq[k % len(seq)]
        if shape == "arp":
            s = SynthStep(s.notes, _force_for(shape, i, ()), s.slide, s.tie, s.ratchet)
        out[i] = s
    return out


def _core_shape(shape, seed, tonic, scale, energy, root, length, rhythm, kw):
    bars = length // STEPS_PER_BAR
    first = _bar_steps(shape, random.Random(seed), tonic, scale, energy, root, kw)
    out = []
    for b in range(bars):
        if b == 0:
            bar = first
        else:
            bar = _bar_steps(shape, _stream(seed, f"bar{b}"), tonic, scale, energy, root, kw)
            if b == bars - 1:                   # the turnaround: bar 1's first half, a new second half
                bar = first[:8] + bar[8:]
        if rhythm:
            bar = _apply_rhythm(shape, bar, rhythm)
        out.extend(bar)
    if shape == "acid":
        out = _add_slides(out, seed, energy)
    return out


def _add_slides(steps, seed, energy):
    """Slides only between two sounding steps, never into a rest."""
    rng = _stream(seed, "slides")               # its own stream: the core's draws stay untouched
    p = 0.15 + 0.35 * energy
    n = len(steps)
    out = list(steps)
    for i in range(n):
        nxt = steps[(i + 1) % n]
        if steps[i].sounding and nxt.sounding and not nxt.tie and rng.random() < p:
            s = steps[i]
            out[i] = SynthStep(s.notes, s.force, True, s.tie, s.ratchet)
    return out


# --------------------------------------------------------- pad/drone/lead

def _pcs_above(pc, scale, candidates):
    for d in candidates:
        if (pc + d) % 12 in scale:
            return (pc + d) % 12
    return None


def _chord_pcs(root_pc, scale):
    """A triad built from the scale; sus when the third is missing."""
    third = _lines._first(_pcs_above(root_pc, scale, (3, 4)),
                          _pcs_above(root_pc, scale, (5, 2)), default=None)
    fifth = _pcs_above(root_pc, scale, (7, 6, 8))
    return [p for p in (root_pc, third, fifth) if p is not None]


def _voice(pcs, home):
    """Stack pitch classes upward from `home`, root first."""
    notes = [home]
    for pc in pcs[1:]:
        m = _lines._nearest(pc, notes[-1] + 5)
        while m <= notes[-1]:
            m += 12
        notes.append(m)
    return tuple(notes)


def _progression_roots(tonic, scale):
    deg = _lines._degrees(scale, tonic)
    pool = [tonic]
    for name in ("fourth", "sixth", "fifth", "third"):
        if name in deg:
            pool.append(deg[name])
    return pool


def _pad(rng, tonic, scale, energy, root, length):
    base = note_to_midi(root)
    pool = _progression_roots(tonic, scale)
    every = 8 if energy > 0.5 else 16
    out = []
    for n, start in enumerate(range(0, length, every)):
        pc = tonic if n == 0 else rng.choice(pool)
        chord = _voice(_chord_pcs(pc, scale), _lines._nearest(pc, base))
        for k in range(every):
            out.append(SynthStep(chord, "SOFT", False, k > 0))
    return out[:length]


def _drone(rng, tonic, scale, energy, root, length):
    base = note_to_midi(root)
    deg = _lines._degrees(scale, tonic)
    pool = [tonic] + [deg[n] for n in ("fourth", "fifth") if n in deg]
    out = []
    for n, start in enumerate(range(0, length, 32)):
        pc = tonic if n == 0 else rng.choice(pool)
        low = _lines._nearest(pc, base)
        top = low + 7 if (pc + 7) % 12 in scale else low + 12
        notes = (low, top)
        for k in range(min(32, length - start)):
            out.append(SynthStep(notes, "SOFT", False, k > 0))
    return out


def _lead(rng, tonic, scale, energy, root, length):
    base = note_to_midi(root) + 12
    home = _lines._nearest(tonic, base)
    ladder = sorted(m for m in range(home - 5, home + 20) if m % 12 in scale)
    pos = ladder.index(home)
    slots = (list(_lines.EIGHTHS) if energy > 0.45 else list(_lines.DOWNBEATS))
    p_play = 0.55 + 0.4 * energy
    out = []
    for _ in range(length // STEPS_PER_BAR):
        rest_at = rng.choice((4, 12))
        bar = [REST] * STEPS_PER_BAR
        for i in slots:
            if rest_at <= i < rest_at + 4 or rng.random() > p_play:
                continue
            leap = rng.random() < 0.15
            pos += rng.choice((-2, 2) if leap else (-1, 1, -1, 1))
            pos = max(0, min(len(ladder) - 1, pos))
            bar[i] = SynthStep((ladder[pos],), "HARD" if i % 4 == 0 else "NORM")
        if not any(s.sounding for s in bar):
            bar[0] = SynthStep((home,), "HARD")
        out.extend(bar)
    return out


_LONG = {"pad": _pad, "drone": _drone, "lead": _lead}


def generate(shape: str = "bass", key: str = "C minor", energy: float | None = None,
             seed: int | None = None, root: str | None = None, length: int = 16,
             rhythm: tuple | None = None, engine: str | None = None, **kw) -> Part:
    """
    One FM-1 part. `length` is 16, 32 or 64 steps; `rhythm=(pulses, rotate)`
    re-places a note shape's hits on a Euclidean pattern. `kw` passes
    `octave_offset` (bass) and `direction` (arp) through.
    """
    if shape not in SYNTH_SHAPES:
        raise ValueError(f"unknown shape {shape!r}; have {', '.join(SYNTH_SHAPES)}")
    if length not in LENGTHS:
        raise ValueError(f"length must be one of {LENGTHS}, not {length}")
    if rhythm is not None:
        if shape in _LONG:
            raise ValueError(f"rhythm applies to bass, acid, stab and arp, not {shape}")
        if not 1 <= rhythm[0] <= STEPS_PER_BAR:
            raise ValueError("rhythm pulses must be 1..16")
    energy = DEFAULT_ENERGY[shape] if energy is None else energy
    root = root or DEFAULT_ROOT[shape]
    rng, tonic, scale, seed = _lines._setup(seed, key)   # draws no numbers; only resolves seed and key

    if shape in _LONG:
        steps = _LONG[shape](random.Random(seed), tonic, scale, energy, root, length)
    else:
        steps = _core_shape(shape, seed, tonic, scale, energy, root, length, rhythm, kw)
    return Part(shape, key, root, seed, energy, steps, engine)
