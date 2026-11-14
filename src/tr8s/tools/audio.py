"""audio tools — see the package docstring for the conventions."""

from __future__ import annotations

import inspect
import re
import subprocess
from pathlib import Path

from .. import config
from ..device import Device, DeviceError, panel_to_slot, slot_to_panel
from ..history import HISTORY
from ..kit import FIELDS as KIT_FIELDS
from ..kit import TRACKS, Kit
from ..melody import MelodyError
from ..melody import read as melody_read
from ..melody import write as melody_write
from ..pattern import VARIATIONS, Pattern
from ..tones import Catalog
from ._core import (DEFAULT_KEYS, DEFAULT_LINE, REGISTRY, ToolError,
                    _library_dir, _slot, opt, tool)
from ._core import device as _device_helper

@tool("audio.record",
      "Record the TR-8S's own audio output to a wav file. Useful for checking "
      "what actually came out.",
      {"seconds": {"type": "number", "minimum": 0.5, "maximum": 60},
       "path": opt({"type": "string"})})
def audio_record(seconds: float, path: str | None = None):
    from .. import audio
    try:
        dev = audio.find_device()
        out = audio.record(seconds, path, device=dev)
    except audio.AudioError as e:
        raise ToolError(str(e)) from None
    return {"path": str(out), "seconds": seconds, "device": dev,
            "format": "FLOAT_LE 96kHz stereo (the only format the TR-8S accepts)"}


# ------------------------------------------------------------------- history

