"""
The cue sheet: a set, on paper, for Live mode (no laptop). Pure: `render(plan, built_on)` turns
the plan that `set.plan` and `set.build` share into markdown, so the sheet a preview prints is
the sheet the build saves.

What it says follows docs/FM1-PROTOCOL.md ("How a song starts and advances"): a song starts from
PLAY on the FM-1's SONG page while the FM-1 is stopped, on the bar that is to be bar 1; a TR-8S
Start starts only the FM-1's plain loop, and a TR-8S Stop ends a running song. The FM-1 panel
wording sits in `FM1_PANEL`, one place to correct from the hardware.
"""

from __future__ import annotations

PROJECTS = "ABCD"

RIG = [
    "TR-8S MIDI OUT → DIN-to-TRS adapter → FM-1 TRS MIDI IN. FM-1 audio out → TR-8S EXT IN L/R "
    "(EXT IN type stereo).",
    "FM-1: clock source (CLK) TRS, MIDI IN block CH1-6, powered from a USB supply. TR-8S: sync out on.",
    "A TR-8S Start starts the FM-1's loop, never its song. A TR-8S Stop ends a running song.",
    "A song starts from PLAY on the FM-1's SONG page while the FM-1 is stopped, and stops by "
    "itself after its last section.",
    "The TR-8S variations change by hand: press the next one during the bar before its bar in the "
    "timeline.",
]

FM1_PANEL = {
    "load": "load project {project}",
    "song_page": "open the SONG page (GLO, tapped)",
    "stop": "If the FM-1 is playing, press PLAY once to stop it.",
    "mute": "mute track {tracks}",
    "unmute": "unmute track {tracks}",
    "volume_down": "turn the FM-1's volume down: it follows the TR-8S's Start with whatever "
                   "project is loaded",
    "volume_down_short": "FM-1 volume down",
}


def project_letter(project: int) -> str:
    return PROJECTS[project]


def _label(t: int, parts: dict) -> str:
    """FM-1 track t (0-based) as the panel counts it, with its shape: `2 (bass)`."""
    return f"{t + 1} ({parts[str(t)]['shape']})"


def _labels(tracks, parts) -> str:
    return ", ".join(_label(t, parts) for t in tracks) or "—"


def _tracks(tracks) -> str:
    return ", ".join(str(t + 1) for t in tracks)


def _table(head, rows) -> list:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def _bpm(tempo) -> str:
    return f"{float(tempo):g}"


def _fm1_cell(t: dict) -> str:
    s = t.get("synth")
    if not s:
        return "—"
    if s["arrange"]:
        return f"song, {len(s['sections'])} sections, {t['end_bar'] - 1} bars"
    return "loop, hand cues"


def _start(t: dict) -> list:
    s = t.get("synth")
    pattern = f"pattern {t['panel']}, variation A"
    if s and s["arrange"]:
        return [
            f"1. TR-8S: select {pattern}. Keep it running; if it is stopped, press Start.",
            f"2. FM-1: {FM1_PANEL['load'].format(project=s['project_letter'])}, then "
            f"{FM1_PANEL['song_page']}.",
            f"3. {FM1_PANEL['stop']}",
            "4. Press PLAY on a downbeat. That bar is bar 1.",
        ]
    if s:
        load = FM1_PANEL["load"].format(project=s["project_letter"])
        if t["start_muted"]:
            mute = FM1_PANEL["mute"].format(tracks=_labels(t["start_muted"], s["parts"]))
            load += ". " + mute[0].upper() + mute[1:]
        return [
            "1. TR-8S: press Stop.",
            f"2. FM-1: {load}.",
            f"3. TR-8S: select {pattern}, press Start. The FM-1's loop starts with it: that bar is bar 1.",
        ]
    return [
        f"1. FM-1: {FM1_PANEL['volume_down']}.",
        f"2. TR-8S: select {pattern}; if it is stopped, press Start. That bar is bar 1.",
    ]


def _hand(ins, outs) -> str:
    """The FM-1 mutes to press on a row: what comes in and what goes out (0-based tracks)."""
    hand = []
    if ins:
        hand.append(FM1_PANEL["unmute"].format(tracks=_tracks(ins)))
    if outs:
        hand.append(FM1_PANEL["mute"].format(tracks=_tracks(outs)))
    return "; ".join(hand) or "—"


def _timeline(t: dict) -> list:
    s = t.get("synth")
    parts = s["parts"] if s else {}
    rows = []
    if s and not s["arrange"]:
        for r in t["timeline"]:
            rows.append([r["bar"], r["variation"], r["role"], _labels(r["plays"], parts),
                         _hand(r["in"], r["out"])])
        rows.append([t["end_bar"], "—", "end", "", "the loop plays on until the TR-8S stops"])
        return _table(["Bar", "TR-8S", "Role", "FM-1 plays", "By hand on the FM-1"], rows)
    if s:
        for r in t["timeline"]:
            rows.append([r["bar"], r["variation"], r["role"], _labels(r["plays"], parts)])
        rows.append([t["end_bar"], "—", "end", "the song stops by itself; go to the next track"])
        return _table(["Bar", "TR-8S", "Role", "FM-1 plays"], rows)
    for r in t["timeline"]:
        rows.append([r["bar"], r["variation"], r["role"]])
    rows.append([t["end_bar"], "—", "end"])
    return _table(["Bar", "TR-8S", "Role"], rows)


def _track(t: dict) -> list:
    s = t.get("synth")
    head = f"## {t['index']}. {t['name']} · TR-8S {t['panel']}"
    if s:
        head += f" · FM-1 project {s['project_letter']}"
    facts = [t["style"]]
    if s:
        facts.append(f"voicing {s['voicing']}")
    facts += [f"energy {t['energy']:g}", t["key"], f"{_bpm(t['tempo'])} BPM", f"seed {t['seed']}"]
    if t.get("kit") is not None:
        facts.append(f"kit {t['kit']}")
    out = [head, "", " · ".join(facts), ""]

    kind = "song" if s and s["arrange"] else "loop" if s else "drums only"
    out += [f"### Start: {kind}", ""] + _start(t) + [""]
    if not s and t.get("line", "none") != "none":
        out += [f"The TR-8S plays its own {t['line']} line on LT: set Coarse Tune and MOTION ON by "
                "hand (issue #13).", ""]

    if s:
        rows = [[int(k) + 1, p["channel"], p["shape"], p["engine"], p["steps"]]
                for k, p in sorted(s["parts"].items(), key=lambda kv: int(kv[0]))]
        out += ["### Parts", ""] + _table(["FM-1 track", "Ch", "Shape", "Engine", "Steps"], rows) + [""]

    out += [f"### Timeline ({t['bars']} bars per variation)", ""] + _timeline(t) + [""]

    if t["warnings"]:
        out += ["### Warnings", ""] + [f"- {w}" for w in t["warnings"]] + [""]
    return out


def render(plan: dict, built_on) -> str:
    """The markdown cue sheet for a set plan (`set.plan`'s `plan`, `name` and `tracks`)."""
    tracks = plan["plan"]
    on_fm1 = sum(1 for t in tracks if t.get("synth"))
    out = [f"# Set: {plan['name']}", "",
           f"Built {built_on.isoformat()} by tr8s set.build · {len(tracks)} tracks · {on_fm1} on the FM-1",
           "", "## Rig", ""]
    out += [f"- {line}" for line in RIG] + ["", "## Running order", ""]
    out += _table(["#", "Track", "TR-8S", "FM-1", "Key", "BPM", "Seed", "FM-1 plays"],
                  [[t["index"], t["name"], t["panel"],
                    t["synth"]["project_letter"] if t.get("synth") else "—",
                    t["key"], _bpm(t["tempo"]), t["seed"], _fm1_cell(t)] for t in tracks])
    out.append("")
    for t in tracks:
        out += _track(t)
    return "\n".join(out).rstrip() + "\n"


# ------------------------------------------------------------------ the reworked set (TR-8S only)

COLOUR_LEGEND = [("red", "kick"), ("orange", "snare, clap"), ("yellow", "acid voice"),
                 ("light green", "odd-cycle voice 1"), ("green", "odd-cycle voice 2"),
                 ("light blue", "tom or perc, no special job"), ("blue", "closed hat"),
                 ("dark blue", "open hat"), ("purple", "crash, ride"), ("pink", "rim, small perc"),
                 ("light pink", "effects")]
ROLE_NAMES = {"kick": "red", "snare": "orange", "acid": "yellow", "odd1": "light green",
              "odd2": "green", "light": "light blue", "hat": "blue", "open": "dark blue",
              "cymbal": "purple", "rim": "pink", "fx": "light pink"}

# Bars per route step. 16 patterns of about 140 bars each come to roughly 70 minutes.
ROUTE_BARS = {"intro": 16, "main": 16, "break (no kick)": 16, "break (mute the kick)": 16,
              "drop": 32, "peak": 16, "breather (no kick)": 8}

REWORK_RIG = [
    "TR-8S only, no laptop. The set is bank 8: hold PTN SELECT, press pad 8, release, then pads 1-16 are the set.",
    "Each pattern 8-NN plays its own kit (8-01 → kit 113 … 8-16 → kit 128) and its own tempo. That needs "
    "UTILITY → GENERAL: KitSel = PTN and TempoSrc = PTN (setup below).",
    "A new pattern starts when the playing one returns to its first step: press the next pad during the last bar.",
    "Variations change by hand: press the next one during the bar before. Hold one and press others to chain them.",
]

SETUP = [
    "UTILITY → GENERAL: **KitSel = PTN**, **TempoSrc = PTN** (so each pattern brings its kit and BPM). "
    "Shuffle = PTN too. Then WRITE → System.",
    "**MOTION [ON]** lit: several sources carry recorded knob motion, which is silent with MOTION off.",
    "AUTO FILL IN: knob on **16**, leave [ON] off until a pattern says so. FILL IN type: the pattern's own fills.",
    "Faders: start everything at about 3/4. Level is the fader's alone; nothing written to the machine sets it.",
    "CTRL map (hold CTRL SELECT, press the instrument, choose with VALUE; it is the machine's, so once covers "
    "every pattern): **MT and HT = filter cutoff** (the acid voices), **SD and RS = delay send** (throws), "
    "**CH and OH = decay or the INST FX**, **CC and RC = reverb send**. Then WRITE → System. [check] the "
    "exact parameter names in the panel's list.",
    "Know your rescue moves: **MUTE + CLEAR** clears every mute; **SHIFT + TEMPO** moves tempo in 0.1 BPM.",
]


def _mins(bars: int, bpm: float) -> float:
    return bars * 4 * 60 / bpm / 60


def _energy_bar(e) -> str:
    return "█" * int(round((e or 0) * 20))


def _route_rows(e: dict) -> tuple[list, int]:
    rows, bar = [], 1
    for r in e["route"]:
        n = ROUTE_BARS.get(r["role"], 16)
        rows.append([bar, r["variation"], r["role"], n, r["hits"]])
        bar += n
    return rows, bar - 1


def _voices(e: dict) -> list:
    out = []
    by_role = {}
    for inst, role in e["roles"].items():
        by_role.setdefault(role, []).append(inst)
    if "acid" in by_role and not set(by_role["acid"]) & set(e.get("tune_lines") or []):
        inst = ", ".join(by_role["acid"])
        out.append(f"**Bass voice (yellow fader): {inst}.** No recorded line: it plays the pattern's hits at "
                   f"one pitch. With {inst}'s CTRL on the filter cutoff (setup), opening it slowly over a "
                   "phrase is the movement. Ride DECAY with it.")
    elif "acid" in by_role:
        from .rework import is_sweep
        inst = ", ".join(by_role["acid"])
        what = ("a recorded TUNE-knob sweep in Fine Tune motion (off the semitone grid, so in no key: "
                "the FM-1 plays no melody against it)" if is_sweep(e.get("tune_offsets") or [])
                else "a recorded line in Fine Tune motion")
        out.append(f"**Acid voice (yellow fader): {inst}.** It plays {what}, so its "
                   f"CTRL knob is free: with {inst}'s CTRL on the filter cutoff (setup) it is the squelch. "
                   "Ride DECAY with it. Mute it for 8 bars and bring it back on a 1.")
    odd = [i for r in ("odd1", "odd2") for i in by_role.get(r, [])]
    if odd:
        cyc = ", ".join(e["poly_tracks"]) or "odd spacing"
        out.append(f"**Odd-cycle voices (green faders): {', '.join(odd)}** ({cyc}). They pull against the four: "
                   "mute them in the intro, bring one in at a time, and pull them out for the break so the 1 lands harder.")
    return out


def _transition(e: dict, nxt: dict | None) -> list:
    if nxt is None:
        return ["Last pattern. Let the route end on its lightest variation, mute the **red** kick fader's "
            "track (MUTE, then BD), let the hats and reverb ring for 8 bars, press STOP on a 1."]
    jump = nxt["bpm"] - e["bpm"]
    head = (f"Next: **{nxt['panel']} {nxt['name']}** at {nxt['bpm']:g} BPM "
            f"({'+' if jump >= 0 else ''}{jump:g}).")
    if abs(jump) > 6:
        return [head, "**Tempo jump, hidden in a bridge:** go to this pattern's kickless or lightest variation; "
                "MUTE the kick (and the snare) for the last 8 bars so only hats, toms and tails play; press the "
                f"next pad in the last bar. The new tempo arrives with nothing to compare it to. Unmute on the "
                f"first 1 of {nxt['panel']}, or wait for its intro to bring the kick back."]
    return [head, "In the last 16 bars: send a fill (MANUAL TRIG) every 8 bars, then hold PTN SELECT and press "
            "the next pad during the final bar. Keep the kick running across the switch."]


FM1_RIG = [
    "FM-1 (Mini-Tone): TR-8S MIDI OUT → DIN-to-TRS adapter → FM-1 TRS MIDI IN; FM-1 audio out → TR-8S "
    "EXT IN L/R. The FM-1 follows the TR-8S's clock and its Start and Stop.",
    "The FM-1 plays loops, one project per chapter of the set. Its parts come in and go out by hand, with "
    "its track mutes, on the rows the route tables give. One bass and one line at a time: where the "
    "yellow fader plays, the FM-1 has no bass and no melody.",
]

FM1_SETUP = [
    "FM-1: clock source (CLK) **TRS**, MIDI IN block **CH1-6**, master volume about 70%: the volume knob is "
    "the FM-1's fader for the whole set.",
    "TR-8S: EXT IN type **stereo**; EXT IN level so the loudest FM-1 part peaks about 6 dB under the kick. "
    "Sidechain on EXT IN from BD in each kit where the FM-1 plays (moderate for bass and 303, deeper for "
    "the pad, release about an 8th), then WRITE the kit. [check] the parameter names on firmware 3.00.",
]


def _fm1_label(t: int, project: dict) -> str:
    return f"{t + 1} ({project['tracks'][t]['shape']})"


def _fm1_labels(tracks, project) -> str:
    return ", ".join(_fm1_label(t, project) for t in tracks) or "—"


def _fm1_order_cell(slot: dict, project: dict | None) -> str:
    if project is None:
        return "—"
    playing = sorted({t for r in slot["rows"] for t in r["plays"]})
    if not playing:
        return f"{project['letter']}, all muted"
    return f"{project['letter']}: " + ", ".join(project["tracks"][t]["shape"] for t in playing)


def _fm1_block(slot: dict, project: dict | None, first: bool) -> list:
    """The FM-1 part of a pattern's page; `project` is None while nothing is loaded yet."""
    out = ["### FM-1", ""]
    if project is None:
        out += ["No project is loaded yet: the FM-1 stays silent (volume down).", ""]
    elif first:
        out += [f"**Project {project['letter']} · {project['name']} · {project['key']}** (the root is "
                f"{project['key_from']}'s, measured: {project['root'].get('note', project['root']['pc'])} "
                f"{project['root'].get('cents', 0):+d} cents).", ""]
        rows = [[t + 1, p["channel"], p["shape"], p["engine"], p["root"], p["steps"]]
                for t, p in sorted(project["tracks"].items())]
        out += _table(["FM-1 track", "Ch", "Shape", "Engine", "Root", "Steps"], rows) + [""]
    playing = sorted({t for r in slot["rows"] for t in r["plays"]})
    if project is not None and not playing:
        out += [f"Project {project['letter']} is loaded and every FM-1 track stays muted: the TR-8S alone.", ""]
    if slot["load"]:
        out += [f"**Load project {slot['load']} during this pattern** (every FM-1 track is muted here): "
                f"{FM1_PANEL['volume_down_short']}, {FM1_PANEL['load'].format(project=slot['load'])}, "
                f"mute every track, then the volume back up. [check] that a load keeps the loop running "
                "on the grid.", ""]
    if slot["warnings"]:
        out += ["Warnings:", ""] + [f"- {w}" for w in slot["warnings"]] + [""]
    return out if len(out) > 2 else []        # the route table already says it all


def render_rework(spec: dict, entries: list, built_on, fm1: dict | None = None) -> str:
    """The printable cue sheet for a set built by `tr8s rework`, with the FM-1 when `fm1` is
    `rework.fm1_plan`'s result."""
    slots = {s["panel"]: s for s in fm1["slots"]} if fm1 else {}
    projects = fm1["projects"] if fm1 else {}
    total_bars = 0
    total_min = 0.0
    order = []
    for e in entries:
        _, bars = _route_rows(e)
        m = _mins(bars, e["bpm"])
        total_min += m
        total_bars += bars
        row = [e["index"], e["panel"], e["name"], e["role"], f"{e['bpm']:g}", e["kit_panel"],
               f"{e['source_panel']} {e['source_name']}", f"{m:.1f}"]
        if fm1:
            s = slots[e["panel"]]
            p = projects.get(s["project"])
            playing = any(r["plays"] for r in s["rows"])
            row += [_fm1_order_cell(s, p), p["key"] if p and playing else "—"]
        order.append(row)
    out = [f"# Set: {spec['name']}", "",
           f"Built {built_on.isoformat()} by `tr8s rework` · {len(entries)} patterns · about "
           f"{total_min:.0f} minutes · bank 8, kits {entries[0]['kit_panel']}-{entries[-1]['kit_panel']}"
           + (f" · FM-1 projects {', '.join(sorted(projects))}" if fm1 else ""), "",
           "## Rig", ""] + [f"- {x}" for x in REWORK_RIG]
    if fm1:
        out += [f"- {x}" for x in FM1_RIG]
    setup = SETUP + (FM1_SETUP if fm1 else [])
    out += ["", "## One-time setup on the panel", ""] + [f"{i}. {x}" for i, x in enumerate(setup, 1)]
    if fm1 and fm1.get("before_set"):
        out += ["", "## Before the set", "",
                f"1. FM-1: {FM1_PANEL['load'].format(project=fm1['before_set'])}, mute every track.",
                f"2. TR-8S: select {entries[0]['panel']}, press Start. The FM-1's loop starts with it, muted."]
    elif fm1:
        out += ["", "## Before the set", "",
                f"1. FM-1: {FM1_PANEL['volume_down']}. The first project is loaded during the set "
                "(see the patterns below).",
                f"2. TR-8S: select {entries[0]['panel']}, press Start."]
    out += ["", "## Fader colours (the same in every kit of the set)", ""]
    out += _table(["Colour", "Role"], COLOUR_LEGEND)
    out += ["", "## Running order", ""]
    head = ["#", "Pad", "Name", "Role", "BPM", "Kit", "Made from", "Min"] + (["FM-1", "Key"] if fm1 else [])
    out += _table(head, order)
    out += ["", "## Energy", "", "```"]
    out += [f"{e['panel']} {e['bpm']:>5g}  {_energy_bar(e.get('energy'))}" for e in entries]
    out += ["```", ""]
    for i, e in enumerate(entries):
        nxt = entries[i + 1] if i + 1 < len(entries) else None
        rows, bars = _route_rows(e)
        out += [f"## {e['index']}. {e['name']} · pad {e['panel']} · kit {e['kit_panel']}", "",
                f"{e['role']} · {e['bpm']:g} BPM · energy {e.get('energy', '—')} · made from "
                f"{e['source_panel']} {e['source_name']} ({e['source_tempo']:g} BPM, kit "
                f"{e['source_kit_panel']}) · about {_mins(bars, e['bpm']):.1f} min", ""]
        if e.get("notes"):
            out += [e["notes"], ""]
        if e.get("kit_design"):
            from .kit import TRACKS as _TR
            krows = [[inst, ROLE_NAMES[e["roles"][inst]], e["kit_design"][inst]["tone"]]
                     for inst in _TR if inst in e["kit_design"]]
            out += [f"### Kit {e['kit_panel']}", ""] + _table(["Fader", "Colour", "Tone"], krows) + [""]
        out += ["### Route through A-H", ""]
        s = slots.get(e["panel"])
        p = projects.get(s["project"]) if s else None
        if p is not None:
            rows = [r + [_fm1_labels(fr["plays"], p), _hand(fr["in"], fr["out"])]
                    for r, fr in zip(rows, s["rows"])]
            out += _table(["Bar", "Var", "Role", "Bars", "Hits", "FM-1 plays", "By hand on the FM-1"], rows)
        else:
            out += _table(["Bar", "Var", "Role", "Bars", "Hits"], rows)
        out += [""]
        voices = _voices(e)
        if voices:
            out += ["### Voices", ""] + [f"- {v}" for v in voices] + [""]
        if s is not None:
            out += _fm1_block(s, p, s["first_of_project"])
        out += ["### Moves", "",
                "- **Intro:** mute everything but the kick and one hat; unmute a voice every 8 bars.",
                "- **Main:** AUTO FILL IN on (16 bars). Ride the ACCENT knob up over 16 bars before the break.",
                "- **Break:** the kick is out (or MUTE it). Open the REVERB level, throw the DELAY on the snare "
                "(raise DELAY LEVEL for one bar, then back). MASTER FX on with a filter for the last 8 bars: "
                "sweep the MASTER FX CTRL knob up into the drop.",
                "- **Drop:** MASTER FX off on the 1, everything unmuted, MANUAL TRIG on the bar before.",
                "- **Peak:** chain two peak variations (hold one, press the other) for 2-bar phrases.",
                "", "### Out", ""] + [f"- {x}" for x in _transition(e, nxt)] + [""]
    return "\n".join(out).rstrip() + "\n"
