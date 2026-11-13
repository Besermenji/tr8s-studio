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


def _timeline(t: dict) -> list:
    s = t.get("synth")
    parts = s["parts"] if s else {}
    rows = []
    if s and not s["arrange"]:
        for r in t["timeline"]:
            hand = []
            if r["in"]:
                hand.append(FM1_PANEL["unmute"].format(tracks=_tracks(r["in"])))
            if r["out"]:
                hand.append(FM1_PANEL["mute"].format(tracks=_tracks(r["out"])))
            rows.append([r["bar"], r["variation"], r["role"], _labels(r["plays"], parts),
                         "; ".join(hand) or "—"])
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
