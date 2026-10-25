"""EXAMPLE snapshot hook for Xplace-Route (Xplace itself is not included): runs Xplace's main() unchanged and wraps a
few of its functions (monkeypatching, read-only) to record frames with placeviz.snapshot.Writer.

usage: python xplace_snapshots.py <xplace dir> <snap_dir> <input.def> <out.def> <every> -- <Xplace main.py args...>

Frames: one every <every> Nesterov iterations (stage mixedGP / cellGP), plus mixedGP_end / macroLG (mixed-size macro
legalization), route_k (start of routability round k), GP_end, LG and DP (with the orientations of Xplace's DP DEF,
which is copied to <out.def>). Coordinates: Xplace's own centre -> lower-left DBU conversion.
"""
import os
import shutil
import sys

import torch

from placeviz.snapshot import Writer, read_components


def to_dbu_lpos(node_pos, data):
    """Xplace's centre (scaled units) -> lower-left DBU conversion (as when it commits positions to its database)."""
    exact = torch.round(node_pos.detach() * data.die_scale + data.die_shift)
    return torch.round(exact - torch.round(data.node_size * data.die_scale) / 2).cpu().numpy()


class Recorder:
    def __init__(self, snap_dir, input_def, out_def, every):
        self.w = Writer(snap_dir, components_from_def=input_def)
        self.out_def, self.every = out_def, every
        self.idx = self.data = self.ps = self.args = None
        self.route_k = 0

    def bind(self, gpdb, data, ps, args):
        name2id = {n: i for i, n in enumerate(gpdb.node_id2node_name())}
        self.idx = [name2id[n] for n in self.w.names]  # KeyError = component mapping bug
        self.data, self.ps, self.args = data, ps, args

    def full_pos(self, mov_node_pos):
        lhs, rhs = self.data.movable_index
        return torch.cat([mov_node_pos[lhs:rhs].detach(), self.data.node_pos[rhs:]], dim=0)

    def save(self, node_pos, stage, it, hpwl=None, overflow=None, orient=None):
        lpos = to_dbu_lpos(node_pos, self.data)[self.idx]
        self.w.frame(lpos[:, 0], lpos[:, 1], iter=it, stage=stage, orient=orient, placer_hpwl=hpwl,
                     placer_overflow=overflow)

    def exact_hpwl(self, node_pos):
        from src.evaluator import get_obj_hpwl
        return get_obj_hpwl(node_pos, self.data, self.args).item()


def install(rec):
    import src.detail_placement as dp
    import src.run_placement_nesterov as rpn
    from src.param_scheduler import ParamScheduler

    orig_step = ParamScheduler.step

    def step(self, hpwl, overflow, node_pos, data):
        if rec.idx is not None and self.iter % rec.every == 0:
            stage = "mixedGP" if self.enable_mixed_size and not self.zero_macro_grad else "cellGP"
            rec.save(rec.full_pos(node_pos), stage, self.iter, hpwl, overflow)
        return orig_step(self, hpwl, overflow, node_pos, data)
    ParamScheduler.step = step

    orig_gp = rpn.global_placement_main

    def gp_main(gpdb, rawdb, ps, data, args, logger, params, gputimer=None):
        rec.bind(gpdb, data, ps, args)
        out = orig_gp(gpdb, rawdb, ps, data, args, logger, params, gputimer)
        node_pos, _, gp_hpwl, overflow = out[:4]
        rec.save(node_pos, "GP_end", ps.iter, gp_hpwl, overflow)
        return out
    rpn.global_placement_main = gp_main

    orig_ml = rpn.macro_legalization_main

    def macro_lg(node_pos, data, args, logger, *a, **kw):
        rec.save(node_pos, "mixedGP_end", rec.ps.iter, rec.exact_hpwl(node_pos))
        out = orig_ml(node_pos, data, args, logger, *a, **kw)  # changes node_pos in place
        rec.save(node_pos, "macroLG", rec.ps.iter, rec.exact_hpwl(node_pos))
        return out
    rpn.macro_legalization_main = macro_lg

    orig_ri = rpn.route_inflation

    def route_infl(args, logger, data, rawdb, gpdb, ps, mov_node_pos, *a, **kw):
        r = ps.recorder
        rec.save(rec.full_pos(mov_node_pos), "route_%d" % rec.route_k, ps.iter,
                 r.hpwl[-1] if r.hpwl else None, r.overflow[-1] if r.overflow else None)
        rec.route_k += 1
        return orig_ri(args, logger, data, rawdb, gpdb, ps, mov_node_pos, *a, **kw)
    rpn.route_inflation = route_infl

    orig_lg = dp.run_lg

    def run_lg(node_pos, data, args, logger):
        out = orig_lg(node_pos, data, args, logger)
        rec.save(out, "LG", rec.ps.iter, rec.exact_hpwl(out))
        return out
    dp.run_lg = run_lg

    orig_wp = dp.write_placement

    def write_placement(node_pos, gpdb, id, data, args, logger):
        prefix = orig_wp(node_pos, gpdb, id, data, args, logger)
        if id == "dp":
            orient = {n: c.orient for n, c in read_components(prefix + ".def").items()}
            rec.save(node_pos, "DP", rec.ps.iter + 1, rec.exact_hpwl(node_pos), orient=orient)
            shutil.copyfile(prefix + ".def", rec.out_def)
        return prefix
    dp.write_placement = write_placement


if __name__ == "__main__":
    xplace, snap, input_def, out_def, every = sys.argv[1:6]
    assert sys.argv[6] == "--"
    rec = Recorder(os.path.abspath(snap), os.path.abspath(input_def), os.path.abspath(out_def), int(every))
    sys.path.insert(0, xplace)
    os.chdir(xplace)  # Xplace loads its third-party tools by relative path
    sys.argv = ["main.py"] + sys.argv[7:]
    import main as xmain
    install(rec)
    xmain.main()
