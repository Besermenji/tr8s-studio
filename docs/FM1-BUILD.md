# Building and flashing the FM-1 firmware (Mini-Tone fork)

Written 2026-11-09, for issue #3. Everything in **Part A** was run on this Mac (Apple silicon, macOS 15,
Docker Desktop 29.8.2) and the results are real. **Part B** is the hardware runbook. Steps 1–7 have been run
(see the log at the end); the rest are still a plan.

The firmware is [Mini-Tone](https://github.com/eldontyrellcorp/Mini-Tone) (GPL-3.0, a Felucca fork). Our fork is
`Besermenji/Mini-Tone`, kept at `~/projects/Mini-Tone`, with `upstream` pointing at the original. Why Mini-Tone:
[`RIG-FM1.md`](RIG-FM1.md), section 2.

## Part A: the build

### Prerequisites

| What | Version used | Notes |
|---|---|---|
| Docker Desktop | 29.8.2 (aarch64) | The JieLi toolchain is Linux x86-64; `build.sh` runs each tool in a `linux/amd64` `debian:bookworm-slim` container (Rosetta). Keep the source under `/Users` so Docker can share it. |
| JieLi toolchain | `jieli-linux-toolchains-20260730.1` | `tools/get_toolchain.sh` installs it to `~/.jieli/toolchain` (clang 4.0.1, pi32v2). |
| JieLi AC79 SDK | tag `AC79NN_SDK_V1.2.1_2023-12-13` | `git clone --depth 1 --branch AC79NN_SDK_V1.2.1_2023-12-13 https://gitee.com/Jieli-Tech/fw-AC79_AIoT_SDK.git ~/fw-AC79_AIoT_SDK`. Only three of its files go into the package. |
| Python | **Homebrew Python 3.14** in `~/projects/Mini-Tone/.venv` | Pillow, fonttools, mido, python-rtmidi. See the trap below. |
| Node.js | 22.14 | Optional: the web tests. |
| Emscripten | not installed | Optional: the browser emulator. Its tests are skipped. |

**Trap: the UI font needs Pillow with raqm.** The system Python 3.9 with Pillow 11.3 has no libraqm, and
`tools/gen_aa_font.py` stops with `KeyError: 'setting text direction, language or font features is not
supported without libraqm'` (the build then ends with `build: gen_aa_font.py failed`). Homebrew Python with
current Pillow wheels (13.0.0 here, which bundle raqm; `brew list fribidi` shows 1.0.18) works:

```
/opt/homebrew/bin/python3 -m venv .venv
.venv/bin/pip install Pillow fonttools mido python-rtmidi
.venv/bin/python -c 'from PIL import features; print(features.check("raqm"))'    # must print True
```

### Build

```
cd ~/projects/Mini-Tone
PYTHON=.venv/bin/python ./build.sh          # about 16 s once the container image is cached
```

It writes `build/felucca.bin` (app), `build/loader/ota.bin` and `build/felucca.fwsc` (the package, identity
`FM-1_900`).

| Tree | `felucca.fwsc` | sha256 |
|---|---|---|
| stock Mini-Tone 1.0.5 (`6803c6c`) | 610,086 B | `5fe4580ac05b84ee996e6558ce02e920a618380c2b9b778b787b6bcd6dd3542a` |
| our fork, `3e3498f` | 610,101 B | `d25d2033a7ecae1d6ba358e8e08a2d37217bf28deed58af1ffdd7820cc553e33` |

Whether two builds of one commit give the same hash has not been checked.

### Tests

```
PATH="$PWD/.venv/bin:$PATH" AC79_SDK=$HOME/fw-AC79_AIoT_SDK sh tests/run_tests.sh      # about 7 min
```

**Stock Mini-Tone already fails four things on this Mac**, and the fork fails exactly the same four (the
suite's output with only the process ID differing):

| Failure | Where |
|---|---|
| `perform: OCT UP over REPEAT 1/16 ... +98, with the shimmer +135 ... FAIL` | `tests/perform_test.c` |
| `regress: 161 golden renders (1 changed ...), 1 CPU entries over budget` | `tests/regress.c` |
| `braids_osc: 384 renders against the original's hashes, 2 differ: FAIL` | `tests/braids_osc_test.c` |
| `AddressSanitizer: global-buffer-overflow ... backup_test.c:315` (`main.v6`, size 3388) | ASan/UBSan: `backup_test` |

Not investigated. They may come from the host compiler (Apple clang on arm64) rather than the firmware, which
the upstream author builds on another machine. None of them touches the flash check.

### The fork's patch list

| # | Branch | What | Status |
|---|---|---|---|
| (a) | `uc25hq80`, merged to fork `main` | `persist_boot()` accepts JEDEC `0x856014` (Puya) **and** `0xB36014` (UC25HQ80); any other id stays RAM-only. The id read at boot is kept in `flash_jedec`. | built, host-tested |
| (a2) | same commit | Bytes 24–26 of the installer handshake reply (cmd `0x11`) carry the JEDEC id, MSB first, 0 = not read. `fm1_install.py --info` prints it. The USB console's `status` prints `flash_id`. | built, host-tested |

(a) is Felucca's own fix (`hugelton/Felucca` commit 7435517, 1.8.2-beta), and (a2) is the layout proposed in
Felucca #320. Why it moved here from issue #4: it is harmless on a Puya unit, so we do not have to know the chip
before the first flash, and the first flash then tells us.

(b), (c) and (d) wait for the spike ([`FM1-PROTOCOL.md`](FM1-PROTOCOL.md)).

Host tests added: `persistence_test.c` (`jedec_test`: Puya, UC25HQ80, another part, no answer, floating bus; the
UC25HQ80 case fails if the allowlist is reverted), `ota_test.c` (bytes 24–26), `install_test.py` and
`web/test_web.mjs` (a running app reports the flash; the text still parses). Nothing here proves the loader
accepts the new chip on real hardware; the loader has no JEDEC gate in the source (`grep` of `firmware/loader/`),
which is the only evidence.

## Part B: hardware runbook (not run yet)

Every step that writes to the FM-1 is confirmed with the person at the keyboard first.

**Two stock versions exist.** M-VAVE released **V16** (`FM-1_016`, sha256
`d017fb65e70aca97ee29d944470c3515a74581df5c0576bf70de917c7b2b61d5`) on 2026-10-21, and V15 is no longer the only
stock. A public report ([m-vave/fm1_open#16](https://github.com/m-vave/fm1_open/issues/16), one user, unconfirmed
by us) says that **going back from V16 to V15 leaves V15 playing one note at a time** on every track. So do not
install V15 over V16 casually, and do not treat V15 as a free way back from a V16 unit. Our unit was on V15
(`FM-1_015`) when we flashed, so V15 is its way back. `fm1_install.py` trusts only the V15 hash without `--force`.

**Before anything**

1. Get M-VAVE's official stock package `FM-1.fwsc` (V15) and check its sha256 is
   `db1642b2b6fa5c2cccb11ffd13878068bb28601678d3644049f99dc40e7edb8a`. `fm1_install.py` accepts the stock file
   without `--force` only if it matches. Keep it somewhere safe. **This is the way back.**
2. Use a USB **data** cable, and close every other app that uses MIDI.
3. Know the recovery before you need it. If an update is interrupted and the screen stays black, check whether
   the Mac lists a USB device **WL80UBOOT** (mass storage, ID `4C4A:8057`). That is the chip's boot mode.
   [FM-1 Transporter](https://github.com/kurogedelic/FM-1-transporter) (a Seeed XIAO RP2040 and three wires to
   the FM-1's USB lines) reads and writes the flash from a Mac. Back up the flash first. Mini-Tone's README has
   the same advice. **[?]** Whether the board's pads are accessible on our unit is not known.

**Flash**

4. `.venv/bin/python tools/fm1_install.py --info`. Expect `FM-1_015` and "not reported" (stock V15 does not
   report the flash).
5. Save anything worth keeping. Stock V15 has no backup we know of, so a flash replaces its projects.
6. `.venv/bin/python tools/fm1_install.py build/felucca.fwsc`. Do not unplug until it says so.
7. `--info` again. Expect `FM-1_900` and a line `flash:   JEDEC 856014 ...` or `JEDEC B36014 ...`. **That
   answers issue #2's chip question.** Write it into `RIG-FM1.md`.
8. Save a project on the FM-1, remove power, switch on, and check it is still there. If it is not, the flash is
   not being used: read `status` on the USB serial console (`flash 0` and `flash_id`).

**Spike** (the script is read-only by default; every frame goes to `fm1-spike.jsonl`)

9. `python3 midi/fm1_spike.py --selftest`, then `ports`, `info`, `track`, and work down the list in
   [`FM1-PROTOCOL.md`](FM1-PROTOCOL.md). Turn each **[D]** or **[I]** row into **[V]** or "not supported", with
   the frame that proved it.

**Back to stock**

10. `.venv/bin/python tools/fm1_install.py ~/path/to/FM-1.fwsc`.

## Run log

**2026-11-09, the first flash.** Done with the person at the keyboard, and only the FM-1 on USB.

| Step | Result |
|---|---|
| `fm1_install.py --info` before | `FM-1_015  [running]  port: FM-1`, `flash: not reported` (stock V15) |
| Way back | official V15 `FM-1.fwsc` verified (699,956 B, sha256 `db1642b2…`, identity `FM-1_015`). Copies of V15 and V16 are kept read-only in `~/fm1-firmware/` |
| Our build | `build/felucca.fwsc`, commit `3e3498f`, 610,101 B, sha256 `d25d2033…`, identity `FM-1_900` |
| `fm1_install.py build/felucca.fwsc --yes` | checked the package, switched to update mode, wrote 0–100 %, restarted: `done: the FM-1 runs FM-1_900` |
| `--info` after | `FM-1_900  [running]  port: Felucca`, **`flash: JEDEC 856014  Puya P25Q80`** |
| Read-only protocol spike | `info`, `ping`, `track`, `track-dump`, `step-get`, `global-get` (`CLK`, `ROUT`), `desc`, `song`, `project-query`, `learn-get`, `mix`: all answered. Results in [`FM1-PROTOCOL.md`](FM1-PROTOCOL.md) |

Findings:

- **This unit has the Puya chip**, so stock Mini-Tone would have worked on it too. The UC25HQ80 allowlist is
  still right for the fleet. The chip id in the handshake works end to end on hardware.
- The FM-1's CoreMIDI port is named `FM-1` under stock V15 and `Felucca` under Mini-Tone. Use `--port Felucca`.

Not yet done: the power-cycle persistence test (step 8) and everything that writes.
