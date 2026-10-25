"""placeviz command line.

  placeviz run <config.yaml> [--stages place,eval,gr,dr,render,final,page] [--only <placer>/<design>_<mode>]...
                             [--jobs N] [--force]
  placeviz page <output dir>                       rebuild web/index.html from existing results
  placeviz convert-defs <dump dir> --def <input.def> --out <run dir> [--stage cellGP]
  placeviz check <run dir>                         validate frames + meta.json of a run

stages: place (run the placer commands), eval (HPWL / overlap / congestion of the snapshots), gr / dr (route the
final placement), render (frame sprites), final (final-tab tiles), page (web/index.html).
--only takes shell patterns (e.g. 'xplace/*_mov'); stages are idempotent (skip finished work unless --force);
a failing unit is reported and recorded, never aborts the batch. Config schema: config.example.yaml.
"""
import argparse
import fnmatch
import json
import os
import sys
import time
from multiprocessing import Pool

import yaml

from . import bench as bench_mod
from . import config, place, route, snapshot

STAGES = ["place", "eval", "gr", "dr", "render", "final", "page"]
UNIT_STAGES = {"place", "eval", "gr", "dr", "render", "final"}


def placers(cfg):
    """Config placer entries in order; with `placers: no`, the placers that have runs in <output>/runs/."""
    if cfg["placers"] is not None:
        return cfg["placers"]
    root = os.path.join(cfg["output"], "runs")
    names = sorted(os.listdir(root)) if os.path.isdir(root) else []
    return [dict(name=n, cmd=None, env={}) for n in names if os.path.isdir(os.path.join(root, n))]


def units(cfg, only=None):
    """[(placer entry, Bench)] in config order (placer-major), filtered by --only patterns."""
    out = [(p, b) for p in placers(cfg) for b in bench_mod.load(cfg)]
    if only:
        out = [(p, b) for p, b in out if any(fnmatch.fnmatch("%s/%s" % (p["name"], b.key), o) for o in only)]
    return out


def _unit(stage, cfg, p, b, force, extra):
    t0 = time.time()
    name = p["name"]
    try:
        if stage == "place":
            s = place.run(cfg, p, b, force)
        elif stage in ("gr", "dr"):
            s = getattr(route, stage)(cfg, name, b, force)
        elif not os.path.exists(os.path.join(place.run_dir(cfg, name, b), "meta.json")):
            s = "skip (no run)"
        elif stage == "eval":
            from . import evaluate
            s = evaluate.run(place.run_dir(cfg, name, b), b, cfg, force=force)
        elif stage == "render":
            from .render import player
            s = player.build_run(cfg, name, b, extra, force=force)
        else:
            from .render import final
            s = final.build_run(cfg, name, b, force=force)
    except Exception as e:  # noqa: BLE001  (report, keep going)
        s = "error: %s: %s" % (type(e).__name__, e)
    return "%s/%s" % (name, b.key), s, time.time() - t0


def run_stage(stage, cfg, us, jobs, force):
    if stage in ("gr", "dr") and cfg[stage] is None:
        print("[%s] disabled in config" % stage)
        return
    if stage == "place" and cfg["placers"] is None:
        print("[place] placers: no -> using existing runs")
        return
    extra = None
    try:  # modules of later stages are imported here so that a missing one disables only its stage
        if stage == "eval":
            from . import evaluate  # noqa: F401
        elif stage == "render":
            from .render import player
            extra = player.limits(cfg, [(p["name"], b) for p, b in us])
        elif stage == "final":
            from .render import final
        elif stage == "page":
            from .web import page
            print("[page] %s" % page.build(cfg, [(p["name"], b) for p, b in units(cfg)]))
            return
    except ImportError as e:
        print("[%s] not available: %s" % (stage, e))
        return
    args = [(stage, cfg, p, b, force, extra) for p, b in us]
    if jobs > 1 and len(args) > 1:
        with Pool(min(jobs, len(args))) as pool:
            res = pool.starmap(_unit, args)
    else:
        res = [_unit(*a) for a in args]
    for key, s, sec in res:
        print("[%s] %-40s %s (%.1fs)" % (stage, key, s, sec), flush=True)
    if stage == "final":
        print("[final] index.js: %d runs" % final.write_index(cfg))


def cmd_run(a):
    cfg = config.load(a.config)
    stages = STAGES if a.stages == "all" else a.stages.split(",")
    bad = [s for s in stages if s not in STAGES]
    if bad:
        sys.exit("unknown stage(s) %s; stages: %s" % (bad, ",".join(STAGES)))
    os.makedirs(cfg["output"], exist_ok=True)
    with open(os.path.join(cfg["output"], "config.json"), "w") as f:  # for `placeviz page <output>`
        json.dump(cfg, f, indent=1)
    us = units(cfg, a.only)
    if not us:
        raise ValueError("no units to run: " + ("no unit matches --only %s" % a.only if a.only else
                         "placers: no, and %s has no runs" % os.path.join(cfg["output"], "runs")))
    print("placeviz: %d units (%s), stages %s, output %s" % (len(us), ", ".join(
        sorted({p["name"] for p, _ in us})), ",".join(stages), cfg["output"]), flush=True)
    for s in STAGES:  # canonical order whatever the order on the command line
        if s in stages:
            run_stage(s, cfg, us, a.jobs or cfg["jobs"], a.force)


def cmd_page(a):
    p = os.path.join(a.output, "config.json")
    if not os.path.exists(p):
        raise ValueError("%s: not a placeviz output dir (no config.json, written by placeviz run)" % a.output)
    with open(p) as f:
        cfg = json.load(f)
    run_stage("page", cfg, [], 1, False)


def cmd_convert(a):
    n = snapshot.convert_defs(a.dumps, a.deff, a.out, a.stage)
    print("%d frames -> %s" % (n, a.out))


def cmd_check(a):
    if not os.path.exists(os.path.join(a.run, "meta.json")):
        raise ValueError("%s: not a run dir (no meta.json; run dirs are <output>/runs/<placer>/<design>_<mode>)"
                         % a.run)
    probs = snapshot.check(a.run)
    print("\n".join(probs) if probs else "ok")
    sys.exit(1 if probs else 0)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="placeviz", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    r = sub.add_parser("run", help="run the stages of a config")
    r.add_argument("config", help="YAML config (schema: config.example.yaml)")
    r.add_argument("--stages", default="all", help="comma list of %s (default: all)" % ",".join(STAGES))
    r.add_argument("--only", action="append", help="<placer>/<design>_<mode> shell pattern (repeatable)")
    r.add_argument("--jobs", type=int, default=None, help="parallel units per stage (default: config jobs)")
    r.add_argument("--force", action="store_true", help="redo finished work of the selected stages and units")
    r.set_defaults(fn=cmd_run)
    p = sub.add_parser("page", help="rebuild web/index.html from existing results")
    p.add_argument("output", help="the config's output dir (holds config.json)")
    p.set_defaults(fn=cmd_page)
    c = sub.add_parser("convert-defs", help="per-iteration DEF dumps -> snapshot frames")
    c.add_argument("dumps", help="dir of <iter>[_<stage>].def dumps")
    c.add_argument("--def", dest="deff", required=True, help="input DEF (component order)")
    c.add_argument("--out", required=True, help="output dir (frames/ + meta.json)")
    c.add_argument("--stage", default="cellGP", help="stage of dumps named <iter>.def (default: cellGP)")
    c.set_defaults(fn=cmd_convert)
    k = sub.add_parser("check", help="validate frames + meta.json of a run dir")
    k.add_argument("run", help="<output>/runs/<placer>/<design>_<mode>")
    k.set_defaults(fn=cmd_check)
    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except (OSError, ValueError, KeyError, yaml.YAMLError) as e:  # user / config errors: one line, no traceback
        sys.exit("placeviz: error: %s" % e)


if __name__ == "__main__":
    main()
