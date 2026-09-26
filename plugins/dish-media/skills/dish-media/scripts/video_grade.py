#!/usr/bin/env python3
"""Trim, grade and loop turntable clips with the session look.

    video_grade.py --session work/session.json --in raw/video --out out/loops --qa qa
    video_grade.py --session work/session.json --in raw/video/dish-0412.mov \
        --out out/loops/dish-0412.mp4 --loop pingpong --start 1.5 --duration 5

The look is the same one grade.py applies to photos, baked into a 3D LUT
(.cube) and applied with ffmpeg's lut3d, so stills and clips match.
`--loop revolution` cuts exactly one turn (period from loop_period.py);
`--loop pingpong` plays the segment forward then backward. Output is
1080p H.264 (yuv420p, CRF 20, faststart, no audio, no metadata), which is
what a cheap Android tablet plays without surprises. Per-file `overrides`
in the session may set start, duration, loop and any look parameter.
HDR sources are refused: run tonemap_hdr.sh first (and shoot SDR).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402
import loop_period  # noqa: E402

LOOK_KEYS = ("wb_gains", "exposure", "contrast", "shadows", "highlights", "saturation")


def probe(path: Path) -> dict:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,r_frame_rate,avg_frame_rate,color_transfer,color_primaries,color_space,color_range,pix_fmt:format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True)
    if r.returncode != 0:
        C.die(f"ffprobe failed on {path}: {r.stderr.strip()}")
    d = json.loads(r.stdout)
    s = (d.get("streams") or [{}])[0]
    num, _, den = (s.get("r_frame_rate") or s.get("avg_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1)
    return {"width": int(s.get("width", 0)), "height": int(s.get("height", 0)), "fps": fps,
            "fps_num": int(num), "fps_den": int(den or 1),
            "duration": float(d.get("format", {}).get("duration") or 0.0),
            "transfer": s.get("color_transfer") or "", "primaries": s.get("color_primaries") or "",
            "matrix": s.get("color_space") or "", "range": s.get("color_range") or "", "pix_fmt": s.get("pix_fmt") or ""}


def look_of(p: dict) -> dict:
    return {k: p[k] for k in LOOK_KEYS}


def build_filters(p: dict, info: dict, lut: Path, n_frames: int, loop: str, long_edge: int, stabilize_trf: Path | None,
                  reverse: bool = False) -> str:
    chain = []
    if not info["transfer"] or not info["matrix"]:
        chain.append("setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv")
    chain.append("setpts=PTS-STARTPTS")
    if stabilize_trf:
        chain.append(f"vidstabtransform=input={stabilize_trf}:smoothing=10:zoom=1:optzoom=1:interpol=bicubic")
    chain.append("format=gbrp16le")
    chain.append(f"lut3d=file={lut}:interp=tetrahedral")
    w, h = info["width"], info["height"]
    scale = min(1.0, long_edge / max(w, h))
    ow, oh = int(round(w * scale / 2)) * 2, int(round(h * scale / 2)) * 2
    chain.append(f"scale={ow}:{oh}:flags=lanczos:out_color_matrix=bt709:out_range=tv,format=yuv420p")
    if reverse:
        chain.append("reverse")          # the whole segment backwards: a revolution stays a revolution
    vf = ",".join(chain)
    # exact constant-rate timestamps and durations: concat and reverse leave
    # uneven pts and no frame durations, and a last frame without a duration
    # ends up outside the edit list and is dropped by players
    retime = f"settb={info['fps_den']}/{info['fps_num']},setpts=N,fps={info['fps_num']}/{info['fps_den']}"
    if loop == "pingpong":
        return (f"[0:v]{vf},split[a][b];[b]reverse,trim=start_frame=1:end_frame={max(2, n_frames - 1)},setpts=PTS-STARTPTS[r];"
                f"[a][r]concat=n=2:v=1:a=0,{retime}[v]")
    return f"[0:v]{vf},{retime}[v]"


def grade_clip(src: Path, dst: Path, session: dict, args, lut_dir: Path, qa: Path | None) -> dict:
    p = C.params_for(session, src.name)
    info = probe(src)
    if info["transfer"] in ("arib-std-b67", "smpte2084"):
        raise RuntimeError(f"{src.name} is HDR ({info['transfer']}); run tonemap_hdr.sh on it first (and shoot SDR next time)")
    start = float(p.get("start", args.start) if p.get("start") is not None else args.start)
    loop = p.get("loop") or args.loop
    reverse = bool(p.get("reverse")) or bool(args.reverse)
    duration = p.get("duration") if p.get("duration") is not None else args.duration
    entry = {"source": src.name, "loop": loop, "start_s": start, "reverse": reverse, "fps": round(info["fps"], 4),
             "source_duration_s": round(info["duration"], 3), "source_size": [info["width"], info["height"]]}
    period = None
    if duration is None or loop == "revolution":
        period = loop_period.detect(src, start=start, min_period=args.min_period, max_period=args.max_period)
        entry["period"] = {k: period.get(k) for k in ("period_frames", "period_s", "score", "score_half_period", "warnings")}
        d = period.get("direction", "none")
        entry["direction"] = ("ccw" if d == "cw" else "cw") if (reverse and d in ("cw", "ccw")) else d
        entry["direction_consistency"] = period.get("direction_consistency")
    if duration is None:
        if loop == "revolution":
            if not period.get("period_frames"):
                raise RuntimeError(f"no revolution found in {src.name}: {'; '.join(period.get('warnings', []))}")
            duration = period["period_frames"] / info["fps"]
        else:
            duration = args.pingpong_duration
    duration = float(duration)
    if start + duration > info["duration"] + 1e-3:
        raise RuntimeError(f"{src.name}: start {start}s + duration {duration:.3f}s exceeds the clip ({info['duration']:.3f}s)")
    n_frames = int(round(duration * info["fps"]))
    entry.update({"duration_s": round(duration, 4), "frames": n_frames})

    # LUT: shared unless this file overrides the look
    if look_of(p) == look_of(C.params_for(session, "__none__")):
        lut = lut_dir / "session.cube"
    else:
        lut = lut_dir / f"{src.stem}.cube"
    lut.parent.mkdir(parents=True, exist_ok=True)
    C.write_cube_lut(lut, p, size=args.lut_size, title=f"dish-media {src.stem if lut.name != 'session.cube' else 'session'}")
    entry["lut"] = str(lut)

    trf = None
    common_in = ["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.4f}", "-t", f"{duration:.4f}", "-i", str(src)]
    if args.stabilize:
        trf = lut_dir / f"{src.stem}.trf"
        r = C.run(common_in + ["-vf", f"vidstabdetect=shakiness=4:accuracy=15:result={trf}", "-f", "null", "-"])
        if r.returncode != 0:
            raise RuntimeError(f"vidstabdetect failed on {src.name}")
    fc = build_filters(p, info, lut, n_frames, loop, args.long_edge, trf, reverse=reverse)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = common_in + [
        "-filter_complex", fc, "-map", "[v]", "-an",
        "-c:v", "libx264", "-preset", args.preset, "-crf", str(args.crf), "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv",
        "-map_metadata", "-1", "-fflags", "+bitexact", "-flags:v", "+bitexact", "-movflags", "+faststart", str(dst)]
    if args.dry_run:
        C.log("$ " + " ".join(cmd))
        return entry
    r = C.run(cmd)
    if r.returncode != 0 or not dst.is_file():
        raise RuntimeError(f"ffmpeg failed on {src.name}")
    entry["output"] = dst.name
    return entry


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--session", required=True)
    ap.add_argument("--in", dest="inp", required=True, help="clip or folder of clips")
    ap.add_argument("--out", required=True, help="output .mp4 (one clip) or folder")
    ap.add_argument("--qa", help="qa folder for video_stats.json and flags.json")
    ap.add_argument("--loop", choices=["revolution", "pingpong", "none"], default="revolution")
    ap.add_argument("--start", type=float, default=1.0, help="seconds into the clip where the cut begins")
    ap.add_argument("--duration", type=float, help="seconds; default: one detected revolution, or --pingpong-duration")
    ap.add_argument("--pingpong-duration", type=float, default=5.0)
    ap.add_argument("--min-period", type=float, default=4.0)
    ap.add_argument("--max-period", type=float, default=20.0)
    ap.add_argument("--stabilize", action="store_true", help="two-pass vidstab (rarely needed on a locked-off phone)")
    ap.add_argument("--reverse", action="store_true", help="play the segment backwards (a clip that turns the other way)")
    ap.add_argument("--long-edge", type=int, default=1920)
    ap.add_argument("--crf", type=int, default=20)
    ap.add_argument("--preset", default="medium")
    ap.add_argument("--lut-size", type=int, default=33)
    ap.add_argument("--lut-dir", help="where .cube files go (default: next to the session file)")
    ap.add_argument("--dry-run", action="store_true", help="print the ffmpeg command, encode nothing")
    args = ap.parse_args()
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        C.die("ffmpeg and ffprobe are required (brew install ffmpeg)")

    session = C.load_session(args.session)
    lut_dir = Path(args.lut_dir) if args.lut_dir else Path(args.session).resolve().parent
    src = Path(args.inp)
    clips = C.list_videos(src)
    if not clips:
        C.die(f"no clips in {src}")
    out = Path(args.out)
    single = out.suffix.lower() == ".mp4"
    if single and len(clips) > 1:
        C.die("--out must be a folder when --in holds more than one clip")
    qa = Path(args.qa) if args.qa else None

    stats, flags, failed = {}, [], 0
    for clip in clips:
        dst = out if single else out / f"{clip.stem}.mp4"
        try:
            entry = grade_clip(clip, dst, session, args, lut_dir, qa)
            stats[dst.stem] = entry   # keyed by the output loop, like frame_grid.py
            per = entry.get("period") or {}
            msg = f"  {clip.name}: {entry['loop']} start {entry['start_s']}s duration {entry['duration_s']}s ({entry['frames']} frames)"
            if per:
                msg += f"  period {per.get('period_s')}s score {per.get('score')}"
            if entry.get("direction"):
                msg += f"  turns {entry['direction']}"
            C.log(msg)
            for w in per.get("warnings") or []:
                code = "period_ambiguous" if "symmetric" in w else "period_weak"
                C.log(f"    warning ({code}): {w}")
                flags.append({"file": clip.name, "code": code, "detail": w, "hint": "check the frame grid; set an explicit --duration or use pingpong"})
        except RuntimeError as e:
            failed += 1
            C.log(f"  ! {e}")
            flags.append({"file": clip.name, "code": "video_failed", "severity": "error", "detail": str(e)})
    if qa and not args.dry_run:
        p = qa / "video_stats.json"
        existing = json.loads(p.read_text()) if p.is_file() else {}
        merged = {k: {**(existing.get(k) or {}), **v} for k, v in stats.items()}
        C.update_stats(p, merged)
        C.write_flags(qa, "video", [c.name for c in clips], flags)
    C.log(f"{len(stats)} clip(s) done, {failed} failed -> {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
