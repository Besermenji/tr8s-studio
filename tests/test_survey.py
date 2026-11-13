"""The survey's analysis is pure: it reads blobs, never the machine."""

from fake import load_fixture_pattern
from tr8s import survey
from tr8s.pattern import Pattern


def test_a_real_pattern_is_analysed():
    r = survey.analyse_pattern(load_fixture_pattern(), slot=0)
    assert r["panel"] == "1-01" and r["name"] == "Sakura" and r["tempo"] == 190.0
    assert set(r["scores"]) == {"hard", "dnb", "light", "acid", "poly"}
    assert all(0 <= v <= 1 for v in r["scores"].values())


def test_odd_cycles_and_four_on_the_floor_are_found():
    p = Pattern.from_bytes(load_fixture_pattern())
    for v in "ABCDEFGH":
        p.clear_variation(v)
        p.set_steps(v, "BD", "x...x...x...x...")
        p.set_steps(v, "RS", "x..x..x..x..x..x")        # a 3-cycle against the 16
    p.tempo = 140
    r = survey.analyse_pattern(p.to_bytes())
    assert r["uses_all_eight"]
    assert "RS/3" in r["poly_tracks"]
    assert r["detail"]["A"]["four_floor"]
    assert r["scores"]["hard"] > r["scores"]["dnb"]


def test_misaligned_files_are_reported_not_skipped(tmp_path):
    good = load_fixture_pattern()
    (tmp_path / "pattern_000.bin").write_bytes(good)
    (tmp_path / "pattern_001.bin").write_bytes(good[2000:] + bytes(2000))
    rows = survey.analyse_dir(tmp_path)
    assert rows[0]["name"] == "Sakura"
    assert "misaligned" in rows[1]["error"]
    md = survey.report(rows)
    assert "1 refused" in md and "## Top poly" in md


def test_an_empty_slot_is_listed_as_empty_and_never_scored():
    p = Pattern.from_bytes(load_fixture_pattern())
    for blk in range(10):
        p.clear_variation(blk)
    r = survey.analyse_pattern(p.to_bytes(), slot=127)
    assert r["empty"] and "scores" not in r
    md = survey.report([r, survey.analyse_pattern(load_fixture_pattern(), slot=0)])
    assert "## Empty slots" in md and "8-16" in md.split("## Empty slots")[1]
