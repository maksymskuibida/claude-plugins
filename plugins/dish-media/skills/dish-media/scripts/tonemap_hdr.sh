#!/usr/bin/env bash
# Tone-map a clip that was shot in HDR by mistake down to SDR Rec.709.
#
#   tonemap_hdr.sh IN.mov OUT.mov [hlg|pq]
#
# Linearises with zscale, tone-maps with the Hable operator (keeps shadow
# detail better than Reinhard), re-encodes as a high-quality SDR
# intermediate for video_grade.py. The transfer is read from the file's
# tags; pass hlg or pq when the clip is untagged. This is a repair, not a
# workflow: turn HDR video off in the phone's camera app (iPhone: Settings >
# Camera > Record Video > HDR Video; Android: the HDR10+ / HDR video switch)
# and shoot SDR, then none of this is needed.
set -euo pipefail

if [ $# -lt 2 ]; then
  sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
fi
in="$1"; out="$2"; mode="${3:-auto}"
command -v ffmpeg >/dev/null || { echo "ffmpeg not found" >&2; exit 2; }
command -v ffprobe >/dev/null || { echo "ffprobe not found" >&2; exit 2; }

trc="$(ffprobe -v error -select_streams v:0 -show_entries stream=color_transfer -of default=nw=1:nk=1 "$in" || true)"
case "$mode" in
  hlg) tin="arib-std-b67" ;;
  pq)  tin="smpte2084" ;;
  auto)
    case "$trc" in
      arib-std-b67|smpte2084) tin="$trc" ;;
      "") echo "$in carries no transfer tag; say which it is: tonemap_hdr.sh IN OUT hlg|pq" >&2; exit 2 ;;
      *)  echo "$in is tagged $trc, which is not HDR; nothing to tone-map" >&2; exit 1 ;;
    esac ;;
  *) echo "mode must be hlg, pq or auto" >&2; exit 2 ;;
esac

# Explicit input tags so an untagged 10-bit clip is still read as bt2020 HDR.
vf="zscale=tin=${tin}:pin=bt2020:min=bt2020nc:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
echo "tone-mapping $in ($tin -> bt709) -> $out" >&2
ffmpeg -v error -y -i "$in" -vf "$vf" -an \
  -c:v libx264 -preset medium -crf 16 -pix_fmt yuv420p \
  -color_primaries bt709 -color_trc bt709 -colorspace bt709 -color_range tv \
  -map_metadata -1 -fflags +bitexact -flags:v +bitexact -movflags +faststart "$out"
echo "done: $out (SDR intermediate; feed it to video_grade.py)" >&2
