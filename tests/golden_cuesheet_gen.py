"""Regenerate tests/fixtures/cuesheet_three.md from the current planner and cuesheet.py.

Only run this on purpose: the fixture exists to prove the printed cue sheet did not change
by accident. Read the diff before committing it. Usage: PYTHONPATH=src python3 tests/golden_cuesheet_gen.py
"""
import json
from datetime import date
from pathlib import Path

from tr8s.tools import setlist

FIXTURES = Path(__file__).resolve().parent / "fixtures"
BUILT_ON = date(2026, 11, 13)


def sheet() -> str:
    doc = json.loads((FIXTURES / "set_three.json").read_text())
    return setlist.plan(doc["name"], doc["tracks"], doc["start_slot"], built_on=BUILT_ON)["cue_sheet"]


if __name__ == "__main__":
    (FIXTURES / "cuesheet_three.md").write_text(sheet())
    print(f"wrote {FIXTURES / 'cuesheet_three.md'}")
