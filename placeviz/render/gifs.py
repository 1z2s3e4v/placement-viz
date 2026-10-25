"""Optional GIFs of one run (config `gifs: yes`): placement, congestion, drv, curves, overlay.

writes <output>/web/gifs/<placer>/<design>_<mode>/*.gif (same panels as the player sprites, plus the curves panel).
"""
import os

import numpy as np
from PIL import Image

from . import frames as fr
from .frames import plt

STAGE_MARK = ("macroLG", "route", "GP_end", "level_end", "LG", "DP", "final")


def curves_frame(rm, frames, k, lim):
    it = np.array([f["iter"] for f in frames])
    series = [("HPWL (DBU)", [f["hpwl"] for f in frames], "#2f6fb0", lim.get("hpwl")),
              ("overlap", [f["overlap"] for f in frames], "#2a9d8f", lim.get("overlap")),
              # pattern-routing values on every frame (a final frame with its own GR + maze RRR is shown as a star)
              ("top5 overflow (%)", [f.get("pat_r_t5", f.get("r_t5")) for f in frames], "#e76f51", lim.get("r_t5")),
              ("GR overflow (log)", [f.get("pat_r_overflow", f.get("r_overflow")) for f in frames], "#8d3b72",
               lim.get("r_overflow"))]
    final_rrr = {"top5 overflow (%)": frames[-1].get("r_t5"), "GR overflow (log)": frames[-1].get("r_overflow")}
    fig, axes = plt.subplots(4, 1, figsize=fr.FIG, sharex=True)
    for ax, (name, vals, col, ylim) in zip(axes, series):
        v = np.array([np.nan if a is None else a for a in vals], dtype=float)
        ax.plot(it[:k + 1], v[:k + 1], color=col, lw=1.2)
        ax.plot(it[k], v[k], "o", color=col, ms=4)
        if k == len(frames) - 1 and final_rrr.get(name) is not None:
            ax.plot(it[k], final_rrr[name], "*", color="#333", ms=7)
        ax.set_xlim(it[0], max(it[-1], it[0] + 1))
        if ylim:
            ax.set_ylim(*ylim)
        if "log" in name and np.nanmax(v, initial=0) > 0:
            ax.set_yscale("log")
        ax.set_ylabel(name, fontsize=7)
        ax.tick_params(labelsize=6)
        for f in frames[:k + 1]:
            if f["stage"].startswith(STAGE_MARK):
                ax.axvline(f["iter"], color="#bbb", lw=0.6, ls="--")
    axes[-1].set_xlabel("iteration", fontsize=7)
    fr.title(fig, rm, "iter %d  %s" % (frames[k]["iter"], frames[k]["stage"]))
    fig.subplots_adjust(left=0.16, right=0.97, bottom=0.07, top=0.90, hspace=0.12)
    return fr.fig_to_img(fig)


def save_gif(imgs, path, ms=140, hold=1500):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    dur = [ms] * len(imgs)
    dur[-1] = hold
    pal = [im.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for im in imgs]
    pal[0].save(path, save_all=True, append_images=pal[1:], duration=dur, loop=0, optimize=True)


def build_run(cfg, placer, bench, rm, info, run, frames, drv, lim):
    out = os.path.join(cfg["output"], "web", "gifs", placer, bench.key)
    save_gif([fr.placement_frame(rm, info, f, run) for f in frames], os.path.join(out, "placement.gif"))
    save_gif([fr.cong_frame(rm, info, f, run) for f in frames], os.path.join(out, "congestion.gif"))
    save_gif([fr.drv_frame(rm, info, frames[-1], run, drv)], os.path.join(out, "drv.gif"))
    save_gif([curves_frame(rm, frames, k, lim) for k in range(len(frames))], os.path.join(out, "curves.gif"))
    ov = [fr.cong_frame(rm, info, f, run, overlay=True) for f in frames]
    ov.append(fr.cong_frame(rm, info, frames[-1], run, overlay=True, drv=drv))
    save_gif(ov, os.path.join(out, "overlay.gif"))
    return out
