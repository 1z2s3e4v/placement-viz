"""Panels of one run's frames (placement, routable congestion, overlay, DRV), shared by the player sprites and GIFs.

Reads runs/<placer>/<key>/{meta.json, frames/, eval/metrics.json, eval/maps/} and route/<placer>/<key>/vio.csv.
Only frames with an eval entry are drawn (the eval stage selects them); the congestion map is optional per frame.
The DRV map exists only for the final placement.
"""
import io
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.collections import PatchCollection  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from PIL import Image  # noqa: E402

from .. import route  # noqa: E402
from ..lefdef import Def, Lef, row_height  # noqa: E402
from ..snapshot import load_npz  # noqa: E402

CONG_VMAX = 1.5  # congestion ratio colour scale, identical for every run
FIG = (5.2, 5.6)
DPI = 90


def fresh(out, inputs):
    """True when `out` exists and is not older than any existing input (stage idempotence)."""
    return os.path.exists(out) and all(os.path.getmtime(out) >= os.path.getmtime(i) for i in inputs
                                       if os.path.exists(i))


def design_info(bench):
    """Die box, component sizes (DBU, N orientation), macro / fixed masks, in DEF COMPONENTS (= frame) order."""
    lef, d = Lef(bench.lef), Def(bench.deff, nets=False)
    comps = list(d.comps.values())
    w = np.array([lef.macros[c.macro]["size"][0] * d.dbu for c in comps])
    h = np.array([lef.macros[c.macro]["size"][1] * d.dbu for c in comps])
    return dict(die=d.die, w=w, h=h, macro=h > row_height(lef, d) + 1,
                fixed=np.array([c.status == "FIXED" for c in comps]), dbu=d.dbu)


def load_run(run):
    meta = json.load(open(os.path.join(run, "meta.json")))
    mp = os.path.join(run, "eval", "metrics.json")
    by_file = {m["file"]: m for m in json.load(open(mp))} if os.path.exists(mp) else {}
    frames = []
    for f in meta["frames"]:
        m = by_file.get(f["file"])
        if m is None:
            continue
        cm = os.path.join(run, "eval", "maps", os.path.basename(f["file"]))
        frames.append(dict(f, **{k: v for k, v in m.items() if k not in f}, map=cm if os.path.exists(cm) else None))
    # label frames that re-show an earlier placement (Xplace GP_end restores the best routability round; NTUplace's
    # CR_end/GP_end/LG can be identical)
    seen = {}
    for f in frames:
        z = load_npz(os.path.join(run, f["file"]))
        key = hash(z["x"].tobytes() + z["y"].tobytes())
        if key in seen and seen[key] != f["stage"]:
            f["note"] = "= %s" % seen[key]
        seen.setdefault(key, f["stage"])
    return meta, frames


def load_drv(cfg, placer, bench):
    """-> (status text, #DRV | None, (n, 4) boxes | None)."""
    st = route.status(cfg, placer, bench)
    if st["placement"] != "ok":  # no final.def (a placer that exits non-zero after writing one is still routed)
        return "no placement (%s)" % st["placement"], None, None
    s = st["drv"]
    vio = os.path.join(route.route_dir(cfg, placer, bench), "vio.csv")
    if s == "ok" and os.path.exists(vio):
        boxes = np.array([r[2:] for r in route.load_vio(vio)], dtype=float).reshape(-1, 4)
        return "ok", len(boxes), boxes
    return (s if s in ("not run", "missing") else "%s %s" % ((cfg.get("dr") or {}).get("name", "DR"), s)), None, None


def macro_patches(info, x, y, fill=True):
    pats = []
    for i in np.where(info["macro"])[0]:
        movable = not info["fixed"][i]
        pats.append(Rectangle((x[i], y[i]), info["w"][i], info["h"][i],
                              facecolor=("#f4a259" if movable else "#9aa5b1") if fill else "none",
                              edgecolor="#b5651d" if movable else "#52606d", lw=0.8, alpha=0.85 if fill else 1))
    return PatchCollection(pats, match_original=True)


def setup_ax(ax, info):
    xl, yl, xh, yh = info["die"]
    ax.set_xlim(xl, xh)
    ax.set_ylim(yl, yh)
    ax.set_aspect("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    for s in ax.spines.values():
        s.set_color("#888")


def fig_to_img(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI)
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def title(fig, rm, line2):
    """rm = {placer, label (design), mode}."""
    fig.suptitle("%s  %s  %s\n%s" % (rm["placer"], rm["label"], "mixed-size" if rm["mode"] == "mov" else "fixed macro",
                                     line2), fontsize=9, y=0.985, family="monospace")


def frame_xy(f, run):
    z = load_npz(os.path.join(run, f["file"]))
    return z["x"].astype(float), z["y"].astype(float)


def draw_cells(ax, info, x, y, color="#1d4e89", alpha=0.6):
    std = ~info["macro"]
    ax.scatter(x[std] + info["w"][std] / 2, y[std] + info["h"][std] / 2, s=0.8, c=color, alpha=alpha, lw=0,
               rasterized=True)


def placement_frame(rm, info, f, run):
    x, y = frame_xy(f, run)
    fig, ax = plt.subplots(figsize=FIG)
    setup_ax(ax, info)
    draw_cells(ax, info, x, y)
    ax.add_collection(macro_patches(info, x, y))
    stage = f["stage"] + (" " + f["note"] if f.get("note") else "")
    title(fig, rm, "iter %5d  %-11s HPWL %.3e  overlap %.3f" % (f["iter"], stage[:22], f["hpwl"], f["overlap"]))
    fig.subplots_adjust(left=0.02, right=0.98, bottom=0.02, top=0.90)
    return fig_to_img(fig)


def cong_frame(rm, info, f, run, overlay=False, drv=None):
    x, y = frame_xy(f, run)
    fig, ax = plt.subplots(figsize=FIG)
    setup_ax(ax, info)
    if overlay:
        draw_cells(ax, info, x, y, color="#444", alpha=0.35)
    im = None
    if f.get("map"):
        m = load_npz(f["map"])
        gx, gy = m["gx"], m["gy"]
        cmap = plt.get_cmap("inferno").copy()
        cmap.set_bad("#bdbdbd")  # fully blocked gcells (macro / PG obstructions in both directions)
        rc = np.ma.masked_invalid(m["rcong"].astype(float).T)
        im = ax.imshow(rc, origin="lower", extent=(gx[0], gx[-1], gy[0], gy[-1]), cmap=cmap,
                       vmin=0, vmax=CONG_VMAX, alpha=0.65 if overlay else 1.0, interpolation="nearest")
    else:
        ax.text(0.5, 0.5, "no congestion map", transform=ax.transAxes, ha="center", va="center", fontsize=11,
                color="#b00")
    ax.add_collection(macro_patches(info, x, y, fill=False))
    line = "iter %5d  overflow %.0f  top5 %.1f%%%s" % (f["iter"], f.get("r_overflow") or 0, f.get("r_t5") or 0,
                                                      " (GR+RRR)" if f.get("rrr_iters") else "")
    if overlay:
        line = "iter %d  HPWL %.3e  ovl %.3f  top5 %.1f%%" % (f["iter"], f["hpwl"], f["overlap"], f.get("r_t5") or 0)
        if drv is not None:
            status, n, boxes = drv
            if boxes is not None:
                ax.scatter((boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2, s=1.2, c="#00e5ff",
                           lw=0, alpha=0.8, rasterized=True)
                line += "  #DRV %d" % n
            else:
                line += "  DRV: %s" % status
    title(fig, rm, line)
    fig.subplots_adjust(left=0.02, right=0.80, bottom=0.02, top=0.90)
    if im is not None:
        colorbar(fig, im, "routable congestion\n(net demand / remaining capacity)", [0, 0.5, 1.0, 1.5],
                 extend="max", blocked=True)
    return fig_to_img(fig)


def colorbar(fig, im, label, ticks, ticklabels=None, extend="neither", blocked=False):
    """Colour bar on the right of the map axes (fixed position so every frame lines up)."""
    cax = fig.add_axes([0.825, 0.12 if blocked else 0.06, 0.03, 0.72 if blocked else 0.78])
    cb = fig.colorbar(im, cax=cax, ticks=ticks, extend=extend)
    if ticklabels:
        cb.ax.set_yticklabels(ticklabels)
    cb.ax.tick_params(labelsize=7)
    cb.set_label(label, fontsize=7)
    if blocked:  # gray = fully blocked gcells (masked in the map)
        bx = fig.add_axes([0.825, 0.035, 0.03, 0.035])
        bx.set_facecolor("#bdbdbd")
        bx.set_xticks([])
        bx.set_yticks([])
        fig.text(0.865, 0.052, "blocked", fontsize=7, va="center")


def drv_frame(rm, info, f, run, drv):
    x, y = frame_xy(f, run)
    status, n, boxes = drv
    fig, ax = plt.subplots(figsize=FIG)
    setup_ax(ax, info)
    ax.add_collection(macro_patches(info, x, y, fill=False))
    xl, yl, xh, yh = info["die"]
    if boxes is not None and n:
        H, xe, ye = np.histogram2d((boxes[:, 0] + boxes[:, 2]) / 2, (boxes[:, 1] + boxes[:, 3]) / 2, bins=64,
                                   range=[[xl, xh], [yl, yh]])
        Hm = np.ma.masked_equal(H, 0)
        im = ax.imshow(np.ma.log10(Hm.T), origin="lower", extent=(xl, xh, yl, yh), cmap="viridis", vmin=0, vmax=3,
                       interpolation="nearest")
        line = "final (after DP)  #DRV %d" % n
    else:
        im = None
        line = "final (after DP)  DRV: %s" % (status if n is None else "0")
        if n is None:
            ax.text(0.5, 0.5, status, transform=ax.transAxes, ha="center", va="center", fontsize=11, color="#b00")
    title(fig, rm, line)
    fig.subplots_adjust(left=0.02, right=0.80, bottom=0.02, top=0.90)
    if im is not None:
        colorbar(fig, im, "DRVs per bin (white = 0)\nbin %.1f x %.1f um" % (
            (xh - xl) / 64 / info["dbu"], (yh - yl) / 64 / info["dbu"]), [0, 1, 2, 3], ["1", "10", "100", "1k+"],
            extend="max")
    return fig_to_img(fig)
