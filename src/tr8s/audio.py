"""
Layer 2 — audio capture and analysis.

The TR-8S streams its own output back over USB, which turns "what does this
byte do?" into a measurable question instead of a guess. That closed loop is
what produced the tone catalogue and the tune calibration.

Capture quirks, all learned the hard way:

  * `FLOAT_LE` at 96 kHz is the ONLY format the device accepts.
  * 14 channels gives individual instrument outputs; 2 gives the mix.
  * Python's `wave` module cannot read IEEE-float WAVs (format tag 3), so the
    parsing here is by hand.
  * Plain autocorrelation does NOT detect pitch -- it always peaks at the
    shortest lag. Use the cumulative-mean normalised difference (YIN).

No numpy: everything is stdlib, decimated hard enough to stay fast.
"""

from __future__ import annotations

import array
import math
import os
import re
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import config

RATE = 96000
FORMAT = "FLOAT_LE"

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


class AudioError(RuntimeError):
    pass


# ------------------------------------------------------------------- capture
# Linux records with ALSA's `arecord`; macOS with ffmpeg's AVFoundation input, which names the
# device ("TR-8S") instead of an ALSA card. Both write a 96 kHz IEEE-float WAV that `read_wav` reads.

MAC_DEVICE_HINTS = ("TR-8S", "TR8S")
LIST_TIMEOUT = 15             # seconds for ffmpeg to list the devices
SLACK = 20                    # seconds a recording may run past its length before it counts as hung


def _run(cmd: list, timeout: float):
    """A recorder or lister, with no terminal attached and a deadline. Failures are AudioError."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=timeout)
    except FileNotFoundError:
        raise AudioError(f"{cmd[0]} is not installed"
                         + (" (brew install ffmpeg)" if cmd[0] == "ffmpeg" else "")) from None
    except subprocess.TimeoutExpired:
        raise AudioError(f"{cmd[0]} did not finish within {timeout:g} s") from None


def mac_audio_devices() -> list:
    """The AVFoundation audio input names ffmpeg lists, in its order. Read-only."""
    proc = _run(["ffmpeg", "-nostdin", "-hide_banner", "-f", "avfoundation", "-list_devices", "true",
                 "-i", ""], timeout=LIST_TIMEOUT)
    names, audio = [], False
    for line in proc.stderr.splitlines():
        if "AVFoundation audio devices" in line:
            audio = True
        elif "AVFoundation video devices" in line:
            audio = False
        elif audio:
            m = re.search(r"\[(\d+)\] (.+)$", line)
            if m:
                names.append(m.group(2).strip())
    return names


def find_device() -> str:
    """The capture device: TR8S_AUDIO if set, else the TR-8S (an ALSA card, or the AVFoundation name)."""
    env = os.environ.get("TR8S_AUDIO")
    if env:
        return env
    if sys.platform != "darwin":
        return config.find_audio_device()
    names = mac_audio_devices()
    for n in names:
        if any(h in n for h in MAC_DEVICE_HINTS):
            return n
    raise AudioError("no TR-8S audio input in CoreAudio (AVFoundation lists: "
                     f"{', '.join(names) or 'nothing'}). Is it connected, and is Roland's "
                     "TR-8S driver installed? Override with TR8S_AUDIO=\"<device name>\"")


def capture_cmd(seconds: float, out: Path, channels: int, device: str,
                platform: str | None = None) -> list:
    """The recorder's argv for this platform. Pure, so a test can check it."""
    secs = str(max(1, int(round(seconds))))
    if (platform or sys.platform) == "darwin":
        # the first `channels` channels as they are (1-2 are the TR-8S's mix), never a downmix
        pan = f"pan={channels}c|" + "|".join(f"c{i}=c{i}" for i in range(channels))
        return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-f", "avfoundation",
                "-i", f":{device}", "-t", secs, "-af", pan, "-ar", str(RATE),
                "-c:a", "pcm_f32le", str(out)]
    return ["arecord", "-D", device, "-f", FORMAT, "-c", str(channels), "-r", str(RATE),
            "-d", secs, str(out)]


def record(seconds: float, path: str | Path | None = None,
           channels: int = 2, device: str | None = None) -> Path:
    """Record the TR-8S's output. Blocks until done."""
    out = Path(path) if path else config.subdir("recordings") / "capture.wav"
    cmd = capture_cmd(seconds, out, channels, device or find_device())
    proc = _run(cmd, timeout=seconds + SLACK)
    if proc.returncode != 0:
        raise AudioError(f"{cmd[0]} failed: {proc.stderr.strip()}")
    return out


def record_async(seconds: float, path: str | Path | None = None,
                 channels: int = 2, device: str | None = None):
    """Start a recording and return (process, path) so triggers can run during it. The recorder
    gets no terminal (stdin is /dev/null): ffmpeg would otherwise read keys from it."""
    out = Path(path) if path else config.subdir("recordings") / "capture.wav"
    cmd = capture_cmd(seconds, out, channels, device or find_device())
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        raise AudioError(f"{cmd[0]} is not installed") from None
    return proc, out


def read_wav(path: str | Path) -> tuple[int, int, array.array]:
    """(channels, rate, samples). Handles the IEEE-float WAVs arecord and ffmpeg write."""
    d = Path(path).read_bytes()
    if d[:4] != b"RIFF" or d[8:12] != b"WAVE":
        raise AudioError(f"{path} is not a RIFF/WAVE file")
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(d):
        cid = d[pos:pos + 4]
        size = struct.unpack("<I", d[pos + 4:pos + 8])[0]
        if cid == b"fmt ":
            fmt = struct.unpack("<HHIIHH", d[pos + 8:pos + 8 + 16])
        elif cid == b"data":
            data = d[pos + 8:pos + 8 + size]
            break
        pos += 8 + size + (size & 1)
    if fmt is None or data is None:
        raise AudioError(f"{path}: missing fmt or data chunk")
    a = array.array("f")
    a.frombytes(data[:len(data) // 4 * 4])
    return fmt[1], fmt[2], a


def mono(samples: array.array, channels: int, decimate: int = 1) -> list[float]:
    """Interleaved frames -> a mono list, optionally decimated in one pass."""
    n = len(samples) // channels
    out: list[float] = []
    acc, cnt = 0.0, 0
    for f in range(n):
        base = f * channels
        s = 0.0
        for c in range(channels):
            s += samples[base + c]
        acc += s / channels
        cnt += 1
        if cnt == decimate:
            out.append(acc / decimate)
            acc, cnt = 0.0, 0
    return out


# ------------------------------------------------------------------ analysis

def yin(seg: list[float], rate: float, fmin: float = 30.0, fmax: float = 2000.0,
        threshold: float = 0.15) -> float | None:
    """
    Cumulative-mean normalised difference pitch detection.

    Plain autocorrelation peaks at the smallest lag and reports the frequency
    ceiling for everything; the normalisation is what makes this work.
    """
    if len(seg) < 256:
        return None
    m = sum(seg) / len(seg)
    seg = [x - m for x in seg]
    if sum(x * x for x in seg) < 1e-10:
        return None
    lo = max(2, int(rate / fmax))
    hi = min(len(seg) // 2, int(rate / fmin))
    if hi <= lo:
        return None

    diff = [0.0] * (hi + 1)
    for lag in range(lo, hi + 1):
        s = 0.0
        for i in range(0, len(seg) - lag, 2):     # stride 2: plenty for a fundamental
            d = seg[i] - seg[i + lag]
            s += d * d
        diff[lag] = s

    cmnd = [1.0] * (hi + 1)
    running = 0.0
    for lag in range(lo, hi + 1):
        running += diff[lag]
        cmnd[lag] = diff[lag] * (lag - lo + 1) / running if running > 0 else 1.0

    best = None
    for lag in range(lo + 1, hi):
        if cmnd[lag] < threshold and cmnd[lag] <= cmnd[lag - 1] \
                and cmnd[lag] <= cmnd[lag + 1]:
            best = lag
            break
    if best is None:
        best = min(range(lo, hi + 1), key=lambda L: cmnd[L])
        if cmnd[best] > 0.55:
            return None
    confidence = 1.0 - cmnd[int(best)]
    if lo < best < hi:                            # parabolic refinement
        a, b, c = cmnd[best - 1], cmnd[best], cmnd[best + 1]
        den = 2 * (a - 2 * b + c)
        if den:
            best = best + (a - c) / den
    freq = rate / best
    return freq if confidence > 0.35 else None


def _fft(x: list[complex]) -> list[complex]:
    n = len(x)
    x = list(x)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j |= bit
        if i < j:
            x[i], x[j] = x[j], x[i]
    length = 2
    while length <= n:
        ang = -2 * math.pi / length
        wl = complex(math.cos(ang), math.sin(ang))
        for i in range(0, n, length):
            w = complex(1)
            for k in range(length // 2):
                u = x[i + k]
                v = x[i + k + length // 2] * w
                x[i + k] = u + v
                x[i + k + length // 2] = u - v
                w *= wl
        length <<= 1
    return x


def spectrum(seg: list[float], rate: float, n: int = 2048) -> list[float]:
    seg = list(seg[:n])
    if len(seg) < 64:
        return []
    seg += [0.0] * (n - len(seg))
    win = [seg[i] * (0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1)))
           for i in range(n)]
    spec = _fft([complex(v) for v in win])
    return [abs(spec[i]) for i in range(n // 2)]


def centroid(seg: list[float], rate: float, n: int = 2048) -> float | None:
    """Spectral centre of mass in Hz -- a workable proxy for brightness."""
    mags = spectrum(seg, rate, n)
    if not mags:
        return None
    num = den = 0.0
    for i in range(1, len(mags)):
        num += mags[i] * (i * rate / n)
        den += mags[i]
    return num / den if den else None


def envelope(seg: list[float], rate: float, window_s: float = 0.005) -> list[float]:
    win = max(1, int(window_s * rate))
    return [max(abs(x) for x in seg[i:i + win])
            for i in range(0, max(0, len(seg) - win), win)]


def note_of(freq: float | None) -> tuple[str, int] | tuple[None, None]:
    if not freq or freq <= 0:
        return None, None
    m = 12 * math.log2(freq / 440.0) + 69
    i = int(round(m))
    return f"{NOTE_NAMES[i % 12]}{i // 12 - 1}", round((m - i) * 100)


@dataclass
class Measurement:
    peak: float = 0.0
    rms: float = 0.0
    decay_ms: int | None = None
    sustained: bool = False
    centroid: int | None = None
    hz: float | None = None
    root: str | None = None
    cents: int | None = None
    silent: bool = False

    def as_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if v is not None}
        return d


def measure(seg: list[float], rate: float, pitch: bool = True) -> Measurement:
    """Everything knowable about one hit, from one window of audio."""
    if not seg:
        return Measurement(silent=True)
    peak = max(abs(x) for x in seg)
    if peak < 0.002:
        return Measurement(peak=round(peak, 5), silent=True)

    m = Measurement(peak=round(peak, 5),
                    rms=round((sum(x * x for x in seg) / len(seg)) ** 0.5, 5))
    env = envelope(seg, rate)
    if env:
        target = peak * 0.1                    # -20 dB
        start = env.index(max(env))
        win_s = 0.005
        for i in range(start, len(env)):
            if env[i] < target:
                m.decay_ms = round((i - start) * win_s * 1000)
                break
        else:
            m.sustained = True
    c = centroid(seg, rate)
    if c:
        m.centroid = round(c)
    if pitch:
        f = yin(seg[int(0.02 * rate):int(0.22 * rate)], rate)
        if f:
            m.hz = round(f, 2)
            m.root, m.cents = note_of(f)
    return m
