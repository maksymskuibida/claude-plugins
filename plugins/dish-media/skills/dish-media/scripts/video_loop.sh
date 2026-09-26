#!/usr/bin/env bash
# One clip -> one tablet-ready loop, with the session look.
#
#   video_loop.sh SESSION.json IN.mov OUT.mp4 [revolution|pingpong] [START_S] [DURATION_S]
#
# Thin wrapper over video_grade.py (which also takes whole folders):
# revolution mode finds the period itself when DURATION_S is omitted.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ $# -lt 3 ]; then
  sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
fi
session="$1"; in="$2"; out="$3"; mode="${4:-revolution}"; start="${5:-1.0}"; duration="${6:-}"
args=(--session "$session" --in "$in" --out "$out" --loop "$mode" --start "$start")
if [ -n "$duration" ]; then
  args+=(--duration "$duration")
fi
qa_dir="$(dirname "$(dirname "$(dirname "$out")")")/qa"   # P/out/loops/x.mp4 -> P/qa
if [ -d "$qa_dir" ]; then
  args+=(--qa "$qa_dir")
fi
exec python3 "$here/video_grade.py" "${args[@]}"
