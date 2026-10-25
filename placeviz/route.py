"""Final global (GR) and detailed (DR) routing of one placed run, as user-supplied command templates.

Route dir: <output>/route/<placer>/<design>_<mode>/
  eval.def   final.def with the macros back in their input orientation (lefdef.restore_orient)
  in.def     eval.def + GCELLGRID if missing (lefdef.add_gcell; CU-GR needs one)
  gr.guide   gr.log    dr.def   dr.log   vio.csv (optional violation boxes)   status.json {gr: {...}, dr: {...}}
"""
import os
import shutil

from . import lefdef
from .place import exit_status, fill, read_status, record, run_cmd, run_dir


def route_dir(cfg, placer, bench):
    return os.path.join(cfg["output"], "route", placer, bench.key)


def prepare(cfg, placer, bench):
    """eval.def + in.def (idempotent). -> route dir, or None when the placer left no final.def."""
    final = os.path.join(run_dir(cfg, placer, bench), "final.def")
    if not os.path.exists(final):
        return None
    rd = route_dir(cfg, placer, bench)
    os.makedirs(rd, exist_ok=True)
    ev, ind = os.path.join(rd, "eval.def"), os.path.join(rd, "in.def")
    if not os.path.exists(ev) or os.path.getmtime(ev) < os.path.getmtime(final):
        n = lefdef.restore_orient(bench.fix_def, final, ev + ".part", bench.lef)
        os.replace(ev + ".part", ev)
        lefdef.add_gcell(ev, ind)
        _clear(rd, OUTPUTS["gr"] + ("status.json",))  # new placement: earlier GR / DR results are stale
        with open(os.path.join(rd, "restore.log"), "w") as f:
            f.write("restored orientation of %d macros\n" % n)
    return rd


def values(cfg, placer, bench, rd, tool):
    return {"lef": list(bench.lef), "lef0": bench.lef[0], "def": os.path.join(rd, "in.def"),
            "guide": os.path.join(rd, "gr.guide"), "dr_def": os.path.join(rd, "dr.def"), "route_dir": rd,
            "run_dir": run_dir(cfg, placer, bench), "mode": bench.mode, "design": bench.name, "placer": placer,
            "threads": cfg["threads"], "name": tool["name"]}


OUTPUTS = {"gr": ("gr.guide", "dr.def", "vio.csv"), "dr": ("dr.def", "vio.csv")}  # a new guide makes DR stale


def _clear(rd, files):
    for f in files:
        if os.path.exists(os.path.join(rd, f)):
            os.remove(os.path.join(rd, f))


def _start(cfg, placer, bench, stage, force):
    """-> (route dir, None) when the stage must run (its old outputs removed, so none can be taken as the new
    result), else (None, message)."""
    rd = prepare(cfg, placer, bench)
    if rd is None:
        return None, "skip (no final.def)"
    if not force and read_status(rd).get(stage, {}).get("status") == "ok":
        return None, "skip (done)"
    _clear(rd, OUTPUTS[stage])
    if stage == "gr" and "dr" in read_status(rd):
        record(rd, "dr", status="not run", note="GR re-run")
    return rd, None


def gr(cfg, placer, bench, force=False):
    rd, msg = _start(cfg, placer, bench, "gr", force)
    if rd is None:
        return msg
    tool = cfg["gr"]
    v = values(cfg, placer, bench, rd, tool)
    cmd = fill(tool["cmd"], v)
    rc, sec = run_cmd(cmd, rd, os.path.join(rd, "gr.log"), {k: fill(x, v, False) for k, x in tool["env"].items()})
    st = exit_status(rc) if rc or os.path.exists(v["guide"]) else "no guide"
    return record(rd, "gr", status=st, rc=rc, seconds=sec, cmd=cmd, tool=tool["name"])


def gr_ok(rd):
    return read_status(rd).get("gr", {}).get("status") == "ok"


def dr(cfg, placer, bench, force=False):
    rd, msg = _start(cfg, placer, bench, "dr", force)
    if rd is None:
        return msg
    if not gr_ok(rd):
        return "skip (no GR guide)"
    tool = cfg["dr"]
    v = values(cfg, placer, bench, rd, tool)
    src = os.path.join(rd, fill(tool["vio_csv"], v, False)) if tool.get("vio_csv") else None  # relative: route dir
    if src and os.path.exists(src):
        os.remove(src)  # a violation file left by an earlier run must not be counted for this placement
    cmd = fill(tool["cmd"], v)
    rc, sec = run_cmd(cmd, rd, os.path.join(rd, "dr.log"), {k: fill(x, v, False) for k, x in tool["env"].items()})
    log = open(os.path.join(rd, "dr.log"), errors="ignore").read()
    has_def = os.path.exists(v["dr_def"])
    # Dr.CU may abort (e.g. exit 134) while freeing memory after a complete report and dr.def: count as ok
    after_report = rc not in (0, 139) and has_def and "Terminated..." in log
    st = "ok" if (rc == 0 and has_def) or after_report else (exit_status(rc) if rc else "no dr.def")
    vio = os.path.join(rd, "vio.csv")
    if src and os.path.abspath(src) != vio and os.path.exists(src):
        shutil.copyfile(src, vio)
    n = len(load_vio(vio)) if st == "ok" and os.path.exists(vio) else None
    return record(rd, "dr", status=st, rc=rc, seconds=sec, cmd=cmd, tool=tool["name"], drv=n,
                  note="exit %d after a complete report" % rc if after_report else None)


def load_vio(path):
    """vio.csv rows [type, layer, xl, yl, xh, yh] (header skipped). Exact duplicate `minarea` rows are dropped:
    Dr.CU's MIN_AREA_VIO counter double-counts them."""
    seen, out = set(), []
    for row in open(path).read().split("\n")[1:]:
        if not row or (row.startswith("minarea") and row in seen):
            continue
        seen.add(row)
        t = row.split(",")
        out.append([t[0], t[1]] + [float(x) for x in t[2:6]])
    return out


def status(cfg, placer, bench):
    """{placement, gr, dr, drv} status strings for the viewers (render / final tab)."""
    rs = read_status(run_dir(cfg, placer, bench)).get("place", {}).get("status")
    has_final = os.path.exists(os.path.join(run_dir(cfg, placer, bench), "final.def"))
    st = read_status(route_dir(cfg, placer, bench))
    g, d = st.get("gr", {}).get("status", "not run"), st.get("dr", {}).get("status", "not run")
    return dict(placement="ok" if has_final else ("placer " + rs if rs else "missing"), gr=g, dr=d,
                drv=d if d != "ok" or st["dr"].get("drv") is not None else "missing")
