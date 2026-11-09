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
    python3 fm1_spike.py song                       query the song sections
    python3 fm1_spike.py project-query SLOT
    python3 fm1_spike.py project-save SLOT --write --slot SLOT   writes the FM-1's flash
    python3 fm1_spike.py learn-get
    python3 fm1_spike.py ping
    python3 fm1_spike.py listen [SECONDS]           print incoming non-clock traffic
    python3 fm1_spike.py raw CMD [BYTE ...] --write

Port: first MIDI port whose name contains "FM-1" (override: --port TEXT or
FM1_PORT).  Needs `pip install python-rtmidi` (the repo's `.[mac]` extra).

Wire format (upstream web/EDITOR_PROTOCOL.md, Felucca / Mini-Tone):

    F0 7D 46 4C <cmd> <args...> F7        7D = non-commercial ID, 46 4C = "FL"
    every valid request gets exactly ONE reply with the same header and cmd.
    value (v14): 2 bytes, LSB first, holding value + 8192
    string:      ASCII bytes ended by a 0 byte

SAFETY
  * Read-only by default.  Anything that changes the FM-1 needs --write, and
    project-save also needs --slot to repeat the slot (flash write, ~2 s,
    stops the transport).
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

HEADER = bytes([0xF0, 0x7D, 0x46, 0x4C])
LOG = os.environ.get("FM1_SPIKE_LOG", "fm1-spike.jsonl")

CMDS = {
    "INFO": 1, "GET": 2, "SET": 3, "DUMP": 4, "DESC": 5, "STEP_GET": 6, "STEP_SET": 7,
    "PRESET": 8, "PROJECT": 9, "WATCH": 22, "CHANGED": 23, "RELOAD": 24, "PING": 25,
    "STEP_CHANGED": 26, "TRACK": 27, "TRACK_MIX": 28, "TRACK_DUMP": 29, "TRACK_STEP": 30,
    "TRACK_PARAM": 31, "TRACK_CHANGED": 32, "SONG": 33, "MOTION": 64, "LEARN_GET": 78,
    "LEARN_SET": 79,
}
NAMES = {v: k for k, v in CMDS.items()}
READ_ONLY = {"INFO", "GET", "DUMP", "DESC", "STEP_GET", "PING", "TRACK_DUMP", "LEARN_GET"}


class SpikeError(RuntimeError):
    pass


# ----------------------------------------------------------------- encoding ---

def v14(value):
    """-8192..8191 -> [lo, hi] (value + 8192, LSB first, 7 bits each)"""
    if not -8192 <= value <= 8191:
        raise SpikeError(f"value {value} outside -8192..8191")
    u = value + 8192
    return [u & 0x7F, u >> 7]


def from_v14(lo, hi):
    return (lo | hi << 7) - 8192


def frame(cmd, args=()):
    """a request frame; refuses anything that is not a 7-bit data byte (so no F8-FC, ever)"""
    args = list(args)
    bad = [a for a in args if not isinstance(a, int) or not 0 <= a < 0x80]
    if not 0 <= cmd < 0x80 or bad:
        raise SpikeError(f"not 7-bit data: cmd={cmd} args={bad}")
    return HEADER + bytes([cmd]) + bytes(args) + b"\xF7"


def parse_reply(pkt):
    """(cmd, args) of a frame with our header, else None"""
    if len(pkt) < 6 or pkt[:4] != HEADER or pkt[-1] != 0xF7:
        return None
    return pkt[4], pkt[5:-1]


def hexs(b):
    return " ".join(f"{x:02X}" for x in b)


def cstring(b, i=0):
    j = b.index(0, i)
    return b[i:j].decode("ascii", "replace"), j + 1


# --------------------------------------------------------------------- port ---

class Fm1:
    def __init__(self, name):
        try:
            import rtmidi
        except ImportError:
            raise SpikeError("python-rtmidi is not installed (pip install -e '.[mac]')")
        self.q = queue.Queue()
        self.mi, self.mo = rtmidi.MidiIn(), rtmidi.MidiOut()
        i, o = self._find(self.mi, name), self._find(self.mo, name)
        self.mi.ignore_types(sysex=False, timing=False, active_sense=True)

        def cb(event, _data=None):
            self.q.put(bytes(event[0]))
        self.mi.set_callback(cb)
        self.mi.open_port(i)
        self.mo.open_port(o)
        self.name = self.mi.get_port_name(i)

    @staticmethod
    def _find(dev, name):
        ports = dev.get_ports()
        for n, p in enumerate(ports):
            if name.lower() in p.lower():
                return n
        raise SpikeError(f"no MIDI port containing {name!r}; ports: {ports}")

    def send(self, data):
        if any(0xF8 <= b <= 0xFC for b in data):
            raise SpikeError("refusing to send MIDI realtime")
        log("tx", data)
        self.mo.send_message(list(data))

    def request(self, cmd, args=(), timeout=2.0):
        """one request, then the first reply with our header and the same cmd; clock and
        other traffic are logged and skipped"""
        self.send(frame(cmd, args))
        end = time.time() + timeout
        while time.time() < end:
            try:
                pkt = self.q.get(timeout=max(0.01, end - time.time()))
            except queue.Empty:
                break
            p = parse_reply(pkt)
            if p and p[0] == cmd:
                log("rx", pkt)
                return p[1]
            if pkt and pkt[0] != 0xF8:
                log("rx-other", pkt)
        raise SpikeError(f"no reply to {NAMES.get(cmd, cmd)} within {timeout:.1f}s")

    def close(self):
        # cancel_callback -> settle -> close_port: close_port can deadlock otherwise
        # (the same order as midi_backends.RtMidiBackend._dispose)
        try:
            self.mi.cancel_callback()
            time.sleep(0.1)
            self.mi.close_port()
            self.mo.close_port()
        except Exception:
            pass


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
        elif c == "song":
            print("raw:", hexs(fm.request(CMDS["SONG"], [4])))
        elif c == "project-query":
            print("raw:", hexs(fm.request(CMDS["PROJECT"], [2, a.slot])), " (op, slot, used)")
        elif c == "project-save":
            need_write(a, "project-save")
            if a.slot_confirm != a.slot:
                raise SpikeError("project-save writes flash: repeat the slot with --slot")
            print("raw:", hexs(fm.request(CMDS["PROJECT"], [1, a.slot], timeout=6.0)))
        elif c == "learn-get":
            print("raw:", hexs(fm.request(CMDS["LEARN_GET"])))
        elif c == "raw":
            need_write(a, "raw")
            print("raw:", hexs(fm.request(a.rcmd, a.bytes)))
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
    except SpikeError:
        return True
    return False


# --------------------------------------------------------------------- main ---

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--port", default=os.environ.get("FM1_PORT", "FM-1"))
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
    p = sub.add_parser("global-get"); p.add_argument("id", type=int)
    p = sub.add_parser("global-set"); p.add_argument("id", type=int); p.add_argument("value", type=int)
    p = sub.add_parser("project-query"); p.add_argument("slot", type=int)
    p = sub.add_parser("project-save"); p.add_argument("slot", type=int)
    p = sub.add_parser("listen"); p.add_argument("seconds", type=float, nargs="?", default=30)
    p = sub.add_parser("raw"); p.add_argument("rcmd", type=int); p.add_argument("bytes", type=int, nargs="*")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.cmd:
        ap.error("give a command or --selftest")
    try:
        run(a)
    except SpikeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
