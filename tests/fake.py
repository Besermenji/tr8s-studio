"""
A fake Transport backed by captured blobs, so everything above the wire can be
tested with no TR-8S attached.

It deliberately reproduces the device's real behaviours rather than an idealised
version, because those are what the code has to cope with:

  * a transfer writes the slot immediately -- there is no scratch buffer
  * `level` in a kit is overwritten by the "fader" on write
  * reads of an unknown slot return None
"""

from __future__ import annotations

from pathlib import Path

from tr8s.kit import TRACKS as KIT_TRACKS
from tr8s.kit import Kit
from tr8s.transport import BLOB_SIZES

FIXTURES = Path(__file__).parent / "fixtures"


def _blank(kind: str) -> bytes:
    return bytes(BLOB_SIZES[kind])


class FakeTransport:
    """Mimics the Transport surface the layers above actually use."""

    def __init__(self, patterns=None, kits=None, fader_level=200):
        self.path = "fake://tr8s"
        self.device_id = 0x10
        self.on_realtime = None
        self.on_channel = None
        self.fader_level = fader_level
        self.slots = {
            "pattern": dict(patterns or {}),
            "kit": dict(kits or {}),
            "tone": {},
            "system": {},
        }
        self.commits: list[tuple[str, int]] = []
        self.sent: list[tuple[str, int]] = []
        self.opened = False

    # ---------------------------------------------------------- lifecycle

    def open(self):
        self.opened = True

    def close(self):
        self.opened = False

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    # ------------------------------------------------------------- device

    def firmware(self) -> dict:
        return {"version": "2.51", "revision": "0B97"}

    def note(self, note, velocity=110, channel=9, length=0.0):
        pass

    def drain(self):
        pass

    def collect(self, idle=0.0, hard_cap=0.0) -> bytes:
        return b""

    # -------------------------------------------------------------- blobs

    def read_blob(self, kind: str, index: int, timeout: float = 0.0):
        return self.slots.get(kind, {}).get(index)

    def send_blob(self, kind: str, slot: int, blob: bytes,
                  settle: float = 0.0, ack_wait: float = 0.0) -> bool:
        if len(blob) != BLOB_SIZES[kind]:
            raise ValueError(f"{kind} blob is {len(blob)} bytes")
        blob = bytearray(blob)
        if kind == "kit":
            # the device overwrites level with the physical fader position
            for inst in KIT_TRACKS:
                blob[Kit.record_offset(inst) + 4] = self.fader_level
        # a transfer writes the slot straight away -- no scratch buffer
        self.slots[kind][slot] = bytes(blob)
        self.sent.append((kind, slot))
        return True

    def commit(self, kind: str, slot: int):
        self.commits.append((kind, slot))

    # --------------------------------------------------------- the machine

    def select_pattern(self, index: int) -> bool:
        """The temp-address writes that move the machine to a pattern."""
        self.selected = getattr(self, "selected", []) + [index]
        return True


def load_fixture_pattern() -> bytes:
    p = FIXTURES / "pattern.bin"
    return p.read_bytes() if p.exists() else _blank("pattern")


def load_fixture_kit() -> bytes:
    p = FIXTURES / "kit.bin"
    return p.read_bytes() if p.exists() else _blank("kit")


def load_empty_kit() -> bytes:
    """A real blank "----" slot: every sample-parameter byte is zero."""
    p = FIXTURES / "kit_empty.bin"
    return p.read_bytes() if p.exists() else _blank("kit")


def make_device(patterns=None, kits=None):
    """A Device wired to a FakeTransport, pre-loaded with fixtures."""
    from tr8s.device import Device
    pats = patterns if patterns is not None else {0: load_fixture_pattern()}
    kts = kits if kits is not None else {0: load_fixture_kit()}
    t = FakeTransport(patterns=pats, kits=kts)
    d = Device(transport=t)
    d.open()
    return d, t


# ------------------------------------------------------------------ FM-1

class FakeFm1:
    """
    An FM-1 at the `rtmidi` level, so the real `Fm1Port` runs against it. It
    answers every editor frame with the same cmd, as the firmware does: a step
    write is echoed (plus the five extra fields the real reply carries), a
    project save is answered `01 slot 01`, anything else echoes its args.

      silent    answer nothing (a port with no FM-1 behind it)
      mangle    the first step write is echoed with a different note
      clamp     a track parameter (the length) is answered with a different value
      playing   the transport runs: a song set is answered rc 2 (busy)

    It keeps a song chain: op 5 stores it (rc 0), op 4 answers it in the firmware's layout
    (`op rc count running row remaining rows...`), and a project save copies it into `slots`.
    """

    def __init__(self, silent=False, mangle=False, clamp=False, playing=False):
        self.sent: list = []
        self.silent, self.mangle, self.clamp, self.playing = silent, mangle, clamp, playing
        self.chain: list = []
        self.slots: dict = {}
        self._cb = None

    def receive(self, msg):
        from tr8s.synth.minitone import CMDS, HEADER
        self.sent.append(bytes(msg))
        if self.silent or self._cb is None:
            return
        f = bytes(msg)
        if f[:4] != HEADER:
            return
        cmd, args = f[4], f[5:-1]
        if cmd == CMDS["TRACK_STEP"] and len(args) == 10:
            reply = bytearray(args) + bytes([1, 100, 1, 0, 0])
            if self.mangle:
                reply[3] = (reply[3] + 1) % 128
                self.mangle = False
        elif cmd == CMDS["PROJECT"] and args[0] == 1:
            self.slots[args[1]] = [list(r) for r in self.chain]
            reply = bytes([1, args[1], 1])
        elif cmd == CMDS["SONG"] and args[0] in (4, 5):
            rc = 0
            if args[0] == 5:
                if self.playing:
                    rc = 2
                else:
                    n = args[1]
                    self.chain = [list(args[2 + i * 7:9 + i * 7]) for i in range(n)]
            reply = bytes([args[0], rc, len(self.chain), int(self.playing), 0, self.chain[0][6] if self.chain else 0])
            reply += bytes(b for r in self.chain for b in r)
        elif cmd == CMDS["SET"] and len(args) == 4 and args[1] == 20:      # G_ENGSEL: the reply says 0
            reply = bytes(args[:2]) + bytes([0, 0x40])
        elif cmd == CMDS["TRACK_PARAM"] and self.clamp and len(args) == 4:
            reply = bytes(args[:2]) + bytes([(args[2] + 1) % 128, args[3]])
        else:
            reply = args
        self._cb((list(HEADER) + [cmd] + list(reply) + [0xF7], 0.0))


def fake_fm1_rtmidi(monkeypatch, fm1=None, ports=("Felucca", "TR-8S")):
    """Install a fake `rtmidi` module wired to `fm1` (a FakeFm1); returns it."""
    import sys
    import types
    fm1 = fm1 or FakeFm1()

    class _In:
        def get_ports(self):
            return list(ports)

        def get_port_name(self, i):
            return ports[i]

        def ignore_types(self, **kw):
            pass

        def set_callback(self, fn):
            fm1._cb = fn

        def cancel_callback(self):
            fm1._cb = None

        def open_port(self, i):
            pass

        def close_port(self):
            pass

    class _Out(_In):
        def send_message(self, msg):
            fm1.receive(msg)

    mod = types.ModuleType("rtmidi")
    mod.MidiIn, mod.MidiOut = _In, _Out
    monkeypatch.setitem(sys.modules, "rtmidi", mod)
    monkeypatch.setattr("tr8s.synth.port.CANCEL_SETTLE", 0)
    return fm1
