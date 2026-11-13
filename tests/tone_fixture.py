"""
A small synthetic tone catalogue, for when `tests/fixtures/tones.json` is absent.

The real catalogue is measured from a TR-8S (`tr8s analyse-tones`), so it cannot
be reproduced on another machine, and `.gitignore` keeps `tones.json` out of the
repo. `conftest.py` writes this one into the throwaway data directory instead,
so `kit.auto_build` and `track.create` (with a `kit_slot`) choose from the same
set on every machine. One plausible candidate per track, plus decoys: enough
for every style and mode to build, not a stand-in for what the machine has.
"""

import json
from pathlib import Path

# id, name, category, centroid, decay_ms, root, type (1 ACB, 2 sample), sustained
_ROWS = [
    (1, "Kick C", "BD", 90, 300, "C1", 1, False),
    (2, "Kick F#", "BD", 95, 300, "F#1", 1, False),
    (3, "Kick G", "BD", 92, 310, "G1", 1, False),
    (10, "Snare", "SD", 1200, 90, None, 1, False),
    (11, "Clap", "HC", 1500, 70, None, 1, False),
    (12, "Rim", "RS", 1200, 40, None, 1, False),
    (13, "CH", "CH/OH", 1800, 60, None, 1, False),
    (14, "OH", "CH/OH", 1700, 350, None, 1, False),
    (15, "Crash", "CC/RC", 1900, 700, None, 1, False),
    (16, "Ride", "CC/RC", 1800, 500, None, 1, False),
    (17, "Tom lo", "TOM", 200, 300, None, 1, False),
    (18, "Tom mid", "TOM", 400, 250, None, 1, False),
    (19, "Tom hi", "TOM", 600, 200, None, 1, False),
    (20, "Bass", "BASS", 200, None, "C2", 2, True),
    (21, "Lead", "SYNTH1", 1000, None, "C4", 2, True),
]


def catalogue() -> dict:
    """The rows as a `tones.json` document, keyed by tone id."""
    return {str(i): {"id": i, "name": name, "cat": cat, "type": typ, "root": root,
                     "hz": None, "cents": 0, "peak": 0.5, "rms": 0.2,
                     "decay_ms": decay, "sustained": sustained, "centroid": centroid}
            for i, name, cat, centroid, decay, root, typ, sustained in _ROWS}


def write(path: Path) -> None:
    Path(path).write_text(json.dumps(catalogue(), indent=1) + "\n")
