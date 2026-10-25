"""Run a user-supplied placer command per (placer, benchmark, mode) and collect final.def + snapshots.

Run dir: <output>/runs/<placer>/<design>_<mode>/ (cwd of the command; stdout/stderr -> run.log; status.json).
A non-zero exit is recorded, never raised. Skipped when a previous run succeeded, unless force.
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import time

from . import snapshot


def fill(template, vals, quote=True):
    """Replace {key} for the known keys only (shell braces such as ${HOME} or awk '{...}' are left alone);
    values are shell-quoted when needed (quote=False for env values), lists are space-joined. Write placeholders
    unquoted (--def {def}, not --def "{def}"): a value that needs quoting would get literal quotes inside yours."""
    q = shlex.quote if quote else str

    def rep(m):
        v = vals.get(m.group(1))
        if v is None:
            return m.group(0)
        return " ".join(q(str(x)) for x in v) if isinstance(v, (list, tuple)) else q(str(v))
    return re.sub(r"\{(\w+)\}", rep, template)


def child_env(extra=None):
    """os.environ + extra, with this placeviz first on PYTHONPATH (adapters can `import placeviz` uninstalled)."""
    pp = [os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.environ.get("PYTHONPATH", "")]
    return dict(os.environ, PYTHONPATH=os.pathsep.join(x for x in pp if x), **(extra or {}))


def run_cmd(cmd, cwd, log, env=None):
    """-> (exit code, seconds); a signal death is reported as 128 + signal (like a shell)."""
    t0 = time.time()
    with open(log, "w") as f:
        f.write("$ %s\n" % cmd)
        f.flush()
        p = subprocess.run(cmd, shell=True, cwd=cwd, stdout=f, stderr=subprocess.STDOUT, env=child_env(env))
    rc = p.returncode if p.returncode >= 0 else 128 - p.returncode
    return rc, round(time.time() - t0, 1)


def exit_status(rc):
    return "ok" if rc == 0 else ("crashed (signal %d)" % (rc - 128) if rc > 128 else "exit %d" % rc)


def read_status(d):
    p = os.path.join(d, "status.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def record(d, stage, **kw):
    st = read_status(d)
    st[stage] = kw
    with open(os.path.join(d, "status.json.part"), "w") as f:
        json.dump(st, f, indent=1)
    os.replace(os.path.join(d, "status.json.part"), os.path.join(d, "status.json"))
    return kw["status"]


def run_dir(cfg, placer, bench):
    return os.path.join(cfg["output"], "runs", placer, bench.key)


def values(cfg, placer, bench, rd):
    """Placeholders of the placer command / env."""
    return {"lef": list(bench.lef), "lef0": bench.lef[0], "def": bench.deff, "out_def": os.path.join(rd, "final.def"),
            "snap_dir": os.path.join(rd, "snap"), "run_dir": rd, "mode": bench.mode, "design": bench.name,
            "placer": placer, "threads": cfg["threads"]}


def run(cfg, placer, bench, force=False):
    """placer: config entry {name, cmd, env}. -> status string."""
    rd = run_dir(cfg, placer["name"], bench)
    if not placer.get("cmd"):
        return "existing run" if os.path.exists(os.path.join(rd, "meta.json")) else "no run (placer has no cmd)"
    if not force and read_status(rd).get("place", {}).get("status") == "ok" \
            and os.path.exists(os.path.join(rd, "meta.json")):
        return "skip (done)"
    shutil.rmtree(rd, ignore_errors=True)
    os.makedirs(os.path.join(rd, "snap"))
    v = values(cfg, placer["name"], bench, rd)
    cmd = fill(placer["cmd"], v)
    rc, sec = run_cmd(cmd, rd, os.path.join(rd, "run.log"), {k: fill(x, v, False) for k, x in placer["env"].items()})
    has_final = os.path.exists(os.path.join(rd, "final.def"))
    status = exit_status(rc) if rc or has_final else "no final.def"
    try:
        meta = snapshot.finalize(rd, placer["name"], bench, crashed=rc != 0)
        note = "%d frames (%s, %s)" % (len(meta["frames"]), meta["snapshots"], meta["final_check"])
    except Exception as e:  # noqa: BLE001  (bad snapshots must not abort the batch)
        status, note = "bad snapshots", "%s: %s" % (type(e).__name__, e)
    return record(rd, "place", status=status, rc=rc, seconds=sec, cmd=cmd, note=note)
