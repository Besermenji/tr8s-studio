"""
Runtime configuration. No paths are hardcoded to any one machine.

Resolution order for the data directory (backups, tone catalogue, templates):

    1. $TR8S_DATA
    2. $XDG_DATA_HOME/tr8s
    3. ~/.local/share/tr8s

The MIDI backend is ALSA rawmidi on Linux and CoreMIDI (python-rtmidi) on
macOS; $TR8S_MIDI_BACKEND=rawmidi|rtmidi overrides that.

The MIDI port can be overridden with $TR8S_PORT: a rawmidi node path
(/dev/snd/midiCxDy) on Linux, a CoreMIDI port name ("TR-8S") on macOS.
Otherwise the TR-8S is discovered automatically.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP = "tr8s"


def data_dir() -> Path:
    env = os.environ.get("TR8S_DATA")
    if env:
        p = Path(env).expanduser()
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
        p = base / APP
    p.mkdir(parents=True, exist_ok=True)
    return p


def subdir(name: str) -> Path:
    p = data_dir() / name
    p.mkdir(parents=True, exist_ok=True)
    return p


def patterns_dir() -> Path:
    return subdir("backups/patterns")


def kits_dir() -> Path:
    return subdir("backups/kits")


def tone_catalog_path() -> Path:
    return data_dir() / "tones.json"


def template_path() -> Path:
    """An empty-slot pattern blob, used as the base when authoring."""
    return data_dir() / "template_pattern.bin"


def settings_path() -> Path:
    return data_dir() / "studio.json"


def load_settings() -> dict:
    """
    Things the studio worked out about this machine and should not have to
    work out again -- which MIDI channel carries the pattern, for instance.
    """
    import json
    p = settings_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text())
    except (ValueError, OSError):
        return {}


def save_settings(values: dict):
    import json
    try:
        p = settings_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        current = load_settings()
        current.update(values)
        p.write_text(json.dumps(current, indent=1))
    except OSError:
        pass                    # a settings file we cannot write is not fatal


def midi_backend() -> str:
    """Which MIDI backend to use: "rawmidi" or "rtmidi"."""
    env = os.environ.get("TR8S_MIDI_BACKEND", "").strip().lower()
    if env:
        if env not in ("rawmidi", "rtmidi"):
            raise RuntimeError(
                f"TR8S_MIDI_BACKEND={env!r} is not a backend; use 'rawmidi' "
                f"(ALSA, Linux) or 'rtmidi' (python-rtmidi, macOS)")
        return env
    return "rtmidi" if sys.platform == "darwin" else "rawmidi"


def rtmidi_ports() -> tuple[list[str], list[str]]:
    """(input names, output names) as python-rtmidi lists them."""
    try:
        import rtmidi
    except ImportError as e:
        from .midi_backends import MISSING_RTMIDI, TransportError
        raise TransportError(MISSING_RTMIDI) from e
    return rtmidi.MidiIn().get_ports(), rtmidi.MidiOut().get_ports()


def find_port(kind: str | None = None) -> str:
    """Locate the TR-8S: a rawmidi device node, or a CoreMIDI port name."""
    env = os.environ.get("TR8S_PORT")
    if env:
        return env
    kind = kind or midi_backend()
    if kind == "rtmidi":
        return _find_rtmidi_port()
    # /proc/asound/cardN/midiM names the device; match the TR-8S
    for card in sorted(Path("/proc/asound").glob("card*")):
        for midi in sorted(card.glob("midi*")):
            try:
                if "TR-8S" in midi.read_text():
                    n = card.name.replace("card", "")
                    d = midi.name.replace("midi", "")
                    node = Path(f"/dev/snd/midiC{n}D{d}")
                    if node.exists():
                        return str(node)
            except OSError:
                continue
    for node in sorted(Path("/dev/snd").glob("midiC*D*")):
        return str(node)
    raise RuntimeError(
        "no TR-8S MIDI device found. Is it connected and powered on? "
        "Override with TR8S_PORT=/dev/snd/midiCxDy"
    )


def _find_rtmidi_port() -> str:
    """
    The TR-8S shows up on CoreMIDI as two ports, "TR-8S" and "TR-8S CTRL".
    Only the first carries clock, notes and knob messages; the CTRL port also
    answers SysEx but streams unsolicited data and no clock. Take the first.
    """
    ins, outs = rtmidi_ports()
    if "TR-8S" in ins:
        return "TR-8S"
    for p in ins:
        if "TR-8S" in p and "CTRL" not in p:
            return p
    from .midi_backends import TransportError
    raise TransportError(
        f"no TR-8S MIDI port found. Is it connected and powered on? Input "
        f"ports seen: {ins}, output ports seen: {outs}. Override with "
        f'TR8S_PORT="<name>"')


def find_audio_device() -> str:
    """ALSA capture device for the TR-8S's own audio stream."""
    env = os.environ.get("TR8S_AUDIO")
    if env:
        return env
    try:
        cards = Path("/proc/asound/cards").read_text()
    except OSError:
        return "hw:1,0"
    for line in cards.splitlines():
        if "TR-8S" in line or "TR8S" in line:
            num = line.strip().split()[0]
            return f"hw:{num},0"
    return "hw:1,0"
