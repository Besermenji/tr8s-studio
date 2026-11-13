**Title:** Mini-Tone fork: the patches the rig needs

**Labels:** `rig`, `firmware`, `fm-1`

## Context

The fork (`Besermenji/Mini-Tone`, issue 02) should stay thin: the smallest
changes that make the rig work, each offered upstream. Issue 02's spike decides
which of these are needed. Skip any that the firmware already covers.

See [`docs/RIG-FM1.md`](../RIG-FM1.md), section 2, "Fork policy".

## Tasks

- [x] **(a) UC25HQ80 flash detection.** Done in issue 02, in the fork's first
      build (JEDEC ID `0xB36014`, Felucca issue #320), together with the chip
      id in the installer handshake. Remaining here: offer it upstream.
- [ ] **(b) Program Change follow.** A Program Change on a configurable channel
      selects a project or song section at the end of the current bar, so the
      FM-1 follows the TR-8S's pattern changes on stage. Off by default.
- [ ] **(c) Whole-project writes over SysEx.** Steps, track parameters,
      generator settings and song sections, plus save. Only what the spike
      found missing.

For each patch:

- [ ] Host tests and simulator scenarios, in the fork's own test setup.
- [ ] Build and flash locally, and test on the FM-1 with the TR-8S as clock.
- [ ] Update `docs/FM1-PROTOCOL.md` in this repo.
- [ ] Open a PR upstream with a short description of why the change is useful
      beyond this rig.

## Acceptance

- Every needed patch is merged in the fork, tested on hardware, and documented.
- With (b): change pattern on the TR-8S while playing, and the FM-1 follows at
  the bar.

## Depends on

Issue 02.

## Pipeline

Sonnet implements (C, JieLi toolchain); Opus reviews. Local builds and flashes
only.
