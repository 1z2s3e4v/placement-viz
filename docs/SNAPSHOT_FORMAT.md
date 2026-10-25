# Snapshot format

Your placer command runs once per benchmark and mode (cwd = run dir). It must write the final DEF to `{out_def}`.
Snapshots in `{snap_dir}` are optional; without them the run has one frame.

Placeholders (leave unquoted): `{lef}` (all LEFs) `{lef0}` `{def}` `{out_def}` `{snap_dir}` `{run_dir}` `{mode}`
`{design}` `{placer}` `{threads}`. In `mov`, `{def}` has every FIXED macro (taller than a row) set to PLACED.

## A: DEF dumps

One DEF per snapshot: `<iter>[_<stage>].def`, e.g. `000120.def`, `000980_LG.def`.

- Only COMPONENTS are read, matched by name; missing or UNPLACED ones keep their previous position.
- No stage: `cellGP` in `fix`, `mixedGP` in `mov`.
- Convert by hand: `placeviz convert-defs <dir> --def <input.def> --out <dir>`.

## B: Python Writer

```python
from placeviz.snapshot import Writer
w = Writer(snap_dir, components_from_def=input_def)
w.frame(x, y, iter=it, stage="cellGP")            # GP frames
w.frame(x, y, iter=last, stage="DP", orient=o)    # after legalization
```

- `x`, `y`: lower-left corners in DBU, in input-DEF COMPONENTS order (or `x={name: (x, y)}`).
- `orient`: optional, `N S W E FN FS FW FE` or 0-7.
- Writes `frames/frame_NNNNN.npz` (`x`, `y`, `orient`) and `meta.json` after every frame.

## Stages

| stage | meaning |
|---|---|
| `mixedGP` | GP, macros and cells move |
| `cellGP` (default), `GP` | GP, macros fixed |
| anything else (`LG`, `DP`, `macroLG`, ...) | stage boundary, always kept |
| `final` | added by placeviz if the last frame differs from `{out_def}` |

GP frames are thinned to `max_frames` (default 100). `placeviz check <run dir>` validates a run.
