#!/usr/bin/env python3
"""Before/after sheets from an eval run: raw | graded | cutout per dish, raw frame | loop frame per clip.

    before_after.py <eval-name> <run-outputs-dir> --out sheet.jpg [--max-rows 5]

Raw frames come from the read-only eval inputs, outputs from the run's project folder.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "skills" / "dish-media" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _common as C  # noqa: E402
from contact_sheet import font  # noqa: E402

INPUTS = HERE / "results" / "data" / "evals"
GREY, BAND, TEXT = (128, 128, 128), (38, 38, 38), (235, 235, 235)


def first_frame(path: Path, t: float = 0.0):
    import cv2
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, fr = cap.read()
    cap.release()
    if not ok:
        return None
    return cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)


def fit(rgb8, w, h):
    from PIL import Image
    im = Image.fromarray(rgb8)
    im.thumbnail((w, h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (w, h), GREY)
    canvas.paste(im, ((w - im.size[0]) // 2, (h - im.size[1]) // 2))
    return canvas


def sheet(rows: list[tuple[str, list[tuple[str, np.ndarray | None]]]], title: str, out: Path, cols: int = 3, max_edge: int = 1568):
    from PIL import Image, ImageDraw
    gutter, label_h, header_h = 8, 22, 26
    cell_w = (max_edge - gutter * (cols + 1)) // cols
    cell_h = int(round(cell_w * 3 / 4))
    W = max_edge
    H = header_h + len(rows) * (cell_h + label_h + gutter) + gutter
    im = Image.new("RGB", (W, H), GREY)
    d = ImageDraw.Draw(im)
    f, fh = font(13), font(15)
    d.rectangle([0, 0, W, header_h], fill=BAND)
    d.text((gutter, 5), title, fill=TEXT, font=fh)
    for r, (name, cells) in enumerate(rows):
        y = header_h + gutter + r * (cell_h + label_h + gutter)
        for c in range(cols):
            x = gutter + c * (cell_w + gutter)
            label, arr = cells[c] if c < len(cells) else ("", None)
            if arr is not None:
                im.paste(fit(arr, cell_w, cell_h), (x, y))
            else:
                d.rectangle([x, y, x + cell_w, y + cell_h], fill=(96, 96, 96))
                d.text((x + 8, y + cell_h // 2 - 8), "none", fill=TEXT, font=fh)
            d.rectangle([x, y + cell_h, x + cell_w, y + cell_h + label_h], fill=BAND)
            d.text((x + 4, y + cell_h + 4), f"{name}  ·  {label}" if c == 0 else label, fill=TEXT, font=f)
    C.save_jpeg(out, np.asarray(im), quality=85)
    return im.size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("eval_name")
    ap.add_argument("run_outputs")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-rows", type=int, default=5)
    ap.add_argument("--offset", type=int, default=0, help="skip this many dishes/clips first (for paging)")
    ap.add_argument("--title", default="")
    args = ap.parse_args()
    out_dir = Path(args.run_outputs)
    proj = out_dir / "project"
    rows = []
    if (INPUTS / args.eval_name / "raw" / "photos").is_dir() and any((INPUTS / args.eval_name / "raw" / "photos").iterdir()):
        raw_dir = INPUTS / args.eval_name / "raw" / "photos"
        deliver = proj / "deliver" / "photos"
        graded_dir = proj / "out" / "photos"
        cut_dir = proj / "out" / "cutouts"
        stems = [p.stem for p in sorted(deliver.glob("*.jpg"))] if deliver.is_dir() else [p.stem for p in sorted(graded_dir.glob("*.jpg"))]
        for stem in stems[args.offset: args.offset + args.max_rows]:
            work = proj / "work" / "photos" / f"{stem}.jpg"      # the ingested copy: the original, only resized to sRGB
            raw = work if work.is_file() else next((p for p in raw_dir.iterdir() if p.stem == stem and p.suffix.lower() != ".heic"), None)
            cells = [("before: as shot", C.load_rgb8(raw) if raw else None)]
            g = graded_dir / f"{stem}.jpg"
            cells.append(("after: graded (session look, crop)", C.load_rgb8(g) if g.is_file() else None))
            cu = cut_dir / f"{stem}.jpg"
            if cu.is_file():
                cells.append(("after: cutout on #F6F4EF", C.load_rgb8(cu)))
            else:
                cells.append(("no cutout (delivered as the graded photo)", None))
            rows.append((stem, cells))
    else:
        raw_dir = INPUTS / args.eval_name / "raw" / "video"
        loops = proj / "out" / "loops"
        for clip in sorted(raw_dir.glob("*.mov"))[args.offset: args.offset + args.max_rows]:
            lp = loops / f"{clip.stem}.mp4"
            cells = [("before: source clip, first frame", first_frame(clip, 1.0)),
                     ("after: loop, first frame (LUT applied)", first_frame(lp) if lp.is_file() else None),
                     ("after: loop, last frame (the seam)", None)]
            if lp.is_file():
                r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames,r_frame_rate", "-of", "json", str(lp)], capture_output=True, text=True)
                st = json.loads(r.stdout)["streams"][0]
                n = int(st["nb_read_frames"]); num, _, den = st["r_frame_rate"].partition("/")
                cells[2] = ("after: loop, last frame (the seam)", first_frame(lp, (n - 1) / (float(num) / float(den or 1))))
            rows.append((clip.stem, cells))
    # no cutouts anywhere: two bigger columns instead of an empty third one
    if rows and all(len(r[1]) >= 3 and r[1][2][1] is None for r in rows):
        rows = [(n, c[:2]) for n, c in rows]
        size = sheet(rows, args.title or f"{args.eval_name}: before / after", Path(args.out), cols=2)
    else:
        size = sheet(rows, args.title or f"{args.eval_name}: before / after", Path(args.out))
    print(f"{args.out} {size[0]}x{size[1]} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
