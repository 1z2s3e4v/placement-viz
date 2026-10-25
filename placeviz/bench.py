"""Benchmarks x modes. fix = the config DEF as is; mov = the same DEF with every FIXED macro made PLACED
(I/O pins untouched), written once to <output>/bench/<name>/<name>_mov.def."""
import os
from dataclasses import dataclass

from . import lefdef


@dataclass(frozen=True)
class Bench:
    name: str
    mode: str
    lef: tuple      # LEF paths, tech first
    deff: str       # input DEF of this mode
    fix_def: str    # the config DEF (original FIXED macros + macro orientations, used by restore_orient)

    @property
    def key(self):
        return "%s_%s" % (self.name, self.mode)


def mov_def(b, out):
    path = os.path.join(out, "bench", b["name"], b["name"] + "_mov.def")
    if not os.path.exists(path) or os.path.getmtime(path) < os.path.getmtime(b["def"]):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        n = lefdef.make_movable(b["def"], path + ".part", b["lef"])
        os.replace(path + ".part", path)
        if n == 0:
            print("[bench] %s: no FIXED macro, mov mode equals fix" % b["name"])
    return path


def load(cfg):
    """-> [Bench] in config order (benchmark-major, then mode)."""
    out = []
    for b in cfg["benchmarks"]:
        for m in cfg["modes"]:
            d = mov_def(b, cfg["output"]) if m == "mov" else b["def"]
            out.append(Bench(b["name"], m, tuple(b["lef"]), d, b["def"]))
    return out
