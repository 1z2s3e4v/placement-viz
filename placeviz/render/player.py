"""Player data for web/index.html: per run, one WebP sprite sheet per view + DRV image + frames.json.

writes <output>/web/data/<placer>/<design>_<mode>/{overlay,placement,congestion}.webp, drv.png, frames.json
(+ <output>/web/gifs/<placer>/<key>/*.gif when the config has `gifs: yes`). Curves are drawn in the browser.
"""
import glob
import json
import os

from PIL import Image

from .. import place, route
from ..web.page import design_labels
from . import frames as fr

COLS = 10
SIZE = (420, 452)  # frame size in the sprite (aspect of frames.FIG)


def limits(cfg, units):
    """Per-design curve limits for the GIFs, over every evaluated run of the design under <output>/runs."""
    lim = {}
    for d in sorted({b.name for _, b in units}):
        L = {}
        for mp in glob.glob(os.path.join(cfg["output"], "runs", "*", "%s_*" % d, "eval", "metrics.json")):
            if os.path.basename(os.path.dirname(os.path.dirname(mp))).rsplit("_", 1)[0] != d:
                continue  # glob also matches designs with a longer name
            m = json.load(open(mp))
            for k in ("hpwl", "overlap", "r_t5", "r_overflow"):
                v = [x[k] for x in m if x.get(k) is not None and (k != "r_overflow" or x[k] > 0)]
                if v:
                    lo, hi = L.get(k, (min(v), max(v)))
                    L[k] = (min(lo, min(v)), max(hi, max(v)))
        if "hpwl" in L:
            L["hpwl"] = (0, L["hpwl"][1] * 1.05)
        L["overlap"] = (0, max(1.0, L.get("overlap", (0, 1))[1]))
        if "r_t5" in L:
            L["r_t5"] = (0, L["r_t5"][1] * 1.05)
        if "r_overflow" in L:
            L["r_overflow"] = (max(L["r_overflow"][0] * 0.8, 1), L["r_overflow"][1] * 1.25)
        lim[d] = L
    return lim


def sprite(imgs, path):
    rows = (len(imgs) + COLS - 1) // COLS
    sheet = Image.new("RGB", (SIZE[0] * COLS, SIZE[1] * rows), "white")
    for k, im in enumerate(imgs):
        sheet.paste(im.resize(SIZE, Image.LANCZOS), ((k % COLS) * SIZE[0], (k // COLS) * SIZE[1]))
    sheet.save(path, "WEBP", quality=78, method=4)
    return rows


def build_run(cfg, placer, bench, limits=None, force=False):
    run = place.run_dir(cfg, placer, bench)
    rd = route.route_dir(cfg, placer, bench)
    out = os.path.join(cfg["output"], "web", "data", placer, bench.key)
    inputs = [os.path.join(run, f) for f in ("meta.json", "status.json", "eval/metrics.json")] + \
        [os.path.join(rd, f) for f in ("status.json", "vio.csv")]
    if not force and fr.fresh(os.path.join(out, "frames.json"), inputs):
        return "skip (done)"
    meta, frames = fr.load_run(run)
    if not frames:
        return "skip (no evaluated frames)"
    info = fr.design_info(bench)
    rm = dict(placer=placer, label=design_labels([b["name"] for b in cfg["benchmarks"]])[bench.name], mode=bench.mode)
    drv = fr.load_drv(cfg, placer, bench)
    os.makedirs(out, exist_ok=True)
    rows = {}
    rows["placement"] = sprite([fr.placement_frame(rm, info, f, run) for f in frames], os.path.join(out, "placement.webp"))
    rows["congestion"] = sprite([fr.cong_frame(rm, info, f, run) for f in frames], os.path.join(out, "congestion.webp"))
    rows["overlay"] = sprite([fr.cong_frame(rm, info, f, run, overlay=True) for f in frames],
                             os.path.join(out, "overlay.webp"))
    fr.drv_frame(rm, info, frames[-1], run, drv).resize(SIZE, Image.LANCZOS).save(os.path.join(out, "drv.png"))
    last = frames[-1]
    data = dict(placer=placer, design=bench.name, mode=bench.mode, crashed=bool(meta.get("crashed")),
                n=len(frames), cols=COLS, rows=rows, w=SIZE[0], h=SIZE[1],
                drv=drv[1], drv_status=drv[0],
                final=dict(hpwl=last["hpwl"], r_t5=last.get("r_t5"), r_overflow=last.get("r_overflow"),
                           pat_r_t5=last.get("pat_r_t5"), pat_r_overflow=last.get("pat_r_overflow")),
                frames=[dict(iter=f["iter"], stage=f["stage"] + (" " + f["note"] if f.get("note") else ""),
                             hpwl=f["hpwl"], overlap=f["overlap"],
                             t5=f.get("pat_r_t5", f.get("r_t5")), ovfl=f.get("pat_r_overflow", f.get("r_overflow")))
                        for f in frames])
    if cfg.get("gifs"):
        from . import gifs
        gifs.build_run(cfg, placer, bench, rm, info, run, frames, drv, (limits or {}).get(bench.name, {}))
    with open(os.path.join(out, "frames.json.part"), "w") as f:
        json.dump(data, f)
    os.replace(os.path.join(out, "frames.json.part"), os.path.join(out, "frames.json"))
    return "%d frames" % len(frames)
