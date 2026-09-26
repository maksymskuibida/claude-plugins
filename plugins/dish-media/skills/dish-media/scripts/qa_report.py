#!/usr/bin/env python3
"""Merge every step's flags into one qa/report.md.

    qa_report.py --project ~/menus/trattoria       # standard layout
    qa_report.py --manifest work/manifest.csv --flags qa/flags.json ... --out qa/report.md

Adds the cross-file checks only a whole batch can answer: exposure
outliers (median luminance far from the batch), colour-cast outliers
(OKLab a/b of the bright pixels far from the batch), a card that did not
come out neutral, and missing outputs. Exit code 1 when any flag has
severity `error`, so deliver.py can refuse.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

EXPOSURE_TOL = 0.06   # median luminance, sRGB 0-1
CAST_TOL = 0.015      # OKLab a/b distance

HINTS = {
    "unreadable": "the original could not be decoded; re-export it from Photos as JPEG",
    "hdr_source": "run tonemap_hdr.sh on the clip, then video_grade.py on the result; turn HDR Video off on the phone",
    "small_source": "fine for a tablet if it is at least the output size; otherwise reshoot",
    "no_capture_time": "harmless; the manifest just has no date for it",
    "clip_high": "per-file override: exposure_ev=-0.2 (or raise `highlights`); check the plate on the sheet",
    "clip_low": "per-file override: exposure_ev=+0.2 or shadows=0.2",
    "exposure_outlier": "per-file override: exposure_ev to bring Y50 back to the batch",
    "cast_outlier": "per-file override: wb_gains, or reshoot with the card; check the sheet first",
    "card_not_neutral": "the session's own card is off neutral; re-measure the card and re-run calibrate.py",
    "matte_low_confidence": "look at the cutout sheet; try cutout.py --erode 2 --feather 2, or deliver the plain graded photo",
    "period_ambiguous": "2-fold symmetric dish: check the frame grid, or set --duration to the full turn",
    "period_weak": "the turntable may not have completed a turn in the clip; check the grid or use --loop pingpong",
    "video_failed": "see the detail; the clip was not exported",
    "loop_seam": "re-run video_grade.py with a different --start, or --loop pingpong",
    "missing_output": "the step that produces this file did not run for it, or it failed",
}


def read_manifest(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(newline="") as fh:
        return list(csv.DictReader(fh))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", help="project folder with raw/ work/ out/ qa/")
    ap.add_argument("--manifest")
    ap.add_argument("--session")
    ap.add_argument("--flags")
    ap.add_argument("--grade-stats")
    ap.add_argument("--video-stats")
    ap.add_argument("--out-photos")
    ap.add_argument("--out-cutouts")
    ap.add_argument("--out-loops")
    ap.add_argument("--out", help="report path (default qa/report.md)")
    args = ap.parse_args()
    P = Path(args.project) if args.project else None

    def pick(v, rel):
        return Path(v) if v else (P / rel if P else None)

    manifest = pick(args.manifest, "work/manifest.csv")
    session_p = pick(args.session, "work/session.json")
    flags_p = pick(args.flags, "qa/flags.json")
    gstats_p = pick(args.grade_stats, "qa/grade_stats.json")
    vstats_p = pick(args.video_stats, "qa/video_stats.json")
    photos = pick(args.out_photos, "out/photos")
    cutouts = pick(args.out_cutouts, "out/cutouts")
    loops = pick(args.out_loops, "out/loops")
    out = pick(args.out, "qa/report.md")
    if out is None:
        C.die("give --project or --out")
    qa_dir = flags_p.parent if flags_p else out.parent

    def rel(path):  # paths in the report relative to the project, so two runs read the same
        if path is None:
            return ""
        try:
            return str(Path(path).resolve().relative_to(P.resolve())) if P else Path(path).name
        except ValueError:
            return Path(path).name

    rows = read_manifest(manifest) if manifest else []
    session = C.load_session(session_p) if session_p and session_p.is_file() else None
    gstats = json.loads(gstats_p.read_text()) if gstats_p and gstats_p.is_file() else {}
    vstats = json.loads(vstats_p.read_text()) if vstats_p and vstats_p.is_file() else {}

    computed = []
    # exposure and cast outliers across the batch
    meds = {k: v["y_median"] for k, v in gstats.items() if "y_median" in v}
    if len(meds) >= 3:
        batch = statistics.median(meds.values())
        for k, m in sorted(meds.items()):
            if abs(m - batch) > EXPOSURE_TOL:
                computed.append({"file": f"{k}.jpg", "code": "exposure_outlier", "value": round(m - batch, 3),
                                 "detail": f"Y50 {m:.3f} vs batch {batch:.3f}"})
    casts = {k: v["bright_ab"] for k, v in gstats.items() if v.get("bright_ab")}
    if len(casts) >= 3:
        a0 = statistics.median(v[0] for v in casts.values())
        b0 = statistics.median(v[1] for v in casts.values())
        for k, (a, b) in sorted(casts.items()):
            dist = ((a - a0) ** 2 + (b - b0) ** 2) ** 0.5
            if dist > CAST_TOL:
                computed.append({"file": f"{k}.jpg", "code": "cast_outlier", "value": round(dist, 4),
                                 "detail": f"bright-pixel OKLab a/b ({a:+.3f},{b:+.3f}) vs batch ({a0:+.3f},{b0:+.3f})"})
    if session:
        pred = (session.get("provenance") or {}).get("predicted_card_srgb255")
        tol = ((session.get("provenance") or {}).get("targets") or {}).get("neutral_tolerance_255", 1.0)
        if pred and max(abs(pred[0] - pred[1]), abs(pred[2] - pred[1])) > tol:
            computed.append({"file": "session.json", "code": "card_not_neutral", "severity": "error",
                             "detail": f"predicted card {pred}, tolerance {tol}/255"})
    # missing outputs
    want_cutouts = bool(session and session.get("background"))
    for r in rows:
        if r.get("kind") == "photo" and r.get("work_file"):
            stem = C.stem_of(r["work_file"])
            if photos and photos.is_dir() and not (photos / f"{stem}.jpg").is_file():
                computed.append({"file": f"{stem}.jpg", "code": "missing_output", "severity": "error", "detail": f"no graded photo in {rel(photos)}"})
            if want_cutouts and cutouts and cutouts.is_dir() and not (cutouts / f"{stem}.jpg").is_file():
                computed.append({"file": f"{stem}.jpg", "code": "missing_output", "detail": f"no cutout in {rel(cutouts)}"})
        elif r.get("kind") == "video":
            stem = C.stem_of(r["original_file"])
            if loops and loops.is_dir() and not (loops / f"{stem}.mp4").is_file():
                computed.append({"file": r["original_file"], "code": "missing_output", "severity": "error", "detail": f"no loop in {rel(loops)}"})
    C.write_flags(qa_dir, "qa", [f["file"] for f in computed] + [r.get("work_file") or r.get("original_file") for r in rows] + list(gstats), computed)
    flags = C.load_flags(qa_dir)

    by_sev = {"error": [], "warn": [], "info": []}
    for f in flags:
        by_sev.setdefault(f.get("severity", "warn"), []).append(f)
    n_photos = sum(1 for r in rows if r.get("kind") == "photo")
    n_videos = sum(1 for r in rows if r.get("kind") == "video")
    lines = ["# dish-media QA report", ""]
    lines.append(f"- manifest: {n_photos} photos, {n_videos} clips" + (f" ({rel(manifest)})" if manifest else ""))
    if session:
        prov = session.get("provenance") or {}
        lines.append(f"- session: wb_gains {session['wb_gains']}, exposure x{session['exposure']}, contrast {session['contrast']}, "
                     f"shadows {session['shadows']}, highlights {session['highlights']}, saturation {session['saturation']}, "
                     f"background {session.get('background')}, {len(session.get('overrides') or {})} override(s)")
        if prov.get("predicted_card_srgb255"):
            lines.append(f"- predicted card: {prov['predicted_card_srgb255']} (target {round(prov['targets']['card_luminance_srgb'] * 255, 1)})")
    if meds:
        lines.append(f"- graded: {len(meds)} files, Y50 median {statistics.median(meds.values()):.3f}, "
                     f"range {min(meds.values()):.3f}..{max(meds.values()):.3f}")
    if vstats:
        seams = [v.get("seam_similarity") for v in vstats.values() if v.get("seam_similarity") is not None]
        lines.append(f"- loops: {len(vstats)} clips" + (f", seam similarity min {min(seams):.3f}" if seams else ""))
    lines.append(f"- flags: {len(by_sev['error'])} error, {len(by_sev['warn'])} warn, {len(by_sev['info'])} info")
    lines.append("")
    for sev, title in (("error", "Errors (fix before delivery)"), ("warn", "Warnings (look at the sheet)"), ("info", "Info")):
        items = by_sev.get(sev) or []
        lines.append(f"## {title}: {len(items)}")
        lines.append("")
        if not items:
            lines.append("none")
            lines.append("")
            continue
        lines.append("| file | code | step | value | detail |")
        lines.append("|---|---|---|---|---|")
        for f in items:
            val = f.get("value")
            lines.append(f"| {f.get('file','')} | `{f.get('code','')}` | {f.get('step','')} | {'' if val is None else val} | {str(f.get('detail','')).replace('|', '/')} |")
        lines.append("")
    codes = sorted({f.get("code") for f in flags if f.get("code")})
    if codes:
        lines.append("## What to do")
        lines.append("")
        for code in codes:
            lines.append(f"- `{code}`: {HINTS.get(code, f.get('hint', ''))}")
        lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    C.log(f"{out}: {len(by_sev['error'])} error, {len(by_sev['warn'])} warn, {len(by_sev['info'])} info")
    return 1 if by_sev["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
