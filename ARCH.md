# placeviz: architecture

From a small config, placeviz runs placers (user-supplied commands), a GR router and a DR router on LEF/DEF
benchmarks (optionally in fixed-macro and mixed-size modes), evaluates every placement snapshot, and produces one web
page (tabs overlay / placement / congestion with the iteration player, and the `final` tab with pins / nets / GR /
DR / DRV toggles). No placer source code is shipped.

## Package layout (repo root = this folder)

```
pyproject.toml            # package "placeviz", console script `placeviz`; deps: numpy, pillow, matplotlib, pyyaml
config.example.yaml
placeviz/
  cli.py                  # placeviz run <config.yaml> [--stages place,eval,gr,dr,render,final,page] [--only <placer>/<design>_<mode>] [--jobs N]
                          # placeviz page <db>   (rebuild page only)   placeviz convert-defs <dir> ...   placeviz check <run dir>
  config.py               # load + validate; "no" (or false/null) disables a stage
  bench.py                # benchmark list; mode fix = input DEF; mode mov = macros FIXED->PLACED (I/O pins untouched)
  snapshot.py             # (1) Writer API for placers; (2) converter from per-iteration DEF dumps to frames
  place.py                # run placer command template per (placer, benchmark, mode); collect final.def + snapshots
  route.py                # GR / DR command templates; macro orientation restore; vio.csv handling; status
  lefdef.py               # shared LEF/DEF parsers, orientation transforms, DEF rewrites, HPWL
  evaluate.py             # per-frame HPWL, overlap, congestion backend dispatch; metrics.json + maps
  congestion/rudy.py      # built-in, pure numpy (default): see below
  congestion/ggr.py       # optional: Xplace GPU router (config: congestion: {backend: ggr, xplace: <Xplace dir>})
  render/frames.py        # panel drawing   render/player.py (sprites)   render/gifs.py
  render/final.py         # final-tab data
  web/page.py + web/template.html + web/final_tab.js   # page generator
tools/drcu_vio_dump.patch + tools/README.md (how to build Dr.CU with the violation dump; licence notice)
docs/SNAPSHOT_FORMAT.md   # what a placer must write
docs/img/                 # README gallery
tests/                    # fast self-tests on tiny synthetic data (no GPU, no external binaries)
```

## Config (YAML)

```yaml
output: /abs/path/to/db            # everything is written here (runs/, route/, web/)
benchmarks:                        # LEF/DEF; lef may be a list (tech first)
  - {name: mgc_fft_a, lef: /path/mgc_fft_a.lef, def: /path/mgc_fft_a.def}
modes: [fix, mov]                  # mov = all FIXED components that are macros (taller than a row) become PLACED
placers:                           # or: no   (then existing runs in output/ are used)
  - name: myplacer
    cmd: "python /path/run.py --lef {lef} --def {def} --out {out_def} --snap {snap_dir} --mode {mode}"
gr:                                # or: no
  name: cugr
  cmd: "/path/iccad19gr -lef {lef} -def {def} -output {guide} -threads 8"
dr:                                # or: no
  name: drcu
  cmd: "/path/ispd19dr -lef {lef} -def {def} -guide {guide} -output {dr_def} -threads 8"
  vio_csv: "{route_dir}/vio.csv"   # optional: violation boxes (type,layer,xl,yl,xh,yh) for the DRV map
  env: {DRCU_VIO_DUMP: "{route_dir}/vio.csv"}   # optional env for the command
congestion: rudy                   # rudy | ggr | no ; ggr needs {backend: ggr, xplace: /path/Xplace}
max_frames: 100
jobs: 2
```
Placeholders: {lef} (space-joined if several), {lef0} (first), {def} (input DEF of the mode), {out_def},
{snap_dir}, {run_dir}, {mode}, {design}, {placer}, {guide}, {dr_def}, {route_dir}, {threads}. Commands run with
cwd = run dir, stdout/stderr to run.log / gr.log / dr.log; non-zero exit is recorded as status, never aborts the batch.
Before GR/DR the placed DEF gets the macros back to their input orientation (restore_orient) and a GCELLGRID if
missing (needed by CU-GR). Stages are idempotent (skip if outputs exist unless --force).

## Placer contract (docs/SNAPSHOT_FORMAT.md)

A placer must write the final placement to {out_def}. Optional snapshots in {snap_dir}, either
(a) `placeviz.snapshot.Writer(snap_dir, components_from_def=...)` → `w.frame(x, y, iter=, stage=, orient=None)`
    (lower-left DBU, DEF COMPONENTS order; the writer also accepts a name->(x,y) dict), or
(b) any DEF dumps named `<iter>[_<stage>].def` (e.g. `000120_cellGP.def`), converted by placeviz.
Without snapshots the run has a single frame (the final placement).

## Congestion backends (metric definitions: placeviz/evaluate.py docstring)

- `ggr`: Xplace GPU global router (pattern routing per frame, + 1 maze RRR on the final frame; routable-resource
  metrics r_overflow / r_t5, plus pat_* for the final frame). Needs an Xplace build with the gpugr extension.
- `rudy` (default, CPU, no extra deps): gcell grid from the DEF rows (default ~ 4 rows per gcell, capped at 256 per
  side); per-layer track capacity from LEF (PITCH / DIRECTION / TRACKS in DEF if present); blockage = LEF OBS of
  components at their frame positions (plus SPECIALNETS stripes) subtracting capacity per layer; demand = RUDY of each
  net's pin bbox (wire density (w+h)/(w*h) * area overlap) split H/V; produce the same 2-D maps (dmdH/V, capH/V, fixH/V,
  rcong) and metrics (r_overflow, r_ovfl_edges, r_t5). It is an estimate, not a router.

## Outputs (db = config.output)

`runs/<placer>/<design>_<mode>/` frames/, meta.json, final.def, run.log, eval/ (metrics.json, maps/)
`route/<placer>/<design>_<mode>/` eval.def, in.def, gr.guide, dr.def, vio.csv, gr.log, dr.log, status.json
`web/index.html`, `web/data/` (sprites, all.js), `web/final/` (index.js, tiles, overview), `web/gifs/` (optional)
The page must work from file:// (no fetch).

## Must-keep behaviours

Iteration-based progress (100% = max final iter of the design's runs), speed in iter/s, shared controls, 4 columns
(fixed, mixed-size, curves, DRV), colour bars, routable congestion with gray blocked cells, final tab LOD with gzip
tiles + DecompressionStream, synchronized zoom, deep links, hashchange reload, macro orientation policy, minarea dedupe,
Dr.CU exit-134-after-report = ok. If a design has only one mode, the page shows just that column.
Generic, not hard-coded: any number of placers (rows, in config order, display name = placer name), any subset of
modes, any benchmark names, any die size/aspect, any number of metal layers (colours cycle beyond 5), designs with or
without macros, LEF/DEF with tech+cell LEF split or merged, runs whose GR/DR/placer failed (status shown).

## Interfaces

Shared objects (all picklable; the CLI runs units in a multiprocessing Pool):
- `cfg` = `placeviz.config.load(path)`: dict with `output` (abs), `benchmarks` [{name, lef: [abs..], def}], `modes`,
  `placers` [{name, cmd|None, env}] or None, `gr` / `dr` {name, cmd, env, vio_csv?} or None,
  `congestion` {backend: rudy|ggr, ...} or None, `max_frames`, `jobs`, `threads`. Saved as `<output>/config.json`.
- `bench` = `placeviz.bench.Bench(name, mode, lef: tuple, deff: input DEF of the mode, fix_def: config DEF)`, `.key`
  = `<design>_<mode>`. `placer` = placer name (str). Unit key = `<placer>/<design>_<mode>`.
- Paths: `place.run_dir(cfg, placer, bench)`, `route.route_dir(cfg, placer, bench)`.
- Status: `<run>/status.json` {"place": {status, rc, seconds, cmd, note}}; `<route>/status.json` {"gr": {...},
  "dr": {status, rc, seconds, cmd, tool, drv: rows after minarea dedupe | None, note}}. Viewer strings:
  `route.status(cfg, placer, bench)` -> {placement, gr, dr, drv} ("ok", "placer exit 1", "crashed (signal 11)",
  "not run", "missing", ...). DRV boxes: `route.load_vio(path)` -> [[type, layer, xl, yl, xh, yh]] (minarea dedupe).
  Router logs are `gr.log` / `dr.log`.
- meta.json (docs/SNAPSHOT_FORMAT.md) extra keys: `crashed`, `snapshots` (writer | def dumps | none), `input_def`, `final_check`.
  Frames from DEF dumps always carry `orient`; dumps without a stage get `cellGP` (fix) / `mixedGP` (mov); a frame
  appended from final.def (last snapshot != final.def, or no snapshots) has stage `final` (treat like `DP`).
  `filled` = number of components missing/UNPLACED in a dump (took the previous / input / die-centre position).
- LEF/DEF: `placeviz.lefdef` (`Lef`, `Def`, `orient`, `orient_rect`, `def_orient_rect`, `hpwl`, `ORIENTS`, ...).
  `Def.comps` keeps DEF order = frame order. `Lef.macros[m]["pins"][p]["ports"][layer]` = rects (um).

Functions the CLI calls (each returns a short status string; raise on error, the CLI records it per unit):
- `placeviz.evaluate.run(run_dir, bench, cfg, force=False)` -> writes `<run>/eval/metrics.json` + `eval/maps/`;
  resumable; frame selection with `cfg["max_frames"]`; congestion backend from `cfg["congestion"]` (None = skip maps).
- `placeviz.render.player.limits(cfg, units) -> limits` once per render stage (units = [(placer, bench)] of this
  invocation; scales should cover every run of a design under `<output>/runs` so `--only` does not change them),
  then `placeviz.render.player.build_run(cfg, placer, bench, limits, force=False)` -> `web/data/<placer>/<key>/`
  (+ optional `web/gifs/`).
- `placeviz.render.final.build_run(cfg, placer, bench, force=False)` -> `web/final/<placer>/<key>/`; after all units
  `placeviz.render.final.write_index(cfg) -> int` (runs in `web/final/index.js`).
- `placeviz.web.page.build(cfg, units) -> str` (path of `web/index.html`); units = every (placer, bench) in config
  order (placer-major, then benchmark, then mode). `placeviz page <output>` calls it with `<output>/config.json`.
A stage whose module is missing is reported as "not available" and skipped.

Eval: `placeviz/evaluate.py` + `placeviz/congestion/{__init__,rudy,ggr}.py`. metrics.json entries carry
`congestion` (backend name or null; a backend change re-evaluates) and `t_cpu` / `t_cong`. Both backends
write the same map keys; r_* for both; gr_* (router metrics) and pat_r_* (last frame, pattern only) for ggr only.
Consumers should read `pat_r_*` with a fallback to `r_*` (rudy has no pat_*).

Integration (configs in examples/): placer / GR / DR commands and the ggr child get this placeviz
first on PYTHONPATH (`place.child_env`), so an adapter can `import placeviz` without installing it. The page drops
the DRV column when `dr: no` and no unit has DR results. examples/adapters/ = command-line glue only (no placer code).
