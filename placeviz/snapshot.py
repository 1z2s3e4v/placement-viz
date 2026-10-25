"""Placement snapshots (docs/SNAPSHOT_FORMAT.md).

(1) Writer, for placers:   w = Writer(snap_dir, components_from_def=input_def); w.frame(x, y, iter=k, stage="cellGP")
    -> <snap_dir>/frames/frame_NNNNN.npz (x, y int32 lower-left DBU in DEF COMPONENTS order, optional orient int8)
       + <snap_dir>/meta.json {"frames": [{file, iter, stage, placer_hpwl, placer_overflow}]}
(2) convert_defs: per-iteration DEF dumps `<iter>[_<stage>].def` -> the same frames.
finalize() turns a run dir into the FORMAT.md layout and checks that the last frame equals final.def.
"""
import json
import os
import re
import shutil

import numpy as np

from .lefdef import ORIENTS, Def, parse_components

DUMP_RE = re.compile(r"^(\d+)(?:_(\w+))?\.def$")


def load_npz(path):
    """All arrays of an .npz as a dict (the file is closed again; NpzFile keeps a descriptor open)."""
    with np.load(path) as z:
        return {k: z[k] for k in z.files}


def read_components(def_path):
    with open(def_path) as f:
        return parse_components(f.read())


class Writer:
    def __init__(self, snap_dir, components_from_def=None, names=None):
        """components_from_def: the input DEF (gives the component order and positions for omitted fixed ones);
        or names: the component names in DEF COMPONENTS order."""
        self.dir = snap_dir
        self.ref = read_components(components_from_def) if components_from_def else {}
        self.names = list(names) if names is not None else list(self.ref)
        self.meta = {"frames": []}
        os.makedirs(os.path.join(snap_dir, "frames"), exist_ok=True)

    def _by_name(self, pos, what):
        out = []
        for n in self.names:
            if n in pos:
                out.append(pos[n])
            elif n in self.ref and self.ref[n].x is not None:  # e.g. fixed components the placer does not report
                c = self.ref[n]
                out.append((c.x, c.y) if what == "xy" else c.orient)
            else:
                raise KeyError("frame: no %s for component %s" % (what, n))
        return out

    def frame(self, x, y=None, iter=0, stage="cellGP", orient=None, placer_hpwl=None, placer_overflow=None, **extra):
        """x, y: sequences (DEF COMPONENTS order, lower-left DBU) or x = {name: (x, y)} and y = None.
        orient: None (input orientation), a sequence / {name: o} of 'N'.. strings or ORIENTS indices."""
        if isinstance(x, dict):
            x, y = np.array(self._by_name(x, "xy"), dtype=float).reshape(-1, 2).T
        arrs = {"x": np.rint(np.asarray(x, dtype=float)).astype(np.int32),
                "y": np.rint(np.asarray(y, dtype=float)).astype(np.int32)}
        if len(arrs["x"]) != len(self.names) or len(arrs["y"]) != len(self.names):
            raise ValueError("frame: %d/%d positions, design has %d components" % (
                len(arrs["x"]), len(arrs["y"]), len(self.names)))
        if orient is not None:
            o = self._by_name(orient, "orient") if isinstance(orient, dict) else list(orient)
            arrs["orient"] = np.array([ORIENTS.index(v) if isinstance(v, str) else int(v) for v in o], dtype=np.int8)
        rel = "frames/frame_%05d.npz" % len(self.meta["frames"])
        np.savez_compressed(os.path.join(self.dir, rel), **arrs)
        f = lambda v: None if v is None else float(v)  # noqa: E731
        self.meta["frames"].append(dict(file=rel, iter=int(iter), stage=stage, placer_hpwl=f(placer_hpwl),
                                        placer_overflow=f(placer_overflow), **extra))
        tmp = os.path.join(self.dir, "meta.json.part")
        with open(tmp, "w") as fp:
            json.dump(self.meta, fp, indent=1)
        os.replace(tmp, os.path.join(self.dir, "meta.json"))
        return rel


def def_frame(def_path, ref, prev=None, die=None):
    """Component positions of a DEF in the order of ref (input DEF components). Components missing or UNPLACED in
    the dump take the previous frame's position, else the input DEF's, else the die centre.
    -> (x, y, orient index array, #filled)."""
    got = read_components(def_path)
    xs, ys, os_, filled = [], [], [], 0
    for k, (n, r) in enumerate(ref.items()):
        c = got.get(n)
        if c is not None and c.x is not None:
            xs.append(c.x), ys.append(c.y), os_.append(ORIENTS.index(c.orient))
            continue
        filled += 1
        if prev is not None:
            xs.append(prev[0][k]), ys.append(prev[1][k]), os_.append(prev[2][k])
        elif r.x is not None:
            xs.append(r.x), ys.append(r.y), os_.append(ORIENTS.index(r.orient))
        else:
            xs.append((die[0] + die[2]) / 2), ys.append((die[1] + die[3]) / 2), os_.append(0)
    return xs, ys, os_, filled


def convert_defs(dump_dir, input_def, out_dir, default_stage="cellGP"):
    """DEF dumps `<iter>[_<stage>].def` in dump_dir (sorted by iter) -> out_dir/frames + out_dir/meta.json.
    Returns the number of frames."""
    dumps = sorted((int(m.group(1)), m.group(2) or default_stage, f) for f in os.listdir(dump_dir)
                   for m in [DUMP_RE.match(f)] if m)
    ref = read_components(input_def)
    die = Def(input_def, nets=False).die
    w = Writer(out_dir, names=list(ref))
    prev = None
    for it, stage, f in dumps:
        x, y, o, filled = def_frame(os.path.join(dump_dir, f), ref, prev, die)
        w.frame(x, y, iter=it, stage=stage, orient=o, **({"filled": filled} if filled else {}))
        prev = (x, y, o)
    return len(dumps)


def frame_matches_def(frame_path, def_path, names):
    """Number of components whose frame position (and orient, if stored) differs from the DEF."""
    with np.load(frame_path) as f:  # NpzFile re-reads an array on every f[key]: load each once
        x, y, o = f["x"].tolist(), f["y"].tolist(), f["orient"].tolist() if "orient" in f.files else None
    got = read_components(def_path)
    bad = 0
    for k, n in enumerate(names):
        c = got.get(n)
        bad += (c is None or c.x is None or (c.x, c.y) != (x[k], y[k])
                or (o is not None and ORIENTS.index(c.orient) != o[k]))
    return bad


def finalize(run_dir, placer, bench, crashed=False):
    """Collect the snapshots of a placer run into run_dir/frames + meta.json (FORMAT.md):
    run_dir/snap/meta.json (Writer) or run_dir/snap/*.def dumps; without snapshots one frame = final.def.
    If the last frame differs from final.def, final.def is appended as a frame with stage 'final'."""
    snap = os.path.join(run_dir, "snap")
    final = os.path.join(run_dir, "final.def")
    has_final = os.path.exists(final)
    ref = read_components(bench.deff)
    names = list(ref)
    shutil.rmtree(os.path.join(run_dir, "frames"), ignore_errors=True)
    frames, source = [], "none"
    if os.path.exists(os.path.join(snap, "meta.json")):
        frames, source = json.load(open(os.path.join(snap, "meta.json")))["frames"], "writer"
        os.replace(os.path.join(snap, "frames"), os.path.join(run_dir, "frames"))
    elif os.path.isdir(snap) and any(DUMP_RE.match(f) for f in os.listdir(snap)):
        convert_defs(snap, bench.deff, run_dir, "mixedGP" if bench.mode == "mov" else "cellGP")
        frames, source = json.load(open(os.path.join(run_dir, "meta.json")))["frames"], "def dumps"
    for fr in frames:
        n = len(load_npz(os.path.join(run_dir, fr["file"]))["x"])
        if n != len(names):
            raise ValueError("%s: %d components, input DEF has %d" % (fr["file"], n, len(names)))
    check = None
    if has_final:
        check = frame_matches_def(os.path.join(run_dir, frames[-1]["file"]), final, names) if frames else -1
        if check:
            w = Writer(run_dir, names=names)
            w.meta["frames"] = frames
            x, y, o, filled = def_frame(final, ref, die=Def(bench.deff, nets=False).die)
            if filled:
                raise ValueError("final.def: %d components unplaced" % filled)
            w.frame(x, y, iter=frames[-1]["iter"] if frames else 0, stage="final", orient=o)
            frames = w.meta["frames"]
    meta = dict(placer=placer, design=bench.name, mode=bench.mode, frames=frames,
                final_def="final.def" if has_final else None, log="run.log", crashed=crashed or not has_final,
                snapshots=source, input_def=bench.deff,
                final_check="ok" if check == 0 else ("final.def appended" if has_final else "no final.def"))
    with open(os.path.join(run_dir, "meta.json"), "w") as fp:
        json.dump(meta, fp, indent=1)
    if has_final and os.path.isdir(snap):
        shutil.rmtree(snap)  # DEF dumps are large; the frames hold the same positions
    return meta


def check(run_dir):
    """Problems of a run dir (FORMAT.md), [] if fine."""
    meta = json.load(open(os.path.join(run_dir, "meta.json")))
    names = list(read_components(meta["input_def"]))
    out = []
    for fr in meta["frames"]:
        p = os.path.join(run_dir, fr["file"])
        if not os.path.exists(p):
            out.append("missing " + fr["file"])
        elif len(load_npz(p)["x"]) != len(names):
            out.append("%s: wrong component count" % fr["file"])
    its = [fr["iter"] for fr in meta["frames"]]
    if its != sorted(its):
        out.append("iter not monotonic")
    if meta.get("final_def") and meta["frames"] and not out:
        bad = frame_matches_def(os.path.join(run_dir, meta["frames"][-1]["file"]),
                                os.path.join(run_dir, meta["final_def"]), names)
        if bad:
            out.append("last frame differs from final.def in %d components" % bad)
    return out
