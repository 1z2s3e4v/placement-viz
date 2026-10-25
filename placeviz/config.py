"""Load and validate the YAML config (schema: config.example.yaml). `no` / false / null disables a stage."""
import difflib
import os

import yaml

MODES = ("fix", "mov")
KEYS = {"config": ("output", "benchmarks", "modes", "placers", "gr", "dr", "congestion", "max_frames", "jobs",
                   "threads", "gifs", "title"),
        "benchmark": ("name", "lef", "def"), "placer": ("name", "cmd", "env", "label"),
        "gr": ("name", "cmd", "env"), "dr": ("name", "cmd", "env", "vio_csv"),
        "congestion": ("backend", "rows_per_gcell", "max_gcells", "xplace", "gpu", "rrr", "rrr_final")}


def _keys(d, what):
    """Typos must not silently fall back to a default."""
    if not isinstance(d, dict):
        raise ValueError("%s: expected a mapping, got %r" % (what, d))
    for k in d:
        if k not in KEYS[what.split()[0]]:
            hint = difflib.get_close_matches(str(k), KEYS[what.split()[0]], 1)
            raise ValueError("%s: unknown key '%s'%s" % (what, k, " (did you mean '%s'?)" % hint[0] if hint else ""))


def _off(v):
    return v is None or v is False or (isinstance(v, str) and v.lower() in ("no", "off", "none", "false"))


def _path(v, base):
    return os.path.normpath(os.path.join(base, os.path.expanduser(str(v))))


def _tool(v, what):
    if _off(v):
        return None
    _keys(v, what)
    if not v.get("cmd"):
        raise ValueError("%s: expected a mapping with 'cmd' (or no)" % what)
    return dict(v, name=str(v.get("name", what)), env={k: str(x) for k, x in (v.get("env") or {}).items()})


def load(path):
    """-> normalized dict: output, benchmarks [{name, lef: [..], def}], modes, placers [{name, cmd|None, env}] or None,
    gr / dr {name, cmd, env, ...} or None, congestion {backend, ...} or None, max_frames, jobs, threads."""
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    base = os.path.dirname(os.path.abspath(path))
    _keys(raw, "config")
    if not raw.get("output"):
        raise ValueError("config: 'output' is required")
    cfg = dict(raw, output=_path(raw["output"], base))

    benches = []
    if not isinstance(raw.get("benchmarks") or [], list):
        raise ValueError("config: benchmarks must be a list")
    for b in raw.get("benchmarks") or []:
        _keys(b, "benchmark")
        if not b.get("lef") or not b.get("def"):
            raise ValueError("benchmark %s: 'lef' and 'def' are required" % b.get("name", "?"))
        lef = b["lef"] if isinstance(b["lef"], list) else [b["lef"]]
        b = dict(b, lef=[_path(x, base) for x in lef], **{"def": _path(b["def"], base)})
        for p in b["lef"] + [b["def"]]:
            if not os.path.isfile(p):
                raise ValueError("benchmark %s: file not found: %s" % (b.get("name"), p))
        b.setdefault("name", os.path.splitext(os.path.basename(b["def"]))[0])
        benches.append(b)
    if not benches:
        raise ValueError("config: no benchmarks")
    if len({b["name"] for b in benches}) != len(benches):
        raise ValueError("config: duplicate benchmark names")
    cfg["benchmarks"] = benches

    modes = raw.get("modes") or ["fix"]
    if isinstance(modes, str):
        modes = [modes]
    if not set(modes) <= set(MODES):
        raise ValueError("config: modes must be a subset of %s" % (MODES,))
    cfg["modes"] = [m for m in MODES if m in modes]

    pl = raw.get("placers")
    if _off(pl):
        cfg["placers"] = None  # use the runs already in output/runs/
    else:
        if not isinstance(pl, list):
            raise ValueError("config: placers must be a list (or no)")
        cfg["placers"] = []
        for p in pl:
            p = {"name": p} if isinstance(p, str) else dict(p)
            _keys(p, "placer %s" % p.get("name", "?"))
            if not p.get("name"):
                raise ValueError("placer: 'name' is required")
            p["env"] = {k: str(x) for k, x in (p.get("env") or {}).items()}
            p.setdefault("cmd", None)  # no cmd: existing runs of this placer (keeps the config row order)
            cfg["placers"].append(p)
        names = [p["name"] for p in cfg["placers"]]
        if len(set(names)) != len(names) or any("/" in n for n in names):
            raise ValueError("config: placer names must be unique and contain no '/'")

    cfg["gr"], cfg["dr"] = _tool(raw.get("gr"), "gr"), _tool(raw.get("dr"), "dr")
    c = raw.get("congestion", "rudy")
    cfg["congestion"] = None if _off(c) else (dict(backend=c) if isinstance(c, str) else dict(c))
    if cfg["congestion"]:
        _keys(cfg["congestion"], "congestion")
    if cfg["congestion"] and cfg["congestion"].get("backend") not in ("rudy", "ggr"):
        raise ValueError("config: congestion backend must be rudy or ggr")
    if cfg["congestion"] and cfg["congestion"].get("xplace"):
        cfg["congestion"]["xplace"] = _path(cfg["congestion"]["xplace"], base)
    for k, d in (("max_frames", 100), ("jobs", 1), ("threads", 8)):
        cfg[k] = int(raw.get(k) or d)
    return cfg
