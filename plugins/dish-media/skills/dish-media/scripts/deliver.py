#!/usr/bin/env python3
"""Assemble the delivery folder for the tablets.

    deliver.py --project ~/menus/trattoria [--out ~/menus/trattoria/deliver] [--force]

Copies each dish's cutout when one exists (otherwise the graded photo) and
each loop into deliver/photos and deliver/loops, and writes
deliver/manifest.csv. Refuses while qa/report.md is missing or the flags
hold an error, unless --force.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", help="default <project>/deliver")
    ap.add_argument("--force", action="store_true", help="deliver despite error flags")
    args = ap.parse_args()
    P = Path(args.project)
    out = Path(args.out) if args.out else P / "deliver"
    report = P / "qa" / "report.md"
    if not report.is_file() and not args.force:
        C.die(f"{report} missing; run qa_report.py first (or --force)")
    errors = [f for f in C.load_flags(P / "qa") if f.get("severity") == "error"]
    if errors and not args.force:
        for f in errors:
            C.log(f"  error: {f.get('file')}: {f.get('code')}: {f.get('detail')}")
        C.die(f"{len(errors)} error flag(s) in {P / 'qa' / 'flags.json'}; fix them or use --force")
    manifest = P / "work" / "manifest.csv"
    if not manifest.is_file():
        C.die(f"{manifest} missing; run ingest.py first")
    with manifest.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    (out / "photos").mkdir(parents=True, exist_ok=True)
    (out / "loops").mkdir(parents=True, exist_ok=True)
    delivered, missing = [], []
    for r in rows:
        if r.get("kind") == "photo" and r.get("work_file"):
            stem = C.stem_of(r["work_file"])
            src = P / "out" / "cutouts" / f"{stem}.jpg"
            kind = "cutout"
            if not src.is_file():
                src, kind = P / "out" / "photos" / f"{stem}.jpg", "graded"
            if not src.is_file():
                missing.append(stem)
                continue
            shutil.copyfile(src, out / "photos" / f"{stem}.jpg")
            delivered.append({"stem": stem, "photo": f"photos/{stem}.jpg", "photo_kind": kind, "loop": "",
                              "original": r.get("original_file", ""), "captured_at": r.get("captured_at", "")})
        elif r.get("kind") == "video":
            stem = C.stem_of(r["original_file"])
            src = P / "out" / "loops" / f"{stem}.mp4"
            if not src.is_file():
                missing.append(stem)
                continue
            shutil.copyfile(src, out / "loops" / f"{stem}.mp4")
            delivered.append({"stem": stem, "photo": "", "photo_kind": "", "loop": f"loops/{stem}.mp4",
                              "original": r.get("original_file", ""), "captured_at": ""})
    with (out / "manifest.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["stem", "photo", "photo_kind", "loop", "original", "captured_at"])
        w.writeheader()
        w.writerows(sorted(delivered, key=lambda d: (d["stem"], d["loop"])))
    n_p = sum(1 for d in delivered if d["photo"])
    n_l = sum(1 for d in delivered if d["loop"])
    C.log(f"delivered {n_p} photos and {n_l} loops -> {out}" + (f"; missing: {', '.join(missing)}" if missing else ""))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
