"""GGR backend: Xplace-Route's GPU global router on every evaluated frame (config:
`congestion: {backend: ggr, xplace: <Xplace dir>, gpu: 0, rrr: 0, rrr_final: 1}`).

Needs an Xplace checkout built with its gpugr extension; its modules are imported at runtime from `xplace` only.
The design is loaded once with Xplace's IOParser (lite mode, the bench's LEF/DEF, so mov-mode macros are movable);
each frame is applied with gpdb.apply_node_lpos (movable nodes only; orientation re-derived from the row as Xplace
does), then pattern routing + `rrr` maze RRR iterations (`rrr_final` on the last frame, 1 = Xplace
--final_route_eval; the last frame is also pattern-routed alone for the pat_* metrics). Route grid as Xplace:
min(512, num_bin_y), num_bin_y = 2^floor(log2(#rows)) if 512 > #rows, die-ratio scaled.
2-D maps: H = sum of the H layers, V = sum of the V layers, M1 excluded (as Xplace's cg_mapHV).
"""
import math
import os
import sys

import numpy as np

from . import top5


def _hv_ids(m1direction):
    """Lowest H / V layer index used for the 2-D maps (M1 skipped), as Xplace's route_force.run_gr_and_fft."""
    h, v = (1, 0) if m1direction else (0, 1)
    return (h + 2 if h == 0 else h), (v + 2 if v == 0 else v)


def _fixed_map(torch, fixed, L, N, X, Y, m1dir):
    """GGR's flat fixed usage (L*N*N, p = l*N*N + a*N + b, edge b -> b+1 along the layer's direction) -> (L, X, Y)
    maps, (a, b) swapped to (b, a) when ((l & 1) == 0) ^ m1direction (= getDmdTensor with no wires and vias)."""
    i = torch.arange(L * N * N)
    i = i[i % N + 1 < N]
    lay, a, b = i // (N * N), i // N % N, i % N
    swap = ((lay & 1) == 0) ^ bool(m1dir)
    x, y = torch.where(swap, b, a), torch.where(swap, a, b)
    k = (x < X) & (y < Y)
    m = torch.zeros(L, X, Y, dtype=torch.float32)
    m[lay[k], x[k], y[k]] = fixed.double()[i][k].float()
    return m


class Router:
    def __init__(self, d, conf):
        xp = os.path.abspath(os.path.expanduser(str(conf.get("xplace") or "")))
        if not conf.get("xplace") or not os.path.isdir(xp):
            raise ValueError("congestion ggr: set 'xplace' to an Xplace checkout with gpugr built (got %r)" % xp)
        import torch
        self.torch = torch
        if xp not in sys.path:
            sys.path.insert(0, xp)
        self.gpu, self.rrr = int(conf.get("gpu", 0)), int(conf.get("rrr", 0))
        self.rrr_final = int(conf.get("rrr_final", 1))
        threads = int(conf.get("threads", 8))
        lef = list(d.bench.lef)
        params = dict(benchmark="placeviz", design_name=d.bench.name, **{"def": d.bench.deff},
                      **({"lef": lef[0]} if len(lef) == 1 else {"lefs": lef}))
        cwd = os.getcwd()
        os.chdir(xp)  # Flute reads its tables relative to the Xplace dir
        try:
            from utils import IOParser
            from src import Flute
            Flute.register(threads)
            self.rawdb, self.gpdb = IOParser().read(params, verbose_log=False, log_level=2, lite_mode=True,
                                                    random_place=False, num_threads=threads)
        finally:
            os.chdir(cwd)
        # component index -> gpdb node id (gpdb puts cells first, node_names covers cells only)
        nn = self.gpdb.node_names()
        types = {t: (a, b) for a, b, t in self.gpdb.node_type_indices()}
        a, b = types["Mov"][0], types["FloatMov"][1]
        self.mov_nodes = np.arange(a, b)
        self.mov_comp = np.array([d.idx[nn[i]] for i in self.mov_nodes])
        assert not d.fixed[self.mov_comp].any(), "gpdb Mov node is FIXED in DEF"
        assert len(self.mov_comp) == (~d.fixed).sum(), "movable count mismatch gpdb vs DEF"
        self.lpos0 = self.gpdb.node_lpos_tensor().float().clone()
        lx, hx, ly, hy = self.gpdb.dieInfo()
        nby = 512
        if hy / d.row_h < nby:
            nby = int(2 ** math.floor(math.log2(math.floor(hy / d.row_h))))
        rs = min(512, nby)
        ratio = (hx - lx) / (hy - ly)
        self.xs = rs if ratio <= 1 else round(rs * ratio)
        self.ys = rs if ratio >= 1 else round(rs / ratio)
        self.desc = "ggr %dx%d" % (self.xs, self.ys)

    def route(self, x, y, orient, rrr_iters):
        torch = self.torch
        from cpp_to_py import gpugr
        from src.core.route_force import calc_gr_wl_via, estimate_num_shorts
        lpos = self.lpos0.clone()
        lpos[self.mov_nodes, 0] = torch.from_numpy(x[self.mov_comp].astype(np.float32))
        lpos[self.mov_nodes, 1] = torch.from_numpy(y[self.mov_comp].astype(np.float32))
        self.gpdb.apply_node_lpos(lpos.contiguous())
        mism = None
        if orient is not None:  # GGR uses the row-derived orientation of apply_node_lpos; count disagreements
            nodes = self.gpdb.nodes()
            xo = np.array([nodes[i].orient() for i in self.mov_nodes])  # Xplace codes N W S E FN FW FS FE
            mism = int((np.array([0, 2, 1, 3, 4, 6, 5, 7])[orient[self.mov_comp]] != xo).sum())
        gpugr.load_gr_params({"device_id": self.gpu, "route_xSize": self.xs, "route_ySize": self.ys,
                              "rrrIters": rrr_iters})
        grdb = gpugr.create_grdatabase(self.rawdb, self.gpdb)
        rf = gpugr.create_routeforce(grdb)
        rf.run_ggr()
        dmd, wdmd, vdmd = rf.dmd_map()
        cap = rf.cap_map()
        gi = dict(grdb.grid_info())
        h, v = _hv_ids(gi["m1direction"])
        fmap = _fixed_map(torch, grdb.fixed_usage().cpu(), gi["nLayers"], gi["nMaxGrid"], gi["xSize"], gi["ySize"],
                          gi["m1direction"])
        mp = {"dmdH": dmd[h::2].sum(0), "capH": cap[h::2].sum(0), "dmdV": dmd[v::2].sum(0), "capV": cap[v::2].sum(0)}
        mp = {k: t.cpu().numpy() for k, t in mp.items()}
        mp["fixH"], mp["fixV"] = fmap[h::2].sum(0).numpy(), fmap[v::2].sum(0).numpy()
        mp["gx"], mp["gy"] = (np.array(gi["gridlines"][k], dtype=np.int32) for k in (0, 1))
        wl, nv = calc_gr_wl_via(grdb, rf)
        met = dict(rrr_iters=rrr_iters, gr_overflow_3d=(dmd[1:] - cap[1:]).clamp(min=0).sum().item(),  # M1 skipped
                   gr_ovfl_nets=int(rf.num_ovfl_nets()), gr_wl=float(wl), gr_vias=int(nv),
                   gr_est_shorts=float(estimate_num_shorts(rf, self.gpdb, cap, wdmd, vdmd)), gr_orient_mismatch=mism)
        del dmd, wdmd, vdmd, cap, rf, grdb
        torch.cuda.empty_cache()
        return met, mp

    def run(self, x, y, orient, has_o, last):
        from . import routable
        met, mp = self.route(x, y, orient if has_o else None, self.rrr_final if last else self.rrr)
        ov = {d: np.maximum(mp["dmd" + d] - mp["cap" + d], 0) for d in "HV"}
        with np.errstate(divide="ignore", invalid="ignore"):
            cong = np.fmax(np.where(mp["capH"] > 0, mp["dmdH"] / mp["capH"], 0),
                           np.where(mp["capV"] > 0, mp["dmdV"] / mp["capV"], 0))
        met.update(gr_overflow=float(ov["H"].sum() + ov["V"].sum()),
                   gr_ovfl_edges=int((ov["H"] > 0).sum() + (ov["V"] > 0).sum()),
                   gr_t5=top5(mp["dmdH"], mp["capH"], mp["dmdV"], mp["capV"]), gr_cong_max=float(cong.max()))
        if last and self.rrr_final != 0:  # pattern routing only, so curves can compare like with like
            _, pm = self.route(x, y, None, 0)
            met.update({"pat_" + k: v for k, v in routable(pm)[0].items()})
        return met, mp
