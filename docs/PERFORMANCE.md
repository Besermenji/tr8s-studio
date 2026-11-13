# Playing a set on the TR-8S

How to play a set from this machine alone, no laptop: the panel moves, what each one does to a
room, and what to do in each part of a set. The cue sheet for a set (`library/sets/NAME.md`)
says *what* to play when; this says *how*.

Every panel move here is from Roland's TR-8S Owner's Manual (2018) or the Reference Manual,
and the page is given. A move marked **[check]** is in the manual but its exact behaviour
while playing has not been confirmed on this machine (firmware 3.00); try it in rehearsal
before relying on it.

## The set on the machine

- **Bank 8 is the set.** Hold `PTN SELECT`, press pad `8`, release; pads 1-16 are now the 16
  patterns in order (manual p.9).
- **Each pattern brings its own kit and tempo** when `UTILITY → GENERAL` has `KitSel = PTN`
  and `TempoSrc = PTN`. Otherwise the panel's kit and tempo win and the stored ones are ignored
  (docs/PROTOCOL.md, "PTN vs SYSTEM").
- **Fader colours mean the same thing in every kit:** red kick, orange snare/clap, yellow acid,
  the two greens the odd-cycle voices, light blue a tom or perc voice, blue closed hat, dark
  blue open hat, purple cymbals, pink rim, light pink effects. Reach for a colour, not a name.

## Moving between patterns

- **Press the next pad during the last bar.** The pad blinks, and the pattern switches when
  playback returns to the first step (p.9), so pressing early still lands at the end of the
  pattern.
- **A pattern's length is its variation chain.** With two variations chained, "the end" is the
  end of the chain. Before a switch, drop back to a single variation so you know when it lands.
- **Keep the kick across a switch.** The new pattern's first beat
  then reads as the next 1, not as a change.
- **Pad pairs chain patterns:** pressing two pads at once plays that run of patterns in
  succession (p.9). Useful to let two patterns alternate hands-free while you work the knobs.

## Variations A-H

- **Switch:** press `A`-`H`. **Chain:** hold one and press others; the lit ones play in order
  (p.9). A two-variation chain makes a 2-bar phrase: a peak variation answered by a busier one.
- **Timing [check]:** press the next variation during the bar before you want it.
- **Each pattern's cue sheet gives a route:** intro → mains → break → drop → peaks, measured
  from what each variation holds. The route is advice. Leave a variation that is working.

## Fills

- **Two fill-ins per pattern** (p.8-10). `MANUAL TRIG` plays one at that moment: press it on the
  last beat of a phrase, and it hands back to the variation on the 1.
- **AUTO FILL IN**: set the knob to the interval (2, 4, 8, 12, 16 or 32 bars), press `[ON]`
  (p.10). On 16 in the mains keeps a long phrase moving without your hands. Turn it off before
  a break so a fill does not land on the silence.
- **Scatter as a fill** (FILL IN type `SCATTER`; hold `MANUAL TRIG` and turn the knob for the
  depth, p.10): a glitched stutter of the loop. Best on the last bar before a drop.

## Mutes and solo

- **MUTE**: press `[MUTE]`, then the instrument buttons toggle each track (unlit is muted).
  Or hold `[MUTE]` and press an instrument (p.20). Works while playing and recording.
- **Solo**: with `[MUTE]` lit, hold `SHIFT` and press an instrument (p.20).
- **Clear every mute at once**: hold `[MUTE]`, press `[CLEAR]` (p.20). This is the "drop" button:
  mute four things during a break, clear them all on the 1.
- **The kick drop:** MUTE the red fader's track for the last bar (or the last two beats) before
  a 1, unmute on the 1. The cheapest and strongest tension trick there is.
- **Choke (kit MUTE setting)**: in KIT Edit, `MUTE` makes one instrument silence another when it
  sounds (p.14). The kick choking a long rumble or acid tail ducks it on every beat, the effect
  people use sidechain for. Set it per kit and WRITE the kit. **[check]** Whether firmware 3.00
  has a real sidechain: look through the KIT Edit list on the panel.

## The knobs

- **TUNE, DECAY, CTRL per instrument** (p.15, docs/PANEL.md). DECAY is the most musical live
  knob: shorten the hats for tension, lengthen the acid voice for squelch, shorten the kick to
  make room.
- **CTRL** does what `CTRL SELECT` assigns: hold `[CTRL SELECT]`, press the instrument, choose
  with `[VALUE]` (p.15). The assignment is the machine's, not the kit's, so it is the same in
  every pattern. Useful assignments: the acid voice's filter cutoff, a delay send on the snare
  (turn up for one hit = a dub throw), a reverb send on the rim.
- **ACCENT LEVEL** (p.11): ride it up over 8-16 bars to make a groove push harder without adding
  a single note; pull it down at the break.
- **TEMPO**: `SHIFT` + `TEMPO` moves in 0.1 BPM (p.20). A 1 BPM push over a minute in a peak is
  felt, not heard.
- **Nudge**: `SHIFT` + `COPY` forward, `SHIFT` + `UTILITY` back (p.20), for lining up with
  another machine later.

## Effects

- **REVERB LEVEL** and **DELAY LEVEL / TIME / FEEDBACK** (p.14). Delay throw: raise DELAY
  LEVEL for one beat on a snare, drop it back; the echoes carry into the next bar. Feedback up
  and time moving during a break is a riser with no notes.
- **MASTER FX** `[ON]` and `CTRL` (p.10, p.15): choose the type with `SHIFT` + MASTER FX `[ON]`.
  A filter type with CTRL swept over 8 bars into a drop, then `[ON]` off exactly on the 1, is the
  classic build. Firmware 2.50 added FATTENER and VINYL SIM.
- **INST FX** per instrument (Reference p.31): DRIVE or SATURATOR on the kick for weight, HPF on
  hats and percussion to clear the middle for an acid line, CRUSHER on a snare for the peaks.
- **MOTION** (p.11-12): with `MOTION [REC]` lit, knob moves (TUNE, DECAY, CTRL, reverb, delay,
  MASTER FX CTRL and ON) are recorded into the variation; `MOTION [ON]` plays them back. Record a
  filter sweep into a build variation once in rehearsal, and it plays by itself in the set.
  Several onboard patterns already carry motion: keep `MOTION [ON]` lit.

## Variation and surprise

- **Probability** (firmware 2.50+): per-step chance, and a master amount. A little on the hats
  or an odd-cycle voice keeps a long loop alive. **[check]** the panel path in the Reference
  Manual 3.00.
- **Step loop** (1.10+): loops a few steps of the pattern, a live stutter. **[check]**.
- **Per-track LAST** (p.9): press `[LAST]`, the instrument, then a pad 1-16. A track with LAST 7
  cycles against the 16 of the kick, which is a polyrhythm made in three button presses. Clear
  it with `[LAST]`, the instrument, `[CLEAR]`. Do this live on a hat or a rim during a long main,
  and undo it before the break.
- **SUB, flam, rolls** (p.11-13): pads 12/13 held while pressing an instrument pad make 16th,
  32nd or 64th rolls; a snare roll on the last bar of a build is a fill you play yourself.

## Each part of a set

- **Warm-up:** few elements, long phrases, no fills. Let the odd-cycle voices come in one at a
  time; the floor learns the groove before it gets more.
- **Build:** add before you take away. ACCENT up, one fill per 8 bars, the acid voice's DECAY
  opening a little each phrase. Tempo creeps up between patterns, never inside one.
- **Peak:** everything in, chains of two peak variations, fills every 4-8 bars, crusher and
  drive. Hold it longer than feels safe, then break.
- **Breakdown:** the kick out, the reverb up, delay throws, the odd-cycle voices alone: they are
  the most interesting thing in the room when the four is gone. Build back with the master
  filter and MANUAL TRIG on the bar before the 1.
- **Light parts:** fewer things, more space: one voice, its reverb, and time. Resist filling the
  space; the next peak needs it.
- **Closer:** take things out on their own cycles, one per 8 bars, until a hat and a tail are
  left. Stop on a 1.

## When something goes wrong

- **A wrong variation:** press the right one **[check]** how soon it takes over.
- **Mutes in a mess:** `[MUTE]` + `[CLEAR]` (p.20).
- **A wrong pattern queued:** press the right pad before the end of the pattern **[check]** that the last press wins.
- **The kit sounds wrong after a switch:** `KitSel` is on SYSTEM. Set it to PTN (setup).
- **Lost in a long chain:** press a single variation to break the chain.
