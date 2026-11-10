"""The TR-8S line generators must produce exactly what they produced before the
shared core was extracted for the FM-1 (see tests/golden_lines_gen.py)."""

import json
from pathlib import Path

from tr8s.lines import generate

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "lines_golden.json").read_text())


def test_golden_fixture_is_not_empty():
    assert len(GOLDEN) > 1000


def test_lines_output_is_byte_identical_to_the_fixture():
    bad = [c for c in GOLDEN if generate(c["shape"], **c["kw"]) != c["out"]]
    assert not bad, f"{len(bad)} of {len(GOLDEN)} changed, first: {bad[0]['shape']} {bad[0]['kw']}"
