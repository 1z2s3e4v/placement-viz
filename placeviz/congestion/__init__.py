"""Congestion backends. Each takes the evaluator's Design and the `congestion` config and has
`run(x, y, orient, has_orient, last) -> (metrics, maps)`; maps are 2-D arrays indexed [ix, iy]:
dmdH/V (demand incl. fixed usage), capH/V (track capacity), fixH/V (fixed usage: OBS, special nets), gx/gy (gcell
gridlines, DBU). Entry (ix, iy) of an H (V) map is the gcell edge (ix, iy)->(ix+1, iy) ((ix, iy)->(ix, iy+1)).
  rudy  built-in estimate (pure numpy, no router)        ggr  Xplace's GPU global router (needs an Xplace build)
"""
import numpy as np


def load(design, conf):
    if conf["backend"] == "ggr":
        from .ggr import Router
        return Router(design, conf)
    from .rudy import Rudy
    return Rudy(design, conf)


def raster(gx, gy, lx, ly, hx, hy, wt=None):
    """sum_r wt_r * area(rect_r & cell) on the grid with gridlines gx, gy (exact; rects are clipped to the grid).
    Rects spanning <= 3 x 3 cells: 9 vectorized passes; larger ones: outer product of the per-axis overlaps.
    (No BLAS: a matmul formulation was 50x slower on a loaded machine through thread oversubscription.)"""
    nx, ny = len(gx) - 1, len(gy) - 1
    wt = np.ones(len(lx)) if wt is None else np.asarray(wt, dtype=float)
    lx, hx = np.clip(lx, gx[0], gx[-1]), np.clip(hx, gx[0], gx[-1])
    ly, hy = np.clip(ly, gy[0], gy[-1]), np.clip(hy, gy[0], gy[-1])
    ix0 = np.clip(np.searchsorted(gx, lx, "right") - 1, 0, nx - 1)
    iy0 = np.clip(np.searchsorted(gy, ly, "right") - 1, 0, ny - 1)
    ix1, iy1 = np.searchsorted(gx, hx, "left"), np.searchsorted(gy, hy, "left")  # exclusive
    g = np.zeros((nx, ny))
    small = (ix1 - ix0 <= 3) & (iy1 - iy0 <= 3)
    for dx in range(3):
        for dy in range(3):
            bx, by = np.minimum(ix0 + dx, nx - 1), np.minimum(iy0 + dy, ny - 1)
            ox = np.minimum(hx, gx[bx + 1]) - np.maximum(lx, gx[bx])
            oy = np.minimum(hy, gy[by + 1]) - np.maximum(ly, gy[by])
            m = small & (ox > 0) & (oy > 0) & (ix0 + dx < nx) & (iy0 + dy < ny)
            np.add.at(g, (bx[m], by[m]), ox[m] * oy[m] * wt[m])
    for i in np.nonzero(~small)[0]:  # ponytail: python loop over large rects (macros, long nets)
        bx, by = np.arange(ix0[i], ix1[i]), np.arange(iy0[i], iy1[i])
        ox = np.clip(np.minimum(hx[i], gx[bx + 1]) - np.maximum(lx[i], gx[bx]), 0, None)
        oy = np.clip(np.minimum(hy[i], gy[by + 1]) - np.maximum(ly[i], gy[by]), 0, None)
        g[ix0[i]:ix1[i], iy0[i]:iy1[i]] += np.outer(ox * wt[i], oy)
    return g


def routable(mp, pct=5.0):
    """Metrics on routable resources only. fix = fixed usage (OBS, PG) already inside the demand.
    a = max(cap - fix, 0) remaining tracks, w = max(dmd - fix, 0) demand created by routed nets.
    r_overflow = sum max(0, w - a); r_ovfl_edges = #edges with w > a; r_t5 = mean of the top pct% of 100*w/a over
    edges with a > 0; rcong map = max over H/V of w/a per gcell, NaN where both directions are fully blocked."""
    out, ratios, rc = {}, [], []
    for d in "HV":
        a = np.maximum(mp["cap" + d] - mp["fix" + d], 0)
        w = np.maximum(mp["dmd" + d] - mp["fix" + d], 0)
        ov = np.maximum(w - a, 0)
        out["ov" + d] = ov
        ratios.append(100 * w[a > 0] / a[a > 0])
        with np.errstate(divide="ignore", invalid="ignore"):
            rc.append(np.where(a > 0, w / a, np.nan))
    v = np.concatenate(ratios)
    k = max(int(len(v) * pct / 100.0), 1)
    rcong = np.fmax(rc[0], rc[1])
    return dict(r_overflow=float(out["ovH"].sum() + out["ovV"].sum()),
                r_ovfl_edges=int((out["ovH"] > 0).sum() + (out["ovV"] > 0).sum()),
                r_t5=float(np.sort(v)[::-1][:k].mean()), r_cong_max=float(np.nanmax(rcong))), rcong


def top5(dmdH, capH, dmdV, capV, pct=5.0):
    """NTUplace4dr 'Top 5 Overflow' = CCongMap::GetTopCongestion(5): mean of the top floor(5% * #cuts) values of
    edge congestion 100*(blockage+demand)/capacity_orig over all H and V cuts. Here: 2-D H/V edges with
    capacity > 0, demand already includes blockage (fixed usage)."""
    v = np.concatenate([100 * dmdH[capH > 0] / capH[capH > 0], 100 * dmdV[capV > 0] / capV[capV > 0]])
    k = int(len(v) * pct / 100.0)
    return float(np.sort(v)[::-1][:k].mean())
