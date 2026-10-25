"""web/index.html: frame player (one shared play / progress / speed control) over web/data/*, plus the final tab.

Bundles web/data/<placer>/<key>/frames.json of the given units into web/data/all.js (`DATA`, plus the status strings
of every unit in `ST`): pages opened from file:// cannot fetch JSON. Layout per view (overlay / placement /
congestion): rows = placers (config order), columns = main view per mode (fixed macro, mixed-size), curves (all
modes on the same axes), final DRV. Tab final: web/final/index.js + tiles (render/final.py), JS in final_tab.js.
Deep link: index.html#d=<design>&v=final&z=6&cx=..&cy=.. (DBU); d may be the button label or the full name.
"""
import json
import os

from .. import route

HERE = os.path.dirname(os.path.abspath(__file__))
MODE_NAME = {"fix": "fixed macro", "mov": "mixed-size"}


def design_labels(names):
    """Button / title labels: with >= 2 benchmarks, names without a shared first word '<word>_' when every name
    keeps another '_' (mgc_fft_a, mgc_fft_b -> fft_a, fft_b; fft_a, fft_b stay). Labels stay unique and non-empty."""
    p = os.path.commonprefix(list(names)) if len(names) > 1 else ""
    p = p[:p.find("_") + 1]
    if not all("_" in n[len(p):].strip("_") for n in names):
        p = ""
    return {n: n[len(p):] for n in names}


def build(cfg, units):
    """units: every (placer name, Bench) in config order. -> path of web/index.html."""
    web = os.path.join(cfg["output"], "web")
    os.makedirs(os.path.join(web, "data"), exist_ok=True)
    data, st = {}, {}
    for placer, b in units:
        k = "%s/%s" % (placer, b.key)
        f = os.path.join(web, "data", k, "frames.json")
        if os.path.exists(f):
            data[k] = json.load(open(f))
        st[k] = route.status(cfg, placer, b)
    with open(os.path.join(web, "data", "all.js"), "w") as f:
        f.write("const DATA=%s;\nconst ST=%s;\n" % (json.dumps(data), json.dumps(st)))
    names = list(dict.fromkeys(b.name for _, b in units))
    modes = [m for m in cfg["modes"] if any(b.mode == m for _, b in units)]
    label = {p["name"]: p.get("label") or p["name"] for p in cfg["placers"] or []}
    conf = dict(pl=[[p, label.get(p, p)] for p in dict.fromkeys(p for p, _ in units)],
                ds=list(design_labels(names).items()), modes=modes, mname=MODE_NAME,
                star=(cfg.get("congestion") or {}).get("backend") == "ggr",
                drv=cfg.get("dr") is not None or any(s["dr"] != "not run" for s in st.values()))  # DRV column
    title = cfg.get("title") or ("placeviz: " + " vs ".join(MODE_NAME[m] for m in modes) if modes else "placeviz")
    html = open(os.path.join(HERE, "template.html"), encoding="utf-8").read()
    js = open(os.path.join(HERE, "final_tab.js"), encoding="utf-8").read()
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;")  # noqa: E731
    html = html.replace("/*FINAL_TAB*/", js).replace("/*CONF*/", json.dumps(conf).replace("</", "<\\/")) \
        .replace("{{TITLE}}", esc(title))
    path = os.path.join(web, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path
