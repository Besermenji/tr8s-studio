"""
Layer 3 — the device facade. One object that owns the connection and speaks in
Patterns, Kits and Tones rather than blobs.

Transfer semantics, measured -- and not what the naming suggests:

    A transfer writes the SLOT straight away, for patterns and kits alike. It
    shows up on read-back and survives writing other slots, so `commit=False`
    is NOT a scratch edit.

    The real asymmetry is whether the device re-reads it into what is playing:
    a pattern does, immediately (audible next loop); a kit does not, so the
    loaded kit keeps sounding until commit or reselection.

    commit() is presumed to be what makes a change durable across power-off.
    That has not been verified -- it needs a power cycle.
"""

from __future__ import annotations

import time
from pathlib import Path

from . import config
from .kit import REC_BASE as KIT_REC_BASE
from .kit import REC_STRIDE as KIT_REC_STRIDE
from .kit import Kit
from .pattern import Pattern
from .tones import CATEGORIES, Catalog, Tone
from .transport import BLOB_SIZES, MAX_SLOT, Transport, TransportError


class DeviceError(RuntimeError):
    pass


def check_blob(kind: str, blob: bytes | None) -> str | None:
    """
    Why a blob read off the machine cannot be what it claims to be, or None.

    A reply that starts mid-stream (another program on the port took the first
    chunks, or a late reply from an earlier request was glued on) still has the
    right length, so length alone proves nothing. The header does: every
    pattern the machine writes has a printable name, a tempo in range, a
    1-based kit number and a known scale; every kit a printable name and tone
    ids the machine has. The corrupt backup of 2026-11-13 fails this on every
    file (docs/LESSONS.md).
    """
    if blob is None:
        return "no reply"
    want = BLOB_SIZES[kind]
    if len(blob) != want:
        return f"{len(blob)} bytes, expected {want}"
    if kind in ("pattern", "kit"):
        name = blob[:16]
        if not all(32 <= c < 127 for c in name):
            return f"name bytes are not text ({bytes(name[:8]).hex()}...): the reply is misaligned"
    if kind == "pattern":
        tempo = blob[16] | blob[17] << 8
        if not 400 <= tempo <= 3000:
            return f"tempo {tempo / 10:g} is outside 40..300"
        if not 1 <= blob[18] <= 128:
            return f"kit byte {blob[18]} is outside 1..128"
        if blob[19] > 3:
            return f"scale byte {blob[19]} is outside 0..3"
    elif kind == "kit":
        for i in range(11):
            o = KIT_REC_BASE + i * KIT_REC_STRIDE
            tone = blob[o] | blob[o + 1] << 8
            if tone > 1023:
                return f"instrument {i + 1} names tone {tone}, past 1023"
    return None


def panel_to_slot(bank: int, index: int) -> int:
    """Panel '8-03' -> linear slot 114."""
    if not 1 <= bank <= 8 or not 1 <= index <= 16:
        raise ValueError("bank must be 1..8 and index 1..16")
    return (bank - 1) * 16 + (index - 1)


def slot_to_panel(slot: int) -> str:
    return f"{slot // 16 + 1}-{slot % 16 + 1:02d}"


class Device:
    def __init__(self, port: str | None = None, device_id: int | None = None,
                 transport=None):
        # `transport` is injectable so the layers above can be tested against a
        # fake that replays captured blobs, with no hardware attached.
        self.transport = transport or Transport(path=port,
                                                device_id=device_id or 0x10)
        self._catalog: Catalog | None = None
        self._tone_cache: dict[int, Tone | None] = {}
        # last known bytes per (kind, slot). Undo snapshots read from here
        # rather than from the wire: a SysEx read is ~0.6s, and paying that
        # before every step edit would make editing feel broken.
        self._blobs: dict[tuple, bytes] = {}
        # the machine writes the committed kit's index into the kit reference
        # of the last pattern transferred (docs/PROTOCOL.md). Remembering
        # which pattern that was is what lets write_kit undo the damage.
        self._last_pattern: int | None = None

    def __enter__(self):
        self.transport.open()
        return self

    def __exit__(self, *exc):
        self.transport.close()

    def open(self):
        self.transport.open()
        return self

    def close(self):
        self.transport.close()

    # ---------------------------------------------------------------- info

    def info(self) -> dict:
        fw = self.transport.firmware_retry()
        return {"port": self.transport.path, "firmware": fw["version"],
                "revision": fw["revision"]}

    @property
    def catalog(self) -> Catalog:
        if self._catalog is None:
            self._catalog = Catalog.load()
        return self._catalog

    # ------------------------------------------------------------ patterns

    # set by the studio: while the machine plays, bulk reads do not fail, they
    # hang for the whole timeout (measured: 25.6s) and freeze everything
    # waiting on the port. Refusing outright is the only good answer.
    playing = None      # callable -> bool, or None if nobody is watching

    def _refuse_if_playing(self, what: str):
        if self.playing is not None and self.playing():
            raise DeviceError(
                f"cannot read {what} while the machine is playing -- the "
                f"TR-8S does not answer bulk reads during playback and the "
                f"request would hang. Stop it first.")

    def read_pattern(self, slot: int) -> Pattern:
        slot = self._check(slot)
        self._refuse_if_playing(f"pattern {slot_to_panel(slot)}")
        blob = self.transport.read_blob("pattern", slot)
        if not blob:
            raise DeviceError(f"could not read pattern {slot} ({slot_to_panel(slot)})")
        self.remember("pattern", slot, blob)
        return Pattern.from_bytes(blob)

    def _repair_kit_reference(self) -> int | None:
        """
        Put back the kit reference the machine just overwrote.

        Committing a kit stamps that kit's index into byte 18 of the last
        pattern transferred, whether or not that pattern has anything to do
        with it. We hold the bytes we sent, so we can simply send them again.
        Costs one 24 KB write; the alternative is patterns quietly re-pointing
        themselves whenever an unrelated kit is saved.
        """
        slot = self._last_pattern
        if slot is None:
            return None
        blob = self._blobs.get(("pattern", slot))
        if not blob:
            return None
        try:
            if self.transport.send_blob("pattern", slot, blob):
                return slot
        except Exception:
            pass                # a failed repair must not fail the kit write
        return None

    def remember(self, kind: str, slot: int, blob: bytes):
        self._blobs[(kind, slot)] = bytes(blob)

    def snapshot(self, kind: str, slot: int) -> bytes | None:
        """
        The current bytes of a slot: from the cache if we have them, else off
        the device. Used by undo, which must not pay for a read on every edit.
        """
        cached = self._blobs.get((kind, slot))
        if cached is not None:
            return cached
        blob = self.transport.read_blob(kind, slot)
        if blob:
            self.remember(kind, slot, blob)
        return blob

    def write_pattern(self, slot: int, pattern: Pattern, commit: bool = True,
                      verify: bool = True) -> dict:
        slot = self._check(slot)
        blob = pattern.to_bytes()
        if not self.transport.send_blob("pattern", slot, blob):
            raise DeviceError("pattern transfer incomplete")
        self.remember("pattern", slot, blob)
        self._last_pattern = slot
        if not commit:
            return {"slot": slot, "panel": slot_to_panel(slot),
                    "committed": False, "live": True}
        self.transport.commit("pattern", slot)
        result = {"slot": slot, "panel": slot_to_panel(slot), "committed": True}
        if verify and self.playing is not None and self.playing():
            verify = False            # a read-back would hang; trust the send
            result["verified"] = None
        if verify:
            time.sleep(0.35)
            back = self.transport.read_blob("pattern", slot)
            if back is None:
                # Right after a burst of saves the machine sometimes drops one reply
                # (2026-11-13: once after 16 kit saves). Give it time, ask once more.
                time.sleep(2.0)
                back = self.transport.read_blob("pattern", slot)
                result["verify_retried"] = True
            result["verified"] = back == blob
            if not result["verified"] and back:
                result["differing_bytes"] = sum(
                    1 for a, b in zip(back, blob) if a != b)
        return result

    # ---------------------------------------------------------------- kits

    def read_kit(self, slot: int) -> Kit:
        slot = self._check(slot)
        if self.playing is not None and self.playing():
            # A bulk read hangs during playback, but a kit WRITE goes through
            # in ~1.4s -- so hotswapping a sound while the pattern plays is
            # possible, exactly as it is on the panel. Serve the last bytes
            # we read or wrote; the after-stop reader refreshes them once the
            # machine stops. Refuse only if we have never seen this kit.
            cached = self._blobs.get(("kit", slot))
            if cached is not None:
                return Kit.from_bytes(cached)
            self._refuse_if_playing(f"kit {slot + 1} (never read yet)")
        blob = self.transport.read_blob("kit", slot)
        if not blob:
            raise DeviceError(f"could not read kit {slot} (panel {slot+1})")
        self.remember("kit", slot, blob)
        return Kit.from_bytes(blob)

    def write_kit(self, slot: int, kit: Kit, verify: bool = True) -> dict:
        """Kits always commit: a transfer alone changes the slot but the
        currently loaded kit keeps playing until it is committed."""
        slot = self._check(slot)
        blob = kit.to_bytes()
        if not self.transport.send_blob("kit", slot, blob):
            raise DeviceError("kit transfer incomplete")
        self.remember("kit", slot, blob)
        self.transport.commit("kit", slot)
        repaired = self._repair_kit_reference()
        result = {"slot": slot, "panel": slot + 1, "committed": True}
        if self.playing is not None and self.playing():
            verify = False            # a read-back would hang; trust the send
            result["verified"] = None
        if repaired is not None:
            result["repaired_kit_reference_of"] = repaired
        else:
            # the machine remembers its last transfer across power cycles; this
            # process does not. Saying nothing would imply nothing happened.
            result["kit_reference_warning"] = (
                "no pattern has been written in this session, so the pattern "
                "the machine just re-pointed at kit "
                f"{slot + 1} could not be identified or repaired. Check the "
                "kit reference of whatever pattern was last written to this "
                "device (docs/PROTOCOL.md).")
        if verify:
            time.sleep(0.35)
            back = self.transport.read_blob("kit", slot)
            if back is None:
                time.sleep(2.0)                       # see write_pattern
                back = self.transport.read_blob("kit", slot)
                result["verify_retried"] = True
            if back:
                a, b = bytearray(back), bytearray(blob)
                for i in range(11):
                    o = Kit.record_offset(["BD", "SD", "LT", "MT", "HT", "RS",
                                           "HC", "CH", "OH", "CC", "RC"][i]) + 4
                    a[o] = b[o] = 0        # level is the fader; ignore it
                result["verified"] = a == b
            else:
                result["verified"] = False
        return result

    # --------------------------------------------------------------- tones

    def read_tone(self, tone_id: int) -> Tone | None:
        blob = self.transport.read_blob("tone", tone_id, timeout=8)
        if not blob or len(blob) < 18:
            return None
        name = "".join(chr(c) for c in blob[:16] if 32 <= c < 127).rstrip()
        if not name:
            return None
        cat = CATEGORIES[blob[16]] if blob[16] < len(CATEGORIES) else str(blob[16])
        return Tone(id=tone_id, name=name, cat=cat, type=blob[17])

    def find_sample_donor(self, prefer_kit: int | None = None,
                          search: int = 24) -> tuple[int, str] | None:
        """
        Locate any instrument that already carries sample parameters.

        Assigning a SAMPLE tone to a record holding ACB defaults produces a
        near-silent sound, because bytes +28..+41 (envelope, gain) only exist
        for samples. Rather than make every caller know that, find a record to
        inherit from. Checks the target kit first -- another instrument in the
        same kit is the cheapest and most faithful donor.
        """
        from .kit import TRACKS
        order = ([prefer_kit] if prefer_kit is not None else []) + list(range(search))
        seen = set()
        for slot in order:
            if slot in seen or not 0 <= slot <= MAX_SLOT:
                continue
            seen.add(slot)
            try:
                k = self.read_kit(slot)
            except DeviceError:
                continue
            for inst in TRACKS:
                if k.has_sample_params(inst):
                    return slot, inst
        return None

    def tone_info(self, tone_id: int) -> Tone | None:
        """Catalogue first (it has measurements), then the device, then cache."""
        if tone_id in self._tone_cache:
            return self._tone_cache[tone_id]
        info = self.catalog.get(tone_id)
        if info is None:
            try:
                info = self.read_tone(tone_id)
            except Exception:
                info = None
        self._tone_cache[tone_id] = info
        return info

    def trigger(self, inst: str, velocity: int = 110):
        notes = {"BD": 36, "RS": 37, "SD": 38, "HC": 39, "CH": 42, "LT": 43,
                 "OH": 46, "MT": 47, "CC": 49, "HT": 50, "RC": 51}
        if inst not in notes:
            raise ValueError(f"unknown instrument {inst!r}")
        self.transport.note(notes[inst], velocity)

    # --------------------------------------------------------------- reads

    def read_checked(self, kind: str, slot: int, twice: bool = False,
                     pause: float = 0.35, sleep=time.sleep) -> bytes:
        """
        One blob, refused unless it is plausibly what the machine holds.

        `check_blob` catches a misaligned or truncated reply. `twice` reads the
        slot again and requires the same bytes, for blobs something will be
        built from. Raises DeviceError naming the slot and the reason; nothing
        is cached from a refused read.
        """
        slot = self._check(slot)
        label = slot_to_panel(slot) if kind == "pattern" else str(slot + 1)
        self._refuse_if_playing(f"{kind} {label}")
        blob = self.transport.read_blob(kind, slot)
        if blob is None:
            # A single dropped reply, with the machine answering normally right after, was
            # seen four times on 2026-11-13. Ask once more after a pause; a second silence
            # stops the caller as before. A wrong reply is never retried.
            sleep(2.0)
            blob = self.transport.read_blob(kind, slot)
        why = check_blob(kind, blob)
        if why is None and twice:
            sleep(pause)
            again = self.transport.read_blob(kind, slot)
            why = check_blob(kind, again)
            if why is None and again != blob:
                diff = sum(1 for a, b in zip(again, blob) if a != b)
                why = f"two reads differ in {diff} bytes"
        if why is not None:
            raise DeviceError(f"{kind} {label}: {why}")
        self.remember(kind, slot, blob)
        return blob

    # -------------------------------------------------------------- backup

    # Pacing. On 2026-11-13 the machine stopped answering after about 150
    # back-to-back reads and needed a power cycle, so these are deliberately slow.
    READ_PAUSE = 1.0          # seconds after every read
    BATCH = 8                 # reads per batch
    BATCH_PAUSE = 10.0        # seconds between batches

    def backup(self, kinds=("pattern", "kit"), lo: int = 0, hi: int = 127,
               progress=None, dirs: dict | None = None, twice: bool = False,
               pause: float | None = None, batch: int | None = None,
               batch_pause: float | None = None, sleep=time.sleep) -> dict:
        """
        Pull slots lo..hi to disk, slowly, stopping at the first bad reply.

        Every blob passes `check_blob` before it is written, so a misaligned
        reply is never kept. A slot whose file is already there and sane is
        skipped without a read, so a stopped run resumes where it stopped.
        Files are written whole (temp file, then rename). Returns counts per
        kind and `stopped`: None, or the slot that failed and why.
        """
        pause = self.READ_PAUSE if pause is None else pause
        batch = batch or self.BATCH
        batch_pause = self.BATCH_PAUSE if batch_pause is None else batch_pause
        where = {k: Path((dirs or {}).get(k) or (config.patterns_dir() if k == "pattern"
                                                 else config.kits_dir())) for k in kinds}
        saved, skipped, reads, stopped = {}, {}, 0, None
        for kind in kinds:
            d = where[kind]
            d.mkdir(parents=True, exist_ok=True)
            saved[kind] = skipped[kind] = 0
            for slot in range(lo, hi + 1):
                path = d / f"{kind}_{slot:03d}.bin"
                if path.exists() and check_blob(kind, path.read_bytes()) is None:
                    skipped[kind] += 1
                    continue
                if reads:
                    sleep(batch_pause if reads % batch == 0 else pause)
                reads += 1
                try:
                    blob = self.read_checked(kind, slot, twice=twice, sleep=sleep)
                except DeviceError as e:
                    stopped = {"kind": kind, "slot": slot,
                               "panel": slot_to_panel(slot) if kind == "pattern" else slot + 1,
                               "reason": str(e)}
                    break
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(blob)
                tmp.replace(path)
                saved[kind] += 1
                if progress:
                    progress(kind, slot, saved[kind])
            if stopped:
                break
        return {"saved": saved, "skipped": skipped, "reads": reads, "stopped": stopped,
                "directory": {k: str(v) for k, v in where.items()}}

    def restore(self, kind: str, slot: int) -> dict:
        d = config.patterns_dir() if kind == "pattern" else config.kits_dir()
        p = d / f"{kind}_{slot:03d}.bin"
        if not p.exists():
            raise DeviceError(f"no backup at {p}")
        blob = p.read_bytes()
        why = check_blob(kind, blob)
        if why is not None:
            raise DeviceError(f"refusing to restore {p}: {why}. Nothing was written.")
        if kind == "pattern":
            return self.write_pattern(slot, Pattern.from_bytes(blob))
        return self.write_kit(slot, Kit.from_bytes(blob))

    # ------------------------------------------------------------- helpers

    @staticmethod
    def _check(slot: int) -> int:
        if not 0 <= slot <= MAX_SLOT:
            raise ValueError(f"slot {slot} out of range 0..{MAX_SLOT}")
        return slot

    def template_pattern(self) -> Pattern:
        """
        A blank pattern to author from.

        Building from zeros does not work: an empty variation still carries
        setting bytes that matter, so this uses a captured empty slot.
        """
        p = config.template_path()
        if p.exists():
            return Pattern.from_bytes(p.read_bytes())
        raise DeviceError(
            f"no pattern template at {p}. Capture one from an empty slot with "
            f"`tr8s capture-template <slot>`"
        )
