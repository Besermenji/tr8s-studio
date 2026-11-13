**Title:** Rig wiring and hardware verification (TR-8S, FM-1)

**Labels:** `rig`, `hardware`, `docs`

## Context

[`docs/RIG-FM1.md`](../RIG-FM1.md) specifies the wiring, the MIDI channels,
the clock rules, gain staging and sidechain. Several of those
facts are marked **[?]**: likely, but not yet seen on our hardware. Code in
later issues depends on some of them, so they get checked first and the doc is
updated from **[?]** to **[V]** (or corrected).

Wiring, the same in Jam and Live mode:

```
TR-8S MIDI OUT ──► (TRS adapter) ──► FM-1 TRS MIDI IN
FM-1 audio out ──► TR-8S EXT IN L/R
```

## Tasks

**MIDI**

- [x] TR-8S MIDI OUT → FM-1 TRS MIDI IN: with `CLK` on TRS the FM-1 follows the
      TR-8S's clock (2026-11-12).
- [ ] FM-1 TRS MIDI type, A or B: note which adapter works, and confirm notes
      arrive.
- [ ] TR-8S: find and record the name of the sync-out setting on firmware 2.51.
- [ ] Confirm the TR-8S's pattern channel (default 10) and that nothing on
      channel 10 reaches the FM-1's tracks once the FM-1 is on a channel block
      starting at 1. This is done after issue 02's flash.

**Clock acceptance test** (after 02's flash)

- [ ] Record the TR-8S main out for five minutes at 130 BPM with a 16th-note
      TB303 line on the FM-1. Check the 303 against the kick at the start and
      the end for drift or audible wobble.

**Audio**

- [ ] FM-1 out → TR-8S EXT IN L/R, EXT IN type stereo.
- [ ] Gain: FM-1 master about 70%. Set the TR-8S EXT IN level so the loudest
      part (a pad chord, or a TB303 at full resonance with accents) peaks about
      6 dB under the kick. Write down the settings.
- [ ] Sidechain: set up the TR-8S kit's sidechain on EXT IN triggered by BD.
      Record the real parameter names and the values that work for a 303 and
      for a pad.

**FM-1 safety, before 02 flashes anything**

- [x] Read the FM-1's flash chip ID: Puya or UC25HQ80. **Puya P25Q80 (0x856014)**, 2026-11-09. Stock V15 does not
      report it. Our Mini-Tone fork does (`fm1_install.py --info`, after issue
      02's flash), and accepts both chips, so this no longer gates the flash.
      Record the ID in RIG-FM1.md.
- [ ] Back up the FM-1's projects and confirm it is on stock V15.

## Acceptance

- Every **[?]** in RIG-FM1.md that this issue covers is now **[V]** with what
  was seen, or corrected.
- The clock test results, with dates, are written into RIG-FM1.md.
- A photo or diagram of the actual wiring is in the doc.

## Depends on

Nothing for the MIDI and audio checks. The clock test and the channel-10 check
need issue 02's flash.

## Pipeline

Hands on hardware. Opus reviews the doc changes.
