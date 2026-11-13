"""
The survey: what the TR-8S already holds, for choosing a set from its own patterns and kits.

Pure. `analyse_pattern(blob)` turns one pattern blob into facts and scores; `report(rows)`
turns a list of them into markdown. Reading the machine is `Device.backup` (paced, checked),
pointed at a survey directory: `tr8s survey --read`.

The scores are a shortlist aid, not a verdict. They are read off the steps alone, so they
cannot hear the tones; the kit reads and your ears settle it.

  hard   tempo, a four-on-the-floor kick, accent density
  dnb    160-180 BPM (or 80-90 written at half speed) with a broken kick
  light  sparse variations, variations with no kick
  acid   pitched motion (tune or CTRL) on a melodic-range instrument
  poly   tracks whose hits repeat on a cycle of 3, 5, 6 or 7 steps against the 16
"""

from __future__ import annotations

from pathlib import Path

from .pattern import NBLOCKS, STEPS, TRACKS, VARIATIONS, Pattern

BLOCK_NAMES = list(VARIATIONS) + ["F1", "F2"]      # [I] the last two blocks are the fill-ins
ODD = (3, 5, 6, 7)
PITCHED = ("LT", "MT", "HT", "RS", "CC")           # where onboard lines usually sit


def _steps(p: Pattern, blk: int, inst: str) -> list:
    return [p.raw[p._inst_offset(blk, inst, s)] for s in range(STEPS)]


def _cycle(hits: list) -> int | None:
    """The step period of a track whose hits are evenly spaced by an odd cycle, else None."""
    if len(hits) < 3:
        return None
    gaps = {b - a for a, b in zip(hits, hits[1:])}
    if len(gaps) == 1:
        g = gaps.pop()
        if g in ODD:
            return g
    return None


def analyse_block(p: Pattern, blk: int) -> dict:
    tracks, accents, poly = {}, 0, {}
    for inst in TRACKS:
        v = _steps(p, blk, inst)
        hits = [i for i, x in enumerate(v) if x]
        if hits:
            tracks[inst] = len(hits)
            accents += sum(1 for x in v if x >= 112)
            c = _cycle(hits)
            if c:
                poly[inst] = c
    bd = [i for i, x in enumerate(_steps(p, blk, "BD")) if x]
    motion = {}
    for inst in TRACKS:
        tune = ctrl = 0
        for s in range(STEPS):
            m = p.get_motion(blk, inst, s)
            tune += m["tune"] is not None
            ctrl += m["ctrl"] is not None
        if tune or ctrl:
            motion[inst] = {"tune": tune, "ctrl": ctrl}
    return {"tracks": tracks, "hits": sum(tracks.values()), "accents": accents,
            "four_floor": set(bd) >= {0, 4, 8, 12},
            "broken_kick": bool(bd) and not set(bd) >= {0, 4, 8, 12},
            "poly": poly, "motion": motion}


def analyse_pattern(blob: bytes, slot: int | None = None) -> dict:
    p = Pattern.from_bytes(blob)
    blocks = {}
    for blk in range(NBLOCKS):
        b = analyse_block(p, blk)
        if b["hits"]:
            blocks[BLOCK_NAMES[blk]] = b
    mains = [blocks[v] for v in VARIATIONS if v in blocks]
    if not mains:
        out = {"name": p.name, "tempo": p.tempo, "kit": p.kit + 1, "empty": True}
        if slot is not None:
            from .device import slot_to_panel
            out.update(slot=slot, panel=slot_to_panel(slot))
        return out
    n = len(mains)
    tempo = p.tempo
    hits = sum(b["hits"] for b in mains) / n
    floor = sum(b["four_floor"] for b in mains) / n
    acc = sum(b["accents"] for b in mains) / max(1, sum(b["hits"] for b in mains))
    kickless = sum(1 for b in mains if "BD" not in b["tracks"]) / n
    broken = sum(b["broken_kick"] for b in mains) / n
    pitched = {i for b in mains for i, m in b["motion"].items() if i in PITCHED and (m["tune"] + m["ctrl"]) >= 4}
    poly = {f"{i}/{c}" for b in mains for i, c in b["poly"].items()}
    dnb_tempo = 160 <= tempo <= 180 or 80 <= tempo <= 90

    scores = {
        "hard": round(min(1.0, max(0.0, (tempo - 120) / 25)) * 0.4 + floor * 0.35 + min(1.0, acc * 3) * 0.25, 2),
        "dnb": round((0.6 if dnb_tempo else 0.0) + broken * 0.4, 2),
        "light": round(max(0.0, 1 - hits / 40) * 0.6 + kickless * 0.4, 2),
        "acid": round(min(1.0, len(pitched) / 2), 2),
        "poly": round(min(1.0, len(poly) / 3), 2),
    }
    out = {"name": p.name, "tempo": tempo, "kit": p.kit + 1, "scale": p.scale, "shuffle": p.shuffle,
           "blocks": sorted(blocks, key=BLOCK_NAMES.index), "uses_all_eight": all(v in blocks for v in VARIATIONS),
           "hits_per_variation": round(hits, 1), "pitched_motion": sorted(pitched), "poly_tracks": sorted(poly),
           "scores": scores, "detail": blocks}
    if slot is not None:
        from .device import slot_to_panel
        out.update(slot=slot, panel=slot_to_panel(slot))
    return out


def analyse_dir(d: Path) -> list:
    """Every sane pattern blob in a survey directory, analysed. Insane files are listed, not skipped silently."""
    from .device import check_blob
    rows = []
    for f in sorted(Path(d).glob("pattern_*.bin")):
        blob = f.read_bytes()
        slot = int(f.stem.split("_")[1])
        why = check_blob("pattern", blob)
        if why:
            rows.append({"slot": slot, "error": why})
            continue
        rows.append(analyse_pattern(blob, slot))
    return rows


def _row(r: dict) -> str:
    s = r["scores"]
    flags = "".join(k[0].upper() if s[k] >= 0.6 else "·" for k in ("hard", "dnb", "light", "acid", "poly"))
    return (f"| {r['panel']} | {r['name']} | {r['tempo']:g} | {r['kit']} | {''.join(r['blocks'])} | "
            f"{r['hits_per_variation']} | {s['hard']} | {s['dnb']} | {s['light']} | {s['acid']} | {s['poly']} | "
            f"{flags} | {', '.join(r['pitched_motion']) or '—'} | {', '.join(r['poly_tracks']) or '—'} |")


def report(rows: list, title: str = "TR-8S survey") -> str:
    good = [r for r in rows if "error" not in r and not r.get("empty")]
    empty = [r for r in rows if r.get("empty")]
    bad = [r for r in rows if "error" in r]
    head = ("| Slot | Name | BPM | Kit | Blocks | Hits/var | hard | dnb | light | acid | poly | HDLAP | "
            "Pitched motion | Odd cycles |\n|" + "---|" * 14)
    out = [f"# {title}", "", f"{len(good)} patterns read" + (f", {len(bad)} refused" if bad else ""), "",
           "Scores 0-1, read from the steps alone (the tones are not heard). HDLAP marks scores ≥ 0.6: "
           "Hard, Dnb, Light, Acid, Poly.", "", "## All patterns", "", head]
    out += [_row(r) for r in good]
    for key in ("hard", "dnb", "light", "acid", "poly"):
        top = sorted(good, key=lambda r: -r["scores"][key])[:12]
        out += ["", f"## Top {key}", "", head] + [_row(r) for r in top]
    kits = {}
    for r in good:
        kits.setdefault(r["kit"], []).append(r["panel"])
    out += ["", "## Kits in use", "", "| Kit | Patterns |", "|---|---|"]
    out += [f"| {k} | {', '.join(v)} |" for k, v in sorted(kits.items())]
    if empty:
        out += ["", "## Empty slots", "", ", ".join(f"{r['panel']} (kit {r['kit']})" for r in empty)]
    if bad:
        out += ["", "## Refused", ""] + [f"- slot {r['slot']}: {r['error']}" for r in bad]
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ tones

def read_tones(dev, lo: int, hi: int, path, pause: float = 0.3, batch: int = 32,
               batch_pause: float = 3.0, sleep=None, progress=None) -> dict:
    """
    Read tone records lo..hi (name, category, type) into a JSON file, slowly, resuming.

    A tone record is 36 bytes: 16-char name, category at +16, type at +17 (2 = sample). An id
    the machine answers with a blank name is recorded as empty; an id it does not answer at
    all stops the run, as every read here does. Already-recorded ids are skipped.
    """
    import json
    import time
    from .tones import CATEGORIES
    sleep = sleep or time.sleep
    path = Path(path)
    known = json.loads(path.read_text()) if path.exists() else {}
    reads, stopped = 0, None
    for i in range(lo, hi + 1):
        if str(i) in known:
            continue
        if reads:
            sleep(batch_pause if reads % batch == 0 else pause)
        reads += 1
        blob = dev.transport.read_blob("tone", i, timeout=8)
        if blob is None or len(blob) < 18:
            stopped = {"id": i, "reason": "no reply" if blob is None else f"{len(blob)} bytes"}
            break
        name = "".join(chr(c) for c in blob[:16] if 32 <= c < 127).rstrip()
        cat = CATEGORIES[blob[16]] if blob[16] < len(CATEGORIES) else str(blob[16])
        known[str(i)] = {"name": name, "cat": cat, "type": "SAMPLE" if blob[17] == 2 else
                         "ACB" if blob[17] == 1 else str(blob[17]), "raw_type": blob[17]}
        if reads % 16 == 0:
            path.write_text(json.dumps(known, indent=0))
        if progress:
            progress(i, known[str(i)])
    path.write_text(json.dumps(known, indent=0))
    return {"read": reads, "known": len(known), "stopped": stopped, "file": str(path)}
