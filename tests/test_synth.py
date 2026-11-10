"""
Tests for the FM-1 part generator and the Mini-Tone encoders.

Musical properties, as in test_lines.py (in key, never silent, shaped like the
thing), plus byte-exact frames: the TRACK_STEP writes are the ones the FM-1
accepted and read back in fm1-spike.jsonl.
"""

import pytest

from tr8s import lines
from tr8s.kitbuild import scale_pitches
from tr8s.melody import COARSE_MAX, midi_to_note, note_to_midi
from tr8s.style import euclid
from tr8s.synth import SYNTH_SHAPES, SynthStep, generate
from tr8s.synth import minitone as mt

KEYS = ["C minor", "F# phrygian", "A dorian", "D# minor", "G major"]


def sounding_midi(part):
    return [m for s in part.steps for m in s.notes]


# ----------------------------------------------------------------- the lines

@pytest.mark.parametrize("shape", SYNTH_SHAPES)
def test_same_arguments_and_seed_give_the_same_part(shape):
    for seed in range(8):
        a = generate(shape, key="A dorian", energy=0.7, seed=seed, length=32)
        b = generate(shape, key="A dorian", energy=0.7, seed=seed, length=32)
        assert a == b


@pytest.mark.parametrize("shape", SYNTH_SHAPES)
@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("length", [16, 64])
def test_every_note_belongs_to_the_scale(shape, key, length):
    _, scale = scale_pitches(key)
    for seed in range(6):
        for energy in (0.1, 0.9):
            p = generate(shape, key=key, energy=energy, seed=seed, length=length)
            assert all(m % 12 in scale for m in sounding_midi(p)), (shape, key, seed)


@pytest.mark.parametrize("shape", SYNTH_SHAPES)
def test_a_part_is_never_silent(shape):
    for seed in range(20):
        p = generate(shape, key="C minor", energy=0.05, seed=seed)
        assert sounding_midi(p), f"{shape} seed {seed}"


@pytest.mark.parametrize("shape", ["bass", "acid", "stab", "arp"])
def test_note_shapes_match_the_tr8s_lines_when_nothing_needs_clamping(shape):
    """One core, two targets: the same seed gives the same notes."""
    for seed in range(10):
        for key in KEYS:
            ref = lines.generate(shape, key=key, energy=0.7, seed=seed, root="C2")
            assert not ref["warnings"]
            p = generate(shape, key=key, energy=0.7, seed=seed, root="C2")
            got = [midi_to_note(s.notes[0]) if s.notes else "." for s in p.steps]
            assert got == ref["notes"].split()


def test_the_synth_path_does_not_clamp_to_coarse_tune():
    ref = lines.bassline(key="C minor", energy=0.6, seed=2, root="C2", octave_offset=4)
    assert ref["warnings"]                      # the TR-8S path had to move notes
    p = generate("bass", key="C minor", energy=0.6, seed=2, root="C2", octave_offset=4)
    top = max(sounding_midi(p)) - note_to_midi("C2")
    assert top > COARSE_MAX


def test_acid_accents_become_acc_force():
    ref = lines.acid(key="C minor", energy=0.8, seed=5, root="C2")
    p = generate("acid", key="C minor", energy=0.8, seed=5, root="C2")
    assert [i for i, s in enumerate(p.steps) if s.force == "ACC"] == ref["accents"]


# -------------------------------------------------------------------- slides

def test_slides_only_go_between_two_sounding_steps():
    for seed in range(40):
        p = generate("acid", key="F# phrygian", energy=0.9, seed=seed, length=32)
        n = p.length
        for i, s in enumerate(p.steps):
            if s.slide:
                assert s.sounding and p.steps[(i + 1) % n].sounding, (seed, i)


def test_only_acid_slides():
    for shape in SYNTH_SHAPES:
        if shape != "acid":
            p = generate(shape, seed=1, length=32)
            assert not any(s.slide for s in p.steps)


def test_acid_slides_more_at_high_energy():
    def total(e):
        return sum(s.slide for seed in range(60)
                   for s in generate("acid", energy=e, seed=seed).steps)
    assert total(1.0) > total(0.1)


def test_slides_do_not_change_the_notes():
    """Slides are drawn from their own stream; the line is the 303 line."""
    for seed in range(10):
        p = generate("acid", key="C minor", energy=0.7, seed=seed, root="C2")
        ref = lines.acid(key="C minor", energy=0.7, seed=seed, root="C2")
        assert " ".join(midi_to_note(s.notes[0]) if s.notes else "." for s in p.steps) == ref["notes"]


# ------------------------------------------------------- pads, drones, leads

@pytest.mark.parametrize("shape", ["pad", "drone"])
def test_a_tie_holds_the_same_notes_as_the_step_before(shape):
    for seed in range(10):
        p = generate(shape, key="D# minor", seed=seed, length=64)
        assert not p.steps[0].tie
        for i, s in enumerate(p.steps):
            if s.tie:
                assert s.notes == p.steps[i - 1].notes


def test_a_pad_is_a_chord_and_a_drone_a_pair():
    assert all(len(s.notes) >= 2 for s in generate("pad", seed=1).steps)
    assert all(len(s.notes) == 2 for s in generate("drone", seed=1).steps)


def test_a_drone_holds_for_a_whole_pattern():
    p = generate("drone", seed=4, length=16)
    assert sum(1 for s in p.steps if not s.tie) == 1


def test_a_lead_has_a_rest_phrase_every_bar():
    for seed in range(10):
        p = generate("lead", energy=0.9, seed=seed, length=32)
        for bar in (p.steps[:16], p.steps[16:]):
            assert any(all(not s.sounding for s in bar[i:i + 4]) for i in (4, 12))


# ------------------------------------------------------------ lengths, rhythm

@pytest.mark.parametrize("length", [16, 32, 64])
@pytest.mark.parametrize("shape", SYNTH_SHAPES)
def test_lengths(shape, length):
    assert generate(shape, seed=2, length=length).length == length


def test_a_bad_length_or_shape_is_refused():
    with pytest.raises(ValueError):
        generate("acid", length=24)
    with pytest.raises(ValueError):
        generate("kazoo")


def _notes(steps):
    return [(s.notes, s.force) for s in steps]


def test_bar_one_is_the_16_step_line():
    one = generate("acid", energy=0.8, seed=3)
    long = generate("acid", energy=0.8, seed=3, length=64)
    assert _notes(long.steps[:16]) == _notes(one.steps)


def test_the_last_bar_turns_around_on_bar_ones_first_half():
    for seed in range(10):
        p = generate("acid", energy=0.8, seed=seed, length=64)
        assert _notes(p.steps[48:56]) == _notes(p.steps[:8])


def test_the_middle_bars_are_new_material():
    differing = sum(_notes(generate("acid", energy=0.8, seed=sd, length=64).steps[16:32])
                    != _notes(generate("acid", energy=0.8, seed=sd, length=64).steps[:16])
                    for sd in range(10))
    assert differing >= 8


@pytest.mark.parametrize("shape", ["bass", "acid", "stab", "arp"])
def test_a_euclidean_rhythm_places_the_hits(shape):
    for pulses, rot in ((5, 2), (7, 0), (3, 4)):
        p = generate(shape, seed=1, length=32, rhythm=(pulses, rot))
        hits = euclid(pulses, 16, rot)
        for bar in range(2):
            played = [i for i, s in enumerate(p.steps[bar * 16:bar * 16 + 16]) if s.sounding]
            assert played == hits


def test_a_rhythm_with_no_pulses_is_refused():
    with pytest.raises(ValueError):
        generate("acid", rhythm=(0, 0))


# ------------------------------------------------------------------ profile

def test_every_style_has_a_voicing_within_the_voice_budget():
    for style in mt.VOICING:
        parts = mt.build_parts(style, key="C minor", seed=1, length=32)
        assert parts and not mt.voice_budget(parts), style


def test_a_song_with_no_303_is_ordinary():
    parts = mt.build_parts("dub", seed=1)
    assert all(p.engine != "TB303" for p in parts.values())


def test_overrides_replace_and_drop_tracks():
    parts = mt.build_parts("dub", seed=1, overrides={1: ("acid", "TB303"), 2: None})
    assert parts[1].engine == "TB303" and 2 not in parts


def test_build_parts_refuses_unknown_things():
    with pytest.raises(ValueError):
        mt.build_parts("polka")
    with pytest.raises(ValueError):
        mt.build_parts("dub", overrides={0: ("pad", "KAZOO")})
    with pytest.raises(ValueError):
        mt.build_parts("dub", overrides={6: ("pad", "SWARM")})


def chord_part(engine, notes):
    from tr8s.synth import Part
    return Part("pad", "C minor", "C3", 0, 0.5, [SynthStep(notes)] * 16, engine)


FOUR = (60, 64, 67, 71)


def test_voice_budget_fires_over_eight_and_not_at_eight():
    assert not mt.voice_budget([chord_part("SWARM", FOUR), chord_part("FMTONE", FOUR)])
    assert mt.voice_budget([chord_part("SWARM", FOUR), chord_part("FMTONE", FOUR),
                            chord_part("DUALVCO", (36,))])
    assert mt.voice_budget([chord_part("SWARM", FOUR)] * 3)


def test_a_mono_engine_counts_one_voice():
    # 4 + 3 + 1 = 8; counted by notes, the 303's chord would make it 10
    assert not mt.voice_budget([chord_part("SWARM", FOUR), chord_part("FMTONE", (60, 64, 67)),
                                chord_part("TB303", (36, 40, 43))])


# ------------------------------------------------------------------- SysEx

def test_step_frames_match_the_writes_the_fm1_accepted():
    """Frames from fm1-spike.jsonl: written to track 1, read back identically."""
    assert mt.step_frame(0, 0, SynthStep((0x30,), "ACC")) == \
        bytes.fromhex("F07D464C1E0000013000000000" "01" "7F" "F7")
    assert mt.step_frame(0, 8, SynthStep((0x37,), "NORM", slide=True)) == \
        bytes.fromhex("F07D464C1E0008013700000000" "02" "64" "F7")
    assert mt.step_frame(0, 12, SynthStep((0x30,), "NORM")) == \
        bytes.fromhex("F07D464C1E000C013000000000" "00" "64" "F7")


def test_a_rest_and_a_tie_frame():
    assert mt.step_frame(1, 3, SynthStep()) == bytes.fromhex("F07D464C1E0103" "00" "00000000" "02" "00" "64" "F7")
    tie = mt.step_frame(1, 3, SynthStep((60, 64), tie=True))
    assert tie[7] == 2 and tie[12] == 1                                   # n, time TIE


def test_to_syx_is_one_frame_per_step_and_all_data_is_seven_bit():
    parts = mt.build_parts("hypnotic", seed=2, length=32)
    frames = mt.to_syx(parts)
    assert len(frames) == 64
    for f in frames:
        assert f[0] == 0xF0 and f[-1] == 0xF7
        assert all(b < 0x80 for b in f[1:-1])
        assert not any(0xF8 <= b <= 0xFC for b in f)
    assert {f[5] for f in frames} == {0, 3}                  # tracks 0 and 3


def test_frames_refuse_non_data_bytes():
    for bad in (0x80, 0xF8, 0xFA, 0xFC, -1, 300):
        with pytest.raises(mt.Fm1FrameError):
            mt.frame(1, [bad])
    with pytest.raises(mt.Fm1FrameError):
        mt.step_frame(0, 0, SynthStep((200,)))
    with pytest.raises(mt.Fm1FrameError):
        mt.step_frame(0, 0, SynthStep((60, 61, 62, 63, 64)))


def test_v14_round_trips_and_matches_the_docs():
    assert mt.v14(0) == [0, 64]
    assert all(mt.from_v14(*mt.v14(v)) == v for v in range(-8192, 8192))


def test_the_docs_example_frame():
    assert mt.frame(79, [0, 74, 3, 98]) == bytes.fromhex("F07D464C4F004A0362F7")


def test_setup_and_save_frames():
    parts = {2: generate("pad", seed=1, engine="SWARM", length=32)}
    f = mt.setup_frames(parts)
    assert f[0] == mt.frame(27, [2])
    assert f[1] == mt.frame(3, [1, 20, *mt.v14(14)])
    assert f[2] == mt.frame(31, [2, 29, *mt.v14(32)])
    assert mt.save_frame(2) == bytes.fromhex("F07D464C0901" "02" "F7")
    with pytest.raises(mt.Fm1FrameError):
        mt.save_frame(4)


def test_to_syx_never_includes_a_save():
    frames = mt.to_syx(mt.build_parts("acid", seed=1))
    assert all(f[4] == 30 for f in frames)


def test_split_syx_round_trips():
    frames = mt.to_syx(mt.build_parts("acid", seed=1))
    assert mt.split_syx(b"".join(frames)) == frames


# ---------------------------------------------------------------- MIDI file

def part_of(steps, shape="bass"):
    from tr8s.synth import Part
    return Part(shape, "C minor", "C3", 0, 0.5, steps, None)


def test_to_mid_header_and_first_events():
    steps = [SynthStep((60,)), SynthStep(), SynthStep((64,), slide=True), SynthStep((67,), "ACC")]
    data = mt.to_mid({0: part_of(steps)}, bpm=120)
    assert data[:14] == bytes.fromhex("4D546864" "00000006" "0001" "0002" "0060")
    tempo_len = int.from_bytes(data[18:22], "big")
    track = data[22 + tempo_len:]
    assert track[:4] == b"MTrk"
    body = track[8:]
    # name meta, C3 on at 0 (vel 100), off 2 ticks early at 22, E3 on at 48
    assert body[:8] == bytes.fromhex("00FF0304") + b"bass"
    assert body[8:20] == bytes.fromhex("00903C64" "16803C00" "1A904064")
    assert 0x7F in body                                   # the accent's velocity


def test_to_mid_tempo_is_microseconds_per_beat():
    data = mt.to_mid({0: part_of([SynthStep((60,))])}, bpm=120)
    assert bytes.fromhex("FF5103" "07A120") in data       # 500000 us


def test_to_mid_has_one_track_per_part_plus_tempo_on_the_right_channels():
    parts = mt.build_parts("peak", seed=1)
    data = mt.to_mid(parts)
    assert int.from_bytes(data[10:12], "big") == len(parts) + 1
    assert data.count(b"MTrk") == len(parts) + 1
    # peak = tracks 0 (acid) and 3 (lead) -> channels 1 and 4 (status 90 / 93)
    assert sorted(note_on_statuses(data)) == [[0x90], [0x93]]


def note_on_statuses(data):
    """Per part track, the distinct note-on status bytes, by walking the events."""
    out, pos = [], 14
    while pos < len(data):
        n = int.from_bytes(data[pos + 4:pos + 8], "big")
        body, pos = data[pos + 8:pos + 8 + n], pos + 8 + n
        seen, i = set(), 0
        while i < len(body):
            while body[i] & 0x80:                       # delta time: continues while bit 7 is set
                i += 1
            i += 1
            if body[i] == 0xFF:
                i += 3 + body[i + 2] if body[i + 1] != 0x2F else 3
                continue
            if body[i] & 0xF0 == 0x90:
                seen.add(body[i])
            i += 3
        if seen:
            out.append(sorted(seen))
    return out


def test_to_mid_loops_the_shorter_part():
    a = generate("acid", seed=1, length=16)
    b = generate("pad", seed=1, length=32)
    data = mt.to_mid({0: a, 2: b})
    assert data.count(b"MTrk") == 3


# ------------------------------------------------- regressions from the review

def midi_events(data, track_index=1):
    """(absolute tick, kind, note) of a track's note events, and the tick its end-of-track sits at."""
    pos, k = 14, 0
    while k < track_index:
        pos += 8 + int.from_bytes(data[pos + 4:pos + 8], "big")
        k += 1
    n = int.from_bytes(data[pos + 4:pos + 8], "big")
    body = data[pos + 8:pos + 8 + n]
    i, tick, out = 0, 0, []
    while i < len(body):
        d = 0
        while body[i] & 0x80:
            d = d << 7 | body[i] & 0x7F
            i += 1
        d = d << 7 | body[i]
        i += 1
        tick += d
        if body[i] == 0xFF:
            if body[i + 1] == 0x2F:
                return out, tick
            i += 3 + body[i + 2]
            continue
        kind = {0x90: "on", 0x80: "off"}[body[i] & 0xF0]
        out.append((tick, kind, body[i + 1]))
        i += 3
    raise AssertionError("no end-of-track")


def test_a_chord_whose_third_is_c_keeps_its_third():
    """C is pitch class 0; an `or` chain reads it as a missing degree."""
    _, c_minor = scale_pitches("C minor")
    assert 0 in mt_chord(8, c_minor)                       # Ab chord: Ab C Eb, not Ab Bb Eb
    tonic, a_minor = scale_pitches("A minor")
    assert mt_chord(tonic, a_minor) == [9, 0, 4]           # A C E, not Asus4


def mt_chord(pc, scale):
    from tr8s.synth.line import _chord_pcs
    return _chord_pcs(pc, scale)


def test_the_tr8s_arp_and_stab_find_a_third_that_is_c():
    arp = lines.generate("arp", key="A minor", energy=0.8, seed=1, root="C2")["notes"].split()
    assert any(n.startswith("C") and n != "." for n in arp)           # the minor third is there
    stabs = {n for sd in range(10)
             for n in lines.generate("stab", key="A minor", energy=0.8, seed=sd, root="C2")["notes"].split()}
    assert any(n.startswith("C") for n in stabs)
    f_minor_fifth = lines.generate("arp", key="F minor", energy=0.8, seed=1, root="C2")["notes"].split()
    assert any(n.startswith("C") for n in f_minor_fifth)              # F minor's fifth is C


def test_a_slide_onto_the_same_pitch_does_not_cut_the_new_note():
    steps = [SynthStep((48,), slide=True), SynthStep((48,)), SynthStep(), SynthStep()]
    events, _ = midi_events(mt.to_mid({0: part_of(steps)}))
    assert (24 - 2, "off", 48) in events or (22, "off", 48) in events
    assert (24, "on", 48) in events
    # the second note lasts almost a full step, not the 6 ticks the slide's overlap left it
    off2 = [t for t, k, n in events if k == "off" and t > 24][0]
    assert off2 == 46


def test_a_slide_onto_a_different_pitch_still_overlaps():
    steps = [SynthStep((48,), slide=True), SynthStep((50,)), SynthStep(), SynthStep()]
    events, _ = midi_events(mt.to_mid({0: part_of(steps)}))
    assert (24 + mt.SLIDE_OVERLAP, "off", 48) in events


def test_no_note_off_follows_a_later_note_on_of_the_same_pitch():
    for seed in range(30):
        part = generate("acid", seed=seed, energy=0.95, length=32)
        events, _ = midi_events(mt.to_mid({0: part}))
        for tick, kind, note in events:
            if kind == "on":
                later = [t for t, k, n in events if k == "on" and n == note and t > tick]
                off = min(t for t, k, n in events if k == "off" and n == note and t > tick)
                assert not later or off <= later[0], (seed, note, tick)


def test_a_slide_off_the_last_step_does_not_outrun_the_tempo_track():
    # step 0 sounds a different note, so the slide off step 15 would overlap it past the loop end
    steps = [SynthStep((50,))] + [SynthStep()] * 14 + [SynthStep((48,), slide=True)]
    part = part_of(steps)
    data = mt.to_mid({0: part})
    events, end = midi_events(data)
    tempo_events, tempo_end = midi_events(data, 0)
    assert end == tempo_end == 16 * mt.STEP_TICKS
    assert max(t for t, _, _ in events) <= end


def test_a_review_proof_seed_slides_into_the_loop_without_overrunning():
    for seed in range(30):
        part = generate("acid", seed=seed, energy=1.0, length=16)
        data = mt.to_mid({0: part})
        assert midi_events(data)[1] == midi_events(data, 0)[1]


def test_send_syx_refuses_clock_channel_and_save_frames():
    ok = [*mt.to_syx({0: generate("pad", seed=1, engine="SWARM")})[:2], *mt.setup_frames(
        {0: generate("pad", seed=1, engine="SWARM", length=32)})]
    for f in ok:
        mt.check_edit_frame(f)
    clk = mt.frame(mt.CMDS["SET"], [1, 2, *mt.v14(2)])                # CLK = TRS: stops the transport
    rout = mt.frame(mt.CMDS["SET"], [1, 14, *mt.v14(2)])              # ROUT = CH5-10: channel 10
    for bad in (clk, rout, mt.save_frame(0), mt.frame(mt.CMDS["TRACK_STEP"], [6, 0, 0, 0, 0, 0, 0, 2, 0, 100]),
                mt.frame(mt.CMDS["TRACK_PARAM"], [0, 30, *mt.v14(1)]), mt.frame(mt.CMDS["TRACK"], [9]),
                mt.frame(mt.CMDS["TRACK_STEP"], [0, 0])):
        with pytest.raises(mt.Fm1FrameError):
            mt.check_edit_frame(bad)


def test_split_syx_refuses_frames_that_are_not_ours_or_are_cut_off():
    good = mt.step_frame(0, 0, SynthStep((60,)))
    for bad in (bytes.fromhex("F0411000" "1E" "F7"),                  # another device's SysEx
                b"\xF0\xF7",                                          # nothing in it
                good[:-1],                                            # no closing F7
                good[:5] + b"\x90" + good[6:],                        # an 8-bit byte inside
                good + b"\x00"):                                      # junk after a frame
        with pytest.raises(mt.Fm1FrameError):
            mt.split_syx(bad)
    assert mt.split_syx(good + good) == [good, good]


def test_a_bar_is_not_the_next_seeds_bar():
    """Bar 2 of seed s used to be bar 1 of seed s + 1: re-rolling gave back what you had heard."""
    for seed in range(10):
        long = generate("acid", energy=0.8, seed=seed, length=32)
        nxt = generate("acid", energy=0.8, seed=seed + 1)
        assert _notes(long.steps[16:24]) != _notes(nxt.steps[:8]) or not any(s.sounding for s in nxt.steps[:8])


def test_the_same_shape_on_two_tracks_is_two_different_parts():
    parts = mt.build_parts("dub", seed=3, length=32,
                           overrides={0: ("bass", "DUALVCO"), 1: ("bass", "DUALVCO"), 2: None})
    assert parts[0].steps != parts[1].steps


def test_track_zero_plays_the_seed_as_given():
    parts = mt.build_parts("acid", key="C minor", energy=0.7, seed=11)
    assert parts[0].steps == generate("acid", key="C minor", energy=0.7, seed=11).steps


def test_an_arp_on_a_euclidean_rhythm_accents_the_beat_not_the_old_position():
    for pulses, rot in ((5, 0), (7, 2), (3, 1)):
        p = generate("arp", seed=1, rhythm=(pulses, rot))
        for i, s in enumerate(p.steps):
            if s.sounding:
                assert s.force == ("HARD" if i % 4 == 0 else "NORM"), (pulses, rot, i)


def test_an_acid_accent_moves_with_its_note_on_a_rhythm():
    p = generate("acid", energy=0.9, seed=2, rhythm=(7, 0))
    assert any(s.force == "ACC" for s in p.steps)


@pytest.mark.parametrize("shape", ["pad", "drone", "lead"])
def test_a_rhythm_is_refused_for_shapes_it_means_nothing_to(shape):
    with pytest.raises(ValueError):
        generate(shape, rhythm=(5, 0))


def test_the_spike_and_the_studio_share_one_command_table():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "midi" / "fm1_spike.py"
    spec = importlib.util.spec_from_file_location("fm1_spike_under_test", path)
    spike = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spike)
    assert spike.CMDS is mt.CMDS and spike.frame is mt.frame and spike.split_syx is mt.split_syx
