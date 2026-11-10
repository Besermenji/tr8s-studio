"""
The Mini-Tone profile for the FM-1, and the encoders for it.

Pure functions: nothing here opens a port. It knows the firmware (tracks,
channels, engines, the voice budget), maps a style to FM-1 parts, and encodes
parts as a Standard MIDI File (`to_mid`) or as the editor-protocol SysEx
(`to_syx`). Marks as in docs/FM1-PROTOCOL.md: the `TRACK_STEP` write form is
verified on hardware; `setup_frames` (engine, track length) is documented
upstream and not yet run on our unit.

The module never builds a realtime byte (F8-FC): `frame` refuses any argument
that is not 7-bit data, so the studio cannot become a second clock.
"""

from __future__ import annotations

from collections.abc import Mapping

from .line import FORCE_VEL, Part, _stream, generate

# --------------------------------------------------------------- profile

TRACKS = 6
CHANNELS = {t: t + 1 for t in range(TRACKS)}    # ROUT = CH1-6: channel 10 stays the TR-8S's
VOICES = 8

# the editor protocol's engine indices (firmware/src/engines.c: "append, never reorder")
ENGINES = {1: "DIGITAL", 10: "DRUM", 12: "FM6", 14: "SWARM", 15: "FMTONE",
           16: "WAVETONE", 17: "FMDRUM", 18: "SID", 19: "CHIP", 20: "DUALVCO",
           21: "ANALOG4", 22: "BRAIDS", 23: "TB303", 24: "TR808"}
ENGINE_INDEX = {name: i for i, name in ENGINES.items()}
MONO = {"TB303"}                                 # one voice however many notes a step holds

# CCs the EMP16 sends (docs/RIG-FM1.md, "EMP16 map"); same on every track's channel
CC = {"cutoff": 74, "resonance": 71, "decay": 75, "glide": 5,
      "delay": 94, "reverb": 91, "chorus": 93, "level": 7}

# firmware ids (core.h enums; P_E0 = 103, P_COUNT = 111 and G_COUNT = 27 match the INFO reply)
P_SLEN = 29
G_ENGSEL = 20

# style -> [(track, shape, engine)]. A song with no 303 is an ordinary case.
# Tracks follow the channel plan: 0 main voice, 1 bass, 2 stab/chord, 3 arp/lead.
VOICING = {
    "acid":     [(0, "acid", "TB303"), (1, "bass", "DUALVCO")],
    "hypnotic": [(0, "drone", "SWARM"), (3, "arp", "WAVETONE")],   # a TB303 may come in late: override
    "dub":      [(0, "pad", "SWARM"), (2, "stab", "FMTONE")],
    "techno":   [(1, "bass", "DUALVCO"), (2, "stab", "ANALOG4")],
    "peak":     [(0, "acid", "TB303"), (3, "lead", "DUALVCO")],
    # the other drum styles (style.STYLES), a starting point to override
    "hard":     [(0, "acid", "TB303"), (3, "lead", "DUALVCO")],
    "house":    [(1, "bass", "DUALVCO"), (2, "stab", "ANALOG4")],
    "broken":   [(1, "bass", "DUALVCO"), (2, "stab", "FMTONE")],
    "dnb":      [(0, "pad", "SWARM"), (1, "bass", "DUALVCO")],
    "lofi":     [(0, "pad", "SWARM"), (3, "lead", "WAVETONE")],
}


def _track_seed(seed: int, track: int) -> int:
    """Track 0 plays the seed as given; the others get their own stream, not `seed + track`."""
    return seed if track == 0 else _stream(seed, f"track{track}").randrange(1 << 30)


def build_parts(style: str, key: str = "C minor", energy: float | None = None,
                seed: int = 0, length: int = 16, overrides: Mapping | None = None) -> dict:
    """
    {track: Part} for a style. `overrides` maps a track to `(shape, engine)`,
    or to None to drop that track.
    """
    if style not in VOICING:
        raise ValueError(f"unknown style {style!r}; have {', '.join(VOICING)}")
    plan = {t: (shape, eng) for t, shape, eng in VOICING[style]}
    for t, v in (overrides or {}).items():
        if v is None:
            plan.pop(t, None)
        else:
            plan[t] = tuple(v)
    out = {}
    for t, (shape, eng) in sorted(plan.items()):
        if not 0 <= t < TRACKS:
            raise ValueError(f"track {t} is outside 0..{TRACKS - 1}")
        if eng not in ENGINE_INDEX:
            raise ValueError(f"unknown engine {eng!r}; have {', '.join(ENGINE_INDEX)}")
        out[t] = generate(shape, key=key, energy=energy, seed=_track_seed(seed, t), length=length, engine=eng)
    return out


def _as_tracks(parts) -> dict:
    """A list means tracks 0, 1, 2...; a mapping is {track: Part}."""
    items = parts.items() if isinstance(parts, Mapping) else enumerate(parts)
    out = {}
    for t, p in items:
        if not 0 <= t < TRACKS:
            raise ValueError(f"track {t} is outside 0..{TRACKS - 1}")
        out[t] = p
    return out


# ---------------------------------------------------------- voice budget

def voice_budget(parts) -> list:
    """
    Warnings for steps where the parts together sound more than eight voices.
    A mono engine (TB303) counts one voice; a pad chord counts its notes. Parts
    shorter than the longest loop, as they do on the FM-1. Release tails are
    not counted, so this is a floor, not a guarantee.
    """
    tracks = _as_tracks(parts)
    if not tracks:
        return []
    n = max(p.length for p in tracks.values())
    over = []
    for i in range(n):
        total = 0
        for p in tracks.values():
            s = p.steps[i % p.length]
            if s.sounding:
                total += 1 if p.engine in MONO else len(s.notes)
        if total > VOICES:
            over.append((i + 1, total))
    if not over:
        return []
    worst = max(t for _, t in over)
    first, last = over[0][0], over[-1][0]
    return [f"{len(over)} step(s) between {first} and {last} need more than {VOICES} voices "
            f"(worst {worst}); the FM-1 will drop notes"]


# --------------------------------------------------------- frames (SysEx)

HEADER = bytes([0xF0, 0x7D, 0x46, 0x4C])
CMDS = {
    "INFO": 1, "GET": 2, "SET": 3, "DUMP": 4, "DESC": 5, "STEP_GET": 6, "STEP_SET": 7,
    "PRESET": 8, "PROJECT": 9, "WATCH": 22, "CHANGED": 23, "RELOAD": 24, "PING": 25,
    "STEP_CHANGED": 26, "TRACK": 27, "TRACK_MIX": 28, "TRACK_DUMP": 29, "TRACK_STEP": 30,
    "TRACK_PARAM": 31, "TRACK_CHANGED": 32, "SONG": 33, "MOTION": 64, "LEARN_GET": 78,
    "LEARN_SET": 79,
}
NAMES = {v: k for k, v in CMDS.items()}
ST_NOTE, ST_TIE, ST_REST = 0, 1, 2
SF_ACCENT, SF_SLIDE = 1, 2


class Fm1FrameError(ValueError):
    pass


def v14(value: int) -> list:
    """-8192..8191 -> [lo, hi] (value + 8192, LSB first, 7 bits each)."""
    if not -8192 <= value <= 8191:
        raise Fm1FrameError(f"value {value} outside -8192..8191")
    u = value + 8192
    return [u & 0x7F, u >> 7]


def from_v14(lo: int, hi: int) -> int:
    return (lo | hi << 7) - 8192


def frame(cmd: int, args=()) -> bytes:
    """A request frame. Refuses anything that is not a 7-bit data byte (so no F8-FC, ever)."""
    args = list(args)
    bad = [a for a in args if not isinstance(a, int) or not 0 <= a < 0x80]
    if not 0 <= cmd < 0x80 or bad:
        raise Fm1FrameError(f"not 7-bit data: cmd={cmd} args={bad}")
    return HEADER + bytes([cmd]) + bytes(args) + b"\xF7"


def step_frame(track: int, index: int, step) -> bytes:
    """
    `TRACK_STEP` set, the 10-byte form verified on hardware:
    track, index, n, note x4, time, flags, vel.
    A rest is n=0, time REST; a tie carries the held notes with time TIE (both [D], not yet run).
    """
    if len(step.notes) > 4:
        raise Fm1FrameError(f"a step holds at most 4 notes, got {len(step.notes)}")
    notes = list(step.notes) + [0] * (4 - len(step.notes))
    if not step.notes:
        time, flags, vel = ST_REST, 0, FORCE_VEL["NORM"]
    else:
        time = ST_TIE if step.tie else ST_NOTE
        flags = (SF_ACCENT if step.force == "ACC" else 0) | (SF_SLIDE if step.slide else 0)
        vel = FORCE_VEL[step.force]
    return frame(CMDS["TRACK_STEP"], [track, index, len(step.notes), *notes, time, flags, vel])


def to_syx(parts) -> list:
    """
    One `TRACK_STEP` set frame per step, rests included so the whole length is
    overwritten. RAM only: it neither saves nor changes the engine or track length
    (see `setup_frames`, `save_frame`).
    """
    out = []
    for t, p in sorted(_as_tracks(parts).items()):
        out += [step_frame(t, i, s) for i, s in enumerate(p.steps)]
    return out


def setup_frames(parts) -> list:
    """
    Engine and track length, per part. **[D], not yet run on our FM-1.** The
    engine goes in by selecting the track and setting G_ENGSEL (the selected
    track's engine); the length by `TRACK_PARAM` P_SLEN.
    """
    out = []
    for t, p in sorted(_as_tracks(parts).items()):
        if p.engine is not None:
            out.append(frame(CMDS["TRACK"], [t]))
            out.append(frame(CMDS["SET"], [1, G_ENGSEL, *v14(ENGINE_INDEX[p.engine])]))
        out.append(frame(CMDS["TRACK_PARAM"], [t, P_SLEN, *v14(p.length)]))
    return out


def save_frame(slot: int) -> bytes:
    """`PROJECT` save. Stops the transport and writes flash: send it deliberately, never automatically."""
    if not 0 <= slot <= 3:
        raise Fm1FrameError("project slot is 0..3")
    return frame(CMDS["PROJECT"], [1, slot])


def split_syx(data: bytes) -> list:
    """
    The frames of a .syx file. Every frame must be ours: our header, a command byte,
    7-bit data, and a closing F7. Anything else (another device's SysEx, a truncated
    file) is an `Fm1FrameError` and nothing is returned.
    """
    out, i = [], 0
    while i < len(data):
        if data[i] != 0xF0:
            raise Fm1FrameError(f"expected F0 at byte {i}")
        j = data.find(0xF7, i)
        if j < 0:
            raise Fm1FrameError(f"the frame at byte {i} has no closing F7")
        f = bytes(data[i:j + 1])
        if len(f) < len(HEADER) + 2 or f[:4] != HEADER:
            raise Fm1FrameError(f"the frame at byte {i} is not an FM-1 frame: {f[:8].hex(' ')}")
        if any(b >= 0x80 for b in f[1:-1]):
            raise Fm1FrameError(f"the frame at byte {i} has a byte that is not 7-bit data")
        out.append(f)
        i = j + 1
    return out


def check_edit_frame(f: bytes) -> None:
    """
    Raise `Fm1FrameError` unless `f` is one of the edits `to_syx` and `setup_frames`
    make: a step write, a track select, the engine (G_ENGSEL) or the track length
    (P_SLEN). Not a save, and not a clock or channel setting: `CLK` stops the
    transport and `ROUT` could put the FM-1 on the TR-8S's channel.
    """
    cmd, args = f[4], f[5:-1]
    ok = False
    if cmd == CMDS["TRACK_STEP"]:
        ok = len(args) == 10 and args[0] < TRACKS
    elif cmd == CMDS["TRACK"]:
        ok = len(args) == 1 and args[0] < TRACKS
    elif cmd == CMDS["SET"]:
        ok = len(args) == 4 and args[0] == 1 and args[1] == G_ENGSEL
    elif cmd == CMDS["TRACK_PARAM"]:
        ok = len(args) == 4 and args[0] < TRACKS and args[1] == P_SLEN
    if not ok:
        raise Fm1FrameError(f"not an edit this tool sends: {NAMES.get(cmd, cmd)} {args.hex(' ')}")


# -------------------------------------------------------------- MIDI file

PPQ = 96
STEP_TICKS = PPQ // 4           # a sixteenth
SLIDE_OVERLAP = 6               # a slid note's off comes this long after the next on
GAP = 2                         # a plain note ends this early


def _vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def _chunk(tag: bytes, body: bytes) -> bytes:
    return tag + len(body).to_bytes(4, "big") + body


def _note_spans(part: Part, length: int):
    """(start_step, end_step, step) for each sounding note, tiling the part over `length` steps."""
    steps = [part.steps[i % part.length] for i in range(length)]
    spans, i = [], 0
    while i < length:
        s = steps[i]
        if s.sounding and not s.tie:
            j = i + 1
            while j < length and steps[j].sounding and steps[j].tie:
                j += 1
            spans.append((i, j, s))
            i = j
        else:
            i += 1
    return spans


def _note_off(part: Part, s, b: int, end: int, total: int, m: int) -> int:
    """
    The tick note `m` of step `s` ends at. A slide overlaps the next note so a mono
    synth glides, but only when that note is a different pitch (the same pitch would
    have its new note cut short by this one's off) and only inside the file (a slide
    into the loop's first step ends at the loop end, so no track outruns the tempo track).
    """
    nxt = part.steps[b % part.length]
    if s.slide and nxt.sounding and not nxt.tie and m not in nxt.notes:
        return min(end + SLIDE_OVERLAP, total)
    return end - GAP


def _part_track(part: Part, channel: int, length: int, name: str) -> bytes:
    events = []                                  # (tick, order, bytes); offs sort before ons at a tick
    total = length * STEP_TICKS
    for a, b, s in _note_spans(part, length):
        start, end = a * STEP_TICKS, b * STEP_TICKS
        vel = FORCE_VEL[s.force]
        if s.ratchet > 1 and b == a + 1:
            per = (end - start) // s.ratchet
            hits = [(start + k * per, start + (k + 1) * per - GAP) for k in range(s.ratchet)]
            for t0, t1 in hits:
                for m in s.notes:
                    events.append((t0, 1, bytes([0x90 | channel - 1, m, vel])))
                    events.append((t1, 0, bytes([0x80 | channel - 1, m, 0])))
            continue
        for m in s.notes:
            events.append((start, 1, bytes([0x90 | channel - 1, m, vel])))
            events.append((_note_off(part, s, b, end, total, m), 0, bytes([0x80 | channel - 1, m, 0])))
    events.sort(key=lambda e: (e[0], e[1]))
    body = bytes([0x00, 0xFF, 0x03, len(name)]) + name.encode("ascii", "replace")
    now = 0
    for tick, _, data in events:
        body += _vlq(tick - now) + data
        now = tick
    end = length * STEP_TICKS
    body += _vlq(max(0, end - now)) + bytes([0xFF, 0x2F, 0x00])
    return _chunk(b"MTrk", body)


def to_mid(parts, bpm: int = 130) -> bytes:
    """
    A Standard MIDI File, format 1, 96 PPQ: a tempo track and one track per part
    on its FM-1 channel. Accents are velocity, a slide is a note that overlaps
    the next, a tie extends the note. Shorter parts loop to the longest.
    """
    tracks = _as_tracks(parts)
    if not tracks:
        raise ValueError("no parts")
    length = max(p.length for p in tracks.values())
    micros = 60_000_000 // bpm
    tempo = (bytes([0x00, 0xFF, 0x51, 0x03]) + micros.to_bytes(3, "big")
             + bytes([0x00, 0xFF, 0x58, 0x04, 4, 2, 24, 8])
             + _vlq(length * STEP_TICKS) + bytes([0xFF, 0x2F, 0x00]))
    chunks = [_chunk(b"MTrk", tempo)]
    for t, p in sorted(tracks.items()):
        chunks.append(_part_track(p, CHANNELS[t], length, f"{p.shape} {p.engine or ''}".strip()))
    header = _chunk(b"MThd", (1).to_bytes(2, "big") + len(chunks).to_bytes(2, "big") + PPQ.to_bytes(2, "big"))
    return header + b"".join(chunks)
