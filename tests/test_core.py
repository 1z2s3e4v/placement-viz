"""Fast self-tests of the core modules on a tiny synthetic LEF/DEF (no GPU, no external binaries).

run: python tests/test_core.py   (tests this source tree, which is put first on sys.path; no install needed)
Output goes to $PLACEVIZ_TEST_OUT (default tests/_out), which is wiped first.
"""
import json
import os
import re
import shutil
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from placeviz import bench, cli, config, lefdef, place, route, snapshot  # noqa: E402

OUT = os.path.abspath(os.environ.get("PLACEVIZ_TEST_OUT", os.path.join(REPO, "tests", "_out")))

LEF = """VERSION 5.8 ;
# a comment ; with a semicolon
UNITS
  DATABASE MICRONS 1000 ;
END UNITS
PROPERTYDEFINITIONS
  LIBRARY LEF58_X STRING "a ; b
 END c" ;
END PROPERTYDEFINITIONS
LAYER M1
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  PITCH 0.2 ;
  WIDTH 0.1 ;
  SPACINGTABLE
    PARALLELRUNLENGTH 0.00
    WIDTH 0.00 0.10 ;
  WIDTH 0.5 ;
END M1
LAYER V1
  TYPE CUT ;
END V1
LAYER M2
  TYPE ROUTING ;
  DIRECTION VERTICAL ;
  PITCH 0.2 0.2 ;
  OFFSET 0.1 ;
  WIDTH 0.1 ;
END M2
VIA V12 DEFAULT
  LAYER M1 ; RECT -0.1 -0.05 0.1 0.05 ;
  LAYER V1 ; RECT -0.05 -0.05 0.05 0.05 ;
  LAYER M2 ; RECT -0.05 -0.1 0.05 0.1 ;
END V12
VIARULE G12 GENERATE
  LAYER M1 ; ENCLOSURE 0 0 ;
END G12
SITE core
  CLASS CORE ; SIZE 0.2 BY 2.0 ;
END core
MACRO INV
  CLASS CORE ;
  SIZE 0.8 BY 2.0 ;
  PIN A
    DIRECTION INPUT ;
    PORT
      LAYER M1 ;
        RECT 0.1 0.5
             0.3 0.7 ;
        RECT 0.1 0.9 0.2 1.1 ;
    END
  END A
  PIN Z
    DIRECTION OUTPUT ;
    PORT
      LAYER M1 ;
        RECT 0.5 0.2 0.7 0.4 ;
      LAYER M2 ;
        RECT 0.6 0.2 0.7 1.8 ;
    END
  END Z
  OBS
    LAYER M1 ; RECT 0 0 0.8 0.1 ;
  END
END INV
MACRO BLK
  CLASS BLOCK ;
  SIZE 4 BY 6 ;
  PIN P DIRECTION INPUT ; PORT LAYER M2 ; RECT MASK 1 3.9 2.9 4.0 3.1 ; END END P
  OBS LAYER M1 ; RECT 0 0 4 6 ; LAYER M2 ; POLYGON 0 0 1 0 1 1 0 1 ; END
END BLK
END LIBRARY
"""

DEF = """VERSION 5.8 ;
DESIGN t ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 20000 12000 ) ;
""" + "".join("ROW r%d core 0 %d %s DO 100 BY 1 STEP 200 0 ;\n" % (k, 2000 * k, "N" if k % 2 == 0 else "FS")
              for k in range(6)) + """TRACKS Y 0 DO 60 STEP 200 LAYER M1 ;
TRACKS X 100 DO 100 STEP 200 LAYER M2 ;
COMPONENTS 4 ;
- u1 INV + PLACED ( 1000 0 ) N ;
- u2 INV
    + PLACED ( 5000.0 2000.0 )
    FS ;
- u3 INV + UNPLACED ;
- b1 BLK + FIXED ( 10000 4000 ) N ;
END COMPONENTS
PINS 1 ;
- in + NET n0 + DIRECTION INPUT + PLACED ( 0 6000 ) N + LAYER M2 ( -50 0 ) ( 50 100 ) ;
END PINS
SPECIALNETS 1 ;
- vdd + ROUTED M1 200 + SHAPE STRIPE ( 0 2000 ) ( 20000 * ) NEW M1 200 ( 0 4000 ) ( 20000 4000 ) + USE POWER ;
END SPECIALNETS
NETS 3 ;
- n0 ( PIN in ) ( u1 A ) ;
- n1 ( u1 Z ) ( u2 A )
  ( b1 P ) + USE SIGNAL ;
- n2 ( u2 Z ) ;
END NETS
END DESIGN
"""
HPWL = (1200 + 5250) + (12350 + 6000)  # n0 + n1, hand computed (n2 has one pin)

FAKE_PLACER = r'''import os, sys
sys.path.insert(0, os.environ["PV_REPO"])
from placeviz.snapshot import Writer
style, deff, out, snap = sys.argv[1:5]
text = open(deff).read()
final = text.replace("- u3 INV + UNPLACED", "- u3 INV + PLACED ( 3000 0 ) N")
if style == "fail":
    sys.exit(3)
if style == "writer":
    w = Writer(snap, components_from_def=deff)
    w.frame({"u1": (0, 0), "u2": (5000, 2000), "u3": (3000, 0)}, iter=0, stage="cellGP", placer_hpwl=1.0)
    w.frame([1000, 5000, 3000, 10000], [0, 2000, 0, 4000], iter=7, stage="DP", orient=["N", "FS", "N", "N"])
elif style == "dumps":
    open(os.path.join(snap, "000005.def"), "w").write(text)
    open(os.path.join(snap, "000010_LG.def"), "w").write(final)
open(out, "w").write(final)
'''


def setup():
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    for name, text in (("t.lef", LEF), ("t.def", DEF), ("fake_placer.py", FAKE_PLACER)):
        with open(os.path.join(OUT, name), "w") as f:
            f.write(text)
    tech, cells = LEF.split("MACRO INV", 1)
    open(os.path.join(OUT, "tech.lef"), "w").write(tech)
    open(os.path.join(OUT, "cells.lef"), "w").write("MACRO INV" + cells)
    return os.path.join(OUT, "t.lef"), os.path.join(OUT, "t.def")


def test_lef():
    lef_p, _ = setup()
    L = lefdef.Lef(lef_p)
    assert L.dbu == 1000 and L.metal == ["M1", "M2"] and L.cut == ["V1"]
    assert L.layers["M1"] == dict(type="ROUTING", direction="HORIZONTAL", pitch=0.2, width=0.1, offset=None)
    assert L.layers["M2"]["pitch"] == 0.2 and L.layers["M2"]["offset"] == 0.1
    assert L.vias == {"V12": ["M1", "V1", "M2"]}
    inv, blk = L.macros["INV"], L.macros["BLK"]
    assert inv["size"] == (0.8, 2.0) and inv["cls"] == "CORE" and blk["cls"] == "BLOCK"
    assert inv["pins"]["A"]["ports"] == {"M1": [[0.1, 0.5, 0.3, 0.7], [0.1, 0.9, 0.2, 1.1]]}
    assert inv["pins"]["Z"]["direction"] == "OUTPUT" and set(inv["pins"]["Z"]["ports"]) == {"M1", "M2"}
    assert inv["obs"] == {"M1": [[0, 0, 0.8, 0.1]]}
    assert blk["pins"]["P"]["ports"] == {"M2": [[3.9, 2.9, 4.0, 3.1]]}
    assert blk["obs"]["M2"] == [[0, 0, 1, 1]]
    c = L.pin_centres()
    assert np.allclose(c["INV"]["A"], (0.2, 0.8)) and np.allclose(c["INV"]["Z"], (0.6, 1.0))
    split = lefdef.Lef([os.path.join(OUT, "tech.lef"), os.path.join(OUT, "cells.lef")])
    assert split.pin_centres() == L.pin_centres() and split.metal == L.metal


def test_def():
    lef_p, def_p = setup()
    D = lefdef.Def(def_p)
    assert D.dbu == 1000 and D.die == [0, 0, 20000, 12000] and len(D.rows) == 6 and D.row_height == 2000
    assert D.rows[1] == lefdef.Row("r1", "core", 0, 2000, "FS", 100, 1, 200, 0)
    assert D.tracks[1] == lefdef.Track("X", 100, 100, 200, ["M2"]) and D.gcells == []
    assert list(D.comps) == ["u1", "u2", "u3", "b1"]
    assert D.comps["u2"] == lefdef.Comp("INV", "PLACED", 5000.0, 2000.0, "FS")
    assert D.comps["u3"] == lefdef.Comp("INV", "UNPLACED", None, None, "N")
    assert D.comps["b1"].status == "FIXED"
    assert D.pins["in"].rect == (-50, 6000, 50, 6100) and D.pins["in"].direction == "INPUT"
    assert D.nets[1] == ("n1", [("u1", "Z"), ("u2", "A"), ("b1", "P")])
    assert D.special == [("vdd", "M1", 0, 1900, 20000, 2100), ("vdd", "M1", 0, 3900, 20000, 4100)]
    assert lefdef.hpwl(lef_p, def_p) == HPWL
    assert lefdef.orient(0.2, 0.8, 0.8, 2.0, "FS") == (0.2, 1.2) and lefdef.orient(1, 2, 4, 6, "E") == (2, 3)
    assert lefdef.def_orient_rect(-50, 0, 50, 100, "S") == (-50, -100, 50, 0)


def test_rewrites():
    lef_p, def_p = setup()
    mov = os.path.join(OUT, "mov.def")
    assert lefdef.make_movable(def_p, mov, lef_p) == 1  # only b1 is taller than a row
    assert lefdef.Def(mov).comps["b1"].status == "PLACED"
    assert open(mov).read() == DEF.replace("b1 BLK + FIXED", "b1 BLK + PLACED")
    placed = os.path.join(OUT, "placed.def")
    open(placed, "w").write(DEF.replace("- b1 BLK + FIXED ( 10000 4000 ) N", "- b1 BLK + PLACED ( 12000.000 4000 ) FS"))
    ev = os.path.join(OUT, "eval.def")
    assert lefdef.restore_orient(def_p, placed, ev, lef_p) == 1
    assert "- b1 BLK + PLACED ( 12000.000 4000 ) N ;" in open(ev).read()
    # a macro already PLACED (movable) in the original DEF is restored too (same policy as evaluate.eval_orient)
    assert lefdef.restore_orient(mov, placed, os.path.join(OUT, "eval2.def"), lef_p) == 1
    # a single row: the row height falls back to the smallest component height
    one = os.path.join(OUT, "one_row.def")
    open(one, "w").write("".join(ln + "\n" for ln in DEF.split("\n") if not re.match(r"ROW r[1-9]", ln)))
    D1 = lefdef.Def(one)
    assert len(D1.rows) == 1 and D1.row_height is None and lefdef.row_height(lefdef.Lef(lef_p), D1) == 2000
    assert lefdef.macro_names(lefdef.Lef(lef_p), D1) == {"b1"}
    g = os.path.join(OUT, "in.def")
    lefdef.add_gcell(ev, g)
    # ratio 20000/12000 > 1: 512 rows of gcells, round(512 * 5/3) = 853 columns
    assert lefdef.Def(g).gcells == [lefdef.Grid("Y", 11753, 2, 247), lefdef.Grid("Y", 0, 512, 23),
                                    lefdef.Grid("X", 19596, 2, 404), lefdef.Grid("X", 0, 853, 23)]
    assert open(g).read().endswith("\nGCELLGRID X 0 DO 853 STEP 23 ;\nEND DESIGN\n")
    g2 = os.path.join(OUT, "in2.def")
    lefdef.add_gcell(g, g2)
    assert open(g2).read() == open(g).read()


def test_snapshot():
    _, def_p = setup()
    w = snapshot.Writer(os.path.join(OUT, "snapw"), components_from_def=def_p)
    w.frame({"u1": (1, 2), "u2": (3, 4.4), "u3": (5, 6)}, iter=3, stage="cellGP")  # b1 from the input DEF
    f = np.load(os.path.join(OUT, "snapw", "frames", "frame_00000.npz"))
    assert f["x"].tolist() == [1, 3, 5, 10000] and f["y"].tolist() == [2, 4, 6, 4000] and "orient" not in f.files
    try:
        w.frame({"u1": (1, 2)}, iter=4)  # u3 is unplaced in the input DEF: must be given
        raise AssertionError("missing component accepted")
    except KeyError:
        pass
    w.frame([0] * 4, [0] * 4, iter=5, stage="DP", orient=["N", "FS", 0, 4])
    meta = json.load(open(os.path.join(OUT, "snapw", "meta.json")))
    assert [(m["iter"], m["stage"]) for m in meta["frames"]] == [(3, "cellGP"), (5, "DP")]
    assert np.load(os.path.join(OUT, "snapw", "frames", "frame_00001.npz"))["orient"].tolist() == [0, 5, 0, 4]
    w.frame([0] * 4, [0] * 4, iter=6)  # default stage is a GP stage: thinned by max_frames, not a boundary
    from placeviz.evaluate import select_frames
    assert json.load(open(os.path.join(OUT, "snapw", "meta.json")))["frames"][-1]["stage"] == "cellGP"
    assert len(select_frames([{"stage": "GP"}] * 500, 100)) == 100
    assert 250 in select_frames([{"stage": "cellGP"}] * 250 + [{"stage": "LG"}] + [{"stage": "cellGP"}] * 249, 10)
    dumps = os.path.join(OUT, "dumps")
    os.makedirs(dumps)
    open(os.path.join(dumps, "000002.def"), "w").write(DEF)  # u3 unplaced -> die centre
    open(os.path.join(dumps, "000010_LG.def"), "w").write(DEF.replace("( 1000 0 ) N", "( 1200 0 ) FN"))
    open(os.path.join(dumps, "notes.txt"), "w").write("ignored")
    assert snapshot.convert_defs(dumps, def_p, os.path.join(OUT, "conv"), "cellGP") == 2
    meta = json.load(open(os.path.join(OUT, "conv", "meta.json")))
    assert [(m["iter"], m["stage"], m.get("filled")) for m in meta["frames"]] == [(2, "cellGP", 1), (10, "LG", 1)]
    f0, f1 = (np.load(os.path.join(OUT, "conv", m["file"])) for m in meta["frames"])
    assert f0["x"].tolist() == [1000, 5000, 10000, 10000] and f0["y"].tolist() == [0, 2000, 6000, 4000]
    assert f1["x"].tolist() == [1200, 5000, 10000, 10000] and f1["orient"].tolist() == [4, 5, 0, 0]


def _cfg(placers, modes="[fix, mov]", extra=""):
    lef_p, def_p = setup()
    py = sys.executable
    text = """output: db
benchmarks: [{name: t, lef: t.lef, def: t.def}]
modes: %s
placers:
%s
gr:
  name: fakegr
  cmd: "%s -c \\"open('gr.guide', 'w').write('n1\\\\n')\\" {def}"
dr:
  name: fakedr
  cmd: "%s -c \\"import sys; open('dr.def', 'w').write('x'); open('v.csv', 'w').write('h\\\\nminarea,M1,0,0,1,1\\\\nminarea,M1,0,0,1,1\\\\nshort,M2,0,0,2,2\\\\n'); print('Terminated...'); sys.exit(134)\\""
  vio_csv: "{route_dir}/v.csv"
jobs: 2
%s""" % (modes, placers, py, py, extra)
    p = os.path.join(OUT, "cfg.yaml")
    open(p, "w").write(text)
    return p


def _placer(name, style):
    return """  - name: %s
    cmd: "%s fake_placer.py.REL %s {def} {out_def} {snap_dir}"
    env: {PV_REPO: "%s"}""" % (name, sys.executable, style, REPO)


def test_pipeline():
    pl = "\n".join(_placer(n, n) for n in ("writer", "dumps", "none", "fail")).replace(
        "fake_placer.py.REL", os.path.join(OUT, "fake_placer.py"))
    cfg_p = _cfg(pl)
    cfg = config.load(cfg_p)
    assert cfg["output"] == os.path.join(OUT, "db") and cfg["congestion"] == {"backend": "rudy"}
    assert [b.key for b in bench.load(cfg)] == ["t_fix", "t_mov"]
    cli.main(["run", cfg_p, "--stages", "place,gr,dr,eval"])
    db = cfg["output"]
    b_fix, b_mov = bench.load(cfg)
    st = {p: place.read_status(place.run_dir(cfg, p, b_fix))["place"] for p in ("writer", "dumps", "none", "fail")}
    assert [st[p]["status"] for p in st] == ["ok", "ok", "ok", "exit 3"], st
    for p, n, chk, src in (("writer", 2, "ok", "writer"), ("dumps", 2, "ok", "def dumps"),
                           ("none", 1, "final.def appended", "none")):
        rd = place.run_dir(cfg, p, b_mov)
        meta = json.load(open(os.path.join(rd, "meta.json")))
        assert (len(meta["frames"]), meta["final_check"], meta["snapshots"]) == (n, chk, src), (p, meta)
        assert snapshot.check(rd) == [] and not os.path.exists(os.path.join(rd, "snap"))
    assert json.load(open(os.path.join(place.run_dir(cfg, "fail", b_fix), "meta.json")))["crashed"]
    # mov input DEF: b1 movable; route: restored orientation, GCELLGRID, GR ok, DR exit 134 after report = ok
    assert lefdef.Def(b_mov.deff, nets=False).comps["b1"].status == "PLACED"
    rd = route.route_dir(cfg, "dumps", b_mov)
    s = place.read_status(rd)
    assert s["gr"]["status"] == "ok" and s["dr"]["status"] == "ok" and s["dr"]["drv"] == 2, s
    assert lefdef.Def(os.path.join(rd, "in.def"), nets=False).gcells
    assert route.status(cfg, "fail", b_fix) == dict(placement="placer exit 3", gr="not run", dr="not run",
                                                    drv="not run")
    assert route.status(cfg, "none", b_fix) == dict(placement="ok", gr="ok", dr="ok", drv="ok")
    assert route.load_vio(os.path.join(rd, "vio.csv")) == [["minarea", "M1", 0, 0, 1, 1], ["short", "M2", 0, 0, 2, 2]]
    # idempotent: second run skips everything; --only + --force reruns one unit
    t = os.path.getmtime(os.path.join(place.run_dir(cfg, "writer", b_fix), "meta.json"))
    assert place.run(cfg, cfg["placers"][0], b_fix) == "skip (done)" and route.gr(cfg, "writer", b_fix) == "skip (done)"
    cli.main(["run", cfg_p, "--stages", "place", "--only", "writer/*_fix", "--force", "--jobs", "1"])
    assert os.path.getmtime(os.path.join(place.run_dir(cfg, "writer", b_fix), "meta.json")) > t
    assert os.path.exists(os.path.join(db, "config.json"))
    # stale route outputs are never reported: a DR run without a violation file has no DRV count ...
    rd = route.route_dir(cfg, "none", b_fix)
    assert place.read_status(rd)["dr"]["drv"] == 2 and os.path.exists(os.path.join(rd, "vio.csv"))
    dr_cfg = cfg["dr"]
    cfg["dr"] = dict(dr_cfg, cmd="%s -c \"open('dr.def', 'w').write('x')\"" % sys.executable)
    assert route.dr(cfg, "none", b_fix, force=True) == "ok"
    assert place.read_status(rd)["dr"]["drv"] is None and not os.path.exists(os.path.join(rd, "vio.csv"))
    assert route.status(cfg, "none", b_fix)["drv"] == "missing"
    # ... a GR re-run invalidates DR, and a new placement invalidates GR and DR
    cfg["dr"] = dr_cfg
    assert route.dr(cfg, "none", b_fix, force=True) == "ok"
    assert route.gr(cfg, "none", b_fix, force=True) == "ok" and place.read_status(rd)["dr"]["status"] == "not run"
    assert not os.path.exists(os.path.join(rd, "dr.def")) and not os.path.exists(os.path.join(rd, "vio.csv"))
    fd, ev = os.path.join(place.run_dir(cfg, "none", b_fix), "final.def"), os.path.join(rd, "eval.def")
    os.utime(ev, (os.path.getmtime(fd) - 10,) * 2)  # = final.def newer than the routed placement
    route.prepare(cfg, "none", b_fix)
    assert place.read_status(rd) == {} and not os.path.exists(os.path.join(rd, "gr.guide"))
    assert route.dr(cfg, "none", b_fix) == "skip (no GR guide)" and route.gr(cfg, "none", b_fix) == "ok"
    # DR crash -> signal status
    cfg["dr"] = dict(cfg["dr"], cmd="kill -SEGV $$")
    assert route.dr(cfg, "none", b_fix, force=True) == "crashed (signal 11)"


def test_config_errors():
    _cfg("  - {name: a, cmd: 'true'}")
    good = open(os.path.join(OUT, "cfg.yaml")).read()
    for edit, msg in ((("benchmarks:", "benchmark:"), "unknown key 'benchmark' (did you mean 'benchmarks'?)"),
                      (("jobs: 2", "maxframes: 100"), "unknown key 'maxframes' (did you mean 'max_frames'?)"),
                      (("modes: [fix, mov]", "modes: [fixx]"), "modes must be a subset"),
                      (("lef: t.lef", "lef: nope.lef"), "file not found"),
                      (("  vio_csv:", "  vio_cvs:"), "dr: unknown key 'vio_cvs'")):
        p = os.path.join(OUT, "bad.yaml")
        open(p, "w").write(good.replace(*edit))
        try:
            cli.main(["run", p, "--stages", "page"])
            raise AssertionError("accepted: %s" % (edit,))
        except SystemExit as e:  # one line, no traceback
            assert str(e.code).startswith("placeviz: error: ") and msg in str(e.code), e.code
    for argv, msg in ((["run", os.path.join(OUT, "none.yaml")], "No such file"),
                      (["page", OUT], "not a placeviz output dir"), (["check", OUT], "not a run dir")):
        try:
            cli.main(argv)
            raise AssertionError(argv)
        except SystemExit as e:
            assert msg in str(e.code), e.code
    open(p, "w").write(good.replace("placers:\n  - {name: a, cmd: 'true'}", "placers: no"))
    try:  # placers: no and no runs: an error, not an empty page
        cli.main(["run", p])
        raise AssertionError("empty run accepted")
    except SystemExit as e:
        assert "has no runs" in str(e.code), e.code


def test_fill():
    v = {"lef": ["/a b/t.lef", "/c.lef"], "def": "/x.def"}
    assert place.fill("p --lef {lef} --def {def} {unknown} ${HOME} awk '{print}'", v) == \
        "p --lef '/a b/t.lef' /c.lef --def /x.def {unknown} ${HOME} awk '{print}'"
    assert place.fill("{lef}", v, quote=False) == "/a b/t.lef /c.lef"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
