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


# ------------------------------------------------------------------ the FM-1 alongside the set

def _fm1_spec(spec, entries, roots=True, plays=None):
    route = [r["variation"] for r in entries[0]["route"]]
    s = json.loads(json.dumps(spec))
    s["fm1"] = {"roots": {"Deep SH Bass": {"pc": "A", "note": "A1", "cents": -8, "clear": True}} if roots else {},
                "projects": {"A": {"name": "WARM", "key_from": "Deep SH Bass", "mode": "minor", "seed": 1,
                                   "parts": {"1": {"shape": "bass", "engine": "DUALVCO", "register": "low",
                                                   "rhythm": [4, 2]},
                                             "2": {"shape": "stab", "engine": "FMTONE", "register": "mid"}}}}}
    s["slots"][0]["fm1"] = {"project": "A",
                            "plays": plays if plays is not None else
                            {v: ([1] if n == 1 else [1, 2] if n > 1 else []) for n, v in enumerate(route)}}
    return s


def test_fm1_plan_needs_a_measured_root(survey):
    d, spec, _ = survey
    entries = rework.plan(spec, d)
    with pytest.raises(rework.ReworkError, match="not measured"):
        rework.fm1_plan(_fm1_spec(spec, entries, roots=False), entries)


def test_fm1_plan_needs_every_route_variation_and_known_tracks(survey):
    d, spec, _ = survey
    entries = rework.plan(spec, d)
    with pytest.raises(rework.ReworkError, match="exactly the route's variations"):
        rework.fm1_plan(_fm1_spec(spec, entries, plays={"A": [1]}), entries)
    route = [r["variation"] for r in entries[0]["route"]]
    with pytest.raises(rework.ReworkError, match="no track"):
        rework.fm1_plan(_fm1_spec(spec, entries, plays={v: [5] for v in route}), entries)


def test_fm1_plan_keys_parts_and_hand_cues(survey):
    d, spec, _ = survey
    entries = rework.plan(spec, d)
    fp = rework.fm1_plan(_fm1_spec(spec, entries), entries)
    a = fp["projects"]["A"]
    assert a["key"] == "A minor" and a["tracks"][1]["root"] == "A1" and a["tracks"][2]["root"] == "A2"
    rows = fp["slots"][0]["rows"]
    assert rows[0]["plays"] == [] and rows[1]["in"] == [1] and rows[2]["in"] == [2]
    assert fp["slots"][0]["first_of_project"] and fp["first"] == "A"


def test_the_cue_sheet_carries_the_fm1(survey):
    from datetime import date
    from tr8s.cuesheet import render_rework
    d, spec, _ = survey
    entries = rework.plan(spec, d)
    s = _fm1_spec(spec, entries)
    md = render_rework(s, entries, date(2026, 11, 13), rework.fm1_plan(s, entries))
    for part in ("| FM-1 | Key |", "A: bass, stab", "## Before the set", "load project A, mute every track",
                 "| FM-1 plays | By hand on the FM-1 |", "unmute track 2", "unmute track 3",
                 "**Project A · WARM · A minor**", "CLK) **TRS**"):
        assert part in md, part
    quiet = _fm1_spec(spec, entries, plays={r["variation"]: [] for r in entries[0]["route"]})
    md = render_rework(quiet, entries, date(2026, 11, 13), rework.fm1_plan(quiet, entries))
    assert "every FM-1 track stays muted: the TR-8S alone" in md and "A, all muted" in md


def test_a_project_nobody_loads_is_refused(survey):
    d, spec, _ = survey
    entries = rework.plan(spec, d)
    s = _fm1_spec(spec, entries)
    s["fm1"]["projects"]["B"] = s["fm1"]["projects"]["A"]
    s["slots"][0]["fm1"]["project"] = "B"
    s["slots"].insert(0, {**s["slots"][0], "slot": "8-15", "fm1": {"project": "A", "plays": {}}})
    # only the ordering matters here: B after A with no load in between
    entries2 = [dict(entries[0], panel="8-15", route=[]), entries[0]]
    with pytest.raises(rework.ReworkError, match="no slot before loads B"):
        rework.fm1_plan(s, entries2)


def test_register_root_and_merge_roots():
    assert rework.register_root("A", "low") == "A1"
    assert rework.register_root("E", "low") == "E1" and rework.register_root("D#", "low") == "D#2"
    assert rework.register_root("C", "high") == "C4"
    one = {"clear": True, "pc": "A", "note": "A1", "cents": -10, "hz": 54.7, "hits": 4, "spread_cents": 2}
    two = dict(one, cents=-4)
    r = rework.merge_roots([("8-01", "MT", "Deep SH Bass", one), ("8-04", "HT", "Deep SH Bass", two)], "d")
    assert r["Deep SH Bass"]["clear"] and r["Deep SH Bass"]["cents"] == -7
    r = rework.merge_roots([("8-01", "MT", "X", one), ("8-04", "HT", "X", dict(one, pc="A#", note="A#1"))], "d")
    assert not r["X"]["clear"] and "disagree" in r["X"]["why"]
    r = rework.merge_roots([("8-01", "MT", "Y", {"clear": False, "hits": 1})], "d")
    assert not r["Y"]["clear"]


def test_sweeps_are_told_apart_from_semitone_lines():
    assert rework.is_sweep([-3.25, 0.29, 2.72])
    assert not rework.is_sweep([-5.0, 0.0, 1.01, 3.0])
    assert not rework.is_sweep([])


def test_a_push_writes_all_six_tracks_and_an_audition_takes_one_project(survey):
    from tr8s.synth import minitone as mt
    d, spec, _ = survey
    entries = rework.plan(spec, d)
    fp = rework.fm1_plan(_fm1_spec(spec, entries), entries)
    full = rework.all_tracks(fp["projects"]["A"]["parts"])
    assert sorted(full) == list(range(mt.TRACKS))
    assert all(not s.sounding for t in (0, 3, 4, 5) for s in full[t].steps) and full[1].steps[2].sounding
    frames = mt.setup_frames(full) + mt.to_syx(full)
    assert {f[5] for f in frames if f[4] == mt.CMDS["TRACK_STEP"]} == set(range(mt.TRACKS))
    fp["projects"]["B"] = fp["projects"]["A"]
    with pytest.raises(rework.ReworkError, match="one project"):
        rework.fm1_push(fp, ["A", "B"])                       # refused before any port is opened


def test_a_first_project_loaded_mid_set_renders(survey):
    from datetime import date
    from tr8s.cuesheet import render_rework
    d, spec, _ = survey
    (e,) = rework.plan(spec, d)
    s = _fm1_spec(spec, [e])
    s["slots"].insert(0, {**s["slots"][0], "slot": "8-15", "fm1": {"load": "A"}})
    entries = [dict(e, panel="8-15", index=1, route=[]), dict(e, index=2)]
    fp = rework.fm1_plan(s, entries)
    assert fp["first"] == "A" and fp["before_set"] is None
    assert fp["slots"][0]["load"] == "A" and fp["slots"][1]["first_of_project"]
    md = render_rework(s, entries, date(2026, 11, 14), fp)
    assert "**Load project A during this pattern**" in md and "No project is loaded yet" in md
    assert "The first project is loaded during the set" in md


def test_negative_shuffle_swings_too():
    from tr8s.synth.line import Part, SynthStep
    steps = [SynthStep(notes=(45,)) if i % 2 else SynthStep() for i in range(16)]
    project = {"parts": {2: Part("stab", "A minor", "A2", 0, 0.5, steps)}, "scale": [9, 11, 0, 2, 4, 5, 7],
               "key": "A minor"}
    e = {"roles": {}, "shuffle": -40}
    assert any("swung 16ths" in w for w in rework._slot_warnings(e, project, [[2]], {}))
    e["shuffle"] = 10
    assert not any("swung 16ths" in w for w in rework._slot_warnings(e, project, [[2]], {}))


def test_a_rerun_keeps_the_roots_it_did_not_measure_and_never_loses_a_good_one():
    good = {"clear": True, "pc": "A", "note": "A1", "cents": -10, "hz": 54.7, "hits": 4, "spread_cents": 2}
    first = rework.merge_roots([("8-01", "MT", "Deep SH Bass", good),
                                ("8-04", "HT", "Deep SH Bass", dict(good, cents=-6))], "d1")
    noisy = {"clear": False, "hits": 1}
    again = rework.merge_roots([("8-04", "HT", "Deep SH Bass", noisy)], "d2", previous=first)
    r = again["Deep SH Bass"]
    assert r["clear"] and r["cents"] == -8 and len(r["each"]) == 2
    better = rework.merge_roots([("8-04", "HT", "Deep SH Bass", dict(good, cents=-12))], "d2", previous=first)
    assert better["Deep SH Bass"]["cents"] == -11 and better["Deep SH Bass"]["measured"] == "d2"


def test_kits_only_writes_the_kit_and_no_pattern_content(survey):
    d, spec, src = survey
    (d / "tones.json").write_text(json.dumps({"0": {"name": "909 Bass1", "type": "ACB"}}))
    base = Kit.from_bytes(load_fixture_kit())
    base.name = "DEFAULT"
    (d / "kit_112.bin").write_bytes(base.to_bytes())
    spec = json.loads(json.dumps(spec))
    spec["base_kit"] = 113
    spec["slots"][0]["kit"] = {"BD": {"tone": "909 Bass1"}}
    entries = rework.plan(spec, d)
    designed = rework.design_kits(spec, entries, d)
    edited = load_fixture_pattern()                           # the player's own 8-16, edited
    d_, t = make_device(patterns={64: src, 127: edited}, kits={127: load_fixture_kit()})
    done = rework.build(d_, entries, d, designed=designed, sleep=lambda s: None,
                        log=lambda *a: None, kits_only=True)
    assert done["failed"] is None and done["kits"] == [128] and done["patterns"] == []
    assert t.slots["pattern"][127] == edited                  # only rewritten from its own read
    assert {i for k, i in t.sent if k == "pattern"} == {127}       # own step + kit-reference repair
    assert any("--kits-only" in w for w in rework.writes(entries, kits_only=True))



def test_master_probability_is_101_plus_the_value_and_0_when_never_set():
    p = Pattern.from_bytes(load_fixture_pattern())
    p.raw[115] = 0
    assert p.master_prob == 0
    p.master_prob = 51
    assert p.raw[115] == 152 and p.master_prob == 51
    p.master_prob = -100
    assert p.raw[115] == 1
    with pytest.raises(Exception, match="-100..100"):
        p.master_prob = 101


def test_the_spec_refuses_a_master_probability_out_of_range(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"slots": [{"slot": "8-01", "source": "5-01", "bpm": 128, "label": "X",
                                        "master_prob": 150}]}))
    with pytest.raises(rework.ReworkError, match="master_prob"):
        rework.load_spec(f)


def test_patch_kit_changes_only_what_the_spec_names():
    current = Kit.from_bytes(load_fixture_kit())
    o_oh, o_rs, o_bd = (Kit.record_offset(i) for i in ("OH", "RS", "BD"))
    current.raw[o_oh + 5] = 75                                   # set by the machine or the panel
    current.set("BD", "lfo", 20)                                  # a panel tweak the spec does not name
    current.raw[o_rs + 4] = 99                                    # the RS fader
    designed = current.copy()
    designed.name = "NEW"
    designed.raw[o_oh + 5] = 104
    designed.set("BD", "lfo", 0)
    designed.set("BD", "decay", (current.get("BD", "decay") + 10) % 256)
    designed.set("RS", "tone", (current.get("RS", "tone") + 1) % 600)
    designed.raw[o_rs + 7] = 33                                   # part of the new record
    designed.set_color("RS", 9)
    k, what = rework.patch_kit(current, designed, {"BD": {"tone": "x"}, "RS": {"tone": "y"}})
    assert k.name == "NEW" and k.color("RS") == 9
    assert k.raw[o_oh + 5] == 75                                  # OH is not in the spec: kept
    assert k.get("BD", "lfo") == 20 and k.get("BD", "decay") == designed.get("BD", "decay")
    assert k.get("RS", "tone") == designed.get("RS", "tone") and k.raw[o_rs + 7] == 33
    assert k.raw[o_rs + 4] == 99                                  # the fader level is the machine's
    assert what == ["BD decay", "RS tone"]


def test_steps_only_sets_probability_on_the_machines_pattern_and_reads_no_source(survey):
    d, spec, src = survey
    spec = json.loads(json.dumps(spec))
    spec["slots"][0]["steps"] = [{"vars": "A", "inst": "RS", "steps": [4, 5], "prob": 60}]
    spec["slots"][0]["master_prob"] = 51
    entries = rework.plan(spec, d)
    mine = Pattern.from_bytes(src)
    mine.set_steps("A", "RS", "x..x............")               # edited on the panel: no hit on 5
    d_, t = make_device(patterns={127: mine.to_bytes()}, kits={})  # the source 5-01 is not there
    done = rework.build(d_, entries, d, sleep=lambda s: None, log=lambda *a: None, steps_only=True)
    assert done["failed"] is None and done["patterns"] == ["8-16"] and done["kits"] == []
    assert done["steps_skipped"] == {"8-16": ["A RS 5"]}
    back = Pattern.from_bytes(t.slots["pattern"][127])
    assert back.get_step("A", "RS", 3)["prob"] == 60 and back.master_prob == 51
    assert back.get_steps("A", "RS") == mine.get_steps("A", "RS")   # nothing rebuilt
    again = rework.build(d_, entries, d, sleep=lambda s: None, log=lambda *a: None, steps_only=True)
    assert again["patterns"] == [] and again["skipped"] == ["pattern 8-16"]


def test_ctrl_map_by_role_and_tone_type():
    from tr8s.kit import CTRL_BASE, CTRL_SEL
    k = Kit.from_bytes(load_fixture_kit())
    k.raw[CTRL_SEL:CTRL_BASE + 11] = bytes(12)                    # the blank kit: OFF, all "---"
    ids = {"BD": 0, "MT": 300, "CH": 1, "LT": 2, "HT": 301}
    for inst, tid in ids.items():
        k.set(inst, "tone", tid)
    tones = {"0": {"type": "ACB"}, "1": {"type": "ACB"}, "2": {"type": "ACB"},
             "300": {"type": "SAMPLE"}, "301": {"type": "SAMPLE"}}
    for inst in TRACKS:
        if inst not in ids:
            k.set(inst, "tone", 1)
    roles = {**rework.DEFAULT_ROLE, "MT": "acid"}
    notes = rework.apply_ctrl(k, roles, tones, "T")
    assert k.ctrl_sel == 6                                        # User
    assert k.ctrl("BD") == 6                                      # ACB kick: Attack, read on the panel
    assert k.ctrl("MT") == 18                                     # sample acid: Filter Cutoff
    assert k.ctrl("CH") == 3 and k.ctrl("OH") == 3                # hats: Delay Send
    assert k.ctrl("SD") == 2 and k.ctrl("CC") == 2                # Reverb Send
    assert k.ctrl("LT") == 3 and k.ctrl("HT") == 18               # a light voice: delay on ACB, filter on a sample
    assert "MT CTRL FltCutoff" in notes
    k.set("BD", "tone", 300)
    rework.apply_ctrl(k, roles, tones, "T")
    assert k.ctrl("BD") == 18                                     # a sample kick gets the filter
    with pytest.raises(rework.ReworkError, match="not available"):
        rework.apply_ctrl(k, {**roles, "SD": "acid"}, tones, "T",
                          scheme={**rework.ROLE_CTRL, "acid": "FltCutoff"})   # SD is ACB here


def test_patch_kit_copies_the_ctrl_map_only_when_asked():
    from tr8s.kit import CTRL_BASE, CTRL_SEL
    current = Kit.from_bytes(load_fixture_kit())
    current.raw[CTRL_SEL:CTRL_BASE + 11] = bytes(12)
    designed = current.copy()
    designed.ctrl_sel = "User"
    designed.set_ctrl("MT", 18)
    k, what = rework.patch_kit(current, designed, {})
    assert k.ctrl_sel == 0 and k.ctrl("MT") == 0 and what == []
    k, what = rework.patch_kit(current, designed, {}, ctrl=True)
    assert k.ctrl_sel == 6 and k.ctrl("MT") == 18 and what == ["CTRL map"]


def test_fx_from_copies_a_donor_inst_fx_block_and_patch_kit_carries_only_named_ones(tmp_path):
    donor = Kit.from_bytes(load_fixture_kit())
    block = bytes([10, 0, 0, 0, 0, 128, 128, 40, 8, 47, 3]) + bytes(21)   # type 10 DRIVE
    donor.set_inst_fx("LT", block)
    donor.name = "EXPCID"
    (tmp_path / "kit_076.bin").write_bytes(donor.to_bytes())           # panel kit 77
    current = Kit.from_bytes(load_fixture_kit())
    current.set_inst_fx("CH", bytes([1]) + bytes(31))                   # an HPF set on the panel
    designed = current.copy()
    designed.set_inst_fx("CH", bytes(32))
    note = rework.apply_fx_from(designed, "HT", {"kit": 77, "inst": "LT"}, tmp_path, "T")
    assert designed.inst_fx("HT") == block and note == "HT INST FX DRIVE from kit 77 EXPCID/LT"
    k, what = rework.patch_kit(current, designed, {"HT": {"tone": "x", "fx_from": {"kit": 77, "inst": "LT"}},
                                                   "CH": {"tone": "y"}})
    assert k.inst_fx("HT") == block and "HT INST FX" in what
    assert k.inst_fx("CH")[0] == 1                                      # not named: the panel's stays
    with pytest.raises(rework.ReworkError, match="fx_from"):
        rework.apply_fx_from(designed, "HT", {"kit": 77}, tmp_path, "T")
    with pytest.raises(rework.ReworkError, match="no survey read"):
        rework.apply_fx_from(designed, "HT", {"kit": 5, "inst": "LT"}, tmp_path, "T")
