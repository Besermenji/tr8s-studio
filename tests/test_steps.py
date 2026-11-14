"""Per-step velocity, sub steps (ratchets) and flams, step and sub step probability.

The byte values here are the ones read off the panel on 2026-11-14 (docs/PROTOCOL.md §2,
"Step bytes"): 8-01, variation A, HC.
"""

from datetime import date

import pytest

from fake import load_fixture_pattern
from tr8s import rework
from tr8s.cuesheet import render_rework
from tr8s.pattern import Pattern, PatternError
from tr8s.survey import analyse_pattern, step_extras


def _blank() -> Pattern:
    p = Pattern.from_bytes(load_fixture_pattern())
    for blk in range(10):                     # A-H and both fill-ins
        p.clear_variation(blk)
    return p


def _off(p, var, inst, step):
    return p._inst_offset(p._blk(var), inst, step)


# What the panel stored, step by step (velocity, +1, +2).
PANEL = [
    # (bytes, velocity, sub, prob, sub_prob)
    ((77, 2, 0x42), 77, "1/2", 80, 60),
    ((73, 2, 0x57), 73, "1/2", 30, 50),
    ((80, 3, 0x0A), 80, "1/3", 0, 100),
    ((80, 1, 0x00), 80, "flam", 100, 100),
    ((127, 0, 0x00), 127, None, 100, 100),
]


@pytest.mark.parametrize("raw,vel,sub,prob,sub_prob", PANEL)
def test_get_step_reads_what_the_panel_stored(raw, vel, sub, prob, sub_prob):
    p = _blank()
    o = _off(p, "A", "HC", 0)
    p.raw[o:o + 3] = bytes(raw)
    st = p.get_step("A", "HC", 0)
    assert (st["velocity"], st["sub"], st["prob"], st["sub_prob"]) == (vel, sub, prob, sub_prob)


@pytest.mark.parametrize("raw,vel,sub,prob,sub_prob", PANEL)
def test_set_step_writes_what_the_panel_stores(raw, vel, sub, prob, sub_prob):
    p = _blank()
    p.set_step("A", "HC", 0, velocity=vel, sub=sub or "none", prob=prob, sub_prob=sub_prob)
    o = _off(p, "A", "HC", 0)
    assert tuple(p.raw[o:o + 4]) == raw + (0,)


def test_set_step_changes_only_that_steps_bytes_and_no_motion():
    p = _blank()
    p.set_steps("C", "RS", "o..............o")
    before = p.to_bytes()
    p.set_step("C", "RS", 15, sub="1/3", sub_prob=60, prob=70, velocity=90)
    o = _off(p, "C", "RS", 15)
    diff = [i for i, (a, b) in enumerate(zip(before, p.to_bytes())) if a != b]
    assert diff and all(o <= i < o + 3 for i in diff)
    assert p.get_motion("C", "RS", 15)["mask"] == 0


def test_set_step_keeps_the_unknown_mark_bit_and_the_other_nibble():
    p = _blank()
    o = _off(p, "B", "RC", 4)
    p.raw[o:o + 3] = bytes((80, 0x80, 0x40))         # factory mark, SUB PROB 60 %
    p.set_step("B", "RC", 4, sub="1/4", prob=50)
    assert p.raw[o + 1] == 0x84
    assert p.get_step("B", "RC", 4)["sub_prob"] == 60 and p.get_step("B", "RC", 4)["prob"] == 50
    assert p.get_step("B", "RC", 4)["mark"]


def test_set_step_refusals():
    p = _blank()
    with pytest.raises(PatternError):
        p.set_step("A", "CH", 0, prob=50)                # no hit there
    p.set_steps("A", "CH", "o")
    for kw in ({"velocity": 128}, {"prob": 55}, {"sub_prob": 110}, {"sub": "1/5"}):
        with pytest.raises(PatternError):
            p.set_step("A", "CH", 0, **kw)


def test_removing_a_hit_clears_its_sub_step_and_probability():
    p = _blank()
    p.set_steps("A", "CH", "o")
    p.set_step("A", "CH", 0, sub="1/3", prob=40)
    p.set_step("A", "CH", 0, velocity=0)
    o = _off(p, "A", "CH", 0)
    assert tuple(p.raw[o:o + 4]) == (0, 0, 0, 0)        # what the panel leaves (step 13, ---)
    p.set_steps("A", "CH", "o")
    p.set_step("A", "CH", 0, sub="flam")
    p.set_steps("A", "CH", "")
    assert tuple(p.raw[o:o + 4]) == (0, 0, 0, 0)


def test_step_extras_lists_what_a_pattern_already_carries():
    p = _blank()
    for v in "ABC":
        p.set_steps(v, "CH", "o.o.o.o.")
        p.set_step(v, "CH", 2, sub="1/2")
    p.set_steps("D", "RS", "o")
    p.set_step("D", "RS", 0, sub="flam", prob=70)
    o = _off(p, "E", "SD", 3)
    p.raw[o + 1] = 3                                     # a sub step on no hit is not counted
    got = {(x["inst"], x["kind"]): (x["where"], x["steps"]) for x in step_extras(p)}
    assert got == {("CH", "1/2"): (["A", "B", "C"], 3), ("RS", "flam"): (["D"], 1),
                   ("RS", "prob"): (["D"], 1)}
    assert analyse_pattern(p.to_bytes())["step_extras"] == step_extras(p)


# ---------------------------------------------------------------- the spec


def _survey(tmp_path, steps):
    p = _blank()
    for v in "ABCDEFGH":
        p.set_steps(v, "BD", "x...x...x...x...")
        p.set_steps(v, "SD", "..o.......o.....")
        p.set_steps(v, "RS", "x..x..x..x..x..x")
    p.kit = 64
    (tmp_path / "pattern_064.bin").write_bytes(p.to_bytes())
    spec = {"name": "t", "slots": [{"slot": "8-16", "source": "5-01", "bpm": 138,
                                    "label": "OUTRO", "steps": steps}]}
    return spec


def test_spec_steps_are_applied_to_hits_and_skip_empty_steps(tmp_path):
    spec = _survey(tmp_path, [
        {"vars": "CG", "inst": "RS", "step": 16, "sub": "1/3", "sub_prob": 60},
        {"vars": "A", "inst": "SD", "steps": [3, 4], "velocity": 55},
    ])
    (e,) = rework.plan(spec, tmp_path)
    src = (tmp_path / "pattern_064.bin").read_bytes()
    p = rework.build_pattern(src, e)
    assert p.get_step("C", "RS", 15)["sub"] == "1/3" and p.get_step("G", "RS", 15)["sub_prob"] == 60
    assert p.get_step("D", "RS", 15)["sub"] is None
    assert p.get_step("A", "SD", 2)["velocity"] == 55
    assert p.get_step("A", "SD", 3)["velocity"] == 0                  # no hit: not added
    assert e["steps_applied"] == {"changed": ["C RS 16", "G RS 16", "A SD 3"], "skipped": ["A SD 4"]}


def test_spec_add_puts_a_hit_at_the_panels_normal_level(tmp_path):
    spec = _survey(tmp_path, [{"vars": "B", "inst": "CH", "step": 8, "add": True, "prob": 50}])
    (e,) = rework.plan(spec, tmp_path)
    p = rework.build_pattern((tmp_path / "pattern_064.bin").read_bytes(), e)
    st = p.get_step("B", "CH", 7)
    assert (st["velocity"], st["prob"], st["sub"]) == (80, 50, None)


@pytest.mark.parametrize("entry", [
    {"vars": "A", "inst": "BD", "step": 1, "prob": 50},                # the kick on the 1
    {"vars": "A", "inst": "RS", "step": 17, "prob": 50},
    {"vars": "Z", "inst": "RS", "step": 1, "prob": 50},
    {"vars": "A", "inst": "XX", "step": 1, "prob": 50},
    {"vars": "A", "inst": "RS", "step": 1},                            # nothing to set
    {"vars": "A", "inst": "RS", "step": 1, "steps": [2], "prob": 50},
    {"vars": "A", "inst": "RS", "step": 1, "prob": 55},
    {"vars": "A", "inst": "RS", "step": 1, "velocity": 0},
    {"vars": "A", "inst": "RS", "step": 1, "sub": "1/8"},
    {"vars": "A", "inst": "RS", "step": 1, "pitch": 3},
])
def test_spec_refuses_bad_step_entries(tmp_path, entry):
    spec = _survey(tmp_path, [entry])
    path = tmp_path / "spec.json"
    import json
    path.write_text(json.dumps(spec))
    with pytest.raises(rework.ReworkError):
        rework.load_spec(path)


def test_the_cue_sheet_and_the_write_list_name_the_steps(tmp_path):
    spec = _survey(tmp_path, [{"vars": "CG", "inst": "RS", "step": 16, "sub": "1/3", "sub_prob": 60}])
    src = _blank()
    src.raw[:] = (tmp_path / "pattern_064.bin").read_bytes()
    src.set_step("A", "RS", 0, sub="flam")
    (tmp_path / "pattern_064.bin").write_bytes(src.to_bytes())
    entries = rework.plan(spec, tmp_path)
    md = render_rework(spec, entries, date(2026, 11, 14))
    assert "### Steps" in md
    assert "- Already there: RS flams in A (1 step)" in md
    assert "- Added: RS step 16 in CG: ratchet 1/3, SUB PROB 60 %" in md
    assert any("ratchet 1/3, SUB PROB 60 %" in w for w in rework.writes(entries))
