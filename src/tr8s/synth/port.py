"""
The FM-1's USB MIDI port: send editor-protocol SysEx and wait for each reply.

The one place the studio talks to the FM-1. `midi/fm1_spike.py` uses it too, so
there is a single copy of the port handling. `python-rtmidi` is imported only
when a port is opened (the `mac` extra); the core keeps zero dependencies.

Two rules hold here, not only in the encoders:

* nothing that is MIDI realtime (F8-FC) is ever sent: the TR-8S is the only
  clock, and a stray Start or Clock from the studio would fight it;
* every frame `write_frames` sends must pass `minitone.check_edit_frame`
  first, all of them before any is sent, so a bad file changes nothing.
"""

from __future__ import annotations

import os
import queue
import time

from .minitone import (CMDS, HEADER, NAMES, check_edit_frame, frame,
                       save_frame)

DEFAULT_PORT = "Felucca"        # the CoreMIDI name under our Mini-Tone build
STOCK_PORT = "FM-1"             # and under M-VAVE's stock firmware
REALTIME = range(0xF8, 0xFD)    # F8 clock, F9, FA start, FB continue, FC stop
CANCEL_SETTLE = 0.1
STEP_TIMEOUT = 2.0
SAVE_TIMEOUT = 6.0              # a flash write takes about two seconds


class Fm1Error(RuntimeError):
    pass


class Fm1EchoError(Fm1Error):
    """The FM-1 answered every frame, but stored some steps differently from what was sent."""

    def __init__(self, message, mismatches):
        super().__init__(message)
        self.mismatches = mismatches        # [(frame number, sent frame, reply args)]


def parse_reply(pkt):
    """(cmd, args) of a frame with our header, else None."""
    if len(pkt) < 6 or pkt[:4] != HEADER or pkt[-1] != 0xF7:
        return None
    return pkt[4], pkt[5:-1]


def port_name() -> str:
    return os.environ.get("FM1_PORT", DEFAULT_PORT)


class Fm1Port:
    """
    One open FM-1. `name` is matched as a case-insensitive substring of the
    port names (default `Felucca`, or `FM1_PORT`). `log(kind, data)` is called
    for every frame sent and received, if given.
    """

    def __init__(self, name: str | None = None, log=None):
        try:
            import rtmidi
        except ImportError:
            raise Fm1Error("python-rtmidi is not installed: pip install -e '.[mac]'") from None
        self._log = log or (lambda kind, data: None)
        self.q = queue.Queue()
        self.mi, self.mo = rtmidi.MidiIn(), rtmidi.MidiOut()
        want = self._pick(name)
        i, o = self._find(self.mi, want), self._find(self.mo, want)
        self.mi.ignore_types(sysex=False, timing=False, active_sense=True)

        def cb(event, _data=None):
            self.q.put(bytes(event[0]))
        self.mi.set_callback(cb)
        self.mi.open_port(i)
        self.mo.open_port(o)
        self.name = self.mi.get_port_name(i) if hasattr(self.mi, "get_port_name") else want

    @staticmethod
    def _pick(name):
        """The port to open. With no name given (and no FM1_PORT), `Felucca` (our Mini-Tone build),
        else `FM-1` (stock firmware), by looking at what is plugged in."""
        if name or os.environ.get("FM1_PORT"):
            return name or port_name()
        import rtmidi
        have = " ".join(rtmidi.MidiOut().get_ports()).lower()
        return DEFAULT_PORT if DEFAULT_PORT.lower() in have or STOCK_PORT.lower() not in have else STOCK_PORT

    @staticmethod
    def _find(dev, name):
        ports = dev.get_ports()
        for n, p in enumerate(ports):
            if name.lower() in p.lower():
                return n
        raise Fm1Error(f"no MIDI port containing {name!r} (is the FM-1 plugged in? "
                       f"set FM1_PORT to choose another); ports: {ports}")

    def send(self, data) -> None:
        data = bytes(data)
        if any(b in REALTIME for b in data):
            raise Fm1Error("refusing to send MIDI realtime (F8-FC): the TR-8S is the only clock")
        self._log("tx", data)
        self.mo.send_message(list(data))

    def request(self, cmd: int, args=(), timeout: float | None = None) -> bytes:
        """Send one request and return the args of the first reply with our header and the same cmd."""
        timeout = STEP_TIMEOUT if timeout is None else timeout
        while True:                                  # a reply that came in late belongs to no request now
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        self.send(frame(cmd, args))
        end = time.monotonic() + timeout
        while True:
            left = end - time.monotonic()
            if left <= 0:
                break
            try:
                pkt = self.q.get(timeout=max(0.01, left))
            except queue.Empty:
                break
            p = parse_reply(pkt)
            if p and p[0] == cmd:
                self._log("rx", pkt)
                return bytes(p[1])
            if pkt and pkt[0] != 0xF8:
                self._log("rx-other", pkt)
        raise Fm1Error(f"no reply to {NAMES.get(cmd, cmd)} within {timeout:.1f}s; "
                       f"is the FM-1 on the {self.name!r} port and running our Mini-Tone build?")

    def write_frames(self, frames) -> dict:
        """
        Send edit frames one at a time, waiting for each reply. Every frame is
        checked before the first is sent. A step the FM-1 stored differently
        from what was sent raises `Fm1EchoError` after the whole batch has gone.
        """
        frames = [bytes(f) for f in frames]
        for f in frames:
            check_edit_frame(f)
        kinds: dict = {}
        odd, differs = [], []
        for n, f in enumerate(frames, 1):
            kinds[NAMES[f[4]]] = kinds.get(NAMES[f[4]], 0) + 1
            reply = self.request(f[4], f[5:-1])
            if f[4] == CMDS["TRACK_STEP"]:
                if reply[:len(f) - 6] != f[5:-1]:
                    odd.append((n, f, reply))       # the step echo is verified on hardware: a miss is an error
            elif f[4] in (CMDS["SET"], CMDS["TRACK_PARAM"]) and reply[:len(f) - 6] != f[5:-1]:
                # engine and length: the reply layout is documented, not yet seen on our unit, so report
                differs.append(f"{NAMES[f[4]]} frame {n}: sent {f[5:-1].hex(' ')}, reply {reply.hex(' ')}")
        if odd:
            raise Fm1EchoError(f"{len(odd)} of {len(frames)} frames came back different "
                               f"(first: frame {odd[0][0]})", odd)
        return {"frames": len(frames), "kinds": kinds, "differs": differs}

    def save(self, project: int) -> dict:
        """Save the working music to project slot 0..3. Stops the FM-1's transport and writes flash."""
        f = save_frame(project)
        reply = self.request(f[4], f[5:-1], timeout=SAVE_TIMEOUT)
        if reply[:2] != bytes([1, project]):
            raise Fm1Error(f"the FM-1 answered the save of project {project} with {reply.hex(' ')}")
        return {"project": project, "used": bool(reply[2]) if len(reply) > 2 else None}

    def close(self) -> None:
        # cancel_callback -> settle -> close_port: close_port can deadlock otherwise
        # (the same order as midi_backends.RtMidiBackend._dispose)
        try:
            self.mi.cancel_callback()
            time.sleep(CANCEL_SETTLE)
            self.mi.close_port()
            self.mo.close_port()
        except Exception:
            pass
