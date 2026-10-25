"""Per-frame evaluator: HPWL, overlap and congestion (backend from cfg["congestion"]) of every selected frame.

Writes <run>/eval/metrics.json (list aligned with meta.frames, evaluated frames only), <run>/eval/maps/frame_NNNNN.npz
and <run>/eval/eval.log. Resumable: frames with a metrics entry from the same backend (and a map) are skipped; a new
meta.json (placer re-run) or force starts over.

Metrics (identical for every placer):
- hpwl (DBU): pin-centre HPWL over nets with >= 2 pins (= lefdef.hpwl). Pin centre = bbox centre of the LEF PORT
  rects, transformed by the component orientation; I/O pins from DEF PINS.
- overlap: density overflow on square bins of 4 x 4 rows over the core (rows bbox), target density 1.0:
  sum_b max(0, A_mov(b) + A_fix(b) - A_bin(b)) / sum A_mov, exact rectangle-bin areas, components clipped to the
  core. "mov" = not FIXED in the mode's input DEF (in mov mode macros are movable).
- congestion (maps + r_overflow, r_ovfl_edges, r_t5, r_cong_max on routable resources, see congestion.routable);
  ggr adds gr_* router metrics and pat_r_* (pattern routing only) on the last frame.
- t_cpu, t_cong: seconds per frame.
maps/frame_NNNNN.npz: dmdH/V capH/V fixH/V ovflH/V (float16), cong = max(dmdH/capH, dmdV/capV) (cap 0 -> 0),
rcong = routable congestion (NaN = fully blocked), gx/gy gridlines (DBU, int32); arrays indexed [ix, iy].
Orientation policy (eval_orient): macros keep their input orientation; std cells use the frame's orient if stored,
else on LG/DP/final frames the orientation of their row, else the input orientation.
"""
import dataclasses
import json
import math
import os
import subprocess
import sys
import time

import numpy as np

from . import congestion
from .lefdef import ORIENTS, Def, Lef, orient_np, row_height
from .place import child_env

GP_STAGES = ("mixedGP", "cellGP", "GP")  # periodic frames; any other stage is a boundary (always kept)


class Design:
    """LEF/DEF of a bench (the mode's input DEF) as numpy arrays, components in DEF order (= frame order)."""

    def __init__(self, bench):
        self.bench = bench
        self.lef, d = Lef(bench.lef), Def(bench.deff)
        self.def_, self.dbu, self.die = d, d.dbu, d.die
        self.row_h = row_height(self.lef, d)
        ry = sorted({r.y for r in d.rows})
        self.core = (min(r.x for r in d.rows), ry[0], max(r.x + r.nx * r.sx for r in d.rows), ry[-1] + self.row_h)
        self.row_orient = {r.y: ORIENTS.index(r.orient) for r in d.rows}
        self.names = list(d.comps)
        self.idx = {n: i for i, n in enumerate(self.names)}
        cs = list(d.comps.values())
        self.macros = [c.macro for c in cs]
        self.fixed = np.array([c.status == "FIXED" for c in cs])
        self.orient0 = np.array([ORIENTS.index(c.orient) for c in cs], dtype=np.int8)
        self.fx = np.array([c.x if c.x is not None else 0.0 for c in cs])  # input positions (fixed must stay)
        self.fy = np.array([c.y if c.y is not None else 0.0 for c in cs])
        size = np.array([self.lef.macros[m]["size"] for m in self.macros]).reshape(-1, 2) * self.dbu
        self.w, self.h = size[:, 0], size[:, 1]  # DBU, N orientation
        self.macro = self.h > self.row_h + 1

        # flat pin arrays in net order (nets with >= 2 pins only, like lefdef.hpwl); I/O pins: absolute DBU
        lp = self.lef.pin_centres()
        io = {n: (((p.rect[0] + p.rect[2]) / 2, (p.rect[1] + p.rect[3]) / 2) if p.rect else (p.x or 0.0, p.y or 0.0))
              for n, p in d.pins.items()}
        pc, px, py, start = [], [], [], []
        for _, conns in d.nets:
            if len(conns) < 2:
                continue
            start.append(len(pc))
            for c, p in conns:
                i = -1 if c == "PIN" else self.idx[c]
                pc.append(i)
                v = io[p] if i < 0 else lp[self.macros[i]][p]  # component pins: um, LEF frame (N)
                px.append(v[0])
                py.append(v[1])
        self.pin_c, self.pin_x, self.pin_y = np.array(pc, dtype=int), np.array(px), np.array(py)
        self.net_start = np.array(start, dtype=int)
        bs = 4 * self.row_h  # overlap bins: 4 x 4 rows over the core, the last row/column clipped
        cx0, cy0, cx1, cy1 = self.core
        self.bins = (bs, int(math.ceil((cx1 - cx0) / bs)), int(math.ceil((cy1 - cy0) / bs)))
        self.bgx = np.minimum(cx0 + np.arange(self.bins[1] + 1) * bs, cx1)
        self.bgy = np.minimum(cy0 + np.arange(self.bins[2] + 1) * bs, cy1)
        self._a_fix = None

    def eval_orient(self, x, y, o, has_o, stage):
        """Orientation used for HPWL/overlap, identical policy for every placer:
        - macros always keep their input orientation (placers snap moved macros to the row orientation although they
          optimized them unflipped; routing is evaluated the same way, see lefdef.restore_orient);
        - std cells use the frame's orient if stored, else on LG/DP frames the orientation of the row they sit on
          (what the placers write), else the input orientation (global placement does not flip cells)."""
        out = (o if has_o else self.orient0).copy()
        if stage in ("LG", "DP", "final") and not has_o:
            std = ~self.macro & ~self.fixed
            ro = np.array([self.row_orient.get(float(v), -1) for v in y[std]])
            out[std] = np.where(ro >= 0, ro, out[std])
        out[self.macro] = self.orient0[self.macro]
        return out

    def pins(self, x, y, orient):
        """Absolute pin centres (DBU) in net order."""
        io = self.pin_c < 0
        c = np.where(io, 0, self.pin_c)
        tx, ty = orient_np(self.pin_x, self.pin_y, self.w[c] / self.dbu, self.h[c] / self.dbu, orient[c])
        return np.where(io, self.pin_x, x[c] + tx * self.dbu), np.where(io, self.pin_y, y[c] + ty * self.dbu)

    def hpwl(self, x, y, orient):
        X, Y = self.pins(x, y, orient)
        s = self.net_start
        return float((np.maximum.reduceat(X, s) - np.minimum.reduceat(X, s)).sum()
                     + (np.maximum.reduceat(Y, s) - np.minimum.reduceat(Y, s)).sum())

    def overlap(self, x, y, orient):
        rot = np.isin(orient, (2, 3, 6, 7))  # W E FW FE swap w/h
        w, h = np.where(rot, self.h, self.w), np.where(rot, self.w, self.h)
        lx, ly = x.astype(float), y.astype(float)

        def area(m):
            return congestion.raster(self.bgx, self.bgy, lx[m], ly[m], lx[m] + w[m], ly[m] + h[m])
        if self._a_fix is None:  # fixed components never move
            self._a_fix = area(self.fixed)
            self._a_bin = np.outer(np.diff(self.bgx), np.diff(self.bgy))
        mov = ~self.fixed
        ov = np.clip(area(mov) + self._a_fix - self._a_bin, 0, None).sum()
        return float(ov / (w[mov] * h[mov]).sum())


def select_frames(frames, max_frames):
    """All stage-boundary frames + first/last, plus evenly spaced periodic GP frames up to max_frames in total."""
    n = len(frames)
    special = sorted({0, n - 1} | {k for k, f in enumerate(frames) if f["stage"] not in GP_STAGES})
    periodic = [k for k in range(n) if k not in special]
    budget = max(max_frames - len(special), 0)
    if len(periodic) > budget:
        periodic = [periodic[i] for i in np.unique(np.linspace(0, len(periodic) - 1, budget).round().astype(int))]
    return set(special) | set(periodic)


def load_frame(path, d):
    with np.load(path) as f:
        x, y = f["x"].astype(np.int64), f["y"].astype(np.int64)
        has = "orient" in f.files
        o = f["orient"].astype(np.int8) if has else d.orient0
    assert len(x) == len(d.names), "%s: %d components, design has %d" % (path, len(x), len(d.names))
    return x, y, o, has


def run(run_dir, bench, cfg, force=False):
    """Evaluate one run (resumable). ggr runs in a child process: Xplace keeps global C++ state and exits the whole
    process on a CUDA error (e.g. out of memory on a shared GPU); one retry, which resumes."""
    if (cfg.get("congestion") or {}).get("backend") != "ggr":
        return _run(run_dir, bench, cfg, force)
    arg = json.dumps([run_dir, dataclasses.asdict(bench), cfg, force])
    env = child_env()
    os.makedirs(os.path.join(run_dir, "eval"), exist_ok=True)
    for _ in range(2):
        p = subprocess.run([sys.executable, "-m", "placeviz.evaluate", arg], env=env, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        with open(os.path.join(run_dir, "eval", "eval.log"), "a") as log:
            log.write(p.stdout)
        if p.returncode == 0:
            return next((ln[16:] for ln in p.stdout.split("\n") if ln.startswith("placeviz status ")), "ok")
        arg = json.dumps([run_dir, dataclasses.asdict(bench), cfg, False])
    raise RuntimeError("ggr evaluation exit %d (see eval/eval.log)" % p.returncode)


def _run(run_dir, bench, cfg, force=False):
    meta_p = os.path.join(run_dir, "meta.json")
    meta = json.load(open(meta_p))
    if not meta["frames"]:
        return "skip (no frames)"
    out = os.path.join(run_dir, "eval")
    os.makedirs(os.path.join(out, "maps"), exist_ok=True)
    mpath = os.path.join(out, "metrics.json")
    conf = cfg.get("congestion")
    conf = conf and dict(conf, threads=cfg.get("threads", 8))
    backend = conf["backend"] if conf else None
    fresh = force or not os.path.exists(mpath) or os.path.getmtime(mpath) < os.path.getmtime(meta_p)
    res = {} if fresh else {m["file"]: m for m in json.load(open(mpath))}
    frames, last = meta["frames"], len(meta["frames"]) - 1
    todo = []
    for k in sorted(select_frames(frames, cfg.get("max_frames", 100))):
        fr = frames[k]
        mapf = os.path.join(out, "maps", os.path.basename(fr["file"]))
        m = res.get(fr["file"])
        if m is None or m.get("congestion") != backend or (backend and not os.path.exists(mapf)):
            todo.append(k)
    if not todo:
        return "skip (done, %d frames)" % len(res)
    log = open(os.path.join(out, "eval.log"), "a")

    def say(s):
        log.write(s + "\n")
        log.flush()
    t0 = time.time()
    d = Design(bench)
    cg = congestion.load(d, conf) if conf else None
    say("[eval] %s/%s: %d frames, %d to do, load %.1fs, bins %s, congestion %s" % (
        meta["placer"], bench.key, len(frames), len(todo), time.time() - t0, d.bins[1:], cg and cg.desc))
    for k in todo:
        fr = frames[k]
        name = os.path.basename(fr["file"])
        t1 = time.time()
        x, y, o, has_o = load_frame(os.path.join(run_dir, fr["file"]), d)
        if k == 0:
            bad = d.fixed & ((x != d.fx) | (y != d.fy))
            if bad.any():
                say("[eval] WARNING %d fixed components moved in frame 0" % bad.sum())
        o = d.eval_orient(x, y, o, has_o, fr["stage"])
        m = {"file": fr["file"], "iter": fr["iter"], "stage": fr["stage"], "congestion": backend,
             "hpwl": d.hpwl(x, y, o), "overlap": d.overlap(x, y, o)}
        t2 = time.time()
        if cg is not None:
            met, mp = cg.run(x, y, o, has_o, k == last)
            for dd in "HV":
                mp["ovfl" + dd] = np.maximum(mp["dmd" + dd] - mp["cap" + dd], 0)
            with np.errstate(divide="ignore", invalid="ignore"):
                cong = np.fmax(np.where(mp["capH"] > 0, mp["dmdH"] / mp["capH"], 0),
                               np.where(mp["capV"] > 0, mp["dmdV"] / mp["capV"], 0))
            rmet, rcong = congestion.routable(mp)
            m.update(rmet, **met)
            f16 = {kk: mp[kk].astype(np.float16) for kk in ("dmdH", "dmdV", "capH", "capV", "fixH", "fixV",
                                                            "ovflH", "ovflV")}
            np.savez_compressed(os.path.join(out, "maps", name), cong=cong.astype(np.float16),
                                rcong=rcong.astype(np.float16), gx=mp["gx"].astype(np.int32),
                                gy=mp["gy"].astype(np.int32), **f16)
        m.update(t_cpu=round(t2 - t1, 3), t_cong=round(time.time() - t2, 3))
        res[fr["file"]] = m
        with open(mpath + ".part", "w") as fp:
            json.dump([res[f["file"]] for f in frames if f["file"] in res], fp, indent=0)
        os.replace(mpath + ".part", mpath)
        say("[eval] %s iter=%s %s hpwl=%.6e ovl=%.4f rovfl=%s rt5=%s (cpu %.2fs cong %.2fs)" % (
            name, fr["iter"], fr["stage"], m["hpwl"], m["overlap"], m.get("r_overflow"), m.get("r_t5"),
            m["t_cpu"], m["t_cong"]))
    say("[eval] done %d frames in %.1fs" % (len(todo), time.time() - t0))
    log.close()
    return "%d frames (%d new), %s, %.1fs" % (len(res), len(todo), backend or "no congestion", time.time() - t0)


if __name__ == "__main__":  # child process of run() for ggr
    rd, b, c, f = json.loads(sys.argv[1])
    from .bench import Bench
    print("placeviz status " + _run(rd, Bench(**dict(b, lef=tuple(b["lef"]))), c, f), flush=True)
