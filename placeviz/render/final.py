"""Final-tab data: vector tiles + overview rasters of the final placement and its routing, per run.

Reads route/<p>/<key>/{eval.def, gr.guide, dr.def, vio.csv, status.json} and the benchmark LEF; writes
<output>/web/final/<p>/<key>/{meta.json, overview/<layer>.png, <layer>/<ix>_<iy>.js}; write_index() bundles every
meta.json into <output>/web/final/index.js (`const FINAL = {"<p>/<key>": meta}`), loaded by the page from file://.

meta: die [xl, yl, xh, yh] DBU, dbu, q (tile unit: DBU = int * q), nt (nt x nt tiles over the die), layers
{name: {n, tiles: ["ix_iy"]}} (only layers that exist), status {placement, gr, dr, drv}, metal, cut, width {metal: DBU},
drv_types, counts (validation). Tile file content: FINAL_TILE("<p>/<key>","<layer>","<ix>_<iy>","<base64 gzip Int32>");
a record goes to every tile its bbox touches (indices clamped to the die). Records, coordinates round(DBU / q):
  cells [xl, yl, xh, yh, kind]  kind 0 std cell, 1 macro FIXED, 2 macro movable
  pins  [xl, yl, xh, yh, metal]  bbox of the pin's PORT rects on its lowest metal; I/O pins from DEF PINS
  nets  [x0, y0, x1, y1, degree] flylines: star from the driver pin (LEF OUTPUT / DEF PIN INPUT, else first pin)
  gr    [xl, yl, xh, yh, metal]  guide rects, exact duplicates (same net, rect, layer) removed
  dr    wires [x0, y0, x1, y1, metal] (centre line), vias [x, y, x, y, 100 + cut]
  drv   [xl, yl, xh, yh, type * 64 + layer]  layer = metal index, or 32 + cut index
Overview PNGs are 1600 px wide (height by die aspect), transparent except `cells`; metal colours cycle every 5.
"""
import base64
import gzip
import json
import os
import re
import shutil
import time
from array import array

import numpy as np
from PIL import Image, ImageDraw

from .. import place, route
from ..lefdef import Def, Lef, orient, row_height, section_span, statements
from .frames import fresh

Q, NT, W_PX = 10, 8, 1600
METAL_RGB = ["#e41a1c", "#377eb8", "#4daf4a", "#984ea3", "#ff7f00"]
VIA_RGB, PIN_RGB, NET_RGB, DRV_RGB = "#222222", "#000000", "#00a0a0", "#ff00ff"
STD_RGB, MAC_FIX_RGB, MAC_MOV_RGB = "#c8d3e0", "#9aa5b1", "#f4a259"
LAYERS = ["cells", "pins", "nets", "gr", "dr", "drv"]


def lowest_metal_pins(lef, metal):
    """macro -> {pin: (is_output, metal_index, x0, y0, x1, y1 um)} using the pin's lowest metal layer."""
    mi = {n: i for i, n in enumerate(metal)}
    out = {}
    for mac, m in lef.macros.items():
        d = {}
        for p, pin in m["pins"].items():
            ls = [l for l in pin["ports"] if l in mi]
            if not ls:
                continue
            rs = pin["ports"][min(ls, key=mi.get)]
            d[p] = (pin["direction"] == "OUTPUT", mi[min(ls, key=mi.get)], min(r[0] for r in rs),
                    min(r[1] for r in rs), max(r[2] for r in rs), max(r[3] for r in rs))
        out[mac] = d
    return out


def parse_dr(sec, mi, via_info):
    """DR NETS section -> wires (n,5) [x0,y0,x1,y1,metal], vias (n,5) [x,y,x,y,100+cut], stats.
    Consecutive points of a statement are a wire (also across a via, whose other metal becomes the current layer);
    '*' repeats the previous coordinate; a 3rd point value (extension) is ignored; RECT patches are counted only."""
    wires, vias = array("i"), array("i")
    st = dict(rect_patches=0, unknown_vias=0, virtual=0)
    toks = sec.split()
    n = len(toks)
    i = 0
    routing = False
    layer = -1
    lname = None
    px = py = None
    nowire = False
    while i < n:
        t = toks[i]
        if t == "(" and routing:
            xs, ys = toks[i + 1], toks[i + 2]
            j = i + 3 if toks[i + 3] == ")" else i + 4
            x = px if xs == "*" else int(xs)
            y = py if ys == "*" else int(ys)
            if px is not None and not nowire:
                wires.extend((px, py, x, y, layer))
            px, py, nowire = x, y, False
            i = j + 1
        elif t == "NEW" and routing:
            lname = toks[i + 1]
            layer = mi.get(lname, -1)
            px = py = None
            i += 2
        elif t == ";":
            routing = False
            i += 1
        elif t == "+":
            kw = toks[i + 1]
            if kw in ("ROUTED", "FIXED", "COVER", "NOSHIELD"):
                routing = True
                lname = toks[i + 2]
                layer = mi.get(lname, -1)
                px = py = None
                i += 3
            elif kw in ("SHAPE", "STYLE") and routing:
                i += 3
            else:
                routing = False
                i += 2
        elif t == "-" and not routing:
            i += 2
        elif not routing:
            i += 1
        elif t == "RECT":
            st["rect_patches"] += 1
            i += 7
        elif t in ("MASK", "STYLE", "TAPERRULE"):
            i += 2
        elif t == "TAPER":
            i += 1
        elif t == "VIRTUAL":
            st["virtual"] += 1
            nowire = True
            i += 1
        else:  # via name at the last point
            v = via_info.get(t)
            if v is None:
                st["unknown_vias"] += 1
            else:
                vias.extend((px, py, px, py, 100 + v[0]))
                other = [m for m in v[1] if m != lname]
                if other:
                    lname = other[0]
                    layer = mi[lname]
            i += 1
    w = np.frombuffer(wires, dtype=np.int32).reshape(-1, 5)
    v = np.frombuffer(vias, dtype=np.int32).reshape(-1, 5)
    return w, v, st


def count_dr_independent(sec):
    """Regex count over the NETS text: wires = sum(points - 1) per ROUTED/NEW statement, vias = names after ')'."""
    pt = re.compile(r"\(\s*[-\d*]+\s+[-\d*]+(?:\s+-?\d+)?\s*\)")
    via = re.compile(r"\)\s+(?!RECT\b|NEW\b|MASK\b|VIRTUAL\b)([A-Za-z_][\w\[\]./-]*)")
    nw = nv = 0
    for chunk in re.split(r"\+\s+ROUTED\s|\bNEW\s", sec)[1:]:
        chunk = chunk.split(";")[0]
        k = len(pt.findall(chunk))
        nw += max(k - 1, 0)
        nv += len(via.findall(chunk))
    return nw, nv


def pins_hit(pins, wires, vias):
    """Number of pins (DBU boxes, metal index) containing a DR wire end or via (via k touches metals k, k+1) on
    their metal: an orientation / transform check against the router's pin access."""
    pts = [np.c_[wires[:, 0:2], wires[:, 4]], np.c_[wires[:, 2:4], wires[:, 4]],
           np.c_[vias[:, 0:2], vias[:, 4] - 100], np.c_[vias[:, 0:2], vias[:, 4] - 99]]
    p = np.concatenate(pts).astype(np.float64)
    p = p[np.argsort(p[:, 0], kind="stable")]
    hit = 0
    for xl, yl, xh, yh, m in pins.tolist():
        a, b = np.searchsorted(p[:, 0], [xl, xh + 1e-9])
        s = p[a:b]
        hit += bool(np.any((s[:, 1] >= yl) & (s[:, 1] <= yh) & (s[:, 2] == m)))
    return hit


def parse_guide(path, mi):
    """GR guide -> (n,5) [xl,yl,xh,yh,metal] after removing exact duplicates per net; raw count; #nets."""
    flat = array("i")
    net = -1
    for line in open(path):
        t = line.split()
        if len(t) == 5:
            flat.extend((net, int(t[0]), int(t[1]), int(t[2]), int(t[3]), mi[t[4]]))
        elif len(t) == 1 and t[0] not in ("(", ")"):
            net += 1
    a = np.frombuffer(flat, dtype=np.int32).reshape(-1, 6)
    u = np.unique(a, axis=0)
    return np.ascontiguousarray(u[:, 1:]), len(a), net + 1


def parse_vio(path, metal, cut):
    """vio.csv (route.load_vio: minarea dedupe) -> (n,5) [xl,yl,xh,yh,type*64+layer], types, #rows dropped because
    their layer is not a LEF metal / cut layer."""
    allrows = route.load_vio(path)
    known = set(metal) | set(cut)
    rows = [r for r in allrows if r[1] in known]
    types = sorted({r[0] for r in rows})
    ti = {n: i for i, n in enumerate(types)}
    li = {n: i for i, n in enumerate(metal)}
    li.update({n: 32 + i for i, n in enumerate(cut)})
    a = np.array([r[2:6] + [ti[r[0]] * 64 + li[r[1]]] for r in rows], dtype=np.float64).reshape(-1, 5)
    return a, types, len(allrows) - len(rows)


# ---------------------------------------------------------------- output
def to_q(a):
    """DBU columns 0..3 -> round(DBU / Q); column 4 kept."""
    r = np.empty(a.shape, dtype=np.int32)
    r[:, :4] = np.rint(np.asarray(a[:, :4], dtype=np.float64) / Q)
    r[:, 4] = a[:, 4]
    return r


def tile_masks(rec, die):
    """{(ix, iy): bool mask} for every tile the record bbox intersects (die split NT x NT)."""
    xl, yl, xh, yh = (v / Q for v in die)
    tw, th = (xh - xl) / NT, (yh - yl) / NT
    ix0 = np.clip(np.floor((np.minimum(rec[:, 0], rec[:, 2]) - xl) / tw), 0, NT - 1).astype(int)
    ix1 = np.clip(np.floor((np.maximum(rec[:, 0], rec[:, 2]) - xl) / tw), 0, NT - 1).astype(int)
    iy0 = np.clip(np.floor((np.minimum(rec[:, 1], rec[:, 3]) - yl) / th), 0, NT - 1).astype(int)
    iy1 = np.clip(np.floor((np.maximum(rec[:, 1], rec[:, 3]) - yl) / th), 0, NT - 1).astype(int)
    out = {}
    for ix in range(NT):
        mx = (ix0 <= ix) & (ix1 >= ix)
        for iy in range(NT):
            m = mx & (iy0 <= iy) & (iy1 >= iy)
            if m.any():
                out[(ix, iy)] = m
    return out


def write_tiles(key, name, rec, die, odir):
    os.makedirs(os.path.join(odir, name), exist_ok=True)
    keys, dup = [], 0
    for (ix, iy), m in tile_masks(rec, die).items():
        t = "%d_%d" % (ix, iy)
        b = base64.b64encode(gzip.compress(rec[m].astype("<i4").tobytes(), compresslevel=6, mtime=0)).decode()
        with open(os.path.join(odir, name, t + ".js"), "w") as f:
            f.write('FINAL_TILE("%s","%s","%s","%s");' % (key, name, t, b))
        keys.append(t)
        dup += int(m.sum())
    return keys, dup


def decode_tile(path):
    s = open(path).read()
    m = re.fullmatch(r'FINAL_TILE\("([^"]+)","([^"]+)","([^"]+)","([A-Za-z0-9+/=]*)"\);', s)
    return np.frombuffer(gzip.decompress(base64.b64decode(m.group(4))), dtype="<i4").reshape(-1, 5)


class Canvas:
    def __init__(self, die):
        self.xl, self.yl, xh, self.yh = die
        self.s = W_PX / (xh - self.xl)
        self.h = max(1, int(round((self.yh - self.yl) * self.s)))

    def xy(self, rec):
        """Records (q units) -> pixel coords [x0, y0, x1, y1] with y flipped (image top = die top)."""
        a = rec[:, :4].astype(np.float64) * Q
        out = np.empty_like(a)
        out[:, 0::2] = (a[:, 0::2] - self.xl) * self.s
        out[:, 1::2] = (self.yh - a[:, 1::2]) * self.s
        return out

    def boxes(self, rec, minpx=0.0):
        """Pixel boxes [x0, y0, x1, y1] sorted per axis, grown to at least minpx."""
        p = self.xy(rec)
        x0, x1 = np.minimum(p[:, 0], p[:, 2]), np.maximum(p[:, 0], p[:, 2])
        y0, y1 = np.minimum(p[:, 1], p[:, 3]), np.maximum(p[:, 1], p[:, 3])
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        hw, hh = np.maximum((x1 - x0) / 2, minpx / 2), np.maximum((y1 - y0) / 2, minpx / 2)
        return np.stack([cx - hw, cy - hh, cx + hw, cy + hh], 1)

    def new(self, bg=(0, 0, 0, 0)):
        return Image.new("RGBA", (W_PX, self.h), bg)


def hex_rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def tinted(mask, colour, alpha):
    """Constant-colour RGBA layer whose alpha = mask * alpha."""
    im = Image.new("RGBA", mask.size, hex_rgb(colour) + (0,))
    im.putalpha(mask.point(lambda v: int(v * alpha)))
    return im


def raster(name, rec, cv, width_px):
    if name == "cells":
        im = cv.new((255, 255, 255, 255))
        d = ImageDraw.Draw(im)
        for kind, col in ((0, STD_RGB), (1, MAC_FIX_RGB), (2, MAC_MOV_RGB)):
            for b in cv.boxes(rec[rec[:, 4] == kind]).tolist():
                d.rectangle(b, fill=col)
        return im
    im = cv.new()
    if name == "pins":
        d = ImageDraw.Draw(im)
        for b in cv.boxes(rec).tolist():
            d.rectangle(b, fill=PIN_RGB)
    elif name == "nets":
        mask = Image.new("L", im.size, 0)
        d = ImageDraw.Draw(mask)
        for p in cv.xy(rec[rec[:, 4] <= 64]).tolist():  # same default as the viewer: no clock-net stars
            d.line(p, fill=255)
        im = tinted(mask, NET_RGB, 0.35)
    elif name == "gr":  # ponytail: filled at alpha 0.25 per metal (lowest first); viewer may style differently
        for li in range(len(width_px)):
            mask = Image.new("L", im.size, 0)
            d = ImageDraw.Draw(mask)
            for b in cv.boxes(rec[rec[:, 4] == li]).tolist():
                d.rectangle(b, fill=255)
            im = Image.alpha_composite(im, tinted(mask, METAL_RGB[li % len(METAL_RGB)], 0.25))
    elif name == "dr":
        d = ImageDraw.Draw(im)
        for li, wp in enumerate(width_px):
            for p in cv.xy(rec[rec[:, 4] == li]).tolist():
                d.line(p, fill=METAL_RGB[li % len(METAL_RGB)], width=max(1, int(round(wp))))
        pts = cv.xy(rec[rec[:, 4] >= 100])[:, :2]
        d.point([tuple(p) for p in pts.tolist()], fill=VIA_RGB)
    elif name == "drv":
        d = ImageDraw.Draw(im)
        for b in cv.boxes(rec, 3.0).tolist():
            d.rectangle(b, outline=DRV_RGB)
    return im


# ---------------------------------------------------------------- run
def run_status(cfg, placer, bench, rd):
    """route.status, with "missing" for a stage reported ok whose output file is absent."""
    st = route.status(cfg, placer, bench)
    for k, f in (("gr", "gr.guide"), ("dr", "dr.def"), ("drv", "vio.csv")):
        if st[k] == "ok" and not (rd and os.path.exists(os.path.join(rd, f))):
            st[k] = "missing"
    return st


def build_run(cfg, placer, bench, force=False):
    t0 = time.time()
    key = "%s/%s" % (placer, bench.key)
    odir = os.path.join(cfg["output"], "web", "final", placer, bench.key)
    run = place.run_dir(cfg, placer, bench)
    rd = route.route_dir(cfg, placer, bench)
    inputs = [os.path.join(run, f) for f in ("final.def", "status.json")] + \
        [os.path.join(rd, f) for f in ("eval.def", "gr.guide", "dr.def", "vio.csv", "status.json")]
    if not force and fresh(os.path.join(odir, "meta.json"), inputs):
        return "skip (done)"
    rd = route.prepare(cfg, placer, bench)  # eval.def (orientation restored) even when GR / DR are disabled
    status = run_status(cfg, placer, bench, rd)
    shutil.rmtree(odir, ignore_errors=True)
    os.makedirs(odir)
    if rd is None:
        meta = dict(status=dict(status, placement=status["placement"] if status["placement"] != "ok" else "missing"))
        json.dump(meta, open(os.path.join(odir, "meta.json"), "w"))
        return "status only (%s)" % meta["status"]["placement"]

    lef = Lef(bench.lef)
    metal, cut = lef.metal, lef.cut
    mi = {n: i for i, n in enumerate(metal)}
    ci = {n: i for i, n in enumerate(cut)}
    lpins = lowest_metal_pins(lef, metal)
    d = Def(os.path.join(rd, "eval.def"))
    dbu, die = d.dbu, [int(round(v)) for v in d.die]
    row_h = row_height(lef, d)
    recs, counts = {}, {}

    # cells
    cells = np.empty((len(d.comps), 5))
    for k, c in enumerate(d.comps.values()):
        sw, sh = lef.macros[c.macro]["size"]
        w, h = round(sw * dbu), round(sh * dbu)
        if c.orient in ("E", "W", "FE", "FW"):
            w, h = h, w
        big = sh * dbu > row_h + 1
        cells[k] = (c.x, c.y, c.x + w, c.y + h, 0 if not big else (1 if c.status == "FIXED" else 2))
    recs["cells"] = cells
    counts.update(cells=len(cells), macros_fixed=int((cells[:, 4] == 1).sum()),
                  macros_movable=int((cells[:, 4] == 2).sum()))

    # pins + nets
    iopins = {n: (p.direction == "INPUT", mi.get(p.layer, 0)) + tuple(p.rect) for n, p in d.pins.items() if p.rect}
    pins, segs = [], []
    n_conn = unresolved = n_nets2 = outside = 0
    for _, conns in d.nets:
        n_conn += len(conns)
        geo = []
        for c, p in conns:
            if c == "PIN":
                g = iopins.get(p)
            else:
                cm = d.comps[c]
                lp = lpins[cm.macro].get(p)
                g = None
                if lp is not None:
                    w, h = lef.macros[cm.macro]["size"]
                    a, b = orient(lp[2], lp[3], w, h, cm.orient), orient(lp[4], lp[5], w, h, cm.orient)
                    cx, cy = cm.x, cm.y
                    # snap to whole DBU before /Q so .5-quantum ties round the same way (float noise otherwise decides)
                    g = (lp[0], lp[1], cx + round(min(a[0], b[0]) * dbu), cy + round(min(a[1], b[1]) * dbu),
                         cx + round(max(a[0], b[0]) * dbu), cy + round(max(a[1], b[1]) * dbu))
                    cw, ch = (h, w) if cm.orient in ("E", "W", "FE", "FW") else (w, h)
                    outside += not (cx - 1 <= g[2] and g[4] <= cx + cw * dbu + 1 and
                                    cy - 1 <= g[3] and g[5] <= cy + ch * dbu + 1)
            if g is None:
                unresolved += 1
                continue
            geo.append(g)
            pins.append((g[2], g[3], g[4], g[5], g[1]))
        if len(geo) >= 2:
            n_nets2 += 1
            drv = next((i for i, g in enumerate(geo) if g[0]), 0)
            dx, dy = (geo[drv][2] + geo[drv][4]) / 2, (geo[drv][3] + geo[drv][5]) / 2
            for i, g in enumerate(geo):
                if i != drv:
                    segs.append((dx, dy, (g[2] + g[4]) / 2, (g[3] + g[5]) / 2, len(conns)))
    recs["pins"] = np.array(pins).reshape(-1, 5)
    recs["nets"] = np.array(segs).reshape(-1, 5)
    counts.update(pins=len(pins), net_connections=n_conn, unresolved_connections=unresolved, io_pins=len(iopins),
                  nets=len(d.nets), nets_ge2=n_nets2, flylines=len(segs), pins_outside_cell=outside)
    del d

    if status["gr"] == "ok":
        gr, raw, ngnet = parse_guide(os.path.join(rd, "gr.guide"), mi)
        recs["gr"] = gr
        counts.update(gr=len(gr), gr_raw=raw, gr_nets=ngnet)

    if status["dr"] == "ok":
        dtext = open(os.path.join(rd, "dr.def")).read()
        dvias = {t[1]: (t[t.index("LAYERS") + 1:t.index("LAYERS") + 4] if "LAYERS" in t else
                        [t[i + 1] for i, v in enumerate(t) if v in ("RECT", "POLYGON")])
                 for t in statements(dtext, "VIAS") if t[0] == "-"}
        via_info = {}
        for name, ls in list(lef.vias.items()) + list(dvias.items()):
            cl = [l for l in ls if l in ci]
            if cl:
                via_info[name] = (ci[cl[0]], [l for l in ls if l in mi])
        a, b = section_span(dtext, "NETS")
        sec = dtext[a:b]
        del dtext
        wires, vias, st = parse_dr(sec, mi, via_info)
        iw, iv = count_dr_independent(sec)
        del sec
        recs["dr"] = np.concatenate([wires, vias]).astype(np.float64)
        counts.update(dr_wires=len(wires), dr_vias=len(vias), dr_wires_indep=iw, dr_vias_indep=iv,
                      dr_bad_layer=int((wires[:, 4] < 0).sum()), **{"dr_" + k: v for k, v in st.items()})
        counts["pins_hit_by_dr"] = pins_hit(recs["pins"], wires, vias)

    drv_types = []
    if status["drv"] == "ok":
        recs["drv"], drv_types, counts["drv_bad_layer"] = parse_vio(os.path.join(rd, "vio.csv"), metal, cut)
        counts["drv"] = len(recs["drv"])

    cv = Canvas(die)
    os.makedirs(os.path.join(odir, "overview"))
    width_px = [(lef.layers[n]["width"] or 0.1) * dbu * cv.s for n in metal]
    layers, rt = {}, []
    for name in LAYERS:
        if name not in recs:
            continue
        r = to_q(recs[name])
        keys, dup = write_tiles(key, name, r, die, odir)
        layers[name] = dict(n=len(r), tiles=keys)
        counts[name + "_tile_records"] = dup
        raster(name, r, cv, width_px).save(os.path.join(odir, "overview", name + ".png"))
        for t in keys[:: max(1, len(keys) // 2)][:2]:  # round-trip 2 tiles per layer
            ix, iy = map(int, t.split("_"))
            rt.append(np.array_equal(decode_tile(os.path.join(odir, name, t + ".js")),
                                     r[tile_masks(r, die)[(ix, iy)]]))
    meta = dict(die=die, dbu=dbu, q=Q, nt=NT, layers=layers, status=status, metal=metal, cut=cut,
                width={n: int(round(lef.layers[n]["width"] * dbu)) for n in metal if lef.layers[n]["width"]},
                drv_types=drv_types, counts=counts)
    json.dump(meta, open(os.path.join(odir, "meta.json"), "w"))
    c = counts
    checks = dict(pins_in_cells=c["pins_outside_cell"] == 0,
                  pins=c["pins"] + c["unresolved_connections"] == c["net_connections"], tiles=all(rt),
                  drv_layers=not c.get("drv_bad_layer"),
                  dr="dr_wires" not in c or (c["dr_wires"] == c["dr_wires_indep"] and c["dr_vias"] == c["dr_vias_indep"]))
    size = sum(os.path.getsize(os.path.join(a, f)) for a, _, fs in os.walk(odir) for f in fs)
    return "%s; checks %s; %.1f MB, %.1f s" % (" ".join("%s %d" % (n, l["n"]) for n, l in layers.items()),
                                              "PASS" if all(checks.values()) else
                                              "FAIL %s" % [k for k, v in checks.items() if not v],
                                              size / 1e6, time.time() - t0)


def write_index(cfg):
    """web/final/index.js with the runs of the config's units (results of removed placers / benchmarks are left out)."""
    from ..cli import units
    out = os.path.join(cfg["output"], "web", "final")
    allm = {}
    for p, b in units(cfg):
        f = os.path.join(out, p["name"], b.key, "meta.json")
        if os.path.exists(f):
            allm["%s/%s" % (p["name"], b.key)] = json.load(open(f))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "index.js"), "w") as f:
        f.write("const FINAL = " + json.dumps(allm, separators=(",", ":")) + ";\n")
    return len(allm)
