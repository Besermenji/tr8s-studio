# FM-1 (Mini-Tone) SysEx protocol: what the studio can write

Written 2026-11-09. **A draft for issue #3: nothing here has been run on the FM-1 yet.** Every row starts as
**[D]** or **[I]** and becomes **[V]** (or "not supported") when `midi/fm1_spike.py` has been run against the
hardware. The log is `fm1-spike.jsonl`; paste the frame that proved each row.

Marks, as in [`PROTOCOL.md`](PROTOCOL.md) and [`RIG-FM1.md`](RIG-FM1.md):

- **[V]** verified on our FM-1, by writing and reading back. *None yet.*
- **[D]** documented by Mini-Tone or Felucca (`web/EDITOR_PROTOCOL.md`, the README, the tests) and **not** seen on
  our hardware. Upstream says it checked commands 1–15, track selection, dumps, step reads and parameter writes
  (27, 29, 30, 31) on hardware. Everything else has host-test coverage only, which "does not establish their
  hardware behavior".
- **[I]** inferred from the source, or the docs disagree.
- **not supported** found missing.

Source: [`web/EDITOR_PROTOCOL.md`](https://github.com/eldontyrellcorp/Mini-Tone/blob/main/web/EDITOR_PROTOCOL.md)
at Mini-Tone 1.0.5, and the firmware in `firmware/src/` (`editor.c`, `midi_uart.c`, `project.c`).

## Framing

```
F0 7D 46 4C <cmd> <args...> F7        7D = non-commercial SysEx ID, 46 4C = "FL"
```

| Item | Encoding | |
|---|---|---|
| reply | exactly one frame per valid request, same header and cmd; unknown commands and wrong fixed lengths get **no** reply | **[D]** |
| data bytes | all 7-bit | **[D]** |
| v14 | `[lo, hi]` holding value + 8192, LSB first: `value = (lo \| hi << 7) − 8192` (so 0 is `00 40`) | **[D]**, matches `fm1_spike.py --selftest` |
| string | ASCII, ended by a 0 byte | **[D]** |
| port | the FM-1's USB-MIDI port, named like `FM-1 MIDI 1` | **[?]** its CoreMIDI name on macOS |

The example frame the upstream doc gives (`LEARN_SET`, `F0 7D 46 4C 4F 00 4A 03 62 F7`) is reproduced byte for
byte by `fm1_spike.py`. That checks our encoder against the doc, not the device against the doc.

## Commands the rig needs

Frames are what `fm1_spike.py` sends (track numbers are 0-based; Mini-Tone has six: 0..5).

| Need | Command | Example frame | Status |
|---|---|---|---|
| Identify, version, engines, track count | `INFO` (1) | `F0 7D 46 4C 01 F7` | **[D]** layout; reply decode in the script is a guess |
| Liveness | `PING` (25) | `F0 7D 46 4C 19 F7` | **[D]** |
| Which track is selected, each track's engine, preset, level, mute | `TRACK` (27) query | `F0 7D 46 4C 1B F7` | **[D]**, hardware-checked upstream |
| Select a track | `TRACK` (27) + track | `F0 7D 46 4C 1B 02 F7` | **[D]**, hardware-checked upstream |
| Read a whole track's parameters | `TRACK_DUMP` (29) | `F0 7D 46 4C 1D 01 F7` | **[D]**, hardware-checked upstream |
| Read a step of any track | `TRACK_STEP` (30) get | `F0 7D 46 4C 1E 01 04 F7` | **[D]**, hardware-checked upstream |
| **Write a step** of any track: notes, accent, slide, vel | `TRACK_STEP` (30) set | `F0 7D 46 4C 1E 01 04 01 3C 00 00 00 00 03 64 F7` | **[D]**; see "Open questions" |
| Read or set a parameter of any track (no selection change) | `TRACK_PARAM` (31) | get `… 1F 01 5E F7`; set `… 1F 01 5E 01 40 F7` | **[D]**, hardware-checked upstream |
| Level and **mute** of a track | `TRACK_MIX` (28) | get `… 1C 01 F7`; set `… 1C 01 64 40 01 F7` | **[D]**, host-tested only |
| Clock source | `GET`/`SET` scope 1, id 2 `G_CLOCK`: 0 INT, 1 USB, 2 TRS | `… 03 01 02 02 40 F7` | **[D]** |
| MIDI IN channel block | `GET`/`SET` scope 1, id 14 `G_ROUTE` (`ROUT`) | `… 03 01 0E 00 40 F7` | **[D]**; the docs disagree, see below |
| Names and ranges of any parameter or enum | `DESC` (5) | `… 05 01 0E F7` | **[D]** |
| Song sections | `SONG` (33) ops 4..7 | query `… 21 04 F7` | **[D]**, host-tested only |
| Save or load a project slot (0..3) | `PROJECT` (9) | save `… 09 01 00 F7`; query `… 09 02 00 F7` | **[D]**; hardware-checked upstream |
| Map a CC to a parameter (16 entries) | `LEARN_GET` (78) / `LEARN_SET` (79) | `… 4F 00 4A 03 62 F7` | **[D]**, host-tested only; INFO advertises it with the tag `43 01 16` |

### What the docs say that the rig depends on

1. **A save stops the transport.** `PROJECT` save, `UP_PUT` and the other flash writes stop the transport first
   (and the device refuses its own saves while it plays: "STOP TO SAVE"). If the transport does not stop within
   100 ms, no flash operation starts. **[D]** For Jam mode this means: audition parts live through `TRACK_STEP`
   (RAM), and `PROJECT` save is a deliberate stop. It cannot be a background autosave.
2. **Clock.** `G_CLOCK` 0 INT / 1 USB / 2 TRS. With 1 or 2 the sequencer follows only *that* port's clock, so a
   stray USB clock is ignored when TRS is pinned. Start restarts from step 0, Stop stops, **500 ms without a
   pulse stops the transport**, and **changing `G_CLOCK` stops it**. **[D]** (Check the 500 ms rule against the
   TR-8S's free-running clock while stopped.)
3. **Song sections are per-track project slots.** A section names the project slot (0..3 = A..D, 4 = "−" silent)
   each track plays, plus repeats 1..16, up to 16 sections. **[D]** So "the pad holds through the break" is a
   track pointing at a slot while another points at "−", not a mute flag. This is what issue #27 (`set.build`)
   has to target.
4. **EUCLID and STOCHASTIC have no parameter ids.** "Not in the protocol: their settings are saved with the
   project (the FUNZ extension) but have no parameter ids yet." **[D]** So the studio cannot write them over
   SysEx today. That is fork patch (c), or they are set by hand on the device.
5. **Writes to a playing song.** During song playback, `TRACK_STEP` writes and `TRACK_PARAM` writes to LEN / DIV /
   SWING / GATE / QNTZ are ignored and return the current value. **[D]**

## Open questions: what the spike must answer

| # | Question | Today | How |
|---|---|---|---|
| 1 | `INFO`: the real reply, and whether `NTRK` is 6 and the engine list has 25 names | **[D]** | `fm1_spike.py info` |
| 2 | `TRACK_STEP` set: the docs give the argument count as 9 bytes (index, n, 4 notes, time, flags, vel) in the table but call the legacy step "8-byte". Which does a *track* write accept, and does the reply echo it? | **[?]** | `step-set` to an unselected track |
| 3 | A step written to a **playing, unselected** track: audible when? this step, the next pass, only after a stop? | **[?]** | write while the TR-8S plays |
| 4 | Do **accent (flag 1) and slide (flag 2)** come through on the TB303 engine, and does a slide need a following note? | **[?]** | listen |
| 5 | `TRACK_PARAM` engine change: is "set the engine" `SET` of `G_ENGSEL` (selected track only) or can any track change engine without selecting it? | **[I]** the doc says `G_ENGSEL` changes the selected track's engine | `track`, `global-set` |
| 6 | **Program Change.** `midi_uart.c` only sizes a Program Change (`Cx Dx: 1 byte`); no handler in `midi_control.c` mentions it. Does the FM-1 react at all? | **[I]** no: fork patch (b) is needed | `listen`, send a PC from the TR-8S |
| 7 | **Mute from MIDI.** Is there a fixed CC? Can `LEARN_SET` map a CC to a track's mute parameter (`P_MUTE`), and do the EMP16 pads (note on/off, or CC 0/127) drive it? | **[?]** | `learn-get`, then learn on the device |
| 8 | **MIDI IN blocks.** `G_ROUTE` enum values. The doc says CH1-6, SEL, CH5-10, CH9-14, CH13-16 (Mini-Tone 1.0.x); another paragraph and the older README text say CH1-4, SEL, CH5-8, CH9-12, CH13-16, and that "channels 5..16 are ignored since 1.0.3". Which is the build's? CH5-10 and CH9-14 **include channel 10**, the TR-8S's. | **[I]** CH1-6 for tracks 1–6 | `DESC` id 14, then try each |
| 9 | Is `G_CLOCK` / `G_ROUTE` stored **per project** or device-wide, and does loading a project change them? (`G_ROUTE` is described as part of the project in the README, and as settings in the editor docs.) | **[?]** | set, save, load another slot, read back |
| 10 | `PROJECT` save/load: survives a power cycle? How long is the reply? does a save with the transport running stop it and reply? | **[D]** | `project-save`, power-cycle |
| 11 | `SONG` set of sections A–D: accepted while the transport runs? rc values? | **[D]** | `song` then a set |
| 12 | Clock pinned to TRS: is a clock on USB then ignored? | **[D]** | set `G_CLOCK` 2, send a USB clock from a test source (not the studio) |
| 13 | CC map: do 74, 71, 75, 5, 91, 93, 94, 7 act on **every engine** (TB303, SWARM, FMTONE, WAVETONE, DUALVCO, ANALOG4)? | **[D]** | `track-dump` before/after |
| 14 | Voice budget: eight voices across six tracks, how are they shared, and what is dropped first? | **[?]** | listen; README |

## Mapping to the patch list (issue #4)

| Patch | Needed? | Why |
|---|---|---|
| (a) UC25HQ80 | **done** | in the fork's first build; see [`FM1-BUILD.md`](FM1-BUILD.md) |
| (b) Program Change follow | **[I] yes** | no handler found; question 6 |
| (c) Whole-project writes | **partly** | steps, track parameters, `PROJECT` save and song sections exist; generator settings (EUCLID, STOCHASTIC) do not |
| (d) Mute CC | **[?]** | question 7 |

This table is final only when the spike has run.
