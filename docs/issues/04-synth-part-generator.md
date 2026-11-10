**Title:** Multi-voice synth part generator and the Mini-Tone encoder

**Labels:** `rig`, `music`, `fm-1`

## Context

The FM-1 carries the synths, and **not every song is acid**: a dub track wants
a stab and a pad, a hypnotic one a drone and an arp. The studio already
generates lines in key in `src/tr8s/lines.py`: `bass`, `acid`, `stab` and
`arp`, all seeded. But they are shaped for the TR-8S:

- `_finish` clamps every note to Coarse Tune's ±24 semitones from the tone's
  root. The FM-1 has no such limit.
- `acid()` returns accents but no slides.
- There is no pad, drone or lead.

This issue adds a `src/tr8s/synth/` package that generates FM-1 parts and
encodes them for Mini-Tone. It is pure logic with no device access, so it is
tested entirely offline.

See [`docs/RIG-FM1.md`](../RIG-FM1.md), section 1.

## Tasks

**`synth/line.py`**

- [ ] A step model: `SynthStep(notes, force, slide, tie, ratchet)`. `notes` is
      a list so chords work; `force` follows Mini-Tone's GHOST / SOFT / NORM /
      HARD / ACC.
- [ ] Extract a shared core from `lines.py`, so `bass`, `acid`, `stab` and
      `arp` generate the same notes for both targets. The TR-8S path keeps its
      clamp and **its output does not change**. The existing `tests/test_lines.py`
      must pass untouched.
- [ ] Acid gains slides: only between two sounding notes, never into a rest,
      with density tied to energy.
- [ ] New shapes: `pad` and `drone` (long tied chords from the scale, moving
      slowly) and `lead`.
- [ ] Options: 16, 32 or 64 steps; an optional Euclidean rhythm from
      `style.euclid` (pulses, rotation).
- [ ] Everything seeded: the same arguments and seed give the same part.

**`synth/minitone.py`**

- [ ] The profile: engine names, track count, channel block, the CC map from
      RIG-FM1.md, and the 8-voice budget.
- [ ] A voice-budget check: warn when the parts together need more than eight
      voices (count a pad chord's notes).
- [ ] `VOICING`, a table in the style of `DEFAULT_LINE` in `tools/_core.py`
      that maps each style to its FM-1 parts and engines. Start from the table
      in RIG-FM1.md. A song with no TB303 is an ordinary case.
- [ ] `to_mid(parts)`: a multi-channel Standard MIDI File, one track per FM-1
      channel. Accents are velocity, slides are overlapping notes. This part can
      start before issue 02 finishes.
- [ ] `to_syx(parts)`: the `TRACK_STEP` frames that write every step of every
      part, in the form verified on hardware in `docs/FM1-PROTOCOL.md`. RAM
      only: saving (`PROJECT`) stops the transport, so `save_frame` is separate.
      `setup_frames` adds the engine and track length (documented, not yet run).
- [ ] EUCLID and STOCHASTIC generator settings: **blocked on issue 03 (c)**.
      They have no SysEx parameter ids, so they cannot be written until the
      fork adds them. The Euclidean option exists on the generator side.

**Tests**

- [ ] Determinism per shape and seed.
- [ ] Slides never point into a rest, and parts stay in key.
- [ ] The voice-budget warning fires when it should.
- [ ] `to_mid` and `to_syx` byte-exact against hand-checked frames.
- [ ] The TR-8S line output is unchanged.

## Acceptance

- `pytest tests/` passes, old tests untouched.
- A `.mid` from `to_mid`, played into the FM-1, sounds like the part described.
- After issue 02, a `.syx` from `to_syx` sent to the FM-1 produces the same
  part, saved.

## Depends on

Issue 02 for `to_syx` only. Everything else can start now.

## Pipeline

Sonnet implements; Opus reviews. No hardware needed until the final `.syx`
check.
