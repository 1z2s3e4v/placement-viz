"""Shared LEF/DEF parsing (pure Python), orientation transforms, pin-centre HPWL, and the DEF rewrites used before
routing (make_movable, restore_orient, add_gcell).

LEF geometry is in microns, DEF geometry in database units (DBU). Macros, pins and layers are plain dicts:
  Lef.layers[name] = {"type", "direction", "pitch", "width", "offset"}   (in file order; um)
  Lef.vias[name]   = [layer names]
  Lef.macros[name] = {"size": (w, h), "cls": "CORE"|"BLOCK"|..., "pins": {pin: {"direction", "use",
                      "ports": {layer: [[xl, yl, xh, yh], ...]}}}, "obs": {layer: [[xl, yl, xh, yh], ...]}}
"""
import re
from collections import namedtuple

import numpy as np

ORIENTS = ["N", "S", "W", "E", "FN", "FS", "FW", "FE"]  # frame orient index (FORMAT.md)
PLACED = ("PLACED", "FIXED", "COVER")

Row = namedtuple("Row", "name site x y orient nx ny sx sy")
Track = namedtuple("Track", "dir start num step layers")
Grid = namedtuple("Grid", "dir start num step")
Comp = namedtuple("Comp", "macro status x y orient")  # status UNPLACED -> x = y = None
IOPin = namedtuple("IOPin", "net direction layer rect x y orient")  # rect: absolute DBU box (None without LAYER)


def orient(x, y, w, h, o):
    """Point (x, y) of a w x h cell (N frame) in the cell frame after orientation o, lower-left kept at origin."""
    return {"N": (x, y), "S": (w - x, h - y), "FN": (w - x, y), "FS": (x, h - y),
            "E": (y, w - x), "W": (h - y, x), "FE": (y, x), "FW": (h - y, w - x)}[o]


def orient_np(x, y, w, h, o):
    """Vectorized orient(): arrays x, y, w, h and o = ORIENTS indices."""
    c = [o == k for k in range(8)]  # N S W E FN FS FW FE
    return (np.select(c, [x, w - x, h - y, y, w - x, x, h - y, y]),
            np.select(c, [y, h - y, x, w - x, y, h - y, w - x, x]))


def orient_rect(r, w, h, o):
    a, b = orient(r[0], r[1], w, h, o), orient(r[2], r[3], w, h, o)
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])]


def def_orient_rect(x0, y0, x1, y1, o):
    """DEF PIN shape rect rotated about the pin origin (FN = mirror about the y axis, E = R270, W = R90)."""
    f = {"N": lambda x, y: (x, y), "S": lambda x, y: (-x, -y), "FN": lambda x, y: (-x, y),
         "FS": lambda x, y: (x, -y), "W": lambda x, y: (-y, x), "E": lambda x, y: (y, -x),
         "FW": lambda x, y: (-y, -x), "FE": lambda x, y: (y, x)}[o]
    a, b = f(x0, y0), f(x1, y1)
    return min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])


# ----------------------------------------------------------------------------------------------------------- LEF
_LEF_TOK = re.compile(r'"[^"]*"|#[^\n]*|;|[^\s;"#]+')


class _Stream:
    def __init__(self, text):
        self.t = [x for x in _LEF_TOK.findall(text) if x[0] != "#"]
        self.i = 0

    def peek(self, k=0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else None

    def stmt(self):
        j = self.t.index(";", self.i)
        s, self.i = self.t[self.i:j], j + 1
        return s

    def end(self, name=None):
        """Consume and return True at `END name` (or a bare `END` when name is None)."""
        if self.peek() == "END" and (name is None or self.peek(1) == name):
            self.i += 1 if name is None else 2
            return True
        return False


def _box(v):
    xs, ys = v[0::2], v[1::2]
    return [min(xs), min(ys), max(xs), max(ys)]


class Lef:
    """One or several LEF files (tech first), merged. Statement based: statements may span lines."""

    def __init__(self, paths):
        self.dbu, self.layers, self.vias, self.macros = None, {}, {}, {}
        for p in [paths] if isinstance(paths, str) else paths:
            with open(p) as f:
                self._parse(_Stream(f.read()))

    @property
    def metal(self):
        return [n for n, l in self.layers.items() if l["type"] == "ROUTING"]

    @property
    def cut(self):
        return [n for n, l in self.layers.items() if l["type"] == "CUT"]

    def pin_centres(self):
        """macro -> {pin: (x, y) um}: centre of the bbox of all PORT rects of the pin (= hpwl.py)."""
        out = {}
        for mac, m in self.macros.items():
            out[mac] = {}
            for p, pin in m["pins"].items():
                rs = [r for rects in pin["ports"].values() for r in rects]
                if rs:
                    out[mac][p] = ((min(r[0] for r in rs) + max(r[2] for r in rs)) / 2,
                                   (min(r[1] for r in rs) + max(r[3] for r in rs)) / 2)
        return out

    def _parse(self, s):
        while s.peek() is not None:
            k = s.peek()
            if k in ("LAYER", "VIA", "VIARULE", "SITE", "MACRO", "NONDEFAULTRULE"):
                name = s.peek(1)
                s.i += 2
                if s.peek() in ("DEFAULT", "GENERATE"):
                    s.i += 1
                getattr(self, "_" + k.lower(), self._skip)(s, name)
            elif k in ("UNITS", "PROPERTYDEFINITIONS", "SPACING"):
                s.i += 1
                while not s.end(k):
                    st = s.stmt()
                    if st[:2] == ["DATABASE", "MICRONS"]:
                        self.dbu = float(st[2])
            elif k == "BEGINEXT":
                s.i = s.t.index("ENDEXT", s.i) + 1
            elif k == "END":  # END LIBRARY
                s.i += 2
            else:
                s.stmt()

    @staticmethod
    def _skip(s, name):
        while not s.end(name):
            s.i += 1

    def _layer(self, s, name):
        L = self.layers.setdefault(name, dict(type=None, direction=None, pitch=None, width=None, offset=None))
        while not s.end(name):
            st = s.stmt()
            k = st[0].lower() if st else ""
            if k in ("type", "direction") and L[k] is None:
                L[k] = st[1]
            elif k in ("pitch", "width", "offset") and L[k] is None:
                L[k] = float(st[1])  # PITCH x y: x

    def _via(self, s, name):
        ls = self.vias.setdefault(name, [])
        while not s.end(name):
            st = s.stmt()
            if st and st[0] == "LAYER":
                ls.append(st[1])
            elif st and st[0] == "LAYERS":  # generated via (VIARULE ... LAYERS bot cut top)
                ls.extend(st[1:4])

    @staticmethod
    def _shapes(s):
        """PORT / OBS body up to its bare END -> {layer: [[xl, yl, xh, yh] um]}."""
        out, cur = {}, None
        while not s.end():
            st = s.stmt()
            if not st:
                continue
            if st[0] == "LAYER":
                cur = st[1]
            elif st[0] in ("RECT", "POLYGON") and cur is not None and "ITERATE" not in st:
                v = st[1:]
                if v[0] == "MASK":
                    v = v[2:]
                out.setdefault(cur, []).append(_box([float(x) for x in v]))  # ponytail: POLYGON -> its bbox
        return out

    def _macro(self, s, name):
        m = self.macros[name] = dict(size=(0.0, 0.0), cls=None, pins={}, obs={})
        while not s.end(name):
            k = s.peek()
            if k == "PIN":
                p = s.peek(1)
                s.i += 2
                pin = m["pins"][p] = dict(direction="INOUT", use=None, ports={})
                while not s.end(p):
                    if s.peek() == "PORT":
                        s.i += 1
                        for l, rs in self._shapes(s).items():
                            pin["ports"].setdefault(l, []).extend(rs)
                    else:
                        st = s.stmt()
                        if st and st[0] in ("DIRECTION", "USE"):
                            pin[st[0].lower()] = st[1]
            elif k == "OBS":
                s.i += 1
                for l, rs in self._shapes(s).items():
                    m["obs"].setdefault(l, []).extend(rs)
            elif k == "END":  # bare END of a sub-block we do not model (e.g. DENSITY)
                s.i += 1
            else:
                st = s.stmt()
                if st and st[0] == "SIZE":
                    m["size"] = (float(st[1]), float(st[3]))
                elif st and st[0] == "CLASS":
                    m["cls"] = st[1]


# ----------------------------------------------------------------------------------------------------------- DEF
def _toks(stmt):
    return stmt.replace("(", " ( ").replace(")", " ) ").split()


def section_span(text, name):
    """(start, end) of the body of `name n ;` ... `END name` (after the header's ';'), or None."""
    m = re.search(r"^[ \t]*%s\b[^;]*;" % name, text, re.M)
    if m is None:
        return None
    e = re.compile(r"^[ \t]*END\s+%s\b" % name, re.M).search(text, m.end())
    return m.end(), e.start()


def statements(text, name):
    sp = section_span(text, name)
    return [] if sp is None else [t for t in (_toks(s) for s in text[sp[0]:sp[1]].split(";")) if t]


def _loc(t):
    """(status, x, y, orient) from a COMPONENTS / PINS statement; UNPLACED or absent -> (status|None, None, None, 'N')."""
    k = next((i for i, v in enumerate(t) if v in PLACED), None)
    if k is None:
        return ("UNPLACED" if "UNPLACED" in t else None), None, None, "N"
    return t[k], float(t[k + 2]), float(t[k + 3]), t[k + 5]


def _points(t, i):
    """Routing points from t[i]: [(x, y), ...] ('*' repeats), stopping at the first non-point token; -> (pts, j)."""
    pts = []
    while i < len(t) and t[i] == "(":
        j = t.index(")", i)
        x, y = t[i + 1], t[i + 2]
        px, py = pts[-1] if pts else (None, None)
        pts.append((px if x == "*" else float(x), py if y == "*" else float(y)))
        i = j + 1
    return pts, i


def parse_components(text):
    """name -> Comp, in DEF order (multi-line statements, float coordinates)."""
    out = {}
    for t in statements(text, "COMPONENTS"):
        if t[0] == "-":
            st, x, y, o = _loc(t)
            out[t[1]] = Comp(t[2], st or "UNPLACED", x, y, o)
    return out


class Def:
    def __init__(self, path, nets=True):
        with open(path) as f:
            text = f.read()
        self.path = path
        m = re.search(r"^[ \t]*COMPONENTS\b", text, re.M)
        head = text[:m.start()] if m else text
        self.dbu = float(re.search(r"UNITS\s+DISTANCE\s+MICRONS\s+(\S+)", head).group(1))
        da = re.search(r"DIEAREA([^;]*);", head)
        p, _ = _points(_toks(da.group(1)), 0)
        self.die = [min(v[0] for v in p), min(v[1] for v in p), max(v[0] for v in p), max(v[1] for v in p)]
        self.rows, self.tracks, self.gcells = [], [], []
        for k, body in re.findall(r"^[ \t]*(ROW|TRACKS|GCELLGRID)\b([^;]*);", text, re.M):
            t = body.split()
            if k == "ROW":
                do = t.index("DO") if "DO" in t else None
                nx, ny, sx, sy = ((int(t[do + 1]), int(t[do + 3]), float(t[do + 5]), float(t[do + 6]))
                                  if do is not None else (1, 1, 0.0, 0.0))
                self.rows.append(Row(t[0], t[1], float(t[2]), float(t[3]), t[4], nx, ny, sx, sy))
            elif k == "TRACKS":
                ls = t[t.index("LAYER") + 1:] if "LAYER" in t else []
                self.tracks.append(Track(t[0], float(t[1]), int(t[3]), float(t[5]), ls))
            else:
                self.gcells.append(Grid(t[0], float(t[1]), int(t[3]), float(t[5])))
        self.vias = {}
        for t in statements(text, "VIAS"):
            if t[0] == "-":
                self.vias[t[1]] = (t[t.index("LAYERS") + 1:t.index("LAYERS") + 4] if "LAYERS" in t else
                                   [t[i + 1] for i, v in enumerate(t) if v in ("RECT", "POLYGON")])
        self.comps = parse_components(text)
        self.pins = {}
        for t in statements(text, "PINS"):
            if t[0] != "-":
                continue
            st, x, y, o = _loc(t)
            net = t[t.index("NET") + 1] if "NET" in t else None
            direc = t[t.index("DIRECTION") + 1] if "DIRECTION" in t else None
            layer = rect = None
            if "LAYER" in t:
                k = t.index("LAYER")
                layer = t[k + 1]
                q = t.index("(", k)
                r = def_orient_rect(float(t[q + 1]), float(t[q + 2]), float(t[q + 5]), float(t[q + 6]), o)
                rect = (r[0] + (x or 0), r[1] + (y or 0), r[2] + (x or 0), r[3] + (y or 0))
            self.pins[t[1]] = IOPin(net, direc, layer, rect, x, y, o)
        self.nets, self.special = [], []
        if nets:
            for t in statements(text, "NETS"):
                if t[0] != "-":
                    continue
                conns, i = [], 2
                while i < len(t) and t[i] != "+":
                    if t[i] == "(":
                        j = t.index(")", i)
                        conns.append((t[i + 1], t[i + 2]))
                        i = j + 1
                    else:
                        i += 1
                self.nets.append((t[1], conns))
            for t in statements(text, "SPECIALNETS"):
                self.special.extend(_stripes(t))

    @property
    def row_height(self):
        ys = sorted({r.y for r in self.rows})
        return min(b - a for a, b in zip(ys, ys[1:])) if len(ys) > 1 else None


def _stripes(t):
    """SPECIALNETS statement -> [(net, layer, xl, yl, xh, yh) DBU] wire rects (flush ends) and + RECT shapes."""
    out, i = [], 2
    while i < len(t):
        if t[i] in ("ROUTED", "FIXED", "COVER", "NEW") and i + 2 < len(t):
            layer, w = t[i + 1], float(t[i + 2])
            i += 3
            while i < len(t) and t[i] != "(" and t[i] not in ("NEW", ";"):
                i += 1  # + SHAPE STRIPE, + MASK n, ...
            pts, i = _points(t, i)
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                hw = w / 2  # ponytail: special wires end flush (no half-width extension); DEF STYLE/ext ignored
                if y0 == y1:
                    out.append((t[1], layer, min(x0, x1), y0 - hw, max(x0, x1), y0 + hw))
                elif x0 == x1:
                    out.append((t[1], layer, x0 - hw, min(y0, y1), x0 + hw, max(y0, y1)))
        elif t[i] == "RECT" and i + 1 < len(t):
            (a, b), i2 = _points(t, i + 2)
            out.append((t[1], t[i + 1], min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])))
            i = i2
        else:
            i += 1
    return out


# ----------------------------------------------------------------------------------------------------------- HPWL
def hpwl(lef, d):
    """Pin-centre HPWL (DBU) over nets with >= 2 pins, no degree cap.
    lef: Lef or LEF path(s); d: Def or DEF path. I/O pins: centre of the DEF PIN rect (or its location)."""
    lef = lef if isinstance(lef, Lef) else Lef(lef)
    d = d if isinstance(d, Def) else Def(d)
    lp = lef.pin_centres()
    io = {n: (((p.rect[0] + p.rect[2]) / 2, (p.rect[1] + p.rect[3]) / 2) if p.rect else (p.x or 0.0, p.y or 0.0))
          for n, p in d.pins.items()}
    total = 0.0
    for _, conns in d.nets:
        xs, ys = [], []
        for c, p in conns:
            if c == "PIN":
                x, y = io[p]
            else:
                cm = d.comps[c]
                w, h = lef.macros[cm.macro]["size"]
                px, py = orient(*lp[cm.macro][p], w, h, cm.orient)
                x, y = cm.x + px * d.dbu, cm.y + py * d.dbu
            xs.append(x)
            ys.append(y)
        if len(xs) >= 2:
            total += max(xs) - min(xs) + max(ys) - min(ys)
    return total


# ----------------------------------------------------------------------------------------------------------- rewrites
def _rewrite_components(text, fn):
    """Apply fn(name, statement_text) -> new text to every COMPONENTS statement; returns (text, #changed)."""
    a, b = section_span(text, "COMPONENTS")
    parts, n = text[a:b].split(";"), 0
    for k, s in enumerate(parts):
        m = re.match(r"\s*-\s+(\S+)", s)
        if m:
            new = fn(m.group(1), s)
            n += new != s
            parts[k] = new
    return text[:a] + ";".join(parts) + text[b:], n


def row_height(lef, d):
    """Row height (DBU): smallest spacing of the DEF rows; with a single row, the smallest component height."""
    return d.row_height or min((lef.macros[c.macro]["size"][1] * d.dbu for c in d.comps.values()), default=0)


def macro_names(lef, d):
    """Components taller than a row (+1 DBU slack) = macros."""
    rh = row_height(lef, d)
    return {c for c, cm in d.comps.items() if lef.macros[cm.macro]["size"][1] * d.dbu > rh + 1}


def make_movable(src_def, dst_def, lef):
    """mov mode: every FIXED macro (taller than a row) becomes PLACED; I/O pins and other components untouched."""
    lef = lef if isinstance(lef, Lef) else Lef(lef)
    mac = macro_names(lef, Def(src_def, nets=False))
    text, n = _rewrite_components(open(src_def).read(), lambda c, s: re.sub(r"\+(\s*)FIXED\b", r"+\1PLACED", s)
                                  if c in mac else s)
    with open(dst_def, "w") as f:
        f.write(text)
    return n


_LOC_RE = re.compile(r"(\+\s*(?:PLACED|FIXED)\s*\(\s*\S+\s+\S+\s*\)\s*)(\w+)")


def restore_orient(orig_def, placed_def, out_def, lef):
    """Give the macros (macro_names of orig_def) their orig_def orientation back; location kept. Placers snap moved
    macros to the row orientation although they optimized them with the original pin offsets; the evaluator uses
    the same policy (evaluate.Design.eval_orient)."""
    lef = lef if isinstance(lef, Lef) else Lef(lef)
    d = Def(orig_def, nets=False)
    want = {c: d.comps[c].orient for c in macro_names(lef, d)}
    text, n = _rewrite_components(open(placed_def).read(), lambda c, s: _LOC_RE.sub(
        lambda k: k.group(1) + want[c], s) if c in want else s)
    with open(out_def, "w") as f:
        f.write(text)
    return n


def add_gcell(def_file, out_def_file, route_size=512):
    """Copy a DEF, adding a GCELLGRID (needed by CU-GR) if it has none. Reimplements Xplace
    tool/cugr_ispd2015_fix/run_cugr.add_gcell: route_size gcells on the shorter die side, square-ish on the longer
    one (Xplace computes round(size / ratio) for wide dies, i.e. fewer columns; here round(size * ratio))."""
    text = open(def_file).read()
    if "GCELLGRID" not in text:
        lx, ly, hx, hy = (int(v) for v in Def(def_file, nets=False).die)
        ratio = (hx - lx) / (hy - ly)
        nx = route_size if ratio <= 1 else round(route_size * ratio)
        ny = route_size if ratio >= 1 else round(route_size / ratio)
        g = ""
        for ax, lo, hi, num in (("Y", ly, hy, ny), ("X", lx, hx, nx)):
            step = (hi - lo) // num
            last = step * (num - 1) + lo
            g += "GCELLGRID %s %i DO 2 STEP %i ;\nGCELLGRID %s %i DO %i STEP %i ;\n" % (ax, last, hi - last, ax, lo, num, step)
        k = text.index("END DESIGN")
        k = text.rfind("\n", 0, k) + 1
        text = text[:k] + "\n" + g + text[k:]
    with open(out_def_file, "w") as f:
        f.write(text)
