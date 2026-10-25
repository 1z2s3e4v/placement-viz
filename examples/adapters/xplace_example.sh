#!/bin/bash
# EXAMPLE placer adapter for placeviz: runs Xplace-Route (https://github.com/cuhk-eda/Xplace, not included) on one
# LEF/DEF and records snapshot frames with placeviz.snapshot.Writer. Command-line glue only; adapt it to your setup.
#
# usage (placeviz placer cmd):
#   bash xplace_example.sh {lef0} {def} {out_def} {snap_dir} {mode} {design} [benchmark]
# env:
#   XPLACE       Xplace checkout (with its C++ extensions built)                         (required)
#   XPLACE_ENV   optional script to source first (e.g. a conda/mamba env with torch)
#   EVERY        frame cadence in Nesterov iterations (default: fix 8, mov 30)
#   THREADS      --num_threads (default 8)
#   XPLACE_FLAGS routability flags (default: Xplace-Route, "--use_cell_inflate True --final_route_eval True";
#                set to "" for plain wirelength-driven Xplace)
# benchmark: Xplace dataset name (ispd2015_fix by default; e.g. ispd2018). Xplace takes one merged LEF.
set -e
LEF=$1; DEF=$2; OUT_DEF=$3; SNAP=$4; MODE=$5; DESIGN=$6; BM=${7:-ispd2015_fix}
[ -n "$XPLACE" ] || { echo "set XPLACE to the Xplace directory" >&2; exit 2; }
[ -n "$XPLACE_ENV" ] && source "$XPLACE_ENV"
case $MODE in fix) EVERY=${EVERY:-8}; EXTRA=();; mov) EVERY=${EVERY:-30}; EXTRA=(--mixed_size True);; *) exit 2;; esac
RAW=$(dirname "$OUT_DEF")/xplace_raw
mkdir -p "$RAW"
python "$(dirname "$0")/xplace_snapshots.py" "$XPLACE" "$SNAP" "$DEF" "$OUT_DEF" "$EVERY" -- \
  --custom_path "benchmark:$BM,design_name:$DESIGN,lef:$LEF,def:$DEF" --load_from_raw True \
  --detail_placement True ${XPLACE_FLAGS---use_cell_inflate True --final_route_eval True} \
  --result_dir "$RAW" --exp_id "_$MODE" --num_threads "${THREADS:-8}" "${EXTRA[@]}"
