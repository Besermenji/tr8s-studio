#!/usr/bin/env python3
"""
THROWAWAY spike: talk to an M-VAVE FM-1 running Mini-Tone / Felucca over its
USB-MIDI SysEx editor protocol, and record what really works (issue #3).

    python3 fm1_spike.py --selftest                 no device needed
    python3 fm1_spike.py ports
    python3 fm1_spike.py info
    python3 fm1_spike.py track [N]                  query; with N, SELECT track N (0-5) -- a write
    python3 fm1_spike.py track-dump N
    python3 fm1_spike.py step-get TRACK INDEX
    python3 fm1_spike.py step-set TRACK INDEX NOTE [--accent] [--slide] --write
    python3 fm1_spike.py param-get TRACK ID
    python3 fm1_spike.py param-set TRACK ID VALUE --write
    python3 fm1_spike.py mix TRACK [LEVEL MUTE --write]
    python3 fm1_spike.py global-get ID              CLK = 2, ROUT = 14
    python3 fm1_spike.py global-set ID VALUE --write
    python3 fm1_spike.py desc SCOPE ID              name, range and enum values (scope 0 track param, 1 global)
    python3 fm1_spike.py song                       query the song sections
    python3 fm1_spike.py project-query SLOT
    python3 fm1_spike.py project-save SLOT --write --slot SLOT   writes the FM-1's flash
    python3 fm1_spike.py project-load SLOT --write  replaces the working music with the saved slot
    python3 fm1_spike.py learn-get
    python3 fm1_spike.py ping
    python3 fm1_spike.py note CH NOTE [--vel N] [--dur S]   play one note on channel 1-6 (makes sound, stores nothing)
    python3 fm1_spike.py listen [SECONDS]           print incoming non-clock traffic
    python3 fm1_spike.py raw CMD [BYTE ...] --write
    python3 fm1_spike.py send-syx FILE --write      send a .syx from tr8s.synth.minitone.to_syx, one frame at a time,
                                                    waiting for each reply (step, track select, engine and length only; never a save, clock or channel)

Port: the first MIDI port whose name contains "Felucca" (our Mini-Tone build), else "FM-1"
(stock firmware); override with --port TEXT or FM1_PORT.  Needs `pip install python-rtmidi` (the repo's `.[mac]` extra).

Wire format (upstream web/EDITOR_PROTOCOL.md, Felucca / Mini-Tone):

    F0 7D 46 4C <cmd> <args...> F7        7D = non-commercial ID, 46 4C = "FL"
    every valid request gets exactly ONE reply with the same header and cmd.
    value (v14): 2 bytes, LSB first, holding value + 8192
    string:      ASCII bytes ended by a 0 byte

SAFETY
  * Read-only by default.  Anything that changes the FM-1 needs --write, and
    project-save also needs --slot to repeat the slot (flash write, ~2 s,
    stops the transport).
  * `note` plays a sound and changes nothing stored; it only sends note on/off on channels 1-6.
  * It never sends MIDI realtime bytes (F8-FC): the studio must never become a
    second clock.  Every data byte is checked to be < 0x80.
  * Every frame sent and received is appended to fm1-spike.jsonl, which is the
    raw material for docs/FM1-PROTOCOL.md.
"""

import argparse
import json
import os
import queue
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src"))
from tr8s.synth.minitone import (CMDS, HEADER, NAMES, Fm1FrameError, check_edit_frame,   # noqa: E402,F401
                                 frame, from_v14, split_syx, v14)
from tr8s.synth.port import Fm1EchoError, Fm1Error, Fm1Port, parse_reply   # noqa: E402,F401

LOG = os.environ.get("FM1_SPIKE_LOG", "fm1-spike.jsonl")

READ_ONLY = {"INFO", "GET", "DUMP", "DESC", "STEP_GET", "PING", "TRACK_DUMP", "LEARN_GET"}


class SpikeError(Fm1Error):
    pass


# ----------------------------------------------------------------- encoding ---
# v14, from_v14 and frame live in tr8s.synth.minitone, and parse_reply and the port in
# tr8s.synth.port: one copy for the spike and the studio.


def hexs(b):
    return " ".join(f"{x:02X}" for x in b)


def cstring(b, i=0):
    j = b.index(0, i)
    return b[i:j].decode("ascii", "replace"), j + 1


# --------------------------------------------------------------------- port ---

class Fm1(Fm1Port):
    """The studio's port (tr8s.synth.port), logging every frame to fm1-spike.jsonl."""

    def __init__(self, name):
        super().__init__(name, log=log)


def log(kind, data):
    try:
        with open(LOG, "a") as f:
            f.write(json.dumps({"t": round(time.time(), 3), "dir": kind, "hex": hexs(data)}) + "\n")
    except OSError:
        pass


# ----------------------------------------------------------------- decoding ---

def show_info(a):
    print("raw:", hexs(a))
    try:                                        # best effort: the layout is the doc's, not yet [V]
        ver, i = cstring(a)
        print(f"version string: {ver!r}")
        n_eng, p_count, g_count, nstep, p_e0 = a[i:i + 5]
        print(f"NENGINES={n_eng} P_COUNT={p_count} G_COUNT={g_count} NSTEP={nstep} P_E0={p_e0}  (unverified decode)")
        i += 5
        names = []
        for _ in range(n_eng):
            s, i = cstring(a, i)
            names.append(s)
        print("engines:", names)
        print("rest:", hexs(a[i:]))
    except (ValueError, IndexError):
        print("(could not decode further; use the raw bytes)")


def show_desc(a):
    print("raw:", hexs(a))
    try:                                        # scope, id, fmt, min v14, max v14, def v14, label, unit, enum names
        scope, pid, fmt = a[0], a[1], a[2]
        lo, hi, df = from_v14(a[3], a[4]), from_v14(a[5], a[6]), from_v14(a[7], a[8])
        label, i = cstring(a, 9)
        unit, i = cstring(a, i)
        print(f"scope={scope} id={pid} fmt={fmt} range={lo}..{hi} default={df} label={label!r} unit={unit!r}")
        names = []
        while i < len(a):
            n, i = cstring(a, i)
            names.append(n)
        if names:
            print("values:", {k: v for k, v in enumerate(names)})
    except (ValueError, IndexError):
        print("(could not decode; use the raw bytes)")


def show_step(a):
    print("raw:", hexs(a))
    if len(a) >= 10:
        track, idx, n = a[0], a[1], a[2]
        notes, tm, flags, vel = list(a[3:3 + 4][:n]), a[7], a[8], a[9]
        print(f"track={track} step={idx} notes={notes} time={['NOTE', 'TIE', 'REST'][tm] if tm < 3 else tm} "
              f"accent={bool(flags & 1)} slide={bool(flags & 2)} vel={vel}  (tail: {hexs(a[10:])})")


# ----------------------------------------------------------------- commands ---

def need_write(a, what):
    if not a.write:
        raise SpikeError(f"{what} changes the FM-1: add --write")


def run(a):
    if a.cmd == "ports":
        import rtmidi
        print("in: ", rtmidi.MidiIn().get_ports())
        print("out:", rtmidi.MidiOut().get_ports())
        return
    fm = Fm1(a.port)
    print(f"port: {fm.name}   (log: {LOG})")
    try:
        c = a.cmd
        if c == "info":
            show_info(fm.request(CMDS["INFO"]))
        elif c == "ping":
            print("reply:", hexs(fm.request(CMDS["PING"])))
        elif c == "track":
            if a.n is None:
                print("raw:", hexs(fm.request(CMDS["TRACK"])))
            else:
                need_write(a, "selecting a track")
                print("raw:", hexs(fm.request(CMDS["TRACK"], [a.n])))
        elif c == "track-dump":
            print("raw:", hexs(fm.request(CMDS["TRACK_DUMP"], [a.n])))
        elif c == "step-get":
            show_step(fm.request(CMDS["TRACK_STEP"], [a.track, a.index]))
        elif c == "step-set":
            need_write(a, "step-set")
            flags = (1 if a.accent else 0) | (2 if a.slide else 0)
            # TRACK_STEP set: track, index, n, note0..3, time, flags, vel
            args = [a.track, a.index, 1, a.note, 0, 0, 0, 0, flags, a.vel]
            show_step(fm.request(CMDS["TRACK_STEP"], args))
        elif c == "send-syx":
            need_write(a, "send-syx")
            frames = split_syx(open(a.file, "rb").read())
            for f in frames:                            # all of it is checked before any of it is sent
                check_edit_frame(f)
            kinds = {}
            for f in frames:
                kinds[NAMES[f[4]]] = kinds.get(NAMES[f[4]], 0) + 1
            print(f"{a.file}: {len(frames)} frames {kinds}")
            try:
                fm.write_frames(frames)                 # raises on no reply: stops at the first miss
            except Fm1EchoError as e:                   # the FM-1 stored something other than what we sent
                for n, f, reply in e.mismatches:
                    print(f"  frame {n} was not echoed as sent:\n    sent  {hexs(f[5:-1])}\n    reply {hexs(reply)}")
                raise SpikeError(f"{e}; see fm1-spike.jsonl") from None
            print(f"sent {len(frames)} frames, every one answered")
        elif c == "param-get":
            r = fm.request(CMDS["TRACK_PARAM"], [a.track, a.id])
            print("raw:", hexs(r), "value:", from_v14(r[2], r[3]) if len(r) >= 4 else "?")
        elif c == "param-set":
            need_write(a, "param-set")
            r = fm.request(CMDS["TRACK_PARAM"], [a.track, a.id] + v14(a.value))
            print("raw:", hexs(r), "value after clamping:", from_v14(r[2], r[3]) if len(r) >= 4 else "?")
        elif c == "mix":
            if a.level is None:
                print("raw:", hexs(fm.request(CMDS["TRACK_MIX"], [a.track])))
            else:
                need_write(a, "mix")
                print("raw:", hexs(fm.request(CMDS["TRACK_MIX"], [a.track] + v14(a.level) + [a.mute or 0])))
        elif c == "global-get":
            r = fm.request(CMDS["GET"], [1, a.id])
            print("raw:", hexs(r), "value:", from_v14(r[2], r[3]) if len(r) >= 4 else "?")
        elif c == "global-set":
            need_write(a, "global-set")
            r = fm.request(CMDS["SET"], [1, a.id] + v14(a.value))
            print("raw:", hexs(r), "value after clamping:", from_v14(r[2], r[3]) if len(r) >= 4 else "?")
        elif c == "desc":
            show_desc(fm.request(CMDS["DESC"], [a.scope, a.id]))
        elif c == "song":
            print("raw:", hexs(fm.request(CMDS["SONG"], [4])))
        elif c == "project-query":
            print("raw:", hexs(fm.request(CMDS["PROJECT"], [2, a.slot])), " (op, slot, used)")
        elif c == "project-save":
            need_write(a, "project-save")
            if a.slot_confirm != a.slot:
                raise SpikeError("project-save writes flash: repeat the slot with --slot")
            print("raw:", hexs(fm.request(CMDS["PROJECT"], [1, a.slot], timeout=6.0)))
        elif c == "project-load":
            need_write(a, "project-load")
            print("raw:", hexs(fm.request(CMDS["PROJECT"], [0, a.slot], timeout=4.0)), " (op, slot, used)")
        elif c == "learn-get":
            print("raw:", hexs(fm.request(CMDS["LEARN_GET"])))
        elif c == "raw":
            need_write(a, "raw")
            print("raw:", hexs(fm.request(a.rcmd, a.bytes)))
        elif c == "note":
            if not 1 <= a.ch <= 6 or not 0 <= a.note < 128 or not 1 <= a.vel < 128:
                raise SpikeError("note: channel 1-6 (never 10, the TR-8S's), note 0-127, velocity 1-127")
            fm.send(bytes([0x90 | (a.ch - 1), a.note, a.vel]))
            time.sleep(a.dur)
            fm.send(bytes([0x80 | (a.ch - 1), a.note, 0]))
            print(f"played note {a.note} on channel {a.ch} for {a.dur}s")
        elif c == "listen":
            end = time.time() + a.seconds
            print(f"listening {a.seconds:.0f}s (clock and active sensing are not shown) ...")
            while time.time() < end:
                try:
                    pkt = fm.q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if pkt and pkt[0] != 0xF8:
                    log("rx-listen", pkt)
                    print(hexs(pkt))
    finally:
        fm.close()


# ----------------------------------------------------------------- selftest ---

def selftest():
    bad = 0

    def check(what, ok):
        nonlocal bad
        print(f"{what:72s} {'ok' if ok else 'FAIL'}")
        bad += not ok

    # the one example frame the upstream doc gives (LEARN_SET op 0: CC74 -> track 3, id 98)
    check("LEARN_SET frame equals the doc's example",
          frame(CMDS["LEARN_SET"], [0, 74, 3, 98]) == bytes.fromhex("F07D464C4F004A0362F7"))
    check("LEARN_GET frame", frame(CMDS["LEARN_GET"]) == bytes.fromhex("F07D464C4EF7"))
    check("v14: 0 -> [0, 64]", v14(0) == [0, 64])
    check("v14 round trip, whole range", all(from_v14(*v14(v)) == v for v in range(-8192, 8192)))
    check("v14 refuses 8192", _raises(lambda: v14(8192)))
    check("frame refuses a data byte >= 0x80", _raises(lambda: frame(1, [0x80])))
    for rt in (0xF8, 0xFA, 0xFB, 0xFC):
        check(f"frame refuses realtime {rt:02X}", _raises(lambda rt=rt: frame(1, [rt])))
    check("parse_reply: the doc's LEARN_SET reply",
          parse_reply(bytes.fromhex("F07D464C4F0010014A0362F7")) == (0x4F, bytes.fromhex("0010014A0362")))
    check("parse_reply ignores another header", parse_reply(bytes.fromhex("F0417F0001F7")) is None)
    check("cstring", cstring(b"Mini-Tone\0\x06") == ("Mini-Tone", 10))
    check("every command id is 7-bit and unique", len(set(CMDS.values())) == len(CMDS) and max(CMDS.values()) < 0x80)
    print("selftest FAILED" if bad else "selftest passed")
    return bad


def _raises(fn):
    try:
        fn()
    except (Fm1Error, Fm1FrameError):
        return True
    return False


# --------------------------------------------------------------------- main ---

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--port", default=None, help="port name text (default: Felucca, else FM-1)")
    ap.add_argument("--write", action="store_true", help="allow commands that change the FM-1")
    ap.add_argument("--slot", dest="slot_confirm", type=int, help="project-save: repeat the slot to confirm")
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("ports")
    sub.add_parser("info")
    sub.add_parser("ping")
    sub.add_parser("song")
    sub.add_parser("learn-get")
    p = sub.add_parser("track"); p.add_argument("n", type=int, nargs="?")
    p = sub.add_parser("track-dump"); p.add_argument("n", type=int)
    p = sub.add_parser("step-get"); p.add_argument("track", type=int); p.add_argument("index", type=int)
    p = sub.add_parser("step-set")
    p.add_argument("track", type=int); p.add_argument("index", type=int); p.add_argument("note", type=int)
    p.add_argument("--accent", action="store_true"); p.add_argument("--slide", action="store_true")
    p.add_argument("--vel", type=int, default=100)
    p = sub.add_parser("param-get"); p.add_argument("track", type=int); p.add_argument("id", type=int)
    p = sub.add_parser("param-set")
    p.add_argument("track", type=int); p.add_argument("id", type=int); p.add_argument("value", type=int)
    p = sub.add_parser("mix")
    p.add_argument("track", type=int); p.add_argument("level", type=int, nargs="?"); p.add_argument("mute", type=int, nargs="?")
    p = sub.add_parser("desc"); p.add_argument("scope", type=int); p.add_argument("id", type=int)
    p = sub.add_parser("global-get"); p.add_argument("id", type=int)
    p = sub.add_parser("global-set"); p.add_argument("id", type=int); p.add_argument("value", type=int)
    p = sub.add_parser("project-query"); p.add_argument("slot", type=int)
    p = sub.add_parser("project-save"); p.add_argument("slot", type=int)
    p = sub.add_parser("project-load"); p.add_argument("slot", type=int)
    p = sub.add_parser("note"); p.add_argument("ch", type=int); p.add_argument("note", type=int)
    p.add_argument("--vel", type=int, default=100); p.add_argument("--dur", type=float, default=1.0)
    p = sub.add_parser("listen"); p.add_argument("seconds", type=float, nargs="?", default=30)
    p = sub.add_parser("send-syx"); p.add_argument("file")
    p = sub.add_parser("raw"); p.add_argument("rcmd", type=int); p.add_argument("bytes", type=int, nargs="*")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.cmd:
        ap.error("give a command or --selftest")
    try:
        run(a)
    except (Fm1Error, Fm1FrameError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
