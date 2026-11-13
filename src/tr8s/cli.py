"""
Layer 4 — command line. A thin shell over the same tool registry an LLM uses,
so the two can never drift apart.

    tr8s tools                       list every tool and its schema
    tr8s call <tool> '<json>'        invoke one
    tr8s info                        firmware and connection
    tr8s patterns [lo] [hi]          list patterns
    tr8s kits [lo] [hi]              list kits
    tr8s tones --category BASS       search the catalogue
    tr8s backup                      pull every pattern and kit to disk
    tr8s capture-template <slot>     save an empty slot as the authoring base
    tr8s set FILE.json [--preview]   build a set (TR-8S slots, FM-1 projects) and print
                                     its cue sheet; --preview touches no device
"""

from __future__ import annotations

import argparse
import json
import sys

from . import config
from .tools import ToolError, call, close, schemas


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


# Commands, and tools under `call`, that never open the TR-8S's port.
OFFLINE = {"tools", "tones"}
OFFLINE_TOOLS = {"set.plan", "styles.list", "lines.preview", "synth.parts", "calibration.describe"}


def studio_running(pidfile=None) -> int | None:
    """
    The PID of a live studio server (`scripts/restart.sh` writes .run/studio.pid), or None.

    The studio polls the current pattern every ~2.5 s while the machine is
    stopped. A second program reading at the same time takes some of its
    replies, and the blobs come back misaligned (docs/LESSONS.md).
    """
    import os
    from pathlib import Path
    p = Path(pidfile) if pidfile else Path(__file__).resolve().parents[2] / ".run" / "studio.pid"
    try:
        pid = int(p.read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    return pid


UNDO_ENDS = ("the undo history lives in this process and ends with it: to keep the TR-8S's "
             "current slots, run `tr8s backup` before building")


def _set_status(r: dict, preview: bool) -> None:
    """What a set run did, on stderr, so stdout stays the cue sheet."""
    err = sys.stderr
    if preview:
        print("preview only: nothing was written. set.build would write:", file=err)
        for w in r["writes"]:
            print(f"  - {w}", file=err)
        print(UNDO_ENDS, file=err)
        return
    print(f"built {len(r['built'])} of {len(r['plan'])} tracks", file=err)
    f = r.get("failed")
    if f:
        print(f"FAILED at track {f['index']} ({f['panel']}, {f['stage']}): {f['error']}", file=err)
        print(f"  {f['state']}", file=err)
        if r["untouched"]:
            print(f"  not touched: tracks {', '.join(map(str, r['untouched']))}", file=err)
        print(f"  re-run: tr8s set {r['files']['inputs']}", file=err)
    for p in r["written"]:
        print(f"wrote {p}", file=err)
    if r["undo"]["call"]:
        print(f"overwrote {r['undo']['history_steps']} TR-8S slots; {UNDO_ENDS}", file=err)
    print(r["undo"]["fm1"], file=err)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tr8s", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("tools", help="list tools and JSON schemas")
    c = sub.add_parser("call", help="invoke a tool")
    c.add_argument("tool")
    c.add_argument("args", nargs="?", default="{}")

    sub.add_parser("info", help="firmware and connection")

    p = sub.add_parser("patterns", help="list patterns")
    p.add_argument("lo", nargs="?", default=0)
    p.add_argument("hi", nargs="?", default=15)

    kk = sub.add_parser("kits", help="list kits")
    kk.add_argument("lo", nargs="?", type=int, default=0)
    kk.add_argument("hi", nargs="?", type=int, default=15)

    tn = sub.add_parser("tones", help="search the tone catalogue")
    tn.add_argument("--category")
    tn.add_argument("--melodic", action="store_true")
    tn.add_argument("--root")
    tn.add_argument("--name-contains")
    tn.add_argument("--limit", type=int, default=25)

    b = sub.add_parser("backup", help="pull patterns and kits to disk")
    b.add_argument("--lo", type=int, default=0)
    b.add_argument("--hi", type=int, default=127)

    an = sub.add_parser("analyse-tones",
                        help="measure every tone: root pitch, loudness, decay, "
                             "brightness (long-running, unattended)")
    an.add_argument("--lo", type=int, default=0)
    an.add_argument("--hi", type=int, default=1023)
    an.add_argument("--category", help="restrict to one category")

    pr = sub.add_parser("probe-byte",
                        help="sweep an unidentified kit-record byte and report "
                             "what moved in the audio")
    pr.add_argument("offset", type=int, nargs="?", default=None,
                    help="omit to probe every unidentified offset in turn")
    pr.add_argument("--tone", type=int, default=None)
    pr.add_argument("--report", action="store_true",
                    help="print the accumulated findings and exit")

    ct = sub.add_parser("capture-template",
                        help="save an empty pattern slot as the authoring base")
    ct.add_argument("slot")

    sv = sub.add_parser("survey", help="read the machine's patterns (or kits) slowly into a survey "
                                       "directory and report what is there")
    sv.add_argument("--dir", help="survey directory (default DATA/survey/TODAY)")
    sv.add_argument("--read", action="store_true",
                    help="read the slots off the TR-8S first (paced, checked, resumable)")
    sv.add_argument("--kinds", default="pattern", help="pattern, kit or pattern,kit")
    sv.add_argument("--lo", type=int, default=0)
    sv.add_argument("--hi", type=int, default=127)
    sv.add_argument("--twice", action="store_true", help="read each slot twice and require the same bytes")

    rw = sub.add_parser("rework", help="build a set reworked from the machine's own patterns and "
                                       "kits (library/sets/NAME.json with slots and sources)")
    rw.add_argument("file")
    rw.add_argument("--preview", action="store_true", help="say what would be written; touch nothing")
    rw.add_argument("--only", help="comma-separated slots (e.g. 8-09,8-10): write only their kits and "
                                   "patterns; every other slot is left alone, not even read")
    rw.add_argument("--sheet", action="store_true",
                    help="only (re)write the cue sheet next to the spec (NAME.md); touch no device")

    se = sub.add_parser("set", help="build a set from a JSON file and print its cue sheet")
    se.add_argument("file", help="{name, start_slot?, tracks}; a library/sets/NAME.json works too")
    se.add_argument("--preview", action="store_true",
                    help="plan only (set.plan): no device is touched, no file written")
    se.add_argument("--json", action="store_true", help="print the full result instead of the sheet")

    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return 0

    offline = args.cmd in OFFLINE or (args.cmd == "set" and args.preview) or \
        (args.cmd == "survey" and not args.read) or \
        (args.cmd == "rework" and (args.preview or args.sheet)) or \
        (args.cmd == "probe-byte" and args.report) or \
        (args.cmd == "call" and args.tool in OFFLINE_TOOLS)
    pid = None if offline else studio_running()
    if pid is not None:
        print(f"error: the studio server is running (pid {pid}) and reads the TR-8S on the same "
              f"port; two programs take each other's replies. Stop it first "
              f"(kill {pid}), then run this again.", file=sys.stderr)
        return 1

    try:
        if args.cmd == "tools":
            for t in schemas():
                flag = "  [mutates device]" if t["mutates_device"] else ""
                print(f"{t['name']}{flag}\n    {t['description']}")
                props = t["input_schema"]["properties"]
                req = set(t["input_schema"]["required"])
                for k, v in props.items():
                    mark = "*" if k in req else " "
                    print(f"      {mark} {k}: {v.get('type')}")
            return 0

        if args.cmd == "call":
            _print(call(args.tool, json.loads(args.args)))
        elif args.cmd == "info":
            _print(call("device.info"))
        elif args.cmd == "patterns":
            _print(call("pattern.list", {"lo": args.lo, "hi": args.hi}))
        elif args.cmd == "kits":
            _print(call("kit.list", {"lo": args.lo, "hi": args.hi}))
        elif args.cmd == "tones":
            q = {"limit": args.limit}
            if args.category:
                q["category"] = args.category
            if args.melodic:
                q["melodic"] = True
            if args.root:
                q["root"] = args.root
            if args.name_contains:
                q["name_contains"] = args.name_contains
            _print(call("tones.search", q))
        elif args.cmd == "backup":
            _print(call("device.backup", {"lo": args.lo, "hi": args.hi}))
        elif args.cmd == "analyse-tones":
            from .analysis import catalogue_tones
            from .device import Device
            with Device() as d:
                only = {args.category.upper()} if args.category else None
                res = catalogue_tones(d, args.lo, args.hi, only=only)
            _print({"measured": len(res),
                    "catalogue": str(config.tone_catalog_path())})
        elif args.cmd == "probe-byte":
            import json as _json
            from .analysis import probe_kit_byte, probe_many, probe_report
            from .device import Device
            path = config.data_dir() / "kit_byte_probe.json"
            if args.report:
                if not path.exists():
                    print("no probe results yet; run `tr8s probe-byte`",
                          file=sys.stderr)
                    return 1
                print(probe_report(_json.loads(path.read_text())))
                return 0
            with Device() as d:
                if args.offset is None:
                    probe_many(d, tone=args.tone)
                    print()
                    print(probe_report(_json.loads(path.read_text())))
                else:
                    _print(probe_kit_byte(d, args.offset, tone=args.tone))
        elif args.cmd == "survey":
            from datetime import date
            from pathlib import Path
            from . import survey
            d = Path(args.dir) if args.dir else config.data_dir() / "survey" / date.today().isoformat()
            kinds = tuple(k.strip() for k in args.kinds.split(",") if k.strip())
            if args.read and kinds == ("tone",):
                from .device import Device
                with Device() as dev:
                    r = survey.read_tones(dev, args.lo, args.hi, d / "tones.json",
                                          progress=lambda i, t: print(f"  tone {i:4d} {t['name']}",
                                                                      file=sys.stderr, flush=True))
                print(json.dumps(r), file=sys.stderr)
                return 1 if r["stopped"] else 0
            if args.read:
                from .device import Device

                def tick(kind, slot, n):
                    print(f"  {kind} {slot:3d} ok ({n})", file=sys.stderr, flush=True)
                with Device() as dev:
                    r = dev.backup(kinds, args.lo, args.hi, progress=tick, twice=args.twice,
                                   dirs={k: d for k in kinds})
                print(json.dumps({k: v for k, v in r.items() if k != "directory"}), file=sys.stderr)
                if r["stopped"]:
                    print(f"STOPPED at {r['stopped']['kind']} {r['stopped']['panel']}: "
                          f"{r['stopped']['reason']}. Re-run the same command to resume.", file=sys.stderr)
                    return 1
            if "pattern" in kinds:
                rows = survey.analyse_dir(d)
                (d / "survey.json").write_text(json.dumps(rows, indent=1))
                (d / "survey.md").write_text(survey.report(rows))
                print(f"wrote {d / 'survey.md'} and survey.json ({len(rows)} patterns)", file=sys.stderr)
        elif args.cmd == "rework":
            from pathlib import Path
            from . import rework
            spec = rework.load_spec(args.file)
            survey_dir = Path(spec.get("survey", "")).expanduser()
            entries = rework.plan(spec, survey_dir)
            designed = rework.design_kits(spec, entries, survey_dir) if not args.sheet else {}
            for e in entries:
                route = " ".join(f"{r['variation']}={r['role']}" for r in e["route"])
                print(f"{e['panel']} {e['name']:<16} {e['bpm']:>5g} BPM  kit {e['kit_panel']}  "
                      f"from {e['source_panel']} {e['source_name']!r} ({e['source_tempo']:g}, "
                      f"kit {e['source_kit_panel']})  route {route}")
            from datetime import date
            from .cuesheet import render_rework
            sheet_path = Path(args.file).with_suffix(".md")
            if args.sheet:
                sheet_path.write_text(render_rework(spec, entries, date.today()))
                print(f"wrote {sheet_path}", file=sys.stderr)
                return 0
            if args.preview:
                print("preview only: nothing was written. The build would write:", file=sys.stderr)
                for w in rework.writes(entries):
                    print(f"  - {w}", file=sys.stderr)
                return 0
            from .device import Device
            with Device() as dev:
                only = [x.strip() for x in args.only.split(",")] if args.only else None
                done = rework.build(dev, entries, survey_dir, designed=designed, only=only,
                                    log=lambda m: print(m, file=sys.stderr, flush=True))
            out = Path(args.file).with_suffix(".plan.json")
            out.write_text(json.dumps({"entries": entries, "done": done}, indent=1, default=str))
            print(json.dumps(done), file=sys.stderr)
            if not done["failed"]:
                sheet_path.write_text(render_rework(spec, entries, date.today()))
                print(f"wrote {sheet_path}", file=sys.stderr)
            return 1 if done["failed"] else 0
        elif args.cmd == "set":
            from pathlib import Path
            try:
                doc = json.loads(Path(args.file).read_text())
            except (OSError, ValueError) as e:
                print(f"error: cannot read {args.file}: {e}", file=sys.stderr)
                return 1
            if not isinstance(doc, dict):
                print(f"error: {args.file} is not a JSON object", file=sys.stderr)
                return 1
            r = call("set.plan" if args.preview else "set.build",
                     {k: doc[k] for k in ("name", "tracks", "start_slot") if k in doc})
            if args.json:
                _print(r)
            else:
                print(r["cue_sheet"], end="")
                _set_status(r, args.preview)
            return 1 if r.get("failed") else 0
        elif args.cmd == "capture-template":
            from .device import Device
            from .tools import _slot
            with Device() as d:
                p = d.read_pattern(_slot(args.slot))
                summary = p.describe()["variations"]
                if summary:
                    print(f"warning: slot {args.slot} is not empty "
                          f"({sorted(summary)} have steps). A template should be "
                          f"an unused slot.", file=sys.stderr)
                config.template_path().write_bytes(p.to_bytes())
                _print({"template": str(config.template_path()),
                        "from_slot": args.slot, "name": p.name})
        return 0
    except ToolError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        close()


if __name__ == "__main__":
    sys.exit(main())
