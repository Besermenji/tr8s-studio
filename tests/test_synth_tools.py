"""
The FM-1 sender, the `synth` tools, FM-1 parts inside track.create, and the
library's synth block. Everything runs against `FakeFm1` (an `rtmidi` stand-in),
so the real `Fm1Port` code is what is tested and no FM-1 is needed.
"""

import json

import pytest

from fake import (FakeFm1, fake_fm1_rtmidi, load_fixture_kit,
                  load_fixture_pattern, make_device)
from tr8s import tools
from tr8s.style import STYLES
from tr8s.synth import minitone as mt
from tr8s.synth import port as port_mod
from tr8s.synth.minitone import Fm1FrameError
from tr8s.synth.port import Fm1EchoError, Fm1Error, Fm1Port
from tr8s.tools import ToolError


@pytest.fixture
def fm1(monkeypatch):
    """An FM-1 on the Felucca port, wired in as the tools' FM-1."""
    fake = fake_fm1_rtmidi(monkeypatch)
    monkeypatch.setattr(port_mod, "STEP_TIMEOUT", 0.05)
    port = Fm1Port()
    tools.set_fm1(port)
    yield fake
    tools.set_fm1(None)
    port.close()


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A throwaway library directory: nothing here may write into the repo's."""
    monkeypatch.setattr("tr8s.tools.synth._library_dir", lambda: tmp_path)
    monkeypatch.setattr("tr8s.tools.library._library_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def wired():
    d, t = make_device(patterns={0: load_fixture_pattern(), 5: load_fixture_pattern()},
                       kits={0: load_fixture_kit(), 89: load_fixture_kit()})
    tools.set_device(d)
    yield d, t
    tools.set_device(None)


def frames_of(fake):
    return [f for f in fake.sent if f[:4] == mt.HEADER]


def cmds(fake):
    return [f[4] for f in frames_of(fake)]


# ------------------------------------------------------------------- port

def test_the_port_is_found_by_name(monkeypatch):
    fake_fm1_rtmidi(monkeypatch, ports=("TR-8S", "Felucca"))
    p = Fm1Port()
    assert p.name == "Felucca"
    p.close()


def test_a_missing_port_says_what_to_check(monkeypatch):
    fake_fm1_rtmidi(monkeypatch, ports=("TR-8S",))
    with pytest.raises(Fm1Error, match="no MIDI port containing 'Felucca'.*plugged in"):
        Fm1Port()


def test_the_port_name_can_be_chosen(monkeypatch):
    fake_fm1_rtmidi(monkeypatch, ports=("TR-8S", "FM-1"))
    monkeypatch.setenv("FM1_PORT", "fm-1")
    p = Fm1Port()
    assert p.name == "FM-1"
    p.close()


@pytest.mark.parametrize("byte", [0xF8, 0xF9, 0xFA, 0xFB, 0xFC])
def test_realtime_bytes_are_refused_at_the_port(fm1, byte):
    p = Fm1Port()
    with pytest.raises(Fm1Error, match="realtime"):
        p.send(bytes([byte]))
    with pytest.raises(Fm1Error, match="realtime"):
        p.send(bytes([0x90, 60, 100, byte]))
    assert fm1.sent == []
    p.close()


def test_a_frame_that_is_not_ours_stops_the_batch_before_anything_is_sent(fm1):
    port = tools.fm1()
    good = mt.to_syx(mt.build_parts("dub", seed=1))[:3]
    bad = mt.frame(mt.CMDS["SONG"], [4])
    with pytest.raises(Fm1FrameError):
        port.write_frames(good + [bad])
    assert fm1.sent == []


def test_a_clock_or_channel_setting_is_not_an_edit(fm1):
    port = tools.fm1()
    for f in (mt.frame(mt.CMDS["SET"], [1, 2, *mt.v14(1)]),      # CLK
              mt.frame(mt.CMDS["SET"], [1, 14, *mt.v14(1)])):    # ROUT
        with pytest.raises(Fm1FrameError):
            port.write_frames([f])
    assert fm1.sent == []


def test_no_fm1_behind_the_port_is_a_timeout_with_a_hint(monkeypatch):
    fake_fm1_rtmidi(monkeypatch, FakeFm1(silent=True))
    p = Fm1Port()
    with pytest.raises(Fm1Error, match="no reply to TRACK_STEP within 0.1s.*Felucca"):
        p.request(mt.CMDS["TRACK_STEP"], [0, 0], timeout=0.05)
    p.close()


def test_a_step_stored_differently_is_reported_after_the_batch(monkeypatch):
    fake = fake_fm1_rtmidi(monkeypatch, FakeFm1(mangle=True))
    p = Fm1Port()
    frames = mt.to_syx(mt.build_parts("dub", seed=1))
    with pytest.raises(Fm1EchoError, match="1 of 32 frames came back different") as e:
        p.write_frames(frames)
    assert [n for n, _, _ in e.value.mismatches] == [1]
    assert len(frames_of(fake)) == len(frames)
    p.close()


def test_a_save_asks_for_the_slot_and_checks_the_reply(fm1):
    r = tools.fm1().save(2)
    assert r == {"project": 2, "used": True}
    assert frames_of(fm1)[-1] == mt.save_frame(2)


def test_a_save_to_a_slot_that_does_not_exist_sends_nothing(fm1):
    with pytest.raises(Fm1FrameError):
        tools.fm1().save(4)
    assert fm1.sent == []


# ------------------------------------------------------------------ voicing

def test_every_drum_style_has_a_voicing():
    assert set(STYLES) <= set(mt.VOICING)


# ------------------------------------------------------------------- tools

def test_the_synth_tools_are_registered():
    for name in ("synth.parts", "synth.export", "synth.send", "library.save"):
        assert name in tools.REGISTRY
    assert tools.REGISTRY["synth.send"]["mutates_device"]
    assert not tools.REGISTRY["synth.parts"]["mutates_device"]
    assert not tools.REGISTRY["synth.export"]["mutates_device"]


def test_synth_send_names_its_slot_project_so_undo_never_opens_the_tr8s():
    props = tools.REGISTRY["synth.send"]["input_schema"]["properties"]
    assert "project" in props and "slot" not in props


def test_synth_parts_is_deterministic_and_seeded():
    a = tools.call("synth.parts", {"style": "dub", "key": "A minor", "seed": 7})
    b = tools.call("synth.parts", {"style": "dub", "key": "A minor", "seed": 7})
    c = tools.call("synth.parts", {"style": "dub", "key": "A minor", "seed": 8})
    assert a == b and a != c


@pytest.mark.parametrize("style", sorted(STYLES))
def test_synth_parts_covers_every_drum_style_within_the_budget(style):
    r = tools.call("synth.parts", {"style": style, "length": 32})
    assert r["tracks"] and r["warnings"] == []


def test_a_dub_voicing_has_no_303():
    r = tools.call("synth.parts", {"style": "dub"})
    assert "TB303" not in {t["engine"] for t in r["tracks"].values()}


def test_an_override_replaces_and_drops_tracks():
    r = tools.call("synth.parts", {"style": "dub", "override": {
        "1": {"shape": "acid", "engine": "TB303"}, "2": None}})
    assert r["tracks"]["1"]["engine"] == "TB303" and "2" not in r["tracks"]


def test_a_bad_style_or_override_is_a_tool_error():
    with pytest.raises(ToolError, match="unknown style"):
        tools.call("synth.parts", {"style": "polka"})
    with pytest.raises(ToolError, match="KAZOO"):
        tools.call("synth.parts", {"style": "dub", "override": {"0": {"shape": "pad", "engine": "KAZOO"}}})
    with pytest.raises(ToolError, match="not a number"):
        tools.call("synth.parts", {"style": "dub", "override": {"x": None}})


def test_synth_send_without_a_project_writes_ram_only(fm1):
    r = tools.call("synth.send", {"style": "dub", "seed": 7})
    parts = mt.build_parts("dub", seed=7)
    expect = mt.setup_frames(parts) + mt.to_syx(parts)
    assert frames_of(fm1) == expect
    assert mt.CMDS["PROJECT"] not in cmds(fm1)
    assert r["saved"] is None and r["sent"]["frames"] == len(expect)


def test_synth_send_with_a_project_saves_last(fm1):
    r = tools.call("synth.send", {"style": "dub", "seed": 7, "project": 1})
    assert cmds(fm1).count(mt.CMDS["PROJECT"]) == 1
    assert frames_of(fm1)[-1] == mt.save_frame(1)
    assert r["saved"]["project"] == 1


def test_a_bad_project_is_refused_before_anything_is_sent(fm1):
    with pytest.raises(ToolError, match="outside 0..3"):
        tools.call("synth.send", {"style": "dub", "project": 4})
    assert fm1.sent == []


def test_nothing_the_tools_send_is_realtime(fm1):
    tools.call("synth.send", {"style": "acid", "seed": 3, "length": 32, "project": 0})
    assert not any(0xF8 <= b <= 0xFC for m in fm1.sent for b in m)


def test_synth_send_with_no_fm1_is_an_error_the_assistant_can_read(monkeypatch):
    fake_fm1_rtmidi(monkeypatch, ports=("TR-8S",))
    tools.set_fm1(None)
    with pytest.raises(ToolError, match="no MIDI port containing 'Felucca'"):
        tools.call("synth.send", {"style": "dub"})


def test_synth_export_writes_the_three_files(library):
    r = tools.call("synth.export", {"name": "dub1", "style": "dub", "key": "A minor",
                                    "seed": 7, "bpm": 120})
    files = {k: library / "synth" / f"dub1.{k}" for k in ("json", "mid", "syx")}
    assert {k: str(v) for k, v in files.items()} == r["files"]
    parts = mt.build_parts("dub", key="A minor", seed=7)
    assert files["mid"].read_bytes() == mt.to_mid(parts, bpm=120)
    assert mt.split_syx(files["syx"].read_bytes()) == mt.setup_frames(parts) + mt.to_syx(parts)
    doc = json.loads(files["json"].read_text())
    assert doc["style"] == "dub" and doc["seed"] == 7 and doc["bpm"] == 120


@pytest.mark.parametrize("name", ["", "../x", "a/b", "a b", "x" * 41])
def test_synth_export_refuses_a_name_that_is_not_a_plain_file_name(library, name):
    with pytest.raises(ToolError, match="name"):
        tools.call("synth.export", {"name": name, "style": "dub"})
    assert not any(library.rglob("*.*"))


# ------------------------------------------------------------- track.create

def test_track_create_with_synth_shares_the_drums_key_and_seed(wired, fm1):
    r = tools.call("track.create", {"slot": 0, "style": "dub", "seed": 42,
                                    "key": "A minor", "energy": 0.6, "synth": {}})
    s = r["synth"]
    assert (s["voicing"], s["key"], s["seed"], s["energy"]) == ("dub", "A minor", 42, 0.6)
    assert s["tempo"] == r["tempo"]
    parts = mt.build_parts("dub", key="A minor", energy=0.6, seed=42)
    assert {int(t): v["notes"] for t, v in s["tracks"].items()} == {t: p.names() for t, p in parts.items()}


def test_track_create_with_synth_defaults_the_tr8s_line_to_none(wired, fm1):
    r = tools.call("track.create", {"slot": 0, "style": "dub", "seed": 1, "synth": {}})
    assert "line" not in r and "line_error" not in r


def test_an_explicit_line_still_wins_over_the_synth_default(wired, fm1):
    r = tools.call("track.create", {"slot": 0, "style": "dub", "seed": 1,
                                    "line": "bass", "synth": {}})
    assert "line" in r or "line_error" in r       # attempted; the fixture's tone may refuse it


def test_without_synth_nothing_changes(wired, fm1):
    r = tools.call("track.create", {"slot": 0, "style": "dub", "seed": 1})
    assert "synth" not in r and fm1.sent == []


def test_synth_without_a_project_only_builds(wired, fm1):
    r = tools.call("track.create", {"slot": 0, "style": "dub", "seed": 1, "synth": {}})
    assert fm1.sent == [] and "saved" not in r["synth"]


def test_synth_with_a_project_sends_and_saves(wired, fm1):
    r = tools.call("track.create", {"slot": 0, "style": "dub", "seed": 1,
                                    "synth": {"project": 2}})
    assert r["synth"]["saved"]["project"] == 2
    assert frames_of(fm1)[-1] == mt.save_frame(2)
    assert "synth_error" not in r


def test_a_voicing_can_differ_from_the_drum_style(wired, fm1):
    r = tools.call("track.create", {"slot": 0, "style": "techno", "seed": 1,
                                    "synth": {"voicing": "dub", "length": 32}})
    assert r["synth"]["voicing"] == "dub"
    assert {t["shape"] for t in r["synth"]["tracks"].values()} == {"pad", "stab"}
    assert all(t["steps"] == 32 for t in r["synth"]["tracks"].values())


def test_an_fm1_failure_leaves_the_drums_written_and_says_so(wired, monkeypatch):
    fake_fm1_rtmidi(monkeypatch, FakeFm1(silent=True))
    monkeypatch.setattr(port_mod, "STEP_TIMEOUT", 0.05)
    tools.set_fm1(None)
    r = tools.call("track.create", {"slot": 0, "style": "dub", "seed": 1,
                                    "synth": {"project": 0}})
    assert "no reply" in r["synth_error"]
    assert set(r["roles"]) == set("ABCDEFGH")
    tools.set_fm1(None)


def test_no_fm1_plugged_in_is_a_synth_error_not_a_crash(wired, monkeypatch):
    fake_fm1_rtmidi(monkeypatch, ports=("TR-8S",))
    tools.set_fm1(None)
    r = tools.call("track.create", {"slot": 0, "style": "dub", "seed": 1,
                                    "synth": {"project": 0}})
    assert "Felucca" in r["synth_error"] and r["roles"]


def test_a_bad_voicing_fails_before_the_tr8s_is_written(wired, fm1):
    d, t = wired
    with pytest.raises(ToolError, match="unknown style"):
        tools.call("track.create", {"slot": 0, "style": "dub", "synth": {"voicing": "polka"}})
    assert t.sent == [] and fm1.sent == []


def test_an_unknown_synth_target_is_refused(wired, fm1):
    with pytest.raises(ToolError, match="only 'minitone'"):
        tools.call("track.create", {"slot": 0, "style": "dub", "synth": {"target": "moog"}})


def test_the_same_seed_gives_the_same_parts_in_two_slots(wired, fm1):
    a = tools.call("track.create", {"slot": 0, "style": "acid", "seed": 7, "synth": {}})
    b = tools.call("track.create", {"slot": 5, "style": "acid", "seed": 7, "synth": {}})
    assert a["synth"]["tracks"] == b["synth"]["tracks"]


def test_remix_with_synth_keeps_the_originals_tempo(wired, fm1):
    d, t = wired
    r = tools.call("track.remix", {"slot": 0, "style": "dub", "into": 5, "seed": 3, "synth": {}})
    assert r["synth"]["tempo"] == r["kept"]["tempo"]
    assert r["synth"]["seed"] == 3


# ------------------------------------------------------------------ library

def test_the_synth_block_survives_a_save_and_a_load(wired, fm1, library):
    d, t = wired
    made = tools.call("track.create", {"slot": 0, "style": "dub", "seed": 7, "key": "A minor",
                                       "name": "DUBBY", "synth": {"length": 32}})
    saved = tools.call("library.save", {"slot": 0, "name": "Dubby", "style": "dub", "key": "A minor",
                                        "seed": 7, "about": "pad and stab", "synth": made["synth"]})
    assert saved["saved"] == "dubby" and saved["synth"]
    doc = json.loads((library / "dubby.json").read_text())
    assert doc["_meta"]["seed"] == 7 and doc["_meta"]["style"] == "dub"
    assert doc["synth"] == {"target": "minitone", "voicing": "dub", "key": "A minor", "energy": 0.7,
                            "seed": 7, "length": 32, "override": {}, "arrange": False,
                            "bars": 16}                                 # no `project`: a replay must not save
    assert "tracks" not in doc["synth"]             # the inputs are kept, not the notes

    listed = tools.call("library.list", {})["tracks"]
    assert [x["synth"] for x in listed if x["name"] == "dubby"] == ["dub"]

    loaded = tools.call("library.load", {"name": "dubby", "slot": 5})
    assert loaded["synth"] == doc["synth"]
    # the stored inputs rebuild the same parts the track was made with
    rebuilt = tools.call("synth.parts", {"style": loaded["synth"]["voicing"], "key": loaded["synth"]["key"],
                                         "energy": loaded["synth"]["energy"], "seed": loaded["synth"]["seed"],
                                         "length": loaded["synth"]["length"]})
    assert rebuilt["tracks"] == made["synth"]["tracks"]
    assert tools.call("pattern.get", {"slot": 5})["name"] == "DUBBY"


def test_a_track_without_synth_loads_without_a_synth_block(wired, library):
    tools.call("library.save", {"slot": 0, "name": "plain", "style": "techno", "key": "C minor"})
    assert "synth" not in json.loads((library / "plain.json").read_text())
    assert "synth" not in tools.call("library.load", {"name": "plain", "slot": 5})


def test_library_save_will_not_replace_a_track_unasked(wired, library):
    tools.call("library.save", {"slot": 0, "name": "one", "style": "techno", "key": "C minor"})
    with pytest.raises(ToolError, match="overwrite"):
        tools.call("library.save", {"slot": 0, "name": "one", "style": "dub", "key": "D minor"})
    tools.call("library.save", {"slot": 0, "name": "one", "style": "dub", "key": "D minor",
                                "overwrite": True})
    assert json.loads((library / "one.json").read_text())["_meta"]["style"] == "dub"


@pytest.mark.parametrize("name", ["", "../x", "a/b", "a b"])
def test_library_save_refuses_a_name_that_is_not_a_plain_file_name(wired, library, name):
    with pytest.raises(ToolError, match="name"):
        tools.call("library.save", {"slot": 0, "name": name, "style": "dub", "key": "D minor"})
    assert list(library.iterdir()) == []


# ------------------------------------------------------------------- prompt

def test_the_prompt_states_the_fm1_facts():
    from tr8s import chat
    s = " ".join(chat.SYSTEM.split())
    for phrase in ("never sends clock", "eight voices", "Not every song needs a 303",
                   "synth_parts", "synth_send", "stops the FM-1's transport"):
        assert phrase in s, phrase


# ------------------------------------------------------ review follow-ups

def test_only_the_style_is_required_by_the_send_and_parts_tools():
    """`tool()` pops the optional marker, so shared schema dicts would make later tools demand everything."""
    req = {n: tools.REGISTRY[n]["input_schema"]["required"] for n in ("synth.parts", "synth.send", "synth.export")}
    assert req == {"synth.parts": ["style"], "synth.send": ["style"], "synth.export": ["name", "style"]}
    for n, spec in tools.REGISTRY.items():
        assert not any("_optional" in v for v in spec["input_schema"]["properties"].values()), n


def test_a_late_reply_is_not_taken_for_the_next_request(fm1):
    port = tools.fm1()
    stale = mt.frame(mt.CMDS["TRACK_STEP"], [0, 0, 1, 60, 0, 0, 0, 0, 0, 100, 1, 100, 1, 0, 0])
    port.q.put(stale)                       # an answer to a request that already timed out
    step = mt.step_frame(0, 1, mt.build_parts("dub", seed=1)[0].steps[0])
    port.write_frames([step])
    assert port.q.empty() and len(frames_of(fm1)) == 1


@pytest.mark.parametrize("override", ["x", {"1": "acid"}, {"1": {"shape": "acid"}}, {"1": {"shape": 1, "engine": "TB303"}}])
def test_a_malformed_override_is_a_tool_error_not_a_crash(override):
    with pytest.raises(ToolError, match="override"):
        tools.call("synth.parts", {"style": "dub", "override": override})


def test_a_malformed_override_fails_before_the_tr8s_is_written(wired):
    d, t = wired
    with pytest.raises(ToolError, match="override"):
        tools.call("track.create", {"slot": 0, "style": "dub", "synth": {"override": {"1": {"shape": "acid"}}}})
    assert t.sent == []


def test_an_fm1_failure_shows_in_steps_too(wired, monkeypatch):
    fake_fm1_rtmidi(monkeypatch, FakeFm1(silent=True))
    monkeypatch.setattr(port_mod, "STEP_TIMEOUT", 0.05)
    tools.set_fm1(None)
    r = tools.call("track.create", {"slot": 0, "style": "dub", "seed": 1, "synth": {"project": 0}})
    assert any("FM-1 write FAILED" in s and "nothing was saved" in s for s in r["steps"])
    tools.set_fm1(None)


def test_a_failed_send_drops_the_cached_port_so_a_replug_can_work(monkeypatch):
    fake_fm1_rtmidi(monkeypatch, FakeFm1(silent=True))
    monkeypatch.setattr(port_mod, "STEP_TIMEOUT", 0.05)
    tools.set_fm1(None)
    with pytest.raises(ToolError, match="no reply"):
        tools.call("synth.send", {"style": "dub"})
    from tr8s.tools import _core
    assert _core._fm1 is None
    fake_fm1_rtmidi(monkeypatch)                    # replugged: the next call opens a fresh port and works
    assert tools.call("synth.send", {"style": "dub"})["sent"]["frames"]
    tools.set_fm1(None)


def test_close_closes_the_fm1_even_if_the_tr8s_session_will_not(fm1):
    class Stuck:
        def close(self):
            raise RuntimeError("stuck")
    tools.set_device(Stuck())
    tools.fm1()
    assert fm1._cb is not None                      # the port is open and listening
    with pytest.raises(RuntimeError):
        tools.close()
    from tr8s.tools import _core
    assert _core._session is None and _core._fm1 is None
    assert fm1._cb is None                          # and was closed (callback cancelled)


def test_a_setup_frame_the_fm1_answers_differently_is_reported_not_fatal(monkeypatch):
    fake_fm1_rtmidi(monkeypatch, FakeFm1(clamp=True))
    p = Fm1Port()
    r = p.write_frames(mt.setup_frames(mt.build_parts("dub", seed=1)))
    assert r["differs"] and all("TRACK_PARAM" in d for d in r["differs"])
    p.close()


def test_a_normal_setup_reports_no_differences(fm1):
    assert tools.call("synth.send", {"style": "dub"})["sent"]["differs"] == []


def test_the_default_port_falls_back_to_the_stock_name(monkeypatch):
    fake_fm1_rtmidi(monkeypatch, ports=("TR-8S", "FM-1"))
    p = Fm1Port()
    assert p.name == "FM-1"
    p.close()


def test_felucca_wins_over_the_stock_name_when_both_are_there(monkeypatch):
    fake_fm1_rtmidi(monkeypatch, ports=("FM-1", "Felucca"))
    p = Fm1Port()
    assert p.name == "Felucca"
    p.close()


def test_library_save_refuses_a_synth_block_that_cannot_be_rebuilt(wired, library):
    for bad in ({"voicing": "polka"}, {"length": 20}, {"override": {"0": {"shape": "pad"}}}):
        with pytest.raises(ToolError):
            tools.call("library.save", {"slot": 0, "name": "x", "style": "dub", "key": "D minor", "synth": bad})
    assert not list(library.glob("*.json"))


def test_library_save_drops_the_project_slot(wired, library):
    block = {"target": "minitone", "voicing": "dub", "key": "D minor", "energy": 0.7, "seed": 1,
             "length": 16, "override": {}, "project": 3}
    tools.call("library.save", {"slot": 0, "name": "p", "style": "dub", "key": "D minor", "synth": block})
    assert "project" not in json.loads((library / "p.json").read_text())["synth"]


# ---------------------------------------------------------------- song sections

def test_arrange_with_a_project_sends_the_steps_then_the_song_then_saves(wired, fm1):
    r = tools.call("track.create", {"slot": 0, "style": "dub", "seed": 1, "energy": 0.6,
                                    "synth": {"project": 2, "arrange": True, "bars": 8}})
    assert "synth_error" not in r
    order = cmds(fm1)
    song_at, save_at = order.index(mt.CMDS["SONG"]), order.index(mt.CMDS["PROJECT"])
    assert max(i for i, c in enumerate(order) if c == mt.CMDS["TRACK_STEP"]) < song_at < save_at
    assert order[-1] == mt.CMDS["PROJECT"]
    parts = mt.build_parts("dub", seed=1, energy=0.6)
    want = mt.song_rows(mt.song_sections(parts, 0.6, 8), 2)
    assert fm1.chain == want and fm1.slots[2] == want       # the save captured the chain
    assert r["synth"]["sections"] == mt.song_sections(parts, 0.6, 8)


def test_arrange_with_no_project_builds_the_sections_and_sends_nothing(wired, fm1):
    r = tools.call("track.create", {"slot": 0, "style": "dub", "seed": 1, "synth": {"arrange": True}})
    assert fm1.sent == [] and r["synth"]["sections"]


def test_without_arrange_no_song_is_sent(wired, fm1):
    r = tools.call("track.create", {"slot": 0, "style": "dub", "seed": 1, "synth": {"project": 2}})
    assert mt.CMDS["SONG"] not in cmds(fm1) and "sections" not in r["synth"]


def test_a_busy_fm1_gives_a_readable_error_and_saves_nothing(wired, monkeypatch):
    fake = fake_fm1_rtmidi(monkeypatch, FakeFm1(playing=True))
    monkeypatch.setattr(port_mod, "STEP_TIMEOUT", 0.05)
    port = Fm1Port()
    tools.set_fm1(port)
    try:
        r = tools.call("track.create", {"slot": 0, "style": "dub", "seed": 1,
                                        "synth": {"project": 2, "arrange": True}})
    finally:
        tools.set_fm1(None)
        port.close()
    assert "the FM-1 is playing" in r["synth_error"]
    assert mt.CMDS["PROJECT"] not in cmds(fake)


def test_a_song_needs_a_project_at_the_tool_boundary(fm1):
    from tr8s.tools.synth import push
    with pytest.raises(ToolError, match="needs a project"):
        push(mt.build_parts("dub", seed=1), None, mt.song_sections(mt.build_parts("dub", seed=1)))
    assert fm1.sent == []


def test_the_library_keeps_arrange_and_bars(wired, library):
    block = {"target": "minitone", "voicing": "dub", "key": "D minor", "energy": 0.7, "seed": 1,
             "length": 16, "override": {}, "arrange": True, "bars": 8}
    tools.call("library.save", {"slot": 0, "name": "arr", "style": "dub", "key": "D minor", "synth": block})
    saved = json.loads((library / "arr.json").read_text())["synth"]
    assert saved["arrange"] is True and saved["bars"] == 8


def test_the_library_refuses_an_arrangement_that_cannot_be_built(wired, library):
    block = {"target": "minitone", "voicing": "dub", "key": "D minor", "seed": 1, "length": 16,
             "arrange": True, "bars": 17}
    with pytest.raises(ToolError):
        tools.call("library.save", {"slot": 0, "name": "bad", "style": "dub", "key": "D minor", "synth": block})
    assert not list(library.glob("*.json"))
