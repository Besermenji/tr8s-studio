"""The rework: copy an onboard pattern and kit into the set's slots, rename, retempo, recolour."""

import json

import pytest

from fake import load_fixture_kit, load_fixture_pattern, make_device
from tr8s import rework
from tr8s.kit import TRACKS, Kit
from tr8s.pattern import Pattern


def _source(tempo=125.0, kit_panel=65) -> bytes:
    p = Pattern.from_bytes(load_fixture_pattern())
    for v in "ABCDEFGH":
        p.clear_variation(v)
        p.set_steps(v, "BD", "x...x...x...x...")
        p.set_steps(v, "RS", "x..x..x..x..x..x")
    p.clear_variation("E")
    p.set_steps("E", "CH", "..x...x...x...x.")            # a kickless break
    p.set_steps("G", "SD", "xxxxxxxxxxxxxxxx")             # the densest
    p.tempo = tempo
    p.kit = kit_panel - 1
    return p.to_bytes()


@pytest.fixture
def survey(tmp_path):
    src = _source()
    (tmp_path / "pattern_064.bin").write_bytes(src)                     # 5-01
    (tmp_path / "pattern_127.bin").write_bytes(load_fixture_pattern())  # 8-16, as it is
    spec = {"name": "t", "slots": [{"slot": "8-16", "source": "5-01", "bpm": 138, "label": "OUTRO",
                                    "role": "closer"}]}
    return tmp_path, spec, src


def test_plan_resolves_slot_kit_roles_and_route(survey):
    d, spec, src = survey
    (e,) = rework.plan(spec, d)
    assert e["panel"] == "8-16" and e["kit_panel"] == 128 and e["source_kit_panel"] == 65
    assert e["name"] == "01 OUTRO" and e["bpm"] == 138
    assert e["roles"]["RS"] == "odd1" and e["colours"]["RS"] == rework.ROLE_COLOURS["odd1"]
    assert e["colours"]["BD"] == 0 and e["colours"]["CH"] == 6
    used = [r["variation"] for r in e["route"]]
    assert sorted(used) == list("ABCDEFGH")                   # every variation once
    assert {"variation": "E", "role": "break (no kick)"}.items() <= next(
        r for r in e["route"] if r["variation"] == "E").items()
    assert e["route"][-2]["variation"] == "G" or e["route"][-1]["variation"] == "G"


def test_route_without_a_kickless_variation_never_repeats_one():
    p = Pattern.from_bytes(_source())
    p.set_steps("E", "BD", "x...............")
    from tr8s.survey import analyse_pattern
    r = rework.route(analyse_pattern(p.to_bytes()))
    vs = [x["variation"] for x in r]
    assert sorted(vs) == list("ABCDEFGH")
    assert any(x["role"] == "break (mute the kick)" for x in r)


def test_build_copies_whole_blobs_kits_first_and_repairs_the_reference(survey):
    d, spec, src = survey
    entries = rework.plan(spec, d)
    kit = bytearray(load_fixture_kit())
    d_, t = make_device(patterns={64: src, 127: load_fixture_pattern()}, kits={64: bytes(kit)})
    done = rework.build(d_, entries, d, sleep=lambda s: None, log=lambda *a: None)
    assert done["failed"] is None
    assert done["kits"] == [128] and done["patterns"] == ["8-16"]
    # order on the wire: the owned pattern, then the kit, then the set's pattern
    kinds = [k for k, _ in t.sent]
    assert kinds.index("kit") < len(kinds) - 1 - kinds[::-1].index("pattern")
    out = Pattern.from_bytes(t.slots["pattern"][127])
    assert out.name == "01 OUTRO" and out.tempo == 138 and out.kit == 127
    body = lambda b: b[144:]                                   # everything after the header
    assert body(t.slots["pattern"][127]) == body(src)          # undecoded bytes come along
    k = Kit.from_bytes(t.slots["kit"][127])
    assert k.name == "01 OUTRO" and k.color("RS") == rework.ROLE_COLOURS["odd1"]
    assert (d / "kit_064.bin").exists()


def test_build_refuses_a_source_that_changed_since_the_survey(survey):
    d, spec, src = survey
    entries = rework.plan(spec, d)
    changed = bytearray(src)
    changed[500] ^= 1
    d_, t = make_device(patterns={64: bytes(changed), 127: load_fixture_pattern()},
                        kits={64: load_fixture_kit()})
    done = rework.build(d_, entries, d, sleep=lambda s: None, log=lambda *a: None)
    assert "changed since the survey" in done["failed"]["error"]
    assert t.sent == []


def test_the_shipped_spec_is_valid():
    import pathlib
    spec = rework.load_spec(pathlib.Path(__file__).parents[1] / "library/sets/poly-acid.json")
    assert len(spec["slots"]) == 16
    assert [s["slot"] for s in spec["slots"]] == [f"8-{i:02d}" for i in range(1, 17)]


def test_the_cue_sheet_has_setup_legend_order_and_a_page_per_pattern(survey):
    from datetime import date
    from tr8s.cuesheet import render_rework
    d, spec, src = survey
    entries = rework.plan(spec, d)
    md = render_rework(spec, entries, date(2026, 11, 13))
    for part in ("## One-time setup on the panel", "KitSel = PTN", "## Fader colours",
                 "| yellow | acid voice |", "## Running order", "## 1. 01 OUTRO · pad 8-16 · kit 128",
                 "### Route through A-H", "Last pattern."):
        assert part in md, part
    assert "Odd-cycle voices (green faders): RS" in md


def test_a_big_tempo_change_is_hidden_in_a_bridge():
    from tr8s.cuesheet import _transition
    a = {"bpm": 140, "panel": "8-08", "name": "08 INTERLUDE"}
    b = {"bpm": 172, "panel": "8-09", "name": "09 DNB ENTRY"}
    assert "hidden in a bridge" in " ".join(_transition(a, b))
    assert "Keep the kick running" in " ".join(_transition(a, dict(b, bpm=142)))


def test_coarse_lines_become_fine_tune_folded_into_the_window():
    p = Pattern.from_bytes(load_fixture_pattern())
    for blk in range(10):
        p.clear_motion(blk, "MT")
    p.set_motion("A", "MT", 0, ctrl=24 + 5)      # +5: fits
    p.set_motion("A", "MT", 1, ctrl=24 + 8)      # +8: folded to -4
    p.set_motion("A", "MT", 2, ctrl=24 - 3)      # -3
    r = rework.coarse_to_fine(p, ["MT"])
    assert r == {"moved": 3, "folded": 1, "clamped": 0}
    got = [p.get_motion("A", "MT", s) for s in range(3)]
    assert [g["tune"] for g in got] == [122, -97, -73]
    assert all(g["ctrl"] is None for g in got)


def test_clear_ctrl_keeps_fine_tune():
    p = Pattern.from_bytes(load_fixture_pattern())
    p.set_motion("B", "LT", 4, tune=50, ctrl=200)
    assert rework.clear_ctrl(p, ["LT"]) >= 1
    m = p.get_motion("B", "LT", 4)
    assert m["tune"] == 50 and m["ctrl"] is None


def test_a_kit_is_designed_from_the_blank_kit_and_a_sample_donor():
    from tr8s.kit import SAMPLE_PARAM_OFFSETS
    base = Kit.from_bytes(load_fixture_kit())
    donor = Kit.from_bytes(load_fixture_kit())
    o = donor.record_offset("LT")
    for off in SAMPLE_PARAM_OFFSETS:
        donor.raw[o + off] = 99
    tones = {"0": {"name": "909 Bass1", "type": "ACB"}, "300": {"name": "Deep SH Bass", "type": "SAMPLE"}}
    colours = {i: 0 for i in TRACKS}
    colours["MT"] = 2
    k, notes = rework.design_kit(base, {"*": (donor, "LT")},
                                 {"BD": {"tone": "909 Bass1", "decay": 110},
                                  "MT": {"tone": "Deep SH Bass", "decay": 100, "reverb": 30}},
                                 tones, "01 TEST", colours)
    assert k.name == "01 TEST"
    assert k.get("BD", "tone") == 0 and k.get("BD", "decay") == 110
    assert k.get("MT", "tone") == 300 and k.has_sample_params("MT") and k.get("MT", "reverb") == 30
    assert k.color("MT") == 2 and any("sample record" in n for n in notes)
    # LFO depth 0 is "no LFO", stored as 0x80 like the blank kit; raw 0 would be depth -128
    assert all(k.raw[k.record_offset(i) + 11] == 0x80 for i in ("BD", "MT"))
    with pytest.raises(rework.ReworkError, match="no tone named"):
        rework.design_kit(base, {}, {"BD": {"tone": "Nope"}}, tones, "x", colours)


def test_a_second_build_skips_what_is_already_in_place(survey):
    d, spec, src = survey
    entries = rework.plan(spec, d)
    d_, t = make_device(patterns={64: src, 127: load_fixture_pattern()}, kits={64: load_fixture_kit()})
    first = rework.build(d_, entries, d, sleep=lambda s: None, log=lambda *a: None)
    assert first["failed"] is None and first["patterns"] == ["8-16"]
    t.sent.clear()
    again = rework.build(d_, rework.plan(spec, d), d, sleep=lambda s: None, log=lambda *a: None)
    assert again["failed"] is None and again["kits"] == [] and again["patterns"] == []
    assert again["skipped"] == ["kit 128", "pattern 8-16"]
    assert t.sent == [("pattern", 127)]            # only the owned-pattern step writes


def test_soften_scales_drum_pitch_clears_decay_and_keeps_lines():
    p = Pattern.from_bytes(load_fixture_pattern())
    for blk in range(10):
        for inst in ("SD", "MT"):
            p.clear_motion(blk, inst)
    p.set_motion("A", "SD", 0, tune=100)
    p.set_motion("A", "SD", 1, tune=1)                 # rounds to 0: removed
    o = p._motion_offset(0, "SD", 2)
    p.raw[o + 1], p.raw[o + 3] = 200, 0x40              # a decay-lane step
    p.set_motion("A", "MT", 0, tune=100)                # the melodic voice
    r = rework.soften(p, keep=["MT"], scale=0.25)
    assert p.get_motion("A", "SD", 0)["tune"] == 25
    assert p.get_motion("A", "SD", 1)["mask"] == 0
    assert p.get_motion("A", "SD", 2)["mask"] == 0
    assert p.get_motion("A", "MT", 0)["tune"] == 100
    assert r["scaled"] >= 1 and r["cleared"] >= 1 and r["decay_cleared"] >= 1


def test_only_touches_the_named_slots(survey):
    d, spec, src = survey
    entries = rework.plan(spec, d)
    d_, t = make_device(patterns={64: src, 127: load_fixture_pattern()}, kits={64: load_fixture_kit()})
    with pytest.raises(rework.ReworkError, match="none of"):
        rework.build(d_, entries, d, sleep=lambda s: None, log=lambda *a: None, only=["8-01"])
    assert t.sent == []
    done = rework.build(d_, entries, d, sleep=lambda s: None, log=lambda *a: None, only=["8-16"])
    assert done["failed"] is None and done["patterns"] == ["8-16"]
