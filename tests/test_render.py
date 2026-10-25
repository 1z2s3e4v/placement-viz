"""Self-test of render (player sprites, final-tab tiles) and the page on the synthetic design of test_core.

run: python tests/test_render.py      (tests this source tree; no install needed)   Output: $PLACEVIZ_TEST_OUT (default tests/_out), wiped.
Eval output is faked (metrics.json + maps) so the test does not depend on the eval stage.
"""
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_core as tc  # noqa: E402
from placeviz import bench, cli, config, place, route  # noqa: E402
from placeviz.render import final, player  # noqa: E402
from placeviz.web import page  # noqa: E402

DR_DEF = """NETS 1 ;
- n1 ( u1 Z ) ( u2 A ) + ROUTED M1 ( 1600 1000 ) ( 5300 * ) V12
  NEW M2 ( 5300 1000 ) ( 5300 3000 ) ;
END NETS
"""


def fake_eval(rd, with_map):
    meta = json.load(open(os.path.join(rd, "meta.json")))
    os.makedirs(os.path.join(rd, "eval", "maps"), exist_ok=True)
    m = [dict(file=f["file"], hpwl=1e4 * (3 - k), overlap=0.5 / (k + 1), r_overflow=10.0 - k, r_t5=50.0 - k)
         for k, f in enumerate(meta["frames"])]
    json.dump(m, open(os.path.join(rd, "eval", "metrics.json"), "w"))
    for f in meta["frames"][:1 if not with_map else None]:
        rc = np.full((10, 6), 0.7, dtype=np.float16)
        rc[0, 0] = np.nan  # blocked gcell
        np.savez(os.path.join(rd, "eval", "maps", os.path.basename(f["file"])), rcong=rc,
                 gx=np.linspace(0, 20000, 11).astype(np.int32), gy=np.linspace(0, 12000, 7).astype(np.int32))


def test_render_page():
    pl = "\n".join(tc._placer(n, n) for n in ("writer", "fail")).replace(
        "fake_placer.py.REL", os.path.join(tc.OUT, "fake_placer.py"))
    cfg_p = tc._cfg(pl)
    cli.main(["run", cfg_p, "--stages", "place,gr,dr"])
    cfg = config.load(cfg_p)
    benches = bench.load(cfg)
    for b in benches:
        fake_eval(place.run_dir(cfg, "writer", b), with_map=b.mode == "fix")
        open(os.path.join(route.route_dir(cfg, "writer", b), "dr.def"), "w").write(DR_DEF)
    units = [(p, b) for p in ("writer", "fail") for b in benches]
    lim = player.limits(cfg, units)
    assert lim["t"]["hpwl"] == (0, 3e4 * 1.05) and lim["t"]["overlap"] == (0, 1.0)
    b_fix, b_mov = benches
    assert player.build_run(cfg, "writer", b_fix, lim) == "2 frames"
    assert player.build_run(cfg, "writer", b_fix, lim) == "skip (done)"
    assert player.build_run(cfg, "writer", b_mov, lim) == "2 frames"  # 2nd frame has no congestion map
    fj = json.load(open(os.path.join(cfg["output"], "web", "data", "writer", "t_fix", "frames.json")))
    assert (fj["n"], fj["drv"], fj["drv_status"], fj["rows"]["overlay"]) == (2, 2, "ok", 1)
    assert [f["iter"] for f in fj["frames"]] == [0, 7] and fj["frames"][1]["t5"] == 49.0

    s = final.build_run(cfg, "writer", b_fix)
    assert "checks PASS" in s and "dr 3" in s and "drv 2" in s, s
    assert final.build_run(cfg, "writer", b_fix) == "skip (done)"
    od = os.path.join(cfg["output"], "web", "final", "writer", "t_fix")
    meta = json.load(open(os.path.join(od, "meta.json")))
    assert meta["metal"] == ["M1", "M2"] and meta["status"] == dict(placement="ok", gr="ok", dr="ok", drv="ok")
    assert meta["counts"]["cells"] == 4 and meta["counts"]["macros_fixed"] == 1 and meta["counts"]["dr_vias"] == 1
    drv = np.concatenate([final.decode_tile(os.path.join(od, "drv", t + ".js")) for t in meta["layers"]["drv"]["tiles"]])
    assert sorted(set(drv[:, 4].tolist())) == [0, 65]  # minarea on M1, short on M2: type * 64 + metal
    assert final.build_run(cfg, "fail", b_fix) == "status only (placer exit 3)"
    assert final.write_index(cfg) == 2

    path = page.build(cfg, units)
    html = open(path, encoding="utf-8").read()
    assert "/*" + "CONF*/" not in html and "{{" not in html and "FINAL_TILE" in html
    conf = json.loads(re.search(r"const C=(\{.*?\});", html).group(1))
    assert conf["pl"] == [["writer", "writer"], ["fail", "fail"]] and conf["modes"] == ["fix", "mov"] and conf["drv"]
    js = open(os.path.join(cfg["output"], "web", "data", "all.js")).read()
    assert '"writer/t_fix"' in js and '"fail/t_mov": {"placement": "placer exit 3"' in js
    conf_of = lambda us: json.loads(re.search(r"const C=(\{.*?\});", open(page.build(  # noqa: E731
        dict(cfg, dr=None), us), encoding="utf-8").read()).group(1))
    assert conf_of(units)["drv"] and not conf_of(units[2:])["drv"]  # dr: no hides the column unless DR results exist
    page.build(cfg, units)
    assert page.design_labels(["mgc_fft_a", "mgc_fft_b"]) == {"mgc_fft_a": "fft_a", "mgc_fft_b": "fft_b"}
    assert page.design_labels(["mgc_fft_a"]) == {"mgc_fft_a": "mgc_fft_a"}
    assert page.design_labels(["ab_x", "ab"]) == {"ab_x": "ab_x", "ab": "ab"}
    assert page.design_labels(["fft_a", "fft_b"]) == {"fft_a": "fft_a", "fft_b": "fft_b"}
    assert page.design_labels(["mgc_", "mgc_a"]) == {"mgc_": "mgc_", "mgc_a": "mgc_a"}
    assert "placeviz: fixed macro vs mixed-size" in open(path, encoding="utf-8").read()
    # a violation on a layer the LEF does not have is dropped and reported, not drawn as M1
    v = os.path.join(tc.OUT, "vio_bad.csv")
    open(v, "w").write("type,layer,xl,yl,xh,yh\nshort,M2,0,0,1,1\nshort,M9,0,0,1,1\n")
    a, types, bad = final.parse_vio(v, ["M1", "M2"], ["V1"])
    assert len(a) == 1 and a[0, 4] == 1 and bad == 1
    # index.js holds only the config's units
    os.makedirs(os.path.join(cfg["output"], "web", "final", "gone", "t_fix"))
    open(os.path.join(cfg["output"], "web", "final", "gone", "t_fix", "meta.json"), "w").write("{}")
    assert final.write_index(cfg) == 2


if __name__ == "__main__":
    test_render_page()
    print("ok test_render_page")
