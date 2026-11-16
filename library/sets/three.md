# Set: three

Built 2026-11-13 by tr8s set.build · 3 tracks · 3 on the FM-1

## Rig

- TR-8S MIDI OUT → DIN-to-TRS adapter → FM-1 TRS MIDI IN. FM-1 audio out → TR-8S EXT IN L/R (EXT IN type stereo).
- FM-1: clock source (CLK) TRS, MIDI IN block CH1-6, powered from a USB supply. TR-8S: sync out on.
- A TR-8S Start starts the FM-1's loop, never its song. A TR-8S Stop ends a running song.
- A song starts from PLAY on the FM-1's SONG page while the FM-1 is stopped, and stops by itself after its last section.
- The TR-8S variations change by hand: press the next one during the bar before its bar in the timeline.

## Running order

| # | Track | TR-8S | FM-1 | Key | BPM | Seed | FM-1 plays |
|---|---|---|---|---|---|---|---|
| 1 | ACID | 1-01 | A | A minor | 137 | 11 | song, 8 sections, 128 bars |
| 2 | DUB | 1-02 | B | D minor | 122 | 22 | loop, hand cues |
| 3 | HYPNOTIC | 1-03 | C | F# phrygian | 132 | 33 | song, 5 sections, 64 bars |

## 1. ACID · TR-8S 1-01 · FM-1 project A

acid · voicing acid · energy 0.7 · A minor · 137 BPM · seed 11

### Start: song

1. TR-8S: select pattern 1-01, variation A. Keep it running; if it is stopped, press Start.
2. FM-1: load project A, then open the SONG page (GLO, tapped).
3. If the FM-1 is playing, press PLAY once to stop it.
4. Press PLAY on a downbeat. That bar is bar 1.

### Parts

| FM-1 track | Ch | Shape | Engine | Steps |
|---|---|---|---|---|
| 1 | 1 | acid | TB303 | 16 |
| 2 | 2 | bass | DUALVCO | 16 |

### Timeline (16 bars per variation)

| Bar | TR-8S | Role | FM-1 plays |
|---|---|---|---|
| 1 | A | intro | — |
| 17 | B | main | 1 (acid), 2 (bass) |
| 33 | C | main | 1 (acid), 2 (bass) |
| 49 | D | fill | 1 (acid), 2 (bass) |
| 65 | E | break | — |
| 81 | F | drop | — |
| 97 | G | main | 1 (acid), 2 (bass) |
| 113 | H | fill | 1 (acid), 2 (bass) |
| 129 | — | end | the song stops by itself; go to the next track |

## 2. DUB · TR-8S 1-02 · FM-1 project B

dub · voicing dub · energy 0.7 · D minor · 122 BPM · seed 22

### Start: loop

1. TR-8S: press Stop.
2. FM-1: load project B. Mute track 3 (stab).
3. TR-8S: select pattern 1-02, variation A, press Start. The FM-1's loop starts with it: that bar is bar 1.

### Parts

| FM-1 track | Ch | Shape | Engine | Steps |
|---|---|---|---|---|
| 1 | 1 | pad | SWARM | 16 |
| 3 | 3 | stab | FMTONE | 16 |

### Timeline (16 bars per variation)

| Bar | TR-8S | Role | FM-1 plays | By hand on the FM-1 |
|---|---|---|---|---|
| 1 | A | intro | 1 (pad) | — |
| 17 | B | main | 1 (pad), 3 (stab) | unmute track 3 |
| 33 | C | main | 1 (pad), 3 (stab) | — |
| 49 | D | fill | 1 (pad), 3 (stab) | — |
| 65 | E | break | 1 (pad) | mute track 3 |
| 81 | F | drop | 1 (pad) | — |
| 97 | G | main | 1 (pad), 3 (stab) | unmute track 3 |
| 113 | H | fill | 1 (pad), 3 (stab) | — |
| 129 | — | end |  | the loop plays on until the TR-8S stops |

## 3. HYPNOTIC · TR-8S 1-03 · FM-1 project C

hypnotic · voicing hypnotic · energy 0.7 · F# phrygian · 132 BPM · seed 33

### Start: song

1. TR-8S: select pattern 1-03, variation A. Keep it running; if it is stopped, press Start.
2. FM-1: load project C, then open the SONG page (GLO, tapped).
3. If the FM-1 is playing, press PLAY once to stop it.
4. Press PLAY on a downbeat. That bar is bar 1.

### Parts

| FM-1 track | Ch | Shape | Engine | Steps |
|---|---|---|---|---|
| 1 | 1 | drone | SWARM | 16 |
| 4 | 4 | arp | WAVETONE | 16 |

### Timeline (8 bars per variation)

| Bar | TR-8S | Role | FM-1 plays |
|---|---|---|---|
| 1 | A | intro | 1 (drone) |
| 9 | B | main | 1 (drone), 4 (arp) |
| 17 | C | main | 1 (drone), 4 (arp) |
| 25 | D | fill | 1 (drone), 4 (arp) |
| 33 | E | break | 1 (drone) |
| 41 | F | drop | 1 (drone) |
| 49 | G | main | 1 (drone), 4 (arp) |
| 57 | H | fill | 1 (drone), 4 (arp) |
| 65 | — | end | the song stops by itself; go to the next track |
