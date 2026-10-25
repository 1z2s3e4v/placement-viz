"""Self-tests of the evaluator and the rudy backend on the synthetic LEF/DEF of test_core (no GPU).

run: python tests/test_eval.py      (tests this source tree; no install needed)
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_core import HPWL, OUT, setup  # noqa: E402
from placeviz import congestion, evaluate, snapshot  # noqa: E402
from placeviz.bench import Bench  # noqa: E402


def brute(gx, gy, lx, ly, hx, hy, wt):
    ox = np.clip(np.minimum(hx[:, None], gx[1:]) - np.maximum(lx[:, None], gx[:-1]), 0, None)
    oy = np.clip(np.minimum(hy[:, None], gy[1:]) - np.maximum(ly[:, None], gy[:-1]), 0, None)
    return (ox * wt[:, None]).T @ oy


def test_raster_top5():
    rng = np.random.default_rng(1)
    gx, gy = np.linspace(0, 100, 11), np.array([0, 5, 30, 31, 100.0])  # non-uniform rows
    a, b = rng.uniform(-10, 110, (2, 400, 2))
    lx, hx = np.minimum(a[:, 0], b[:, 0]), np.maximum(a[:, 0], b[:, 0])
    ly, hy = np.minimum(a[:, 1], b[:, 1]), np.maximum(a[:, 1], b[:, 1])
    lx[:100], hx[:100] = lx[:100] / 10 + 40, lx[:100] / 10 + 45  # small rects (3x3 path) too
    w = rng.uniform(0, 2, 400)
    assert np.allclose(congestion.raster(gx, gy, lx, ly, hx, hy, w), brute(gx, gy, lx, ly, hx, hy, w))
    # Top 5: capacity-0 edge ignored -> 39 edges, k = int(39 * 0.05) = 1 -> max edge congestion 100*19/10
    dh, ch = np.arange(20, dtype=float).reshape(4, 5), np.full((4, 5), 10.0)
    dv, cv = np.zeros((4, 5)), np.full((4, 5), 10.0)
    cv[0, 0], dv[0, 0] = 0, 99
    assert np.isclose(congestion.top5(dh, ch, dv, cv), 190.0)
    # routable: fix removed from both sides; fully blocked cell -> NaN, r_t5 over a > 0 only
    mp = dict(dmdH=np.array([[3.0, 9.0]]), capH=np.array([[4.0, 5.0]]), fixH=np.array([[2.0, 5.0]]),
              dmdV=np.zeros((1, 2)), capV=np.zeros((1, 2)), fixV=np.zeros((1, 2)))
    r, rc = congestion.routable(mp)
    assert r["r_overflow"] == 4 and r["r_ovfl_edges"] == 1 and r["r_t5"] == 50 and np.isnan(rc[0, 1])


def test_design_rudy():
    lef_p, def_p = setup()
    b = Bench("t", "fix", (lef_p,), def_p, def_p)
    d = evaluate.Design(b)
    x, y = d.fx.copy(), d.fy.copy()
    x[2], y[2] = 3000, 0  # u3 (unplaced in the DEF)
    o = d.eval_orient(x, y, d.orient0, False, "DP")
    assert o.tolist() == [0, 5, 0, 0]  # u2 from its row (FS), macro b1 keeps N
    assert d.hpwl(x, y, o) == HPWL
    assert d.bins == (8000, 3, 2) and d.overlap(x, y, o) == 0  # 4 x 4 rows bins over the core, no overflow
    d.bgx, d.bgy, d._a_fix = np.array([3000.0, 3800]), np.array([0.0, 2000]), None  # one bin = u3; u1 on top: 1 cell area over
    x[0] = 3000
    assert np.isclose(d.overlap(x, y, o), 1 / 3)
    x[0] = 1000
    cg = congestion.load(d, {"backend": "rudy", "rows_per_gcell": 2})  # 5 x 3 gcells of 4000 x 4000
    assert cg.desc == "rudy 5x3" and [lay[0] for lay in cg.layers] == ["M2"]  # M1 excluded, M2 vertical
    _, mp = cg.run(x, y, o, False, True)
    assert np.allclose(mp["capV"], 20) and np.all(mp["capH"] == 0)  # TRACKS X step 200: 20 per 4000-wide column
    # b1 OBS on M2 (1 x 1 um at its lower-left) blocks 1000*1000 / (4000 * 200) tracks of gcell (2, 1)
    exp = np.zeros((5, 3))
    exp[2, 1] = 1000 * 1000 / (4000 * 200)
    assert np.allclose(mp["fixV"], exp)
    X, Y = d.pins(x, y, o)
    s = d.net_start
    w = np.maximum(np.maximum.reduceat(X, s) - np.minimum.reduceat(X, s), cg.pmin)
    h = np.maximum(np.maximum.reduceat(Y, s) - np.minimum.reduceat(Y, s), cg.pmin)
    assert np.isclose((mp["dmdV"] - mp["fixV"]).sum(), (h / 4000).sum())  # RUDY: a net adds h / gcell_h V wires
    assert np.isclose((mp["dmdH"] - mp["fixH"]).sum(), (w / 4000).sum())


def test_run_resume():
    lef_p, def_p = setup()
    b = Bench("t", "fix", (lef_p,), def_p, def_p)
    run = os.path.join(OUT, "evalrun")
    w = snapshot.Writer(run, components_from_def=def_p)
    for k in range(5):
        w.frame({"u1": (1000, 0), "u2": (5000, 2000), "u3": (3000 + 100 * k, 0)}, iter=k, stage="cellGP")
    w.frame({"u1": (1000, 0), "u2": (5000, 2000), "u3": (3000, 0)}, iter=9, stage="DP")
    json.dump(dict(w.meta, placer="p", design="t", mode="fix"), open(os.path.join(run, "meta.json"), "w"))
    cfg = dict(congestion={"backend": "rudy"}, max_frames=4, threads=1)
    assert evaluate.run(run, b, cfg).startswith("4 frames (4 new), rudy")  # first, last, 2 evenly spaced GP
    m = json.load(open(os.path.join(run, "eval", "metrics.json")))
    assert [e["iter"] for e in m] == [0, 1, 4, 9] and m[-1]["hpwl"] == HPWL and m[-1]["congestion"] == "rudy"
    f = np.load(os.path.join(run, "eval", "maps", "frame_00005.npz"))
    assert {"cong", "rcong", "gx", "gy", "dmdH", "capV", "fixV", "ovflH"} <= set(f.files) and f["gx"].dtype == np.int32
    assert evaluate.run(run, b, cfg).startswith("skip (done")
    assert evaluate.run(run, b, dict(cfg, congestion=None)).startswith("4 frames (4 new), no congestion")
    assert evaluate.run(run, b, cfg, force=True).startswith("4 frames (4 new)")
    try:  # ggr runs in a child process (retried once); a missing Xplace fails there and is raised here
        evaluate.run(run, b, dict(cfg, congestion={"backend": "ggr", "xplace": os.path.join(OUT, "no_xplace")}))
        raise AssertionError("ggr without Xplace accepted")
    except RuntimeError:
        assert open(os.path.join(run, "eval", "eval.log")).read().count("ValueError: congestion ggr: set 'xplace'") == 2


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
