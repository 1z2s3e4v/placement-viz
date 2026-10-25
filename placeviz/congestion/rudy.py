"""RUDY backend (default): a pure-numpy congestion ESTIMATE, not a router (config: `congestion: rudy` or
`{backend: rudy, rows_per_gcell: 4, max_gcells: 256}`).

- gcells: uniform grid over the die, ~rows_per_gcell rows per gcell, at most max_gcells per side.
- capacity per routing layer (the lowest one excluded, like the ggr maps): tracks of the layer's preferred direction
  from DEF TRACKS (H layers: TRACKS Y) counted per gcell row/column, else gcell size / LEF PITCH.
- fixed usage: LEF OBS of every component at its frame position and orientation, plus SPECIALNETS shapes, as
  blocked tracks = area(shape & gcell) / (gcell length * pitch), per layer, capped at the layer capacity.
- demand: RUDY of each net's pin bbox (nets with >= 2 pins), bbox at least one pitch wide/tall: a net adds
  area(bbox & gcell) / (h * gcell_w) horizontal and area(bbox & gcell) / (w * gcell_h) vertical wires
  (wire density (w + h) / (w h) split into H = 1/h and V = 1/w), plus the fixed usage, as GGR's demand does.
H maps sum the H layers, V maps the V layers; the metrics (r_overflow, r_ovfl_edges, r_t5, r_cong_max) are the same
functions as for ggr, so values are comparable in trend but not equal (no detours, vias or pin access).
"""
from collections import defaultdict

import numpy as np

from . import raster
from ..lefdef import orient_np


class Rudy:
    def __init__(self, d, conf):
        self.d = d
        lx, ly, hx, hy = d.die
        per, cap = float(conf.get("rows_per_gcell", 4)), int(conf.get("max_gcells", 256))
        nx, ny = (int(min(cap, max(1, round(s / (per * d.row_h))))) for s in (hx - lx, hy - ly))
        self.gx, self.gy = np.linspace(lx, hx, nx + 1), np.linspace(ly, hy, ny + 1)
        self.desc = "rudy %dx%d" % (nx, ny)
        by_mac = defaultdict(list)
        for i, m in enumerate(d.macros):
            by_mac[m].append(i)
        self.layers = []  # (name, horizontal, pitch DBU, capacity map, OBS (comp idx, rects um) or None)
        for name in d.lef.metal[1:]:
            L = d.lef.layers[name]
            hor = (L["direction"] or "").upper().startswith("H")
            tr = [t for t in d.def_.tracks if name in t.layers and t.dir == ("Y" if hor else "X")]
            if tr:
                pos = np.concatenate([t.start + t.step * np.arange(t.num) for t in tr])
                c1 = np.histogram(pos, self.gy if hor else self.gx)[0].astype(float)
                pitch = min(t.step for t in tr)
            elif L["pitch"]:
                pitch = L["pitch"] * d.dbu
                c1 = np.diff(self.gy if hor else self.gx) / pitch
            else:
                continue  # no track information: layer not counted
            capm = np.broadcast_to(c1[None, :] if hor else c1[:, None], (nx, ny))
            ci, rr = [], []
            for m, idx in by_mac.items():
                rs = d.lef.macros[m]["obs"].get(name)
                if rs:
                    ci.append(np.repeat(idx, len(rs)))
                    rr.append(np.tile(np.array(rs, dtype=float), (len(idx), 1)))
            obs = (np.concatenate(ci), np.concatenate(rr)) if ci else None
            self.layers.append((name, hor, pitch, capm, obs))
        if not self.layers:
            raise ValueError("rudy: no routing layer with TRACKS or PITCH above the lowest one")
        self.pmin = min(lay[2] for lay in self.layers)
        sp = defaultdict(list)
        for s in d.def_.special:
            sp[s[1]].append(s[2:])
        self.fix_static = {}  # layer -> blocked area of special nets + fixed components (they never move)
        for name, hor, pitch, capm, obs in self.layers:
            a = np.zeros((nx, ny))
            if sp[name]:
                r = np.array(sp[name], dtype=float)
                a += raster(self.gx, self.gy, r[:, 0], r[:, 1], r[:, 2], r[:, 3])
            self.fix_static[name] = a
        self.static_done = False

    def _obs_area(self, obs, x, y, orient, sel):
        """Blocked area of the OBS rects of the components selected by sel (bool per component)."""
        ci, r = obs
        k = sel[ci]
        ci, r = ci[k], r[k]
        if not len(ci):
            return 0.0
        d = self.d
        w, h, o = d.w[ci] / d.dbu, d.h[ci] / d.dbu, orient[ci]
        ax, ay = orient_np(r[:, 0], r[:, 1], w, h, o)
        bx, by = orient_np(r[:, 2], r[:, 3], w, h, o)
        X, Y = x[ci].astype(float), y[ci].astype(float)
        return raster(self.gx, self.gy, X + np.minimum(ax, bx) * d.dbu, Y + np.minimum(ay, by) * d.dbu,
                      X + np.maximum(ax, bx) * d.dbu, Y + np.maximum(ay, by) * d.dbu)

    def run(self, x, y, orient, has_o, last):
        d = self.d
        gw, gh = np.diff(self.gx)[:, None], np.diff(self.gy)[None, :]
        mp = {k: np.zeros((len(gw), gh.size)) for k in ("capH", "capV", "fixH", "fixV")}
        for name, hor, pitch, capm, obs in self.layers:
            if obs is not None and not self.static_done:
                self.fix_static[name] = self.fix_static[name] + self._obs_area(obs, x, y, orient, d.fixed)
            a = self.fix_static[name] + (self._obs_area(obs, x, y, orient, ~d.fixed) if obs is not None else 0.0)
            D = "H" if hor else "V"
            mp["cap" + D] = mp["cap" + D] + capm
            mp["fix" + D] = mp["fix" + D] + np.minimum(a / ((gw if hor else gh) * pitch), capm)
        self.static_done = True
        X, Y = d.pins(x, y, orient)
        s = d.net_start
        x0, x1 = np.minimum.reduceat(X, s), np.maximum.reduceat(X, s)
        y0, y1 = np.minimum.reduceat(Y, s), np.maximum.reduceat(Y, s)
        w, h = np.maximum(x1 - x0, self.pmin), np.maximum(y1 - y0, self.pmin)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
        mp["dmdH"] = raster(self.gx, self.gy, *box, wt=1 / h) / gw + mp["fixH"]
        mp["dmdV"] = raster(self.gx, self.gy, *box, wt=1 / w) / gh + mp["fixV"]
        mp["gx"], mp["gy"] = np.rint(self.gx), np.rint(self.gy)
        return {}, mp
