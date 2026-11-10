"""Regenerate tests/fixtures/lines_golden.json from the current lines.py.

Only run this on purpose: the fixture exists to prove the TR-8S line output did
not change. Usage: PYTHONPATH=src python3 tests/golden_lines_gen.py

History: regenerated once, deliberately, when `deg.get(x) or ...` chains in
lines.py were fixed (C is pitch class 0, which is falsy, so a scale whose third
or fifth is C had it read as missing). Only `arp` and `stab` in A dorian changed;
bass, acid and every other key were byte-identical.
"""
import json
from pathlib import Path

from tr8s.lines import generate

KEYS = ["C minor", "F# phrygian", "A dorian", "D# minor", "G major"]
SHAPES = ("bass", "acid", "stab", "arp")
ENERGIES = (0.05, 0.4, 0.6, 0.8, 1.0)


def cases():
    for shape in SHAPES:
        for key in KEYS:
            for seed in range(12):
                for e in ENERGIES:
                    yield shape, dict(key=key, energy=e, seed=seed, root="C2")
    for direction in ("up", "down", "updown"):
        for key in KEYS:
            for seed in range(12):
                for e in ENERGIES:
                    yield "arp", dict(key=key, energy=e, seed=seed, root="C2",
                                      direction=direction)


def build():
    return [{"shape": s, "kw": kw, "out": generate(s, **kw)} for s, kw in cases()]


if __name__ == "__main__":
    out = Path(__file__).parent / "fixtures" / "lines_golden.json"
    out.write_text(json.dumps(build(), indent=0, sort_keys=True) + "\n")
    print(f"wrote {out}")
