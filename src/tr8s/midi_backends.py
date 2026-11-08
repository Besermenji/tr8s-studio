"""
The operating system's side of the MIDI connection.

`Transport` owns the protocol: it demultiplexes realtime, SysEx and channel
bytes in arrival order, serialises exchanges and frames blobs. A backend owns
only the port. It opens it, hands every incoming byte to one callback in the
order it arrived, and writes bytes out. No parsing happens here, so every
backend inherits the arrival-order rule from the shared layer.

    RawMidiBackend   an ALSA rawmidi node (Linux)
    RtMidiBackend    CoreMIDI through python-rtmidi (macOS; `pip install -e '.[mac]'`)
"""

from __future__ import annotations

import atexit
import os
import threading
import time
from typing import Callable, Protocol


class TransportError(RuntimeError):
    pass


MISSING_RTMIDI = ("python-rtmidi is not installed; "
                  "run: pip install -e '.[mac]'")


class MidiBackend(Protocol):
    name: str            # the port, for error messages
    kind: str            # "rawmidi" or "rtmidi"
    is_open: bool

    def open(self, on_bytes: Callable[[bytes], None]) -> None: ...
    def close(self) -> None: ...
    def send(self, data: bytes) -> None: ...


class RawMidiBackend:
    """A duplex ALSA rawmidi node, read by one daemon thread."""

    kind = "rawmidi"

    def __init__(self, path: str):
        self.name = path
        self.fd: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def is_open(self) -> bool:
        return self.fd is not None

    def open(self, on_bytes):
        if self.fd is not None:
            return
        try:
            self.fd = os.open(self.name, os.O_RDWR)
        except FileNotFoundError as e:
            raise TransportError(f"{self.name} not found; is the TR-8S on?") from e
        except PermissionError as e:
            raise TransportError(
                f"no permission on {self.name}; add your user to the 'audio' group"
            ) from e
        os.set_blocking(self.fd, False)
        # One reader thread owns the fd, and everything in this process shares
        # it. The OS will happily let a second process open the same node, but
        # incoming bytes go to whichever reader asks first -- two readers split
        # the stream and both see corruption. Run one process at a time.
        self._stop.clear()
        self._thread = threading.Thread(target=self._reader, args=(on_bytes,),
                                        daemon=True, name="tr8s-reader")
        self._thread.start()

    def _reader(self, on_bytes):
        while not self._stop.is_set():
            try:
                chunk = os.read(self.fd, 4096)
            except BlockingIOError:
                time.sleep(0.0005)
                continue
            except OSError:
                break
            if not chunk:
                continue
            on_bytes(chunk)

    def send(self, data: bytes):
        os.write(self.fd, data)

    def close(self):
        self._stop.set()
        th = self._thread
        if th is not None:
            th.join(timeout=1.0)
            self._thread = None
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class RtMidiBackend:
    """
    CoreMIDI through python-rtmidi, in callback mode. Polling would work for
    short replies, but its queue overflows during a 24 KB blob read.

    CoreMIDI delivers each SysEx as one whole F0..F7 message and fans input out
    to every client, but nothing here relies on either: the demux in
    `Transport._feed` copes with SysEx in parts, and a second process on macOS
    does not split the stream.
    """

    kind = "rtmidi"
    # After cancel_callback, new messages go to rtmidi's internal queue without
    # touching Python. This pause lets a callback that was already in flight
    # finish; see close().
    CANCEL_SETTLE = 0.1

    def __init__(self, port_name: str):
        self.name = port_name
        self._in = None
        self._out = None
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        return self._in is not None

    @staticmethod
    def _find(ports: list[str], name: str) -> int | None:
        if name in ports:
            return ports.index(name)
        for i, p in enumerate(ports):
            if name in p:
                return i
        return None

    def open(self, on_bytes):
        if self._in is not None:
            return
        try:
            import rtmidi
        except ImportError as e:
            raise TransportError(MISSING_RTMIDI) from e

        mi, mo = rtmidi.MidiIn(), rtmidi.MidiOut()
        try:
            # CoreMIDI lists inputs and outputs independently, so match each
            ins, outs = mi.get_ports(), mo.get_ports()
            i, o = self._find(ins, self.name), self._find(outs, self.name)
            if i is None or o is None:
                raise TransportError(
                    f"MIDI port {self.name!r} not found (inputs: {ins}, "
                    f"outputs: {outs}); is the TR-8S on? Override with "
                    f'TR8S_PORT="<name>"')
            mi.ignore_types(sysex=False, timing=False, active_sense=True)

            def cb(event, _data=None):
                try:
                    on_bytes(bytes(event[0]))
                except Exception:
                    pass      # an error in a listener must not kill rtmidi's thread

            mi.set_callback(cb)
            mi.open_port(i)
            mo.open_port(o)
        except Exception as e:
            self._dispose(mi, mo)
            if isinstance(e, TransportError):
                raise
            raise TransportError(f"could not open MIDI port {self.name!r}: {e}") from e
        self._in, self._out = mi, mo
        # A MidiIn that still has a callback when the interpreter exits can
        # hang forever (see close()), so close it on the way out.
        atexit.register(self.close)

    def _dispose(self, mi, mo):
        if mi is not None:
            # close_port() holds the GIL while CoreMIDI waits for an in-flight
            # Python callback, and that callback is waiting for the GIL: a
            # permanent hang, and the TR-8S's continuous clock makes an
            # in-flight callback nearly certain. Cancelling first and sleeping
            # (which releases the GIL) lets it finish.
            try:
                mi.cancel_callback()
            except Exception:
                pass
            time.sleep(self.CANCEL_SETTLE)
            try:
                mi.close_port()
            except Exception:
                pass
        if mo is not None:
            try:
                mo.close_port()
            except Exception:
                pass
        del mi, mo

    def send(self, data: bytes):
        self._out.send_message(list(data))

    def close(self):
        with self._lock:
            mi, mo = self._in, self._out
            self._in = self._out = None
        if mi is None and mo is None:
            return
        self._dispose(mi, mo)
        atexit.unregister(self.close)


def make_backend(port: str | None = None) -> MidiBackend:
    from . import config
    kind = config.midi_backend()
    port = port or config.find_port(kind)
    return RtMidiBackend(port) if kind == "rtmidi" else RawMidiBackend(port)
