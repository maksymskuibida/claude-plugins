#!/usr/bin/env bash
# Check the toolchain and install what is missing, after saying what it will do.
#
#   setup.sh [--check] [--yes] [--model isnet-general-use] [--python /path/to/python3]
#
# Checks ffmpeg/ffprobe and the filters the pipeline uses, `sips` (macOS) or
# pillow-heif, and the Python modules; then offers to `pip install` the
# missing packages into the chosen interpreter and to fetch the rembg model
# (a one-time download of roughly 170 MB). Nothing is installed with --check,
# and nothing is installed without a yes (or --yes).
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="${PYTHON:-python3}"
model="isnet-general-use"
check_only=0
yes=0
while [ $# -gt 0 ]; do
  case "$1" in
    --check) check_only=1 ;;
    --yes|-y) yes=1 ;;
    --model) shift; model="$1" ;;
    --python) shift; py="$1" ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

ok=1
say()  { printf '  %-28s %s\n' "$1" "$2"; }
need() { say "$1" "MISSING  ($2)"; ok=0; }

echo "dish-media setup"
echo "interpreter: $("$py" -c 'import sys; print(sys.executable)' 2>/dev/null || echo "$py (not runnable)")"
echo
echo "tools"
for t in ffmpeg ffprobe; do
  if command -v "$t" >/dev/null 2>&1; then say "$t" "$(command -v "$t")"; else need "$t" "brew install ffmpeg"; fi
done
if command -v ffmpeg >/dev/null 2>&1; then
  filters="$(ffmpeg -hide_banner -filters 2>/dev/null || true)"
  for f in lut3d zscale tonemap reverse concat scale vidstabdetect vidstabtransform; do
    if grep -qE " $f " <<<"$filters"; then say "ffmpeg filter $f" "yes"; else need "ffmpeg filter $f" "reinstall ffmpeg with libzimg/libvidstab; brew's ffmpeg has them"; fi
  done
  if ffmpeg -hide_banner -encoders 2>/dev/null | grep -q libx264; then say "libx264" "yes"; else need "libx264" "ffmpeg built without libx264"; fi
fi
if command -v sips >/dev/null 2>&1; then say "sips (HEIC, ICC)" "$(command -v sips)"; else say "sips" "no (not macOS): pillow-heif will be used for HEIC"; fi
if command -v shellcheck >/dev/null 2>&1; then say "shellcheck (tests only)" "yes"; else say "shellcheck (tests only)" "no, optional"; fi

echo
echo "python modules"
missing=()
check_mod() {  # module pip-name
  if "$py" -c "import $1" >/dev/null 2>&1; then
    say "$1" "$("$py" -c "import $1; print(getattr($1, '__version__', 'ok'))" 2>/dev/null)"
  else
    say "$1" "MISSING  (pip: $2)"; missing+=("$2"); ok=0
  fi
}
check_mod PIL "Pillow>=10.1"
check_mod numpy "numpy>=1.26"
check_mod cv2 "opencv-python-headless>=4.8"
check_mod rembg "rembg[cpu]>=2.0.60"
check_mod onnxruntime "onnxruntime"
if ! command -v sips >/dev/null 2>&1; then check_mod pillow_heif "pillow-heif>=0.16"; fi

model_ready=0
if "$py" -c "import rembg" >/dev/null 2>&1; then
  # rembg >= 2.0.8x keeps models in ~/.rembg/models/<name>/ (REMBG_HOME / XDG_DATA_HOME
  # honoured); older versions and U2NET_HOME use a flat ~/.u2net/<name>.onnx
  if "$py" - "$model" <<'PY' >/dev/null 2>&1
import os, sys
from pathlib import Path
name = sys.argv[1]
roots = []
if os.environ.get("U2NET_HOME"):
    roots.append(Path(os.environ["U2NET_HOME"]))
if os.environ.get("REMBG_HOME"):
    roots.append(Path(os.environ["REMBG_HOME"]) / "models" / name)
if os.environ.get("XDG_DATA_HOME"):
    roots.append(Path(os.environ["XDG_DATA_HOME"]) / "rembg" / "models" / name)
roots += [Path.home() / ".rembg" / "models" / name, Path.home() / ".u2net"]
sys.exit(0 if any(r.is_dir() and any(r.glob(name + "*")) for r in roots) else 1)
PY
  then say "rembg model $model" "downloaded"; model_ready=1; else say "rembg model $model" "not downloaded yet (~170 MB on first use)"; fi
fi

echo
if [ "$ok" = 1 ] && { [ "$model_ready" = 1 ] || ! "$py" -c "import rembg" >/dev/null 2>&1; }; then
  echo "everything is in place"
  exit 0
fi
if [ "$check_only" = 1 ]; then
  echo "missing pieces listed above (--check: nothing installed)"
  exit 1
fi

echo "plan"
if [ ${#missing[@]} -gt 0 ]; then
  echo "  $py -m pip install ${missing[*]}"
  echo "  (versions as in $here/requirements.txt)"
fi
if [ "$model_ready" = 0 ]; then
  echo "  download the rembg model '$model' into ~/.u2net (about 170 MB, once)"
fi
if [ "$ok" = 0 ] && [ ${#missing[@]} -eq 0 ]; then
  echo "  (the missing tools above are not pip packages; install them and re-run)"
fi
if [ "$yes" != 1 ]; then
  if [ -t 0 ]; then
    read -r -p "proceed? [y/N] " ans
    case "$ans" in y|Y|yes) ;; *) echo "nothing installed"; exit 1 ;; esac
  else
    echo "not a terminal: re-run with --yes to proceed"
    exit 2
  fi
fi
if [ ${#missing[@]} -gt 0 ]; then
  "$py" -m pip install "${missing[@]}"
fi
if [ "$model_ready" = 0 ] && "$py" -c "import rembg" >/dev/null 2>&1; then
  "$py" -c "from rembg import new_session; new_session('$model'); print('model ready: $model')"
fi
echo "done; run setup.sh --check to confirm"
