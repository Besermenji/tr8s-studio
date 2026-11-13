"""
set.plan, set.build and the cue sheet. The TR-8S is `make_device`'s fake and the FM-1 is
`FakeFm1` behind the real `Fm1Port`, so the real sending code runs and no hardware is needed.
"""

import json
from datetime import date
from pathlib import Path

import pytest

import golden_cuesheet_gen
from fake import (FakeFm1, fake_fm1_rtmidi, load_fixture_kit,
                  load_fixture_pattern, make_device)
from tr8s import cli, cuesheet, tools
from tr8s.history import HISTORY
from tr8s.synth import minitone as mt
from tr8s.synth import port as port_mod
from tr8s.synth.port import Fm1Port
from tr8s.tools import ToolError, setlist

FIXTURES = Path(__file__).parent / "fixtures"
THREE = json.loads((FIXTURES / "set_three.json").read_text())


@pytest.fixture
def wired():
    d, t = make_device(patterns={s: load_fixture_pattern() for s in (0, 1, 2, 3, 4)},
                       kits={0: load_fixture_kit(), 61: load_fixture_kit(),
                             70: load_fixture_kit(), 89: load_fixture_kit()})
    tools.set_device(d)
    HISTORY.clear()
    yield d, t
    tools.set_device(None)


def _fm1(monkeypatch, fake=None):
    fake = fake_fm1_rtmidi(monkeypatch, fake or FakeFm1())
    monkeypatch.setattr(port_mod, "STEP_TIMEOUT", 0.05)
    port = Fm1Port()
    tools.set_fm1(port)
    return fake, port


@pytest.fixture
def fm1(monkeypatch):
    fake, port = _fm1(monkeypatch)
    yield fake
    tools.set_fm1(None)
    port.close()


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setattr("tr8s.tools.setlist._library_dir", lambda: tmp_path)
    monkeypatch.setattr("tr8s.tools.synth._library_dir", lambda: tmp_path)
    return tmp_path


class Untouchable:
    """A device that fails the test on any use (closing it, as the CLI always does, is not a use)."""

    def close(self):
        pass

    def __getattr__(self, name):
        raise AssertionError(f"a device was used: .{name}")


@pytest.fixture
def no_devices():
    tools.set_device(Untouchable())
    tools.set_fm1(Untouchable())
    yield
    tools.set_device(None)
    tools.set_fm1(None)


def frames_of(fake):
    return [f for f in fake.sent if f[:4] == mt.HEADER]


def files_in(d: Path) -> list:
    return sorted(p for p in d.rglob("*") if p.is_file())


def three():
    return {k: THREE[k] for k in ("name", "start_slot", "tracks")}


# ------------------------------------------------------------------ refusals

@pytest.mark.parametrize("args, match", [
    ({"tracks": [{"style": "acid"}] * 5}, "at most 4 synth tracks"),
    ({"tracks": [{"style": "acid", "slot": 0}, {"style": "dub", "slot": "1-01"}]}, "both use TR-8S slot 0"),
    ({"tracks": [{"style": "acid", "project": 2}, {"style": "dub", "project": 2}]}, "both use FM-1 project 2"),
    ({"tracks": [{"style": "acid", "kit_slot": 61}, {"style": "dub", "kit_slot": 61}]}, "both use kit slot"),
    ({"tracks": [{"style": "acid", "voicing": "polka"}]}, "track 1"),
    ({"tracks": [{"style": "acid", "arrange": True, "bars": 12}]}, "bars is one of"),
    ({"tracks": [{"style": "acid", "synth": False, "project": 1}]}, "need synth: true"),
    ({"tracks": [{"style": "acid", "colour": "red"}]}, "unknown fields"),
    ({"tracks": [{"style": "waltz"}]}, "unknown style"),
])
def test_a_bad_set_is_refused_before_anything_is_sent(wired, fm1, library, args, match):
    d, t = wired
    args = {"name": "bad", "start_slot": 0, **args}
    with pytest.raises(ToolError, match=match):
        tools.call("set.build", args)
    assert t.sent == [] and frames_of(fm1) == [] and files_in(library) == []


def test_a_set_needs_somewhere_to_start(no_devices):
    with pytest.raises(ToolError, match="no slot, and no start_slot"):
        tools.call("set.plan", {"name": "x", "tracks": [{"style": "dub"}]})


def test_a_bad_name_is_refused(no_devices):
    with pytest.raises(ToolError, match="name"):
        tools.call("set.plan", {"name": "My Set!", "start_slot": 0, "tracks": [{"style": "dub"}]})


def test_drums_only_tracks_do_not_count_toward_the_four(no_devices):
    tracks = [{"style": s} for s in ("acid", "dub", "hypnotic", "techno")] + [{"style": "house", "synth": False}]
    p = tools.call("set.plan", {"name": "five", "start_slot": 0, "tracks": tracks})
    assert [t["synth"] for t in p["tracks"]] == [True] * 4 + [False]
    assert p["plan"][4]["synth"] is None and "drums only" in p["cue_sheet"]


# ------------------------------------------------------------------ plan

def test_the_defaults(no_devices):
    p = tools.call("set.plan", {"name": "d", "start_slot": "2-01", "tracks": [
        {"style": "acid", "project": 1}, {"style": "dub", "slot": "3-05"}, {"style": "hypnotic"}]})
    t = p["tracks"]
    assert [p["plan"][i]["panel"] for i in range(3)] == ["2-01", "3-05", "3-06"]
    assert [e["project"] for e in t] == [1, 0, 2]       # the free ones, in order, around the given one
    assert all(isinstance(e["seed"], int) for e in t)
    assert t[0]["key"] == tools.DEFAULT_KEYS["acid"] and t[0]["energy"] == 0.7
    assert t[0]["voicing"] == "acid" and t[0]["arrange"] is False and t[0]["bars"] == 16
    assert t[0]["name"] == "ACID" and t[0]["kit_slot"] is None


def test_the_plan_touches_no_device_and_writes_no_file(no_devices, library):
    p = tools.call("set.plan", three())
    assert files_in(library) == []
    assert p["cue_sheet"].startswith("# Set: three")
    assert p["files"]["cue_sheet"].endswith("sets/three.md")
    assert any("no undo" in w for w in p["writes"])


def test_the_plan_is_marked_read_only_and_the_build_is_not():
    assert tools.REGISTRY["set.plan"]["mutates_device"] is False
    assert tools.REGISTRY["set.build"]["mutates_device"] is True


def test_the_resolved_tracks_plan_the_same_set_again(no_devices):
    p = tools.call("set.plan", {"name": "r", "start_slot": 0, "tracks": [{"style": "dub"}, {"style": "acid"}]})
    again = tools.call("set.plan", {"name": "r", "tracks": p["tracks"]})
    assert again["tracks"] == p["tracks"] and again["cue_sheet"] == p["cue_sheet"]


def test_the_hand_cues_follow_the_same_curve_as_a_song(no_devices):
    p = tools.call("set.plan", three())
    dub = p["plan"][1]
    assert dub["synth"]["arrange"] is False and dub["synth"]["sections"] is None
    assert dub["start_muted"] == [2]                     # the stab waits; the pad starts the loop
    by_variation = {r["variation"]: r for r in dub["timeline"]}
    assert by_variation["B"]["in"] == [2] and by_variation["E"]["out"] == [2]
    assert [r["bar"] for r in dub["timeline"]] == [1 + 16 * n for n in range(8)]


def test_the_cue_sheet_snapshot():
    assert golden_cuesheet_gen.sheet() == (FIXTURES / "cuesheet_three.md").read_text(), \
        "the cue sheet changed: if on purpose, run tests/golden_cuesheet_gen.py and read the diff"


def test_the_sheet_says_how_to_start_each_kind_of_track():
    sheet = (FIXTURES / "cuesheet_three.md").read_text()
    assert "open the SONG page" in sheet and "Press PLAY on a downbeat" in sheet
    assert "Mute track 3 (stab)" in sheet and "unmute track 3" in sheet
    assert "TR-8S MIDI OUT → DIN-to-TRS adapter → FM-1 TRS MIDI IN" in sheet and "CLK) TRS" in sheet


# ------------------------------------------------------------------ build

def test_three_tracks_on_the_fake_boxes(wired, fm1, library):
    d, t = wired
    r = tools.call("set.build", three())
    assert r["failed"] is None and r["untouched"] == [] and len(r["built"]) == 3

    patterns = [s for k, s in t.sent if k == "pattern"]
    assert sorted(set(patterns)) == [0, 1, 2] and patterns.index(0) < patterns.index(1) < patterns.index(2)

    acid, dub, hyp = r["plan"]
    assert fm1.slots[0] == acid["synth"]["song_rows"] and fm1.slots[0]
    assert fm1.slots[1] == []                            # a plain loop: the chain was emptied first
    assert fm1.slots[2] == hyp["synth"]["song_rows"]
    assert frames_of(fm1)[-1][4] == mt.CMDS["PROJECT"]
    assert [b["tempo"] for b in r["built"]] == [p["tempo"] for p in r["plan"]]
    assert "warning" not in json.dumps(r["built"])

    assert [Path(p).name for p in r["written"]] == ["three.json", "three.md"]
    assert (library / "sets" / "three.md").read_text() == r["cue_sheet"]
    saved = json.loads((library / "sets" / "three.json").read_text())
    assert saved == {"name": "three", "tracks": r["tracks"]}
    assert r["undo"]["history_steps"] == 3 and "A, B, C" in r["undo"]["fm1"]


def test_the_preview_sheet_is_the_built_sheet(wired, fm1, library):
    preview = tools.call("set.plan", three())
    built = tools.call("set.build", {"name": "three", "tracks": preview["tracks"]})
    assert built["cue_sheet"] == preview["cue_sheet"]


def test_every_kit_is_built_before_any_pattern_and_each_pattern_keeps_its_own(wired, fm1, library):
    d, t = wired
    r = tools.call("set.build", {"name": "kits", "start_slot": 0, "tracks": [
        {"style": "techno", "seed": 1, "kit_slot": 61}, {"style": "dub", "seed": 2, "kit_slot": 70}]})
    assert r["failed"] is None
    kinds = [k for k, _ in t.sent]
    assert max(i for i, k in enumerate(kinds) if k == "kit") < kinds.index("pattern")
    assert d.read_pattern(0).kit == 61 and d.read_pattern(1).kit == 70
    assert r["undo"]["history_steps"] == 4


def test_the_undo_puts_the_tr8s_back(wired, fm1, library):
    d, t = wired
    before = {s: t.slots["pattern"][s] for s in (0, 1, 2)}
    r = tools.call("set.build", three())
    assert any(t.slots["pattern"][s] != before[s] for s in before)
    tools.call(r["undo"]["call"]["tool"], r["undo"]["call"]["args"])
    assert {s: t.slots["pattern"][s] for s in before} == before


def test_an_fm1_failure_stops_the_set_and_says_what_was_done(wired, monkeypatch, library):
    d, t = wired
    fake, port = _fm1(monkeypatch, FakeFm1(playing=True))
    try:
        r = tools.call("set.build", {"name": "half", "start_slot": 0, "tracks": [
            {"style": "techno", "seed": 1, "synth": False},
            {"style": "dub", "seed": 2},
            {"style": "acid", "seed": 3}]})
    finally:
        tools.set_fm1(None)
        port.close()
    f = r["failed"]
    assert f["index"] == 2 and f["stage"] == "FM-1" and "stop the TR-8S" in f["error"]
    assert "was not saved" in f["state"]
    assert r["untouched"] == [3]
    assert mt.CMDS["PROJECT"] not in [x[4] for x in frames_of(fake)]
    assert sorted({s for k, s in t.sent if k == "pattern"}) == [0, 1]
    assert [Path(p).name for p in r["written"]] == ["half.json"]      # no sheet for a half-built set
    assert not (library / "sets" / "half.md").exists()
    assert r["undo"]["history_steps"] == 2 and r["undo"]["fm1"] == "no FM-1 project was saved"


def test_an_unreachable_fm1_refuses_before_anything_is_written(wired, monkeypatch, library):
    d, t = wired
    fake_fm1_rtmidi(monkeypatch, ports=("TR-8S",))
    tools.set_fm1(None)
    try:
        with pytest.raises(ToolError, match="not reachable, nothing was written"):
            tools.call("set.build", three())
    finally:
        tools.set_fm1(None)
    assert t.sent == [] and files_in(library) == []


def test_an_unreachable_tr8s_refuses_before_anything_is_written(fm1, library, monkeypatch):
    from tr8s.midi_backends import TransportError

    def off():
        raise TransportError("no TR-8S MIDI port found")
    monkeypatch.setattr(setlist, "_device_helper", off)
    with pytest.raises(ToolError, match="TR-8S is not reachable, nothing was written"):
        tools.call("set.build", three())
    assert frames_of(fm1) == [] and files_in(library) == []


def test_a_drums_only_set_never_opens_the_fm1(wired, library, monkeypatch):
    d, t = wired
    tools.set_fm1(Untouchable())
    try:
        r = tools.call("set.build", {"name": "drums", "start_slot": 3, "tracks": [
            {"style": "techno", "seed": 1, "synth": False}]})
    finally:
        tools.set_fm1(None)
    assert r["failed"] is None and r["undo"]["fm1"] == "no FM-1 project was saved"


# ------------------------------------------------------------------ CLI

def test_the_cli_preview_prints_the_sheet_and_writes_nothing(no_devices, library, capsys):
    assert cli.main(["set", str(FIXTURES / "set_three.json"), "--preview"]) == 0
    out, err = capsys.readouterr()
    assert out.startswith("# Set: three") and "preview only" in err
    assert files_in(library) == []


def test_the_cli_says_when_it_cannot_read_the_file(capsys, tmp_path):
    assert cli.main(["set", str(tmp_path / "missing.json")]) == 1
    assert "cannot read" in capsys.readouterr().err


def test_render_is_pure_and_dated():
    p = setlist.plan("x", [{"style": "dub", "seed": 5}], 0, built_on=date(2026, 1, 2))
    assert cuesheet.render(p, date(2026, 1, 2)) == p["cue_sheet"]
    assert "Built 2026-01-02" in p["cue_sheet"]
