"""
The MIDI backends and the demux they feed. A backend only moves bytes; the
Transport sorts them. These tests need neither hardware nor python-rtmidi (a
fake `rtmidi` module stands in for it).
"""

import os
import sys
import threading
import time
import types

import pytest

from tr8s import config
from tr8s.midi_backends import (MISSING_RTMIDI, RawMidiBackend, RtMidiBackend,
                                TransportError, make_backend)
from tr8s.transport import (DT1, Transport, make_sysex, split_sysex,
                            TransportError as ReexportedError)


def test_transport_error_is_importable_from_transport():
    assert ReexportedError is TransportError


# ------------------------------------------------------------------ demux

def _collecting_transport():
    t = Transport(path="fake")
    order = []
    t.on_realtime = lambda d: order.append(("rt", bytes(d)))
    t.on_channel = lambda d: order.append(("ch", bytes(d)))
    return t, order


def test_sysex_in_parts_is_reassembled_with_realtime_and_notes_in_order():
    """CoreMIDI may deliver a SysEx in fragments; the demux's own F0/F7 state
    machine must join them. Clock may fall between and inside the parts (a
    note-on cannot: a channel status byte ends a SysEx on the wire), and clock
    and notes must still reach their listeners in arrival order."""
    t, order = _collecting_transport()
    msg = make_sysex(DT1, [0x50, 0, 0, 0x13], [0x33, 0x2E, 0x30, 0x30])
    a, b, c = msg[:5], msg[5:11], msg[11:]

    t._feed(a + bytes([0xF8]))                         # part 1, clock after it
    t._feed(b[:2] + bytes([0xF8]) + b[2:])             # part 2, clock inside it
    t._feed(c + bytes([0xF8, 0x99, 36, 100, 0xF8]))    # tail, then clock, note, clock
    t._feed(bytes([0x99, 38, 90]))                     # a note in a later chunk

    assert split_sysex(t._take()) == [msg]
    assert order == [
        ("rt", b"\xf8"),
        ("rt", b"\xf8"),
        ("rt", b"\xf8"),
        ("ch", bytes([0x99, 36, 100])),
        ("rt", b"\xf8"),
        ("ch", bytes([0x99, 38, 90])),
    ]


def test_a_whole_sysex_amid_realtime_bytes():
    t, order = _collecting_transport()
    msg = make_sysex(DT1, [0x50, 0, 0, 0x13], [1, 2, 3])
    t._feed(bytes([0xF8]) + msg + bytes([0xF8, 0xF8]))
    assert split_sysex(t._take()) == [msg]
    assert order == [("rt", b"\xf8\xf8\xf8")]    # one burst: SysEx is not an event


def test_a_listener_error_does_not_stop_delivery():
    t = Transport(path="fake")
    seen = []

    def bad(d):
        seen.append(d)
        raise ValueError("boom")
    t.on_realtime = bad
    t._feed(bytes([0xF8]))
    t._feed(bytes([0xF8]))
    assert len(seen) == 2


def test_open_clears_state_and_close_without_open_is_safe():
    t = Transport(path="fake")
    t.close()                                           # no backend yet
    t._buf += b"stale"
    t._in_sysex = True

    class B:
        name, kind, is_open = "b", "rawmidi", False

        def open(self, on_bytes):
            self.on_bytes = on_bytes
            self.is_open = True

        def close(self):
            self.is_open = False

    t.backend = B()
    t.open()
    assert t._buf == b"" and not t._in_sysex
    t.backend.on_bytes(bytes([0xF0, 1, 0xF7]))
    assert t._take() == bytes([0xF0, 1, 0xF7])
    t.open()                                            # already open: no-op
    t.close()
    assert not t.backend.is_open


def test_firmware_retry_wording_follows_the_backend():
    def garbled(kind):
        t = Transport(path="fake")
        if kind:
            t.backend = types.SimpleNamespace(kind=kind)
        t.utility = lambda *a, **k: [bytes([0xF0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                                            0x50, 0x50, 0x50, 0x50, 0, 0xF7])]
        t.drain = lambda: None
        import tr8s.transport as tm
        real = tm.time.sleep
        tm.time.sleep = lambda s: None
        try:
            with pytest.raises(TransportError) as e:
                t.firmware_retry(attempts=2)
        finally:
            tm.time.sleep = real
        return str(e.value)

    assert "another program" in garbled("rawmidi")
    assert "another program" in garbled(None)
    rt = garbled("rtmidi")
    assert "another program" not in rt and "check the connection" in rt


# ---------------------------------------------------------------- rawmidi

def test_rawmidi_backend_delivers_bytes_from_the_node(monkeypatch):
    r, w = os.pipe()
    got = []
    be = RawMidiBackend("pipe")
    be.fd = r
    os.write(w, bytes([0xF8, 0x99, 36, 100, 0xF8, 0xF8, 0x99, 38, 100]))
    os.close(w)
    os.set_blocking(r, False)
    real_read = os.read

    def one_chunk(fd, n):
        data = real_read(fd, n)
        be._stop.set()      # on a closed pipe an empty read is EOF: stop the loop
        return data
    monkeypatch.setattr(os, "read", one_chunk)
    try:
        be._reader(got.append)
    finally:
        monkeypatch.setattr(os, "read", real_read)
        os.close(r)
    assert got == [bytes([0xF8, 0x99, 36, 100, 0xF8, 0xF8, 0x99, 38, 100])]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a FIFO")
def test_rawmidi_backend_opens_sends_and_closes(tmp_path):
    node = str(tmp_path / "midi")
    os.mkfifo(node)
    got = []
    be = RawMidiBackend(node)
    be.open(got.append)
    assert be.is_open
    be.send(b"\x90\x24\x64")            # a FIFO opened O_RDWR loops back
    deadline = time.time() + 2
    while not got and time.time() < deadline:
        time.sleep(0.005)
    be.close()
    be.close()                           # idempotent
    assert not be.is_open
    assert b"".join(got) == b"\x90\x24\x64"


def test_rawmidi_backend_reports_a_missing_node(tmp_path):
    with pytest.raises(TransportError, match="not found"):
        RawMidiBackend(str(tmp_path / "nope")).open(lambda b: None)


# ----------------------------------------------------------------- rtmidi

class FakePort:
    def __init__(self, log, kind, ports):
        self.log, self.kind, self._ports = log, kind, ports
        self.callback = None

    def get_ports(self):
        return list(self._ports)

    def open_port(self, i):
        self.log.append((self.kind, "open_port", i))

    def close_port(self):
        self.log.append((self.kind, "close_port"))

    def ignore_types(self, **kw):
        self.log.append((self.kind, "ignore_types", kw))

    def set_callback(self, fn):
        self.callback = fn
        self.log.append((self.kind, "set_callback"))

    def cancel_callback(self):
        self.callback = None
        self.log.append((self.kind, "cancel_callback"))

    def send_message(self, msg):
        self.log.append((self.kind, "send", list(msg)))


def fake_rtmidi(monkeypatch, ins=("TR-8S", "TR-8S CTRL"),
                outs=("TR-8S", "TR-8S CTRL")):
    log, made = [], {}
    mod = types.ModuleType("rtmidi")

    def MidiIn():
        made["in"] = FakePort(log, "in", ins)
        return made["in"]

    def MidiOut():
        made["out"] = FakePort(log, "out", outs)
        return made["out"]
    mod.MidiIn, mod.MidiOut = MidiIn, MidiOut
    monkeypatch.setitem(sys.modules, "rtmidi", mod)
    monkeypatch.setattr(RtMidiBackend, "CANCEL_SETTLE", 0)
    return log, made


@pytest.fixture(autouse=True)
def _no_atexit_leak(monkeypatch):
    import atexit
    registered = []
    monkeypatch.setattr(atexit, "register", lambda f, *a: registered.append(f))
    monkeypatch.setattr(atexit, "unregister",
                        lambda f: registered.remove(f) if f in registered else None)
    return registered


def test_rtmidi_opens_the_named_port_in_both_lists(monkeypatch):
    log, made = fake_rtmidi(monkeypatch, ins=("Other", "TR-8S"),
                            outs=("TR-8S CTRL", "Other", "TR-8S"))
    be = RtMidiBackend("TR-8S")
    be.open(lambda b: None)
    assert ("in", "open_port", 1) in log
    assert ("out", "open_port", 2) in log
    assert ("in", "ignore_types",
            {"sysex": False, "timing": False, "active_sense": True}) in log
    assert be.is_open


def test_rtmidi_callback_delivers_bytes_in_order_and_survives_errors(monkeypatch):
    log, made = fake_rtmidi(monkeypatch)
    got = []

    def on_bytes(b):
        got.append(b)
        if len(got) == 1:
            raise ValueError("listener bug")
    be = RtMidiBackend("TR-8S")
    be.open(on_bytes)
    cb = made["in"].callback
    cb(([0xF8], 0.0), None)
    cb(([0x99, 36, 100], 0.01), None)
    cb(([0xF0, 1, 2, 0xF7], 0.02), None)
    assert got == [b"\xf8", bytes([0x99, 36, 100]), bytes([0xF0, 1, 2, 0xF7])]
    assert all(isinstance(g, bytes) for g in got)


def test_rtmidi_send_forwards(monkeypatch):
    log, made = fake_rtmidi(monkeypatch)
    be = RtMidiBackend("TR-8S")
    be.open(lambda b: None)
    be.send(bytes([0xF0, 0x41, 0xF7]))
    assert ("out", "send", [0xF0, 0x41, 0xF7]) in log


def test_rtmidi_close_cancels_the_callback_first_and_is_idempotent(monkeypatch):
    log, made = fake_rtmidi(monkeypatch)
    be = RtMidiBackend("TR-8S")
    be.open(lambda b: None)
    log.clear()
    be.close()
    assert log == [("in", "cancel_callback"), ("in", "close_port"),
                   ("out", "close_port")]
    be.close()
    assert len(log) == 3
    assert not be.is_open


def test_rtmidi_close_pauses_between_cancel_and_close(monkeypatch):
    """The pause is what lets an in-flight callback finish; see LESSONS.md."""
    log, made = fake_rtmidi(monkeypatch)
    be = RtMidiBackend("TR-8S")
    be.open(lambda b: None)
    import tr8s.midi_backends as mb
    monkeypatch.setattr(mb.time, "sleep", lambda s: log.append(("sleep", s)))
    monkeypatch.setattr(RtMidiBackend, "CANCEL_SETTLE", 0.1)
    log.clear()
    be.close()
    assert log[:3] == [("in", "cancel_callback"), ("sleep", 0.1),
                       ("in", "close_port")]


def test_rtmidi_registers_and_removes_an_atexit_close(monkeypatch, _no_atexit_leak):
    fake_rtmidi(monkeypatch)
    be = RtMidiBackend("TR-8S")
    be.open(lambda b: None)
    assert be.close in _no_atexit_leak
    be.close()
    assert be.close not in _no_atexit_leak


def test_rtmidi_missing_port_lists_what_exists(monkeypatch):
    log, made = fake_rtmidi(monkeypatch, ins=("Foo",), outs=("Foo",))
    be = RtMidiBackend("TR-8S")
    with pytest.raises(TransportError) as e:
        be.open(lambda b: None)
    assert "TR-8S" in str(e.value) and "Foo" in str(e.value)
    assert not be.is_open
    assert ("in", "open_port", 0) not in log


def test_rtmidi_port_missing_from_one_list_only(monkeypatch):
    fake_rtmidi(monkeypatch, ins=("TR-8S",), outs=("Foo",))
    with pytest.raises(TransportError):
        RtMidiBackend("TR-8S").open(lambda b: None)


def test_rtmidi_module_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "rtmidi", None)    # makes `import rtmidi` fail
    with pytest.raises(TransportError) as e:
        RtMidiBackend("TR-8S").open(lambda b: None)
    assert MISSING_RTMIDI in str(e.value) and ".[mac]" in str(e.value)


def test_transport_drives_an_rtmidi_backend_end_to_end(monkeypatch):
    log, made = fake_rtmidi(monkeypatch)
    t = Transport(path="TR-8S", backend=RtMidiBackend("TR-8S"))
    seen = []
    t.on_realtime = lambda d: seen.append("clk")
    t.open()
    msg = make_sysex(DT1, [0x50, 0, 0, 0x13], [0x33])
    made["in"].callback(([0xF8], 0), None)
    made["in"].callback((list(msg), 0), None)
    assert seen == ["clk"] and split_sysex(t._take()) == [msg]
    t.send(b"\x90\x24\x64")
    assert ("out", "send", [0x90, 0x24, 0x64]) in log
    t.close()
    assert not t.backend.is_open


# ----------------------------------------------------------------- config

@pytest.fixture
def clean_env(monkeypatch):
    monkeypatch.delenv("TR8S_PORT", raising=False)
    monkeypatch.delenv("TR8S_MIDI_BACKEND", raising=False)


def test_midi_backend_defaults_by_platform(monkeypatch, clean_env):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert config.midi_backend() == "rtmidi"
    monkeypatch.setattr(sys, "platform", "linux")
    assert config.midi_backend() == "rawmidi"


def test_midi_backend_env_override(monkeypatch, clean_env):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("TR8S_MIDI_BACKEND", "rtmidi")
    assert config.midi_backend() == "rtmidi"
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("TR8S_MIDI_BACKEND", "RawMidi")
    assert config.midi_backend() == "rawmidi"


def test_midi_backend_rejects_nonsense(monkeypatch, clean_env):
    monkeypatch.setenv("TR8S_MIDI_BACKEND", "jack")
    with pytest.raises(RuntimeError, match="jack"):
        config.midi_backend()


def test_find_port_env_wins_for_both_kinds(monkeypatch, clean_env):
    monkeypatch.setenv("TR8S_PORT", "Whatever")
    assert config.find_port("rtmidi") == "Whatever"
    assert config.find_port("rawmidi") == "Whatever"
    assert config.find_port() == "Whatever"


def _ports(monkeypatch, ins, outs=None):
    monkeypatch.setattr(config, "rtmidi_ports",
                        lambda: (list(ins), list(ins if outs is None else outs)))


def test_find_port_prefers_the_main_port_over_ctrl(monkeypatch, clean_env):
    _ports(monkeypatch, ["TR-8S CTRL", "TR-8S"])
    assert config.find_port("rtmidi") == "TR-8S"


def test_find_port_falls_back_to_a_non_ctrl_match(monkeypatch, clean_env):
    _ports(monkeypatch, ["Other", "TR-8S CTRL", "TR-8S Main"])
    assert config.find_port("rtmidi") == "TR-8S Main"


def test_find_port_errors_helpfully(monkeypatch, clean_env):
    _ports(monkeypatch, ["IAC Driver Bus 1", "TR-8S CTRL"], ["Foo"])
    with pytest.raises(TransportError) as e:
        config.find_port("rtmidi")
    msg = str(e.value)
    assert "IAC Driver Bus 1" in msg and "TR-8S CTRL" in msg and "Foo" in msg
    assert 'TR8S_PORT="<name>"' in msg


def test_find_port_uses_the_backend_kind_by_default(monkeypatch, clean_env):
    monkeypatch.setenv("TR8S_MIDI_BACKEND", "rtmidi")
    _ports(monkeypatch, ["TR-8S"])
    assert config.find_port() == "TR-8S"


def test_rtmidi_ports_without_the_module(monkeypatch):
    monkeypatch.setitem(sys.modules, "rtmidi", None)
    with pytest.raises(TransportError, match=r"\.\[mac\]"):
        config.rtmidi_ports()


def test_make_backend_picks_by_kind(monkeypatch, clean_env):
    monkeypatch.setenv("TR8S_MIDI_BACKEND", "rtmidi")
    b = make_backend("TR-8S")
    assert isinstance(b, RtMidiBackend) and b.name == "TR-8S"
    monkeypatch.setenv("TR8S_MIDI_BACKEND", "rawmidi")
    b = make_backend("/dev/snd/midiC1D0")
    assert isinstance(b, RawMidiBackend) and b.name == "/dev/snd/midiC1D0"
    monkeypatch.setenv("TR8S_MIDI_BACKEND", "rtmidi")
    _ports(monkeypatch, ["TR-8S CTRL", "TR-8S"])
    assert make_backend(None).name == "TR-8S"
