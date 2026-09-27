#!/usr/bin/env bash
# Regression suite for the dish-media scripts. Renders a synthetic shoot in a
# temp dir, runs every script end to end (twice, for determinism) and asserts.
#
#   tests/run_tests.sh [--keep] [--skip-cutout]
#
# --keep leaves the temp dir behind and prints its path. --skip-cutout runs
# without rembg (for machines without the model); DISH_MEDIA_SKIP_CUTOUT=1
# does the same. Needs ffmpeg/ffprobe, Pillow, numpy, OpenCV; shellcheck if
# present is run over the shell scripts.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
scripts="$(dirname "$here")"
keep=0
skip_cutout="${DISH_MEDIA_SKIP_CUTOUT:-0}"
for a in "$@"; do
  case "$a" in
    --keep) keep=1 ;;
    --skip-cutout) skip_cutout=1 ;;
    *) echo "unknown option $a" >&2; exit 2 ;;
  esac
done
if [ "$skip_cutout" = 0 ] && ! python3 -c "import rembg" >/dev/null 2>&1; then
  echo "rembg not importable: cutout steps skipped (pip install 'rembg[cpu]' to cover them)"
  skip_cutout=1
fi

echo "== static checks"
n_py=0
for f in "$scripts"/*.py "$scripts"/tests/*.py; do
  python3 -m py_compile "$f"
  n_py=$((n_py + 1))
done
echo "py_compile: $n_py files ok"
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck "$scripts"/*.sh "$scripts"/tests/*.sh
  echo "shellcheck: ok"
else
  echo "shellcheck: not installed, skipped"
fi

root="$(mktemp -d "${TMPDIR:-/tmp}/dish-media-tests.XXXXXX")"
cleanup() { if [ "$keep" = 1 ]; then echo "kept: $root"; else rm -rf "$root"; fi; }
trap cleanup EXIT
P="$root/run1"
Q="$root/run2"

echo "== fixtures -> $P"
python3 "$here/make_fixtures.py" --out "$P" --hdr 2>/dev/null
mkdir -p "$Q/raw"
cp -R "$P/raw/." "$Q/raw/"
cp "$P/fixtures.json" "$Q/fixtures.json"

run_pipeline() {  # PROJECT SESSION_SOURCE(optional)
  local D="$1" src="${2:-}"
  local S="$scripts"
  python3 "$S/ingest.py" --in "$D/raw/photos" --out "$D/work/photos" --manifest "$D/work/manifest.csv" --video-in "$D/raw/video" --qa "$D/qa" 2>/dev/null
  python3 "$S/measure.py" "$D/work/photos/dish-001.jpg" --card 180,1380,360,300 --out "$D/work/measure/card.json" 2>/dev/null
  python3 "$S/measure.py" "$D/work/photos/dish-001.jpg" --auto --out "$D/work/measure/card-auto.json" --preview "$D/qa/card-box.jpg" 2>/dev/null
  python3 "$S/measure.py" "$D/work/photos/dish-001.jpg" --auto --white --out "$D/work/measure/plate-auto.json" --preview "$D/qa/plate-box.jpg" 2>/dev/null
  if [ -n "$src" ]; then
    mkdir -p "$D/work" && cp "$src" "$D/work/session.json"
  else
    python3 "$S/calibrate.py" --measure "$D/work/measure/card.json" --out "$D/work/session.json" --crop-ratio none --warmth 0 \
      --background '#F6F4EF' --override dish-005 straighten_deg=2.0 --note "regression fixture" 2>/dev/null
    python3 "$S/calibrate.py" --measure "$D/work/measure/card.json" --out "$D/work/session-warm.json" --crop-ratio none 2>/dev/null
    python3 "$S/grade.py" --session "$D/work/session-warm.json" --in "$D/work/photos/dish-001.jpg" --out "$D/out/photos-warm" 2>/dev/null
  fi
  python3 "$S/grade.py" --session "$D/work/session.json" --variants "$D/work/photos/dish-001.jpg" --out "$D/qa/variants" 2>/dev/null
  python3 "$S/contact_sheet.py" --in "$D/qa/variants" --out "$D/qa/variants.jpg" --variants 2>/dev/null
  python3 "$S/grade.py" --session "$D/work/session.json" --in "$D/work/photos" --out "$D/out/photos" --qa "$D/qa" --workers 2 2>/dev/null
  python3 "$S/contact_sheet.py" --in "$D/out/photos" --out "$D/qa/sheets" --reference dish-001 --stats "$D/qa/grade_stats.json" 2>/dev/null
  # a per-file override rendered on its own, the way an outlier is fixed
  python3 "$S/calibrate.py" --from "$D/work/session.json" --out "$D/work/session-override.json" --override dish-004 exposure_ev=-0.5 2>/dev/null
  python3 "$S/grade.py" --session "$D/work/session-override.json" --in "$D/work/photos/dish-004.jpg" --out "$D/out/photos-override" 2>/dev/null
  # the same outlier fixed from its own card measurement (--file-measure), and a non-dish excluded
  python3 "$S/measure.py" "$D/work/photos/dish-004.jpg" --card 180,1380,360,300 --out "$D/work/measure/dish-004.json" 2>/dev/null
  python3 "$S/calibrate.py" --from "$D/work/session.json" --out "$D/work/session-filemeasure.json" \
    --file-measure dish-004 "$D/work/measure/dish-004.json" --wb-strength 1 --anchor patch \
    --exclude dish-002 --override dish-003 crop_box=0.30,0.10,0.45,0.80 2>/dev/null
  # the snapshot defaults: damped gains, exposure from the frame's highlights
  python3 "$S/calibrate.py" --from "$D/work/session.json" --out "$D/work/session-snapshot.json" \
    --file-measure dish-004 "$D/work/measure/dish-004.json" 2>/dev/null
  python3 "$S/grade.py" --session "$D/work/session-snapshot.json" --in "$D/work/photos/dish-004.jpg" --out "$D/out/photos-snapshot" --no-vessel-check 2>/dev/null
  python3 "$S/grade.py" --session "$D/work/session-filemeasure.json" --in "$D/work/photos" --out "$D/out/photos-filemeasure" --qa "$D/qa-filemeasure" 2>/dev/null
  if [ "$skip_cutout" = 0 ]; then
    python3 "$S/cutout.py" --session "$D/work/session.json" --in "$D/out/photos" --out "$D/out/cutouts" --mattes "$D/work/mattes" --qa "$D/qa" 2>/dev/null
    python3 "$S/contact_sheet.py" --in "$D/out/cutouts" --out "$D/qa/sheets-cutouts" --stats "$D/qa/cutout_stats.json" 2>/dev/null
  fi
  python3 "$S/loop_period.py" "$D/raw/video/turn-001.mov" --out "$D/qa/period.json" >/dev/null 2>&1
  # the batch: turn-001 loops, hdr-001 must fail per clip (HDR) without stopping the batch
  python3 "$S/video_grade.py" --session "$D/work/session.json" --in "$D/raw/video" --out "$D/out/loops" --qa "$D/qa" >/dev/null 2>&1 || true
  cp "$D/qa/flags.json" "$D/qa/flags-after-batch.json"   # the HDR failure flag, before the repair below clears it
  bash "$S/video_loop.sh" "$D/work/session.json" "$D/raw/video/turn-001.mov" "$D/out/loops/turn-001-pp.mp4" pingpong 1.0 5.0 >/dev/null 2>&1
  python3 "$S/video_grade.py" --session "$D/work/session.json" --in "$D/raw/video/turn-001.mov" --out "$D/out/loops/turn-001-rev.mp4" --reverse --qa "$D/qa" >/dev/null 2>&1
  mkdir -p "$D/work/sdr"
  bash "$S/tonemap_hdr.sh" "$D/raw/video/hdr-001.mov" "$D/work/sdr/hdr-001.mov" 2>/dev/null
  python3 "$S/video_grade.py" --session "$D/work/session.json" --in "$D/work/sdr/hdr-001.mov" --out "$D/out/loops/hdr-001.mp4" --loop pingpong --duration 2 --qa "$D/qa" >/dev/null 2>&1
  for clip in turn-001 turn-001-pp turn-001-rev hdr-001; do
    python3 "$S/frame_grid.py" "$D/out/loops/$clip.mp4" --out "$D/qa/grids/$clip.jpg" --qa "$D/qa" >/dev/null 2>&1
  done
  python3 "$S/qa_report.py" --project "$D" >/dev/null 2>&1 || true
  python3 "$S/deliver.py" --project "$D" --force >/dev/null 2>&1
}

echo "== run 1"
time run_pipeline "$P"
echo "== run 2 (same inputs, same session.json)"
mkdir -p "$Q/work"
run_pipeline "$Q" "$P/work/session.json"

echo "== assertions"
extra=()
if [ "$skip_cutout" = 1 ]; then extra+=(--skip-cutout); fi
python3 "$here/check_pipeline.py" "$P" --second-run "$Q" ${extra[@]+"${extra[@]}"}   # bash 3.2 + set -u safe
